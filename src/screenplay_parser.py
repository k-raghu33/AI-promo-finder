"""Parse Kuku-format screenplay .txt files into typed, time-stamped blocks.

Expected format (one file per episode):

    EPISODE NUMBER: Episode 12
    EPISODE LOGLINE: ...
    CONTINUITY NOTES: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. COTTAGE - NIGHT
    Scene start: 00:00.00
    Scene end: 01:12.50
    **CAMERA:** | **ACTION:** | **SOUND FX:** | **SPEAKER (Delivery):**
    Start: (MM:SS.cc) | End: (MM:SS.cc)
    <text, may span lines>
    --- SCRIPT END ---

Timestamps are relative to the start of each episode. Pure Python, no deps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TS_RE = re.compile(r"^(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?$")
HEADER_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
TIMING_RE = re.compile(r"^Start:\s*\(([\d:.]+)\)\s*\|\s*End:\s*\(([\d:.]+)\)\s*$", re.I)
EP_NUM_RE = re.compile(r"^EPISODE NUMBER:\s*(?:Episode\s*)?(\d+)", re.I)
FILE_EP_RE = re.compile(r"episode[-_ ]?0*(\d+)", re.I)
TECH_KINDS = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sfx", "SFX": "sfx"}
VOICE_FLAGS = {"O.S.", "V.O.", "O.C.", "CONT'D", "OFF-SCREEN", "CONTINUED"}


def parse_timestamp(ts: str) -> float:
    """'01:23.45' -> 83.45 seconds. Raises ValueError on bad input."""
    m = TS_RE.match(ts.strip())
    if not m:
        raise ValueError(f"bad timestamp: {ts!r}")
    minutes, seconds, frac = m.group(1), m.group(2), m.group(3) or "0"
    return int(minutes) * 60 + int(seconds) + int(frac) / (10 ** len(frac))


def format_timestamp(sec: float) -> str:
    """83.45 -> '01:23.45' (same format as the source scripts)."""
    cs = int(round(sec * 100))
    return f"{cs // 6000:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


@dataclass
class Block:
    idx: int
    kind: str              # camera | action | sfx | dialogue
    start: float
    end: float
    text: str
    scene: int
    speaker: str = ""      # normalised speaker (dialogue only)
    raw_speaker: str = ""
    delivery: str = ""     # e.g. "Cold, Commanding"
    voice_flags: tuple = ()

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Scene:
    number: int
    heading: str
    start: float
    end: float


@dataclass
class Episode:
    number: int
    path: str
    logline: str = ""
    continuity: str = ""
    scenes: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        ends = [s.end for s in self.scenes] + [b.end for b in self.blocks]
        return max(ends) if ends else 0.0

    @property
    def dialogue(self):
        return [b for b in self.blocks if b.kind == "dialogue"]


def _split_speaker(header: str):
    """'MATRIARCH (O.S.) (Firm)' -> ('MATRIARCH', 'Firm', ('O.S.',))."""
    parens = re.findall(r"\(([^()]*)\)", header)
    name = re.sub(r"\([^()]*\)", "", header).strip()
    flags, deliveries = [], []
    for p in parens:
        p = p.strip()
        parts = [x.strip() for x in p.split("/")]
        if any(x.upper() in VOICE_FLAGS for x in parts):
            flags.extend(x for x in parts if x.upper() in VOICE_FLAGS)
            deliveries.extend(x for x in parts if x.upper() not in VOICE_FLAGS)
        else:
            deliveries.append(p)
    return name.upper(), ", ".join(d for d in deliveries if d), tuple(flags)


def parse_episode(path: str | Path) -> Episode:
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    ep = Episode(number=-1, path=path.name)

    scene_no, scene_info = 0, {}
    cur = None          # dict for block being built
    in_script = False

    def flush():
        nonlocal cur
        if cur is None:
            return
        if cur.get("start") is None:
            ep.warnings.append(f"block without timing: {cur['header'][:40]}")
        else:
            text = " ".join(t.strip() for t in cur["text"] if t.strip())
            header = cur["header"]
            kind = TECH_KINDS.get(header.upper())
            if kind:
                b = Block(len(ep.blocks), kind, cur["start"], cur["end"], text, scene_no)
            else:
                name, delivery, flags = _split_speaker(header)
                b = Block(len(ep.blocks), "dialogue", cur["start"], cur["end"],
                          text.strip().strip('"').strip("“”").strip(), scene_no,
                          speaker=name, raw_speaker=name, delivery=delivery, voice_flags=flags)
            if b.end + 1e-6 < b.start:
                ep.warnings.append(f"block {b.idx} ends before it starts ({b.start}>{b.end})")
            ep.blocks.append(b)
        cur = None

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not in_script:
            m = EP_NUM_RE.match(s)
            if m:
                ep.number = int(m.group(1))
            elif s.upper().startswith("EPISODE LOGLINE:"):
                ep.logline = s.split(":", 1)[1].strip()
            elif s.upper().startswith("CONTINUITY NOTES:"):
                ep.continuity = s.split(":", 1)[1].strip()
            elif s.upper().startswith("--- SCRIPT START"):
                in_script = True
            continue
        if s.upper().startswith("--- SCRIPT END"):
            flush()
            break
        if re.match(r"^SCENE\s+\d+", s):
            flush()
            if scene_info:
                _close_scene(ep, scene_info)
            scene_no = int(re.findall(r"\d+", s)[0])
            scene_info = {"number": scene_no}
            continue
        low = s.lower()   # headers vary in case across files (e.g. "SCENE START:" in ep 6)
        if low.startswith("scene heading:"):
            scene_info["heading"] = s.split(":", 1)[1].strip()
            continue
        if low.startswith("scene start:") or low.startswith("scene end:"):
            key = "start" if "start" in low.split(":", 1)[0] else "end"
            try:
                scene_info[key] = parse_timestamp(s.split(":", 1)[1])
            except ValueError:
                ep.warnings.append(f"bad scene timestamp: {s}")
            continue
        if low.startswith("actions and dialogue:"):
            continue
        h = HEADER_RE.match(s)
        if h:
            flush()
            cur = {"header": h.group(1).strip(), "start": None, "end": None, "text": []}
            continue
        t = TIMING_RE.match(s)
        if t and cur is not None and cur["start"] is None:
            try:
                cur["start"], cur["end"] = parse_timestamp(t.group(1)), parse_timestamp(t.group(2))
            except ValueError as e:
                ep.warnings.append(str(e))
            continue
        if cur is not None and s:
            cur["text"].append(s)
    flush()
    if scene_info:
        _close_scene(ep, scene_info)

    if ep.number < 0:  # fall back to file name
        m = FILE_EP_RE.search(path.name)
        ep.number = int(m.group(1)) if m else 0
        ep.warnings.append("episode number taken from file name")
    if not in_script:
        ep.warnings.append("no '--- SCRIPT START ---' marker found")
    _normalise_speakers(ep)
    return ep


def _close_scene(ep: Episode, info: dict):
    if "start" not in info or "end" not in info:
        ep.warnings.append(f"scene {info.get('number')} missing start/end")
        return
    ep.scenes.append(Scene(info["number"], info.get("heading", ""), info["start"], info["end"]))


def _normalise_speakers(ep: Episode):
    """Merge honorific variants: 'LADY JULIANA' -> 'JULIANA' when both exist."""
    names = {b.speaker for b in ep.dialogue}
    for b in ep.dialogue:
        for prefix in ("LADY ", "LORD ", "SIR ", "DUKE ", "DUCHESS "):
            if b.speaker.startswith(prefix) and b.speaker[len(prefix):] in names:
                b.speaker = b.speaker[len(prefix):]


def load_episodes(input_dir: str | Path, pattern: str = "*.txt") -> list[Episode]:
    files = sorted(Path(input_dir).rglob(pattern))
    episodes = [parse_episode(f) for f in files]
    episodes.sort(key=lambda e: e.number)
    return episodes
