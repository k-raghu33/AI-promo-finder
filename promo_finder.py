#!/usr/bin/env python3
"""Kuku AI Promo Finder: find and rank 20-60s promos in microdrama screenplays.

Examples
  python promo_finder.py --input input/ --output outputs/
  python promo_finder.py --input examples/ --output outputs_demo/
  python promo_finder.py --llm ollama --model llama3.1        # optional semantic judge
  python promo_finder.py --overrides overrides.json           # human corrections
  python promo_finder.py --check-repro                        # run twice, compare
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src import quality_checker as qc  # noqa: E402
from src.promo_ranker import rank_reason, run_pipeline  # noqa: E402
from src.screenplay_parser import load_episodes  # noqa: E402


def tier(score: float) -> str:
    if score >= config.TIER_POST_FIRST:
        return "post-first"
    return "review" if score >= config.TIER_REVIEW else "weak"


def candidate_record(c, rank=None):
    rec = {
        "rank": rank, "promo_id": c.id, "tier": tier(c.total), "type": c.kind, "episodes": c.episodes,
        "segments": [s.to_dict() for s in c.segments], "total_duration_sec": c.duration,
        "score": round(c.total, 4), "scores": c.scores, "eligible": c.eligible,
        "reject_reasons": c.reject_reasons, "hook": c.hook, "cliffhanger": c.cliffhanger,
        "why_it_works": c.why, "scoring_mode": c.scoring_mode,
        "cliffhanger_flags": c.evidence.get("_cliff_info", {}).get("flags", []),
        "evidence": {k: v for k, v in c.evidence.items() if not k.startswith("_")},
    }
    if rank is not None:
        rec["rank_reason"] = rank_reason(c, rank)
    return rec


def write_outputs(result, out_dir: Path, meta: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = [candidate_record(c, i + 1) for i, c in enumerate(result["ranked"])]
    raw = [candidate_record(c) for c in result["raw"]]
    (out_dir / "ranked_promos.json").write_text(json.dumps({"meta": meta, "promos": ranked}, indent=2,
                                                           ensure_ascii=False))
    (out_dir / "raw_candidates.json").write_text(json.dumps(
        {"meta": {**meta, "note": f"top {config.RAW_CANDIDATES_PER_EPISODE} scored candidates per "
                                  "episode (eligible or not), unedited"},
         "stats": result["stats"], "candidates": raw}, indent=2, ensure_ascii=False))
    cols = ["rank", "tier", "promo_id", "type", "episodes", "segments", "total_duration_sec", "score",
            "hook_score", "cliffhanger_score", "emotion_score", "standalone_score", "clarity_score",
            "hook", "cliffhanger", "why_it_works", "rank_reason", "cliffhanger_flags", "scoring_mode"]
    with open(out_dir / "ranked_promos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in ranked:
            w.writerow({
                "rank": r["rank"], "tier": r["tier"], "promo_id": r["promo_id"], "type": r["type"],
                "episodes": ";".join(map(str, r["episodes"])),
                "segments": "; ".join(f"E{s['episode']} {s['start']}-{s['end']}" for s in r["segments"]),
                "total_duration_sec": r["total_duration_sec"], "score": r["score"],
                **{f"{k}_score": r["scores"][k] for k in ("hook", "cliffhanger", "emotion", "standalone", "clarity")},
                "hook": r["hook"], "cliffhanger": r["cliffhanger"], "why_it_works": r["why_it_works"],
                "rank_reason": r["rank_reason"], "cliffhanger_flags": "; ".join(r["cliffhanger_flags"]),
                "scoring_mode": r["scoring_mode"]})
    selected_rank = {c.id: i + 1 for i, c in enumerate(result["ranked"])}
    with open(out_dir / "all_candidates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["promo_id", "type", "episodes", "segments", "total_duration_sec", "rule_score",
                    "hook", "cliffhanger", "emotion", "standalone", "clarity", "eligible",
                    "reject_reasons", "selected_rank"])
        for c in sorted(result["all"], key=lambda c: (c.segments[-1].episode, -c.total, c.id)):
            s = c.scores
            w.writerow([c.id, c.kind, ";".join(map(str, c.episodes)),
                        "; ".join(f"E{x.episode} {x.to_dict()['start']}-{x.to_dict()['end']}" for x in c.segments),
                        c.duration, s.get("rule_total", 0), s.get("hook", ""), s.get("cliffhanger", ""),
                        s.get("emotion", ""), s.get("standalone", ""), s.get("clarity", ""), c.eligible,
                        "; ".join(c.reject_reasons), selected_rank.get(c.id, "")])
    with open(out_dir / "review_sheet.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "tier", "promo_id", "segments", "duration", "hook", "cliffhanger",
                    "reviewer_verdict (post/maybe/reject)", "reject_reason", "adjusted_start",
                    "adjusted_end", "notes"])
        for r in ranked:
            w.writerow([r["rank"], r["tier"], r["promo_id"],
                        "; ".join(f"E{s['episode']} {s['start']}-{s['end']}" for s in r["segments"]),
                        r["total_duration_sec"], r["hook"], r["cliffhanger"], "", "", "", "", ""])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=str(ROOT / "input"), help="folder of episode .txt screenplays")
    ap.add_argument("--output", default=str(ROOT / "outputs"), help="where to write results")
    ap.add_argument("--pattern", default="*.txt", help="glob for screenplay files")
    ap.add_argument("--llm", default=config.LLM_PROVIDER, choices=["none", "openai", "gemini", "ollama", "mock"])
    ap.add_argument("--model", default=config.LLM_MODEL, help="model name for the chosen provider")
    ap.add_argument("--max-per-episode", type=int, default=config.MAX_PROMOS_PER_EPISODE)
    ap.add_argument("--no-script-cues", action="store_true",
                    help="ignore the writers' literal 'cliffhanger' SFX annotations (ablation)")
    ap.add_argument("--overrides", help="JSON with {'reject': [promo_id], 'pin': [promo_id]}")
    ap.add_argument("--check-repro", action="store_true", help="run twice and compare outputs")
    ap.add_argument("--top", type=int, default=10, help="print the top N promos")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    log = (lambda *x: None) if a.quiet else print

    in_dir = Path(a.input)
    files = sorted(in_dir.rglob(a.pattern)) if in_dir.exists() else []
    if not files:
        print(f"No screenplay files matching {a.pattern!r} in {in_dir}. "
              "Put episode .txt files there or pass --input.", file=sys.stderr)
        return 2
    t0 = time.time()
    episodes = load_episodes(in_dir, a.pattern)
    log(f"Parsed {len(episodes)} episodes ({sum(len(e.blocks) for e in episodes)} blocks) from {in_dir}")
    overrides = json.loads(Path(a.overrides).read_text()) if a.overrides else None

    kwargs = dict(llm_provider=a.llm, llm_model=a.model, use_script_cues=not a.no_script_cues,
                  overrides=overrides, cap_per_episode=a.max_per_episode,
                  llm_cache=Path(a.output) / ".llm_cache.json", log=log)
    Path(a.output).mkdir(parents=True, exist_ok=True)
    result = run_pipeline(episodes, **kwargs)
    runtime = round(time.time() - t0, 2)
    st = result["stats"]
    log(f"Scored {st['single_candidates']} single + {st['stitched_candidates']} stitched candidates; "
        f"{st['eligible']} passed hook/cliffhanger gates; selected {st['selected']} promos in {runtime}s")
    if st["llm"]["enabled"]:
        log(f"LLM {st['llm']['provider']}: {st['llm']['calls']} calls, {st['llm']['cache_hits']} cached, "
            f"{st['llm']['failures']} failures (failures fall back to rules)")

    report = {"input_checks": qc.check_inputs(episodes, len(files)),
              "output_checks": qc.check_outputs(result, episodes),
              "distribution": qc.distribution(result["ranked"]),
              "fingerprint": qc.output_fingerprint(result["ranked"])}
    if a.check_repro:
        again = run_pipeline(load_episodes(in_dir, a.pattern), **{**kwargs, "log": lambda *x: None})
        fp2 = qc.output_fingerprint(again["ranked"])
        report["output_checks"].append(qc._check("reproducible (two runs identical)",
                                                 fp2 == report["fingerprint"],
                                                 f"{report['fingerprint']} vs {fp2}"))
    meta = {"tool": "Kuku AI Promo Finder", "input_dir": in_dir.name, "episodes": len(episodes),
            "llm_provider": st["llm"]["provider"], "script_cues": not a.no_script_cues,
            "max_per_episode": a.max_per_episode, "weights": config.WEIGHTS,
            "fingerprint": report["fingerprint"], "runtime_sec": runtime}
    write_outputs(result, Path(a.output), meta)
    (Path(a.output) / "quality_report.json").write_text(json.dumps(report, indent=2))

    tiers = {}
    for c in result["ranked"]:
        tiers[tier(c.total)] = tiers.get(tier(c.total), 0) + 1
    log(f"Tiers: {tiers}")
    fails = [c for c in report["input_checks"] + report["output_checks"] if not c["pass"]]
    log(f"Quality checks: {len(report['input_checks']) + len(report['output_checks']) - len(fails)} passed, "
        f"{len(fails)} failed" + "".join(f"\n  FAIL {c['check']}: {c['detail']}" for c in fails))
    for i, c in enumerate(result["ranked"][: a.top]):
        segs = " + ".join(f"E{s.episode} {s.to_dict()['start']}-{s.to_dict()['end']}" for s in c.segments)
        log(f"{i + 1:>3}. [{c.total:.3f} {tier(c.total)}] {segs} ({c.duration:.1f}s)\n      HOOK  {c.hook[:150]}\n"
            f"      CLIFF {c.cliffhanger[:150]}")
    log(f"Wrote outputs to {a.output}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
