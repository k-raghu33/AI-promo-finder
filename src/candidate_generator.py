"""Generate promo candidates whose cut points are editorially valid.

A cut point is only allowed on a camera-shot boundary (where an editor would
cut anyway) and never while a line of dialogue is being spoken. Every
start/end pair inside one episode with a 20-60 s duration becomes a
single-segment candidate. Multi-segment ("stitched") candidates are built later
from the best singles plus a short hook "sting" (see build_stitched).
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

import config
from src.screenplay_parser import Episode, format_timestamp


@dataclass(frozen=True)
class Segment:
    episode: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)

    def to_dict(self) -> dict:
        return {"episode": self.episode, "start": format_timestamp(self.start),
                "end": format_timestamp(self.end), "start_sec": round(self.start, 2),
                "end_sec": round(self.end, 2), "duration_sec": self.duration}


@dataclass
class Candidate:
    segments: tuple
    kind: str = "single"                  # single | stitched
    scores: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    total: float = 0.0
    eligible: bool = True
    reject_reasons: list = field(default_factory=list)
    hook: str = ""
    cliffhanger: str = ""
    why: str = ""
    scoring_mode: str = "rules"

    @property
    def id(self) -> str:
        return "+".join(
            f"E{s.episode:03d}_{format_timestamp(s.start).replace(':', 'm')}-"
            f"{format_timestamp(s.end).replace(':', 'm')}" for s in self.segments)

    @property
    def duration(self) -> float:
        return round(sum(s.duration for s in self.segments), 2)

    @property
    def episodes(self) -> list:
        return sorted({s.episode for s in self.segments})


def _dialogue_spans(ep: Episode):
    return [(b.start, b.end) for b in ep.dialogue]


def _inside_dialogue(t: float, spans) -> bool:
    eps = config.TIME_EPS
    return any(s + eps < t < e - eps for s, e in spans)


def cut_points(ep: Episode):
    """Return (valid_starts, valid_ends) in seconds for an episode."""
    cams = [b for b in ep.blocks if b.kind == "camera"]
    source = cams if cams else ep.blocks          # fall back if a script has no CAMERA blocks
    starts = sorted({round(b.start, 2) for b in source} | {0.0})
    ends = sorted({round(b.end, 2) for b in source} | {round(ep.duration, 2)})
    spans = _dialogue_spans(ep)
    starts = [t for t in starts if not _inside_dialogue(t, spans) and t < ep.duration]
    ends = [t for t in ends if not _inside_dialogue(t, spans) and t > 0]
    return starts, ends


def generate_single_candidates(ep: Episode) -> list[Candidate]:
    starts, ends = cut_points(ep)
    out = []
    for s in starts:
        lo = bisect_left(ends, s + config.MIN_DURATION - 1e-6)
        for e in ends[lo:]:
            if e - s > config.MAX_DURATION + 1e-6:
                break
            out.append(Candidate(segments=(Segment(ep.number, s, e),)))
    return out


def generate_stings(ep: Episode) -> list[Segment]:
    """Short windows (2-8 s) that could open a stitched promo."""
    starts, ends = cut_points(ep)
    out = []
    for s in starts:
        lo = bisect_left(ends, s + config.STING_MIN - 1e-6)
        for e in ends[lo:]:
            if e - s > config.STING_MAX + 1e-6:
                break
            out.append(Segment(ep.number, s, e))
    return out


def build_stitched(mains: list[Candidate], stings: dict, sting_scores: dict,
                   characters=None) -> list[Candidate]:
    """Prepend the best-hook sting to mains that end well but open weakly.

    Constraints: the sting finishes before the main stretch starts (chronological,
    so the sting can never reveal how the cliffhanger resolves), comes from the
    same or the previous episode, total duration stays within 20-60 s, and the
    sting shares at least one character who is ON SCREEN in both (otherwise a
    stranger sees two unrelated scenes).
    """
    out = []
    for m in mains:
        main = m.segments[0]
        if m.scores.get("hook", 1) >= 0.6 or m.scores.get("cliffhanger", 0) < 0.5:
            continue
        best = None
        main_chars = characters(main) if characters else None
        for ep_no in range(main.episode - config.STITCH_EPISODE_RADIUS, main.episode + 1):
            for st in stings.get(ep_no, []):
                if st.episode == main.episode and st.end > main.start + 1e-6:
                    continue
                total = st.duration + main.duration
                if not (config.MIN_DURATION <= total <= config.MAX_DURATION):
                    continue
                sc = sting_scores.get(st, 0)
                if sc <= m.scores.get("hook", 0) + 0.15:
                    continue
                if main_chars is not None and not (characters(st) & main_chars):
                    continue
                if best is None or sc > best[0]:
                    best = (sc, st)
        if best:
            out.append(Candidate(segments=(best[1], main), kind="stitched"))
    return out
