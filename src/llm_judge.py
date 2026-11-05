"""Optional LLM semantic judge for the rule-based shortlist.

Providers: none (default) | openai | gemini | ollama | mock.
  * Uses only the Python standard library (urllib), so no SDK installs.
  * Only the top-K shortlist is sent, so cost scales with promos kept rather
    than candidates generated.
  * Responses are cached on disk (keyed by provider+model+prompt hash), which
    makes reruns reproducible and free.
  * Any failure (no key, no network, bad JSON) falls back to rules-only scoring
    for that candidate and is logged. The pipeline never crashes on the LLM.
  * 'mock' is a deterministic fake used by the tests to exercise the code path.
    It is NOT a model and its output must never be reported as LLM results.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path

import config

PROMPT = """You are a senior promo editor for vertical microdramas (ReelShort/DramaBox style).
A promo is a 20-60s clip posted on TikTok/Reels to pull in viewers who have never seen the show.

Series context for YOU only (the viewer does NOT know this): {logline}

Candidate clip transcript (episode {episode}, {duration:.1f}s, times relative to the episode):
{transcript}

Judge the clip as it would play to a stranger scrolling a feed. Score each 0-10:
- hook_score: does the first 3 seconds make a scroller stop (slap, reveal, shocking line)?
- cliffhanger_score: does it cut at an unresolved moment that makes them need the next part?
- standalone_score: does it make sense with zero knowledge of the show?
- spoiler_risk: does it give away a major twist or show the conflict being resolved?

Reply with JSON only, no prose:
{{"hook_score": n, "cliffhanger_score": n, "standalone_score": n, "spoiler_risk": n,
 "hook": "<one line: what happens in the first 3 seconds>",
 "cliffhanger": "<one line: what is left unresolved at the cut>",
 "why": "<one line: why this promo works or fails>"}}"""


def build_transcript(ctx, cand) -> str:
    from src.screenplay_parser import format_timestamp
    lines = []
    for i, seg in enumerate(cand.segments):
        if len(cand.segments) > 1:
            lines.append(f"--- segment {i + 1}: episode {seg.episode} "
                         f"{format_timestamp(seg.start)}-{format_timestamp(seg.end)} ---")
        for b in ctx.blocks(seg):
            if b.kind == "camera":
                continue
            ts = format_timestamp(b.start)
            if b.kind == "dialogue":
                d = f" ({b.delivery})" if b.delivery else ""
                lines.append(f'[{ts}] {b.speaker}{d}: "{b.text}"')
            else:
                lines.append(f"[{ts}] {b.kind.upper()}: {b.text}")
    return "\n".join(lines)


class LLMJudge:
    def __init__(self, provider: str = config.LLM_PROVIDER, model: str = config.LLM_MODEL,
                 cache_path: str | Path | None = None, log=print):
        self.provider = (provider or "none").lower()
        self.model = model or config.DEFAULT_MODELS.get(self.provider, "")
        self.log = log
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text())
            except json.JSONDecodeError:
                self.cache = {}
        self.calls = self.failures = self.cache_hits = 0

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider

    # ------------------------------------------------------------ public
    def judge(self, ctx, cand) -> dict | None:
        ep = ctx.eps[cand.segments[-1].episode]
        prompt = PROMPT.format(logline=ep.logline or "n/a", episode=", ".join(map(str, cand.episodes)),
                               duration=cand.duration, transcript=build_transcript(ctx, cand))
        key = hashlib.sha256(f"{self.label}\n{prompt}".encode()).hexdigest()
        if key in self.cache:
            self.cache_hits += 1
            return self.cache[key]
        try:
            raw = self._call(prompt)
            self.calls += 1
            parsed = self._parse(raw)
        except Exception as e:  # noqa: BLE001  - any provider error -> fallback
            self.failures += 1
            if self.failures <= 3:
                self.log(f"[llm] {self.label} failed ({type(e).__name__}: {str(e)[:120]}); using rules for this candidate")
            return None
        self.cache[key] = parsed
        return parsed

    def save_cache(self):
        if self.cache_path and self.enabled and self.provider != "mock":
            self.cache_path.write_text(json.dumps(self.cache, indent=1, sort_keys=True))

    @staticmethod
    def to_score(j: dict) -> float:
        """Collapse the LLM's 0-10 ratings into one 0-1 number."""
        base = (0.4 * j["hook_score"] + 0.4 * j["cliffhanger_score"] + 0.2 * j["standalone_score"]) / 10
        return max(0.0, min(1.0, base * (1 - 0.5 * j["spoiler_risk"] / 10)))

    # ------------------------------------------------------------ internals
    @staticmethod
    def _parse(raw: str) -> dict:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise ValueError("no JSON object in response")
        j = json.loads(m.group(0))
        for k in ("hook_score", "cliffhanger_score", "standalone_score", "spoiler_risk"):
            j[k] = max(0.0, min(10.0, float(j[k])))
        for k in ("hook", "cliffhanger", "why"):
            j[k] = str(j.get(k, "")).strip()
        return j

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as r:
            return json.loads(r.read().decode())

    def _call(self, prompt: str) -> str:
        p = self.provider
        if p == "openai":
            if not config.OPENAI_API_KEY:
                raise RuntimeError("OPENAI_API_KEY not set")
            r = self._post(f"{config.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
                           {"model": self.model, "temperature": 0,
                            "response_format": {"type": "json_object"},
                            "messages": [{"role": "user", "content": prompt}]},
                           {"Authorization": f"Bearer {config.OPENAI_API_KEY}"})
            return r["choices"][0]["message"]["content"]
        if p == "gemini":
            if not config.GEMINI_API_KEY:
                raise RuntimeError("GEMINI_API_KEY not set")
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
                   f"?key={config.GEMINI_API_KEY}")
            r = self._post(url, {"contents": [{"parts": [{"text": prompt}]}],
                                 "generationConfig": {"temperature": 0,
                                                      "responseMimeType": "application/json"}}, {})
            return r["candidates"][0]["content"]["parts"][0]["text"]
        if p == "ollama":
            r = self._post(f"{config.OLLAMA_URL.rstrip('/')}/api/chat",
                           {"model": self.model, "stream": False, "format": "json",
                            "options": {"temperature": 0},
                            "messages": [{"role": "user", "content": prompt}]}, {})
            return r["message"]["content"]
        if p == "mock":
            h = int(hashlib.md5(prompt.encode()).hexdigest(), 16)
            return json.dumps({"hook_score": h % 11, "cliffhanger_score": (h >> 4) % 11,
                               "standalone_score": (h >> 8) % 11, "spoiler_risk": (h >> 12) % 6,
                               "hook": "mock hook", "cliffhanger": "mock cliffhanger",
                               "why": "mock reason"})
        raise ValueError(f"unknown provider {p!r}")
