"""Transparent rule-based scoring of promo candidates.

Each criterion returns a 0-1 score plus human-readable evidence strings that
say exactly which line/action earned or lost points. Weights live in config.py.

Criteria (brief -> function):
  opening hook         -> score_hook        (first 3 s of the first segment)
  cliffhanger          -> score_cliffhanger (last ~6 s of the last segment)
  emotional intensity  -> score_emotion     (delivery tags + physical action)
  standalone context   -> score_standalone  (unexplained names, backstory refs)
  narrative clarity    -> score_clarity     (dialogue coverage, speakers, scene jumps)
  duration compliance  -> hard gate + small multiplier
"""
from __future__ import annotations

import re
from bisect import bisect_left
from functools import lru_cache

import config
from src.candidate_generator import Candidate, Segment
from src.screenplay_parser import Episode


# ---------------------------------------------------------------- helpers
def _term_pattern(t: str) -> str:
    if t.endswith("*"):                      # explicit prefix stem
        return re.escape(t[:-1]) + r"[a-z]*"
    return re.escape(t) + r"(?:s|es|ed|d|ing)?"


def _compile(terms):
    # whole-word match (so "beg" no longer fires on "beggar", "explo" on "explosive")
    return re.compile(r"(?<![a-z])(" + "|".join(_term_pattern(t) for t in terms) + r")(?![a-z])", re.I)


RX = {name: _compile(getattr(config, name)) for name in (
    "PHYSICAL_HOOK_TERMS", "SHOCK_LINE_TERMS", "STAKES_TERMS", "TENSION_END_TERMS",
    "SCRIPT_CLIFFHANGER_TERMS", "ENTRANCE_TERMS", "RESOLUTION_TERMS", "BACKSTORY_TERMS",
    "ANCHOR_TERMS", "SLOW_OPEN_CAMERA_TERMS")}
INTENSITY_RX = [(lvl, _compile(words)) for lvl, words in
                sorted(config.DELIVERY_INTENSITY.items(), reverse=True)]


def hits(rx_name: str, text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in RX[rx_name].finditer(text or "")})


@lru_cache(maxsize=20000)
def line_intensity(delivery: str, text: str) -> float:
    lvl = config.DEFAULT_LINE_INTENSITY
    for level, rx in INTENSITY_RX:          # highest level that matches wins
        if rx.search(delivery or ""):
            lvl = float(level)
            break
    if text.rstrip().endswith("!"):
        lvl = min(3.0, lvl + 0.3)
    return lvl


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _short(text: str, n: int = 110) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


class ScoringContext:
    """Indexes episodes once so scoring ~100k candidates stays fast."""

    def __init__(self, episodes: list[Episode], use_script_cues: bool = config.USE_SCRIPT_CLIFFHANGER_CUES):
        self.eps = {e.number: e for e in episodes}
        self.starts = {e.number: [b.start for b in e.blocks] for e in episodes}
        self.use_script_cues = use_script_cues
        tokens = set()
        for e in episodes:
            for b in e.dialogue:
                for w in re.findall(r"[A-Z][A-Z']+", b.speaker):
                    tokens.add(re.sub(r"'S$", "", w).capitalize())   # "ALEXANDER'S MOTHER" -> Alexander
        generic = {"Lady", "Lord", "The", "Man", "Woman", "Old", "Elder", "Young", "Royal", "Guard",
                   "Maid", "Judge", "High", "Priest", "Father", "Mother", "Noble", "Thug", "Off",
                   "Screen", "Voice", "Third", "Tall", "Bald", "With", "Cap", "In", "Gold", "Gown",
                   "Former", "Groom", "Mysterious", "Bearded", "Stranger", "Blond", "Nobleman",
                   "Noblewoman", "Matriarch", "Crown", "Official", "Informant", "Constable",
                   "Police", "Housekeeper", "Peasant", "Villager", "Townsman", "Cart", "Driver",
                   "Scout", "Loyalist", "Fighter", "Sentry", "Footman", "Butler", "Conspirator",
                   "Aristocratic", "Dowager", "Duchess", "Duke", "Scarred", "Sir"}
        self.name_tokens = {t for t in tokens if t not in generic and len(t) > 2}
        self.name_rx = re.compile(r"\b(" + "|".join(sorted(self.name_tokens)) + r")\b") if self.name_tokens else None

    def blocks(self, seg: Segment):
        ep = self.eps[seg.episode]
        i = bisect_left(self.starts[seg.episode], seg.start - config.TIME_EPS)
        out = []
        for b in ep.blocks[i:]:
            if b.start >= seg.end - config.TIME_EPS:
                break
            out.append(b)
        return out

    def characters(self, seg: Segment) -> set:
        """Who is ON SCREEN in a segment: speakers plus names in ACTION/CAMERA/SFX text.

        Names that are only spoken about in dialogue do not count (a stitch joined a
        Catherine sting to a scene that merely mentioned her; see quality_report.md).
        """
        out = set()
        for b in self.blocks(seg):
            if b.kind == "dialogue":
                out.update(w.capitalize() for w in re.findall(r"[A-Z']+", b.speaker))
            elif self.name_rx:
                out.update(self.name_rx.findall(b.text))
        return out & self.name_tokens if self.name_tokens else out

    def blocks_before(self, seg: Segment, seconds: float):
        """Non-dialogue blocks that start in the `seconds` just before the segment."""
        ep = self.eps[seg.episode]
        i = bisect_left(self.starts[seg.episode], seg.start - seconds - config.TIME_EPS)
        out = []
        for b in ep.blocks[i:]:
            if b.start >= seg.start - config.TIME_EPS:
                break
            if b.kind != "dialogue":
                out.append(b)
        return out

    def next_dialogue_after(self, seg: Segment):
        ep = self.eps[seg.episode]
        i = bisect_left(self.starts[seg.episode], seg.end - config.TIME_EPS)
        for b in ep.blocks[i:]:
            if b.kind == "dialogue":
                return b
        return None


# ---------------------------------------------------------------- criteria
def score_hook(ctx: ScoringContext, seg: Segment):
    window = [b for b in ctx.blocks(seg) if b.start < seg.start + config.HOOK_WINDOW]
    dlg = [b for b in window if b.kind == "dialogue"]
    visual = [b for b in window if b.kind != "dialogue"]
    score, ev, info = 0.0, [], {}

    phys = sorted({h for b in visual for h in hits("PHYSICAL_HOOK_TERMS", b.text)})
    if phys:
        score += 0.35
        ev.append(f"+0.35 physical event in first 3s ({', '.join(phys[:3])})")
        vb = next(b for b in visual if hits("PHYSICAL_HOOK_TERMS", b.text))
        info["visual"] = _short(vb.text)

    if dlg:
        first = dlg[0]
        info["line"] = (first.speaker, first.delivery, first.text)
        score += 0.15
        ev.append(f"+0.15 dialogue starts {first.start - seg.start:.1f}s in")
        shock = sorted({h for b in dlg for h in hits("SHOCK_LINE_TERMS", b.text)})
        if shock:
            score += 0.25
            ev.append(f"+0.25 shocking line ({', '.join(shock[:3])})")
        if any("!" in b.text for b in dlg):
            score += 0.10
            ev.append("+0.10 exclamation")
        if any("?" in b.text for b in dlg):
            score += 0.08
            ev.append("+0.08 confrontational question")
        peak = max(line_intensity(b.delivery, b.text) for b in dlg)
        if peak >= 3:
            score += 0.20
            ev.append(f"+0.20 explosive delivery ({first.delivery or 'tagged'})")
        elif peak >= 2:
            score += 0.10
            ev.append("+0.10 charged delivery")
        if first.start - seg.start <= 1.0:
            score += 0.05
            ev.append("+0.05 line lands within 1s")
        if first.text.lower().startswith(config.OPENS_AS_REPLY):
            score -= 0.20
            ev.append("-0.20 opens on a reply to something unseen")
    else:
        if not phys:
            ev.append("+0.00 no dialogue or physical event in first 3s")
        slow = sorted({h for b in visual for h in hits("SLOW_OPEN_CAMERA_TERMS", b.text)})
        if slow:
            score -= 0.10
            ev.append(f"-0.10 slow/establishing open ({slow[0]})")
    missed = sorted({h for b in ctx.blocks_before(seg, config.MISSED_IMPACT_WINDOW)
                     for h in hits("PHYSICAL_HOOK_TERMS", b.text)})
    if missed:
        score -= config.MISSED_IMPACT_PENALTY
        ev.append(f"-{config.MISSED_IMPACT_PENALTY:.2f} opens just after the impact it should show ({missed[0]})")
    return _clip(score), ev, info


def score_cliffhanger(ctx: ScoringContext, seg: Segment, clip_blocks):
    ep = ctx.eps[seg.episode]
    tail = [b for b in clip_blocks if b.end > seg.end - config.CLIFF_WINDOW]
    dlg_all = [b for b in clip_blocks if b.kind == "dialogue"]
    score, ev, info, flags = 0.10, ["+0.10 base"], {}, []
    last = dlg_all[-1] if dlg_all else None

    if last and last.end > seg.end - config.CLIFF_WINDOW - 2:
        info["line"] = (last.speaker, last.delivery, last.text)
        t = last.text.strip()
        if t.endswith("?"):
            score += 0.20
            ev.append("+0.20 ends on a question")
            nxt = ctx.next_dialogue_after(seg)
            if nxt and nxt.speaker != last.speaker:
                score += 0.10
                ev.append("+0.10 the answer exists but is cut off")
                flags.append("answer withheld")
            else:
                flags.append("unanswered question")
        stakes = hits("STAKES_TERMS", t)
        if stakes:
            score += 0.25
            ev.append(f"+0.25 stakes/threat in final line ({', '.join(stakes[:3])})")
            flags.append(f"threat left hanging ({stakes[0]})")
        if t.endswith(("...", "…", "—", "-")):
            score += 0.10
            ev.append("+0.10 line trails off / interrupted")
            flags.append("interrupted line")
        li = line_intensity(last.delivery, last.text)
        if li >= 3:
            score += 0.15
            ev.append("+0.15 final line delivered at peak intensity")
        elif li >= 2:
            score += 0.08
            ev.append("+0.08 final line charged")
    else:
        ev.append("+0.00 no dialogue near the cut")

    tail_text = " ".join(b.text for b in tail if b.kind != "dialogue")
    tension = hits("TENSION_END_TERMS", tail_text)
    if tension:
        score += 0.20
        ev.append(f"+0.20 tension cue at the cut ({', '.join(tension[:3])})")
        flags.append("tension held at the cut")
        info["visual"] = _short(next(b.text for b in reversed(tail) if b.kind != "dialogue"
                                     and hits("TENSION_END_TERMS", b.text)))
    if hits("ENTRANCE_TERMS", tail_text):
        score += 0.15
        ev.append("+0.15 someone arrives/interrupts at the cut")
        flags.append("new arrival")
    if ctx.use_script_cues and hits("SCRIPT_CLIFFHANGER_TERMS", tail_text):
        score += 0.10
        ev.append("+0.10 writer marked this beat as a cliffhanger")
        flags.append("script-annotated cliffhanger")
    if ep.duration - seg.end <= 1.5:
        score += 0.20
        ev.append("+0.20 cut matches the episode's own ending")
        flags.append("episode-ending cut")

    tail_all = " ".join(b.text for b in tail)
    res = hits("RESOLUTION_TERMS", tail_all)
    if res:
        score -= 0.30
        ev.append(f"-0.30 tension resolves before the cut ({', '.join(res[:3])})")
        flags.append("resolves")
    if dlg_all:
        peak = max(line_intensity(b.delivery, b.text) for b in dlg_all)
        tail_d = [b for b in tail if b.kind == "dialogue"]
        tail_peak = max((line_intensity(b.delivery, b.text) for b in tail_d), default=1.0)
        if tail_peak < peak - 1.0:
            score -= 0.15
            ev.append("-0.15 energy drops before the cut")
    if not [b for b in clip_blocks if b.kind == "dialogue" and b.end > seg.end - 8] and not tension:
        score -= 0.10
        ev.append("-0.10 dead air at the end")
    info["flags"] = flags
    return _clip(score), ev, info


def score_emotion(all_blocks):
    dlg = [b for b in all_blocks if b.kind == "dialogue"]
    phys = sum(1 for b in all_blocks if b.kind != "dialogue" and hits("PHYSICAL_HOOK_TERMS", b.text))
    if not dlg:
        s = 0.2 * min(1.0, phys / 3)
        return _clip(s), [f"no dialogue; {phys} physical beats"]
    vals = [line_intensity(b.delivery, b.text) for b in dlg]
    mean, peak = sum(vals) / len(vals), max(vals)
    s = 0.5 * mean / 3 + 0.3 * peak / 3 + 0.2 * min(1.0, phys / 3)
    return _clip(s), [f"mean delivery {mean:.2f}/3, peak {peak:.0f}/3, {phys} physical beats"]


def score_standalone(ctx: ScoringContext, all_blocks, n_segments_eps: int):
    dlg = [b for b in all_blocks if b.kind == "dialogue"]
    if not dlg:
        return 0.3, ["no dialogue: a stranger gets no context"]
    s, ev = 0.85, ["0.85 base"]
    speakers = {b.speaker for b in dlg}
    on_screen = {w.capitalize() for sp in speakers for w in re.findall(r"[A-Z']+", sp)}
    text = " ".join(b.text for b in dlg)
    if ctx.name_rx:
        absent = sorted({m for m in ctx.name_rx.findall(text)} - on_screen)
        if absent:
            p = min(0.24, 0.06 * len(absent))
            s -= p
            ev.append(f"-{p:.2f} names an off-screen character ({', '.join(absent[:3])})")
    back = hits("BACKSTORY_TERMS", text)
    if back:
        p = min(0.21, 0.07 * len(back))
        s -= p
        ev.append(f"-{p:.2f} leans on earlier events ({', '.join(back[:3])})")
    anchors = hits("ANCHOR_TERMS", text)
    if anchors:
        b_ = min(0.15, 0.05 * len(anchors))
        s += b_
        ev.append(f"+{b_:.2f} relationships stated on screen ({', '.join(anchors[:3])})")
    if len(speakers) > 4:
        s -= 0.10
        ev.append("-0.10 more than four speakers")
    if n_segments_eps > 1:
        s -= 0.05
        ev.append("-0.05 jumps between episodes")
    return _clip(s), ev


def score_clarity(ctx: ScoringContext, cand: Candidate, all_blocks):
    dlg = [b for b in all_blocks if b.kind == "dialogue"]
    dur = cand.duration or 1
    cov = sum(b.end - b.start for b in dlg) / dur   # cuts never split a line, so lines are fully inside
    cov_s = 1.0 if 0.35 <= cov <= 0.85 else (cov / 0.35 if cov < 0.35 else max(0.5, 1 - (cov - 0.85) * 2))
    nspk = len({b.speaker for b in dlg})
    spk_s = {0: 0.2, 1: 0.6, 2: 1.0, 3: 1.0, 4: 0.8}.get(nspk, 0.6)
    changes = sum(1 for a, b in zip(dlg, dlg[1:]) if a.speaker != b.speaker)
    alt = changes / (len(dlg) - 1) if len(dlg) > 1 else 0
    alt_s = 0.5 + 0.5 * alt
    crossings = sum(len({b.scene for b in ctx.blocks(s)}) - 1 for s in cand.segments)
    s = (cov_s + spk_s + alt_s) / 3 - 0.15 * crossings
    ev = [f"dialogue covers {cov:.0%} of clip", f"{nspk} speakers", f"{alt:.0%} speaker turns",
          f"{crossings} scene change(s)"]
    return _clip(s), ev


def duration_multiplier(d: float) -> float:
    lo, hi = config.SWEET_SPOT
    f = config.DURATION_MULTIPLIER_FLOOR
    if lo <= d <= hi:
        return 1.0
    if d < lo:
        return f + (1 - f) * (d - config.MIN_DURATION) / (lo - config.MIN_DURATION)
    return f + (1 - f) * (config.MAX_DURATION - d) / (config.MAX_DURATION - hi)


# ---------------------------------------------------------------- main entry
def score_candidate(ctx: ScoringContext, cand: Candidate) -> Candidate:
    d = cand.duration
    if not (config.MIN_DURATION - 1e-6 <= d <= config.MAX_DURATION + 1e-6):
        cand.eligible, cand.total = False, 0.0
        cand.reject_reasons = [f"duration {d:.1f}s outside {config.MIN_DURATION:.0f}-{config.MAX_DURATION:.0f}s"]
        return cand
    all_blocks = [b for s in cand.segments for b in ctx.blocks(s)]
    h, h_ev, h_info = score_hook(ctx, cand.segments[0])
    last_blocks = ctx.blocks(cand.segments[-1])
    c, c_ev, c_info = score_cliffhanger(ctx, cand.segments[-1], last_blocks)
    e, e_ev = score_emotion(all_blocks)
    st, st_ev = score_standalone(ctx, all_blocks, len(cand.episodes))
    cl, cl_ev = score_clarity(ctx, cand, all_blocks)
    comps = {"hook": h, "cliffhanger": c, "emotion": e, "standalone": st, "clarity": cl}
    base = sum(config.WEIGHTS[k] * v for k, v in comps.items())
    mult = duration_multiplier(d)
    penalty = 0.0
    if cand.kind == "stitched":
        penalty = config.STITCH_PENALTY_CROSS_EP if len(cand.episodes) > 1 else config.STITCH_PENALTY_SAME_EP
    cand.scores = {k: round(v, 3) for k, v in comps.items()}
    cand.scores.update({"duration_multiplier": round(mult, 3), "stitch_penalty": penalty,
                        "rule_total": round(base * mult - penalty, 4)})
    cand.total = cand.scores["rule_total"]
    cand.evidence = {"hook": h_ev, "cliffhanger": c_ev, "emotion": e_ev, "standalone": st_ev,
                     "clarity": cl_ev, "_hook_info": h_info, "_cliff_info": c_info}
    cand.reject_reasons = []
    if h < config.GATE_MIN_HOOK:
        cand.reject_reasons.append(f"hook {h:.2f} < gate {config.GATE_MIN_HOOK}")
    if c < config.GATE_MIN_CLIFF:
        cand.reject_reasons.append(f"cliffhanger {c:.2f} < gate {config.GATE_MIN_CLIFF}")
    cand.eligible = not cand.reject_reasons
    return cand


def score_sting(ctx: ScoringContext, seg: Segment) -> float:
    return score_hook(ctx, seg)[0]


# ---------------------------------------------------------------- explanations
def _fmt_line(line) -> str:
    sp, delivery, text = line
    d = f" ({delivery})" if delivery else ""
    return f'{sp}{d}: "{_short(text, 120)}"'


def describe(cand: Candidate) -> Candidate:
    """Template explanations built only from real script text (no LLM)."""
    hi = cand.evidence.get("_hook_info", {})
    ci = cand.evidence.get("_cliff_info", {})
    parts = []
    if "line" in hi:
        parts.append("Opens on " + _fmt_line(hi["line"]))
    if "visual" in hi:
        parts.append("Visual: " + hi["visual"])
    cand.hook = " | ".join(parts) or "Weak opening: no line or physical event in the first 3 seconds."

    flags = ci.get("flags", [])
    parts = []
    if "line" in ci:
        parts.append("Cuts on " + _fmt_line(ci["line"]))
    if "visual" in ci:
        parts.append("Visual: " + ci["visual"])
    if flags:
        parts.append("Left open: " + ", ".join(flags))
    cand.cliffhanger = " | ".join(parts) or "No clear cliffhanger signal."

    s = cand.scores
    reasons = []
    hook_ev = " ".join(cand.evidence.get("hook", []))
    if "physical event" in hook_ev and "shocking line" in hook_ev:
        reasons.append("it opens on a physical action and a shocking line at once")
    elif "physical event" in hook_ev:
        reasons.append("it opens on visible physical action")
    elif "shocking line" in hook_ev:
        reasons.append("it opens on a shocking line")
    elif s.get("hook", 0) >= 0.5:
        reasons.append("it opens mid-confrontation")
    if any(f.startswith("threat") for f in flags):
        reasons.append("the threat is spoken out loud")
    if "answer withheld" in flags or "unanswered question" in flags:
        reasons.append("it cuts before the question is answered")
    if "new arrival" in flags:
        reasons.append("someone walks in at the cut")
    if "episode-ending cut" in flags and len(reasons) < 3:
        reasons.append("it ends on the episode's own cliffhanger")
    if s.get("standalone", 0) >= 0.85 and len(reasons) < 3:
        reasons.append("who-is-who is clear without backstory")
    if s.get("emotion", 0) >= 0.6 and len(reasons) < 3:
        reasons.append("the performances stay at high intensity")
    if not reasons:
        reasons.append("it holds moderate conflict throughout")
    r = reasons[:3]
    joined = r[0] if len(r) == 1 else ", ".join(r[:-1]) + " and " + r[-1]
    cand.why = f"Works because {joined}."
    return cand
