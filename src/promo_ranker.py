"""End-to-end selection: generate -> score -> stitch -> de-duplicate -> (LLM) -> rank."""
from __future__ import annotations

import config
from src.candidate_generator import (Candidate, build_stitched, generate_single_candidates,
                                     generate_stings)
from src.llm_judge import LLMJudge
from src.promo_scorer import ScoringContext, describe, score_candidate, score_sting


def overlap_ratio(a: Candidate, b: Candidate) -> float:
    """Shared screen time as a fraction of the shorter promo's total duration.

    Measured per clip, not per segment: with a per-segment denominator a 3 s hook
    sting reused inside another promo counted as 100% overlap and suppressed a
    44 s promo (this removed Episode 17's only promo during the audit).
    """
    shared = 0.0
    for sa in a.segments:
        for sb in b.segments:
            if sa.episode == sb.episode:
                shared += max(0.0, min(sa.end, sb.end) - max(sa.start, sb.start))
    return shared / max(1e-6, min(a.duration, b.duration))


def same_opening(a: Candidate, b: Candidate) -> bool:
    """True if two promos open on the same moment (their first-3-s windows overlap).

    A viewer judges a promo by its opening, so reusing a hook sting in several
    promos is a duplicate even when total shared time is small.
    """
    sa, sb = a.segments[0], b.segments[0]
    if sa.episode != sb.episode:
        return False
    return min(sa.start, sb.start) + config.HOOK_WINDOW > max(sa.start, sb.start)


def select_non_overlapping(pool, cap_per_episode, pinned=()):
    """Greedy non-maximum suppression over a list sorted best-first."""
    chosen, per_ep = [], {}
    for c in list(pinned) + list(pool):
        if any(c.id == x.id for x in chosen):
            continue
        ep = c.segments[-1].episode
        is_pinned = c in pinned
        if not is_pinned and per_ep.get(ep, 0) >= cap_per_episode:
            continue
        if not is_pinned and any(overlap_ratio(c, x) > config.OVERLAP_THRESHOLD or same_opening(c, x)
                                 for x in chosen):
            continue
        chosen.append(c)
        per_ep[ep] = per_ep.get(ep, 0) + 1
    return chosen


def _sort_key(c: Candidate):
    return (-round(c.total, 6), c.id)       # id tiebreak keeps the ranking deterministic


def run_pipeline(episodes, llm_provider="none", llm_model="", use_script_cues=True,
                 overrides=None, cap_per_episode=config.MAX_PROMOS_PER_EPISODE,
                 llm_cache=None, log=print) -> dict:
    overrides = overrides or {}
    reject_ids = set(overrides.get("reject", []))
    pin_ids = list(overrides.get("pin", []))
    ctx = ScoringContext(episodes, use_script_cues=use_script_cues)
    stats = {"episodes": len(episodes), "single_candidates": 0, "stitched_candidates": 0,
             "eligible": 0, "per_episode": {}}

    by_ep, all_scored = {}, []
    stings, sting_scores = {}, {}
    for ep in episodes:
        cands = [score_candidate(ctx, c) for c in generate_single_candidates(ep)]
        by_ep[ep.number] = cands
        stats["single_candidates"] += len(cands)
        if config.ENABLE_STITCHING:
            sts = generate_stings(ep)
            for s in sts:
                sting_scores[s] = score_sting(ctx, s)
            stings[ep.number] = sorted(sts, key=lambda s: -sting_scores[s])[:30]

    if config.ENABLE_STITCHING:
        for ep in episodes:
            cands = by_ep[ep.number]
            # main stretch = best ending/body regardless of its own (weak) opening
            body = sorted(cands, key=lambda c: (-(c.total - config.WEIGHTS["hook"] * c.scores.get("hook", 0)), c.id))
            stitched = [score_candidate(ctx, c) for c in
                        build_stitched(body[: config.STITCH_MAIN_POOL], stings, sting_scores,
                                       characters=ctx.characters)]
            by_ep[ep.number] = cands + stitched
            stats["stitched_candidates"] += len(stitched)

    for ep in episodes:
        cands = by_ep[ep.number]
        elig = [c for c in cands if c.eligible]
        stats["per_episode"][ep.number] = {"candidates": len(cands), "eligible": len(elig)}
        stats["eligible"] += len(elig)
        all_scored.extend(cands)

    index = {c.id: c for c in all_scored}
    pinned = [index[i] for i in pin_ids if i in index]
    missing_pins = [i for i in pin_ids if i not in index]
    pool = sorted((c for c in all_scored if c.eligible and c.id not in reject_ids), key=_sort_key)

    # Shortlist: a wider net (cap+1 per episode) so the LLM can reorder within it.
    shortlist = select_non_overlapping(pool, cap_per_episode + 1, pinned)

    judge = LLMJudge(llm_provider, llm_model, cache_path=llm_cache, log=log)
    if judge.enabled:
        for c in shortlist[: config.LLM_TOP_K]:
            j = judge.judge(ctx, c)
            if j is None:
                continue
            llm = judge.to_score(j)
            c.scores["llm"] = {k: j[k] for k in ("hook_score", "cliffhanger_score",
                                                 "standalone_score", "spoiler_risk")}
            c.scores["llm_total"] = round(llm, 4)
            c.total = round((1 - config.LLM_BLEND) * c.scores["rule_total"] + config.LLM_BLEND * llm, 4)
            c.scoring_mode = f"rules+llm({judge.label})"
            c.evidence["llm"] = {"hook": j["hook"], "cliffhanger": j["cliffhanger"], "why": j["why"]}
        judge.save_cache()
        shortlist.sort(key=_sort_key)

    final = select_non_overlapping(shortlist, cap_per_episode, [c for c in pinned if c in shortlist])
    final.sort(key=_sort_key)
    for c in final:
        describe(c)
        if "llm" in c.evidence:          # keep template text as evidence, show the LLM's wording
            c.evidence["template"] = {"hook": c.hook, "cliffhanger": c.cliffhanger, "why": c.why}
            c.hook = c.evidence["llm"]["hook"] or c.hook
            c.cliffhanger = c.evidence["llm"]["cliffhanger"] or c.cliffhanger
            c.why = c.evidence["llm"]["why"] or c.why

    stats.update({"selected": len(final), "rejected_by_override": len(reject_ids & set(index)),
                  "pinned": [c.id for c in pinned], "missing_pins": missing_pins,
                  "llm": {"provider": judge.label, "enabled": judge.enabled, "calls": judge.calls,
                          "cache_hits": judge.cache_hits, "failures": judge.failures}})

    raw = []
    for ep in episodes:
        top = sorted(by_ep[ep.number], key=_sort_key)[: config.RAW_CANDIDATES_PER_EPISODE]
        for c in top:
            if not c.hook:
                describe(c)
        raw.extend(top)
    return {"ranked": final, "raw": raw, "all": all_scored, "stats": stats, "ctx": ctx}


def rank_reason(c: Candidate, rank: int) -> str:
    s = c.scores
    strengths = sorted(((s[k], k) for k in ("hook", "cliffhanger", "emotion", "standalone", "clarity")),
                       reverse=True)
    top2 = " & ".join(f"{k} {v:.2f}" for v, k in strengths[:2])
    weak = strengths[-1]
    return (f"#{rank}: score {c.total:.3f}; strongest {top2}; weakest {weak[1]} {weak[0]:.2f}"
            + (f"; {len(c.segments)} segments" if len(c.segments) > 1 else ""))
