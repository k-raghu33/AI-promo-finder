"""Automated checks on inputs and outputs. Every check reports pass/fail + details.

These are *mechanical* checks (valid timestamps, durations, no duplicates,
reproducibility). They say nothing about whether a promo is actually good; that
needs a human reviewer (see outputs/review_sheet.csv).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

import config
from src.candidate_generator import cut_points
from src.promo_ranker import overlap_ratio, same_opening


def _check(name, ok, detail=""):
    return {"check": name, "pass": bool(ok), "detail": detail}


def check_inputs(episodes, files_found: int):
    out = []
    nums = [e.number for e in episodes]
    out.append(_check("all input files parsed", len(episodes) == files_found,
                      f"{len(episodes)}/{files_found} files"))
    dupes = [n for n, k in Counter(nums).items() if k > 1]
    out.append(_check("episode numbers unique", not dupes, f"duplicates: {dupes}" if dupes else ""))
    if nums:
        missing = sorted(set(range(min(nums), max(nums) + 1)) - set(nums))
        out.append(_check("episode numbers contiguous", not missing,
                          f"range {min(nums)}-{max(nums)}" + (f", missing {missing}" if missing else "")))
    bad_blocks, outside_scene, warn = 0, 0, []
    for e in episodes:
        warn += [f"E{e.number}: {w}" for w in e.warnings]
        scenes = {s.number: s for s in e.scenes}
        for b in e.blocks:
            if b.end + 1e-6 < b.start or b.start < 0:
                bad_blocks += 1
            sc = scenes.get(b.scene)
            if sc and (b.start < sc.start - 0.5 or b.end > sc.end + 0.5):
                outside_scene += 1
    out.append(_check("block timestamps valid (start <= end, >= 0)", bad_blocks == 0, f"{bad_blocks} bad"))
    out.append(_check("blocks inside their scene bounds (±0.5s)", outside_scene == 0,
                      f"{outside_scene} outside"))
    out.append(_check("parser warnings", not warn, "; ".join(warn[:10])))
    out.append(_check("every episode has dialogue", all(e.dialogue for e in episodes),
                      ", ".join(f"E{e.number}" for e in episodes if not e.dialogue)))
    return out


def check_outputs(result, episodes):
    ranked, ctx = result["ranked"], result["ctx"]
    out = []
    eps = {e.number: e for e in episodes}
    cps = {e.number: cut_points(e) for e in episodes}
    bad_ts, bad_cut, bad_dur, missing_text = [], [], [], []
    for c in ranked:
        if not (config.MIN_DURATION - 1e-6 <= c.duration <= config.MAX_DURATION + 1e-6):
            bad_dur.append(c.id)
        for s in c.segments:
            e = eps.get(s.episode)
            if e is None or not (0 <= s.start < s.end <= e.duration + 1e-6):
                bad_ts.append(c.id)
            starts, ends = cps[s.episode]
            if round(s.start, 2) not in starts or round(s.end, 2) not in ends:
                bad_cut.append(c.id)
        if not (c.hook and c.cliffhanger and c.why):
            missing_text.append(c.id)
    out.append(_check("segment timestamps inside episode", not bad_ts, ", ".join(bad_ts[:5])))
    out.append(_check("cuts on shot boundaries, never mid-dialogue", not bad_cut, ", ".join(bad_cut[:5])))
    out.append(_check("total duration 20-60s", not bad_dur, ", ".join(bad_dur[:5])))
    out.append(_check("hook / cliffhanger / why present", not missing_text, ", ".join(missing_text[:5])))
    dup = [(a.id, b.id) for i, a in enumerate(ranked) for b in ranked[i + 1:]
           if overlap_ratio(a, b) > config.OVERLAP_THRESHOLD]
    out.append(_check(f"no overlapping promos (>{config.OVERLAP_THRESHOLD:.0%})", not dup, str(dup[:3])))
    same = [(a.id, b.id) for i, a in enumerate(ranked) for b in ranked[i + 1:] if same_opening(a, b)]
    out.append(_check("no two promos share the same opening", not same, str(same[:3])))
    covered = {c.segments[-1].episode for c in ranked}
    uncovered = sorted(set(eps) - covered)
    out.append(_check("every episode yields at least one promo", not uncovered,
                      f"no promo for episodes {uncovered}" if uncovered else f"{len(covered)} episodes"))
    return out


def output_fingerprint(ranked) -> str:
    payload = [(c.id, round(c.total, 4)) for c in ranked]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16]


def distribution(ranked) -> dict:
    n = len(ranked) or 1
    flags = Counter(f for c in ranked for f in c.evidence.get("_cliff_info", {}).get("flags", []))
    durs = sorted(c.duration for c in ranked)
    return {
        "selected": len(ranked),
        "multi_segment": sum(1 for c in ranked if len(c.segments) > 1),
        "cross_episode": sum(1 for c in ranked if len(c.episodes) > 1),
        "duration_min_median_max": [durs[0], durs[len(durs) // 2], durs[-1]] if durs else [],
        "ends_at_episode_end_pct": round(100 * flags.get("episode-ending cut", 0) / n, 1),
        "uses_script_cliffhanger_annotation_pct": round(100 * flags.get("script-annotated cliffhanger", 0) / n, 1),
        "cliffhanger_flag_counts": dict(flags.most_common()),
        "mean_scores": {k: round(sum(c.scores[k] for c in ranked) / n, 3)
                        for k in ("hook", "cliffhanger", "emotion", "standalone", "clarity")},
    }
