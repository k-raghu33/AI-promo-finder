"""Unit tests. Run: python -m unittest discover -s tests -v

Uses only the fictional sample in examples/ so the tests never need Kuku's
confidential screenplays.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src.candidate_generator import Candidate, Segment, cut_points, generate_single_candidates  # noqa: E402
from src.llm_judge import LLMJudge  # noqa: E402
from src.promo_ranker import overlap_ratio, run_pipeline, same_opening  # noqa: E402
from src.promo_scorer import ScoringContext, hits, score_candidate  # noqa: E402
from src.screenplay_parser import format_timestamp, load_episodes, parse_timestamp  # noqa: E402

EXAMPLES = ROOT / "examples"


class TestParser(unittest.TestCase):
    def test_timestamps_roundtrip(self):
        self.assertAlmostEqual(parse_timestamp("01:23.45"), 83.45)
        self.assertAlmostEqual(parse_timestamp("00:07.5"), 7.5)
        self.assertEqual(format_timestamp(83.45), "01:23.45")
        with self.assertRaises(ValueError):
            parse_timestamp("1:2:3")

    def test_parses_sample(self):
        eps = load_episodes(EXAMPLES)
        self.assertEqual([e.number for e in eps], [1, 2])
        e1 = eps[0]
        self.assertTrue(e1.logline)
        self.assertEqual(len(e1.scenes), 1)
        self.assertEqual(e1.warnings, [])
        kinds = {b.kind for b in e1.blocks}
        self.assertEqual(kinds, {"camera", "action", "sfx", "dialogue"})
        first_line = e1.dialogue[0]
        self.assertEqual(first_line.speaker, "SELENE")
        self.assertEqual(first_line.delivery, "Sweet")
        self.assertFalse(first_line.text.startswith('"'))

    def test_uppercase_headers(self):
        txt = (EXAMPLES / "episode-001-sample.txt").read_text()
        txt = txt.replace("Scene start:", "SCENE START:").replace("Scene end:", "SCENE END:")
        with tempfile.TemporaryDirectory() as d:
            Path(d, "ep.txt").write_text(txt)
            ep = load_episodes(d)[0]
        self.assertEqual(len(ep.scenes), 1)
        self.assertEqual(ep.warnings, [])


class TestCandidates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eps = load_episodes(EXAMPLES)

    def test_cuts_never_split_dialogue(self):
        for ep in self.eps:
            starts, ends = cut_points(ep)
            for t in starts + ends:
                for b in ep.dialogue:
                    self.assertFalse(b.start + 0.05 < t < b.end - 0.05, f"cut {t} inside line {b.text}")

    def test_durations_in_range(self):
        for ep in self.eps:
            for c in generate_single_candidates(ep):
                self.assertGreaterEqual(c.duration, config.MIN_DURATION - 1e-6)
                self.assertLessEqual(c.duration, config.MAX_DURATION + 1e-6)

    def test_overlap_ratio(self):
        a = Candidate(segments=(Segment(1, 0, 30),))
        b = Candidate(segments=(Segment(1, 15, 45),))
        c = Candidate(segments=(Segment(2, 0, 30),))
        self.assertAlmostEqual(overlap_ratio(a, b), 0.5)
        self.assertEqual(overlap_ratio(a, c), 0.0)
        # a 3 s sting reused inside a 30 s stitched promo is 10% of it, not 100%
        stitched = Candidate(segments=(Segment(1, 10, 13), Segment(3, 0, 27)), kind="stitched")
        self.assertAlmostEqual(overlap_ratio(a, stitched), 0.1)
        self.assertTrue(same_opening(Candidate(segments=(Segment(1, 10, 13), Segment(1, 40, 60))),
                                     Candidate(segments=(Segment(1, 11, 45),))))
        self.assertFalse(same_opening(a, b))


class TestScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eps = load_episodes(EXAMPLES)
        cls.ctx = ScoringContext(cls.eps)

    def test_whole_word_matching(self):
        self.assertEqual(hits("SHOCK_LINE_TERMS", "a beggar's coat"), [])
        self.assertEqual(hits("PHYSICAL_HOOK_TERMS", "explosive fury in her eyes"), [])
        self.assertIn("slaps", hits("PHYSICAL_HOOK_TERMS", "she slaps him"))

    def test_slap_open_beats_establishing_open(self):
        slap = score_candidate(self.ctx, Candidate(segments=(Segment(1, 13.5, 39.5),)))
        calm = score_candidate(self.ctx, Candidate(segments=(Segment(1, 0.0, 26.5),)))
        self.assertGreater(slap.scores["hook"], calm.scores["hook"])
        self.assertTrue(any("physical" in e for e in slap.evidence["hook"]))

    def test_episode_ending_cliffhanger_detected(self):
        ep = self.eps[0]
        starts, _ = cut_points(ep)
        s = next(t for t in starts if 25 <= ep.duration - t <= 40)
        c = score_candidate(self.ctx, Candidate(segments=(Segment(1, s, round(ep.duration, 2)),)))
        self.assertIn("episode-ending cut", c.evidence["_cliff_info"]["flags"])
        self.assertGreaterEqual(c.scores["cliffhanger"], 0.5)

    def test_out_of_range_duration_rejected(self):
        c = score_candidate(self.ctx, Candidate(segments=(Segment(1, 0.0, 10.0),)))
        self.assertFalse(c.eligible)


class TestPipeline(unittest.TestCase):
    def test_deterministic(self):
        a = run_pipeline(load_episodes(EXAMPLES), log=lambda *x: None)
        b = run_pipeline(load_episodes(EXAMPLES), log=lambda *x: None)
        self.assertEqual([c.id for c in a["ranked"]], [c.id for c in b["ranked"]])
        self.assertTrue(a["ranked"])
        for c in a["ranked"]:
            self.assertTrue(c.hook and c.cliffhanger and c.why)

    def test_overrides_reject_and_pin(self):
        base = run_pipeline(load_episodes(EXAMPLES), log=lambda *x: None)
        top = base["ranked"][0].id
        rej = run_pipeline(load_episodes(EXAMPLES), overrides={"reject": [top]}, log=lambda *x: None)
        self.assertNotIn(top, [c.id for c in rej["ranked"]])
        raw_ids = [c.id for c in base["raw"] if c.eligible and c.id not in {x.id for x in base["ranked"]}]
        if raw_ids:
            pin = run_pipeline(load_episodes(EXAMPLES), overrides={"pin": [raw_ids[0]]}, log=lambda *x: None)
            self.assertIn(raw_ids[0], [c.id for c in pin["ranked"]])

    def test_mock_llm_path_blends_scores(self):
        r = run_pipeline(load_episodes(EXAMPLES), llm_provider="mock", log=lambda *x: None)
        judged = [c for c in r["ranked"] if "llm_total" in c.scores]
        self.assertTrue(judged)
        for c in judged:
            self.assertIn("mock", c.scoring_mode)

    def test_llm_failure_falls_back(self):
        old = config.OPENAI_API_KEY
        config.OPENAI_API_KEY = ""
        try:
            r = run_pipeline(load_episodes(EXAMPLES), llm_provider="openai", log=lambda *x: None)
        finally:
            config.OPENAI_API_KEY = old
        self.assertTrue(r["ranked"])
        self.assertTrue(all(c.scoring_mode == "rules" for c in r["ranked"]))
        self.assertGreater(r["stats"]["llm"]["failures"], 0)

    def test_llm_json_parsing(self):
        j = LLMJudge._parse('Sure! {"hook_score": 12, "cliffhanger_score": 7, "standalone_score": "6",'
                            ' "spoiler_risk": 2, "hook": "h", "cliffhanger": "c", "why": "w"}')
        self.assertEqual(j["hook_score"], 10.0)  # clamped
        self.assertGreater(LLMJudge.to_score(j), 0)

    def test_cli_writes_outputs(self):
        import promo_finder
        with tempfile.TemporaryDirectory() as d:
            rc = promo_finder.main(["--input", str(EXAMPLES), "--output", d, "--quiet"])
            self.assertEqual(rc, 0)
            for f in ("ranked_promos.json", "ranked_promos.csv", "raw_candidates.json", "all_candidates.csv",
                      "quality_report.json", "review_sheet.csv"):
                self.assertTrue(Path(d, f).exists(), f)
            rep = json.loads(Path(d, "quality_report.json").read_text())
            self.assertTrue(all(c["pass"] for c in rep["output_checks"]))


if __name__ == "__main__":
    unittest.main()
