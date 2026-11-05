"""Central configuration: every weight, threshold and lexicon lives here.

Nothing in src/ hard-codes a number that changes ranking, so an evaluator can
tune behaviour without touching code. LLM settings are read from environment
variables so no key is ever committed.
"""
import os

# --------------------------------------------------------------------------
# Promo constraints (from the Kuku brief)
# --------------------------------------------------------------------------
MIN_DURATION = 20.0          # seconds, hard limit
MAX_DURATION = 60.0          # seconds, hard limit
SWEET_SPOT = (25.0, 50.0)    # no duration penalty inside this range
HOOK_WINDOW = 3.0            # "something in the first 3 seconds"
CLIFF_WINDOW = 6.0           # how much of the tail is read as "the ending"
TIME_EPS = 0.05              # tolerance for timestamp comparisons
# A clip that starts within this many seconds AFTER a physical event (slap, strike)
# has cut off its own best moment (E24 started 1.3 s after the riding-crop strike).
MISSED_IMPACT_WINDOW = 2.5
MISSED_IMPACT_PENALTY = 0.15

# --------------------------------------------------------------------------
# Scoring weights (sum to 1.0). Duration is a gate + small multiplier.
# --------------------------------------------------------------------------
WEIGHTS = {
    "hook": 0.27,
    "cliffhanger": 0.27,
    "emotion": 0.16,
    "standalone": 0.15,
    "clarity": 0.15,
}
DURATION_MULTIPLIER_FLOOR = 0.90   # score multiplier at exactly 20s / 60s

# Minimum component scores for a candidate to be selectable at all.
GATE_MIN_HOOK = 0.30
GATE_MIN_CLIFF = 0.30

# --------------------------------------------------------------------------
# Selection / de-duplication
# --------------------------------------------------------------------------
MAX_PROMOS_PER_EPISODE = 2       # keeps the list diverse across the series
OVERLAP_THRESHOLD = 0.15         # drop a candidate sharing >15% of its time with a better one (was 0.25; E15 near-duplicate slipped through)
RAW_CANDIDATES_PER_EPISODE = 20  # how many scored candidates per episode go to raw_candidates.json

# Confidence tiers on the final score (calibrated by reading the pack's output,
# not by human labels; see docs/quality_report.md).
TIER_POST_FIRST = 0.75   # >= : strongest; post first
TIER_REVIEW = 0.60       # >= : usable after a quick human check; below = weak, likely skip

# Multi-segment ("stitched") promos: a short hook sting + a main stretch.
ENABLE_STITCHING = True
STING_MIN, STING_MAX = 2.0, 8.0  # sting length in seconds
STITCH_MAIN_POOL = 15            # top singles per episode considered as the main stretch
STITCH_EPISODE_RADIUS = 1        # sting may come from the same or an adjacent episode
STITCH_PENALTY_SAME_EP = 0.03    # coherence risk of a jump cut
STITCH_PENALTY_CROSS_EP = 0.06

# The pack's writers sometimes annotate SFX with the word "cliffhanger".
# That is a real signal in this input but will not exist in every script,
# so it is capped and can be switched off (--no-script-cues) for an ablation.
USE_SCRIPT_CLIFFHANGER_CUES = True

# --------------------------------------------------------------------------
# LLM (optional semantic judge). Provider: none | openai | gemini | ollama | mock
# --------------------------------------------------------------------------
LLM_PROVIDER = os.getenv("PROMO_LLM_PROVIDER", "none").lower()
LLM_MODEL = os.getenv("PROMO_LLM_MODEL", "")          # blank = provider default
LLM_TOP_K = int(os.getenv("PROMO_LLM_TOP_K", "40"))   # only re-judge the shortlist
LLM_BLEND = float(os.getenv("PROMO_LLM_BLEND", "0.5"))  # final = (1-b)*rules + b*llm
LLM_TIMEOUT = 60
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODELS = {"openai": "gpt-4o-mini", "gemini": "gemini-1.5-flash", "ollama": "llama3.1"}

# --------------------------------------------------------------------------
# Lexicons (lower-case). Matched as whole words with optional s/es/ed/d/ing
# suffix; a trailing "*" makes the term a prefix stem (e.g. "accus*").
# --------------------------------------------------------------------------
# Delivery-tag intensity, 3 = explosive, 2 = charged, 1 = calm.
DELIVERY_INTENSITY = {
    3: ["furious", "screaming", "shouting", "outraged", "enraged", "shriek*", "horrified",
        "devastated", "crying out", "desperate", "terrified", "murderous", "roaring",
        "sobbing", "hysterical", "vicious", "venomous", "menacing", "threatening",
        "panicked", "frantic", "livid", "anguish*", "agoniz*", "wailing", "yelling",
        "seething", "unhinged", "blood-curdling", "maniacal", "deadly", "lethal", "screech", "heartbroken",
        "betrayed", "begging", "weeping", "shattered", "thunder*", "booming", "sadistic"],
    2: ["cold", "defiant", "mocking", "trembling", "urgent", "smug", "cruel", "commanding",
        "fierce", "pleading", "steely", "dismissive", "demanding", "sharp", "scornful",
        "tearful", "taunting", "spiteful", "incredulous", "sinister", "condescend*",
        "accus*", "harsh", "chilling", "bitter", "arrogant", "unyielding", "gasping",
        "shaken", "gloating", "derisive", "alarmed", "stunned", "shocked", "scheming",
        "grim", "contempt*", "malicious", "indignant", "vindictive", "ruthless", "piercing",
        "condemn", "callous", "sneer", "scoff", "fearful", "disdain*", "choked", "warning",
        "dark", "icy", "biting", "cutting", "scathing", "haughty", "imperious", "resolute",
        "stern", "firm", "breathless", "broken", "pained", "ominous", "triumphant",
        "passionate", "intense", "determined", "authoritative", "dangerous", "vengeful"],
    1: ["calm", "gentle", "soft", "quiet", "tender*", "warm", "reassuring", "sincere",
        "earnest", "loving", "relieved", "grateful", "amused", "playful", "fond",
        "comforting", "soothing", "peaceful", "reflective", "nostalgic", "polite",
        "curious", "measured", "steady", "humble", "caring", "compassionate"],
}
DEFAULT_LINE_INTENSITY = 1.5

# Physical / visual hook events in ACTION, CAMERA or SOUND FX text.
PHYSICAL_HOOK_TERMS = [
    # events only: a prop being visible (crop, sword, chain) is not a hook, and
    # bare "burst" was dropped because it fired on "bursting into laughter"
    "slap", "strike", "struck", "punch", "grab", "seize", "shove", "hurl", "throw", "thrown",
    "crash", "shatter", "explosion", "explode", "blood", "bleed", "fire", "flame", "ablaze",
    "inferno", "collapse", "drops to one knee", "choke", "red-hot", "branding", "scream",
    "slam", "bursts in", "bursts through", "bursts open", "burst open", "burst through", "storms in", "storms forward", "kick", "tackle", "wrench", "yank", "drag",
    "pinned", "noose", "lunge", "rip", "smash", "stab", "faint", "falls to", "shoves",
]
# Shocking-line vocabulary in dialogue.
SHOCK_LINE_TERMS = [
    "married", "marry", "wedding", "dead", "alive", "kill", "murder", "hang", "gallows",
    "execut*", "witch", "poison*", "arsenic", "arrest", "guards", "how dare", "who are you",
    "get out", "liar", "lied", "traitor", "betray*", "thief", "steal", "stole", "planted",
    "the truth", "disown*", "banish*", "burn", "never", "pregnant", "duke", "duchess",
    "worthless", "nothing", "crawl", "beg", "die", "blood", "invalid", "stop",
]
# Ending cues that leave the viewer hanging. ("before" was removed after it fired on
# "scrub floors before these commoners", i.e. "in front of", not a deadline.)
STAKES_TERMS = [
    "hang", "gallows", "die", "dead", "kill", "execut*", "burn", "destroy", "arrest",
    "never", "or else", "unless", "will pay", "take you", "take him", "take her",
    "witch", "ruin", "end you", "last time", "suffer", "pay for", "won't", "will not",
    "tonight", "tomorrow", "dawn", "trial", "sentence",
]
TENSION_END_TERMS = [
    "freeze", "frozen", "ominous", "drone", "swell*", "tension peaks", "slam", "burst",
    "doors fly", "door bursts", "gasp", "footsteps", "shadow", "cut to black",
    "smash cut", "hold on", "holding on", "stares", "glares", "locks eyes", "trembl*",
    "dread", "sinister", "menac*", "thunder*", "heartbeat", "silence falls",
]
SCRIPT_CLIFFHANGER_TERMS = ["cliffhanger"]
ENTRANCE_TERMS = ["bursts in", "burst in", "storms in", "enters", "appears", "arrives",
                  "doors open", "door opens", "steps out of the shadows", "interrupt"]
RESOLUTION_TERMS = [
    "embrace", "hug", "smiles softly", "smiles warmly", "relief", "relieved", "laughs warmly",
    "thank you", "safe now", "you're safe", "kiss", "peace", "peaceful", "rests her head",
    "falls asleep", "sleep", "contentment", "reassur*", "comfort*", "tender*", "gently",
    "together", "it's over", "all is well", "forgive*",
]
# Phrases that usually need earlier episodes to make sense.
BACKSTORY_TERMS = [
    "again", "remember", "last night", "what happened", "after everything", "you promised",
    "like before", "as i told you", "as we planned", "the plan", "that night", "back then",
    "earlier", "yesterday", "still", "once more",
]
# Relationship words that let a stranger understand who is who.
ANCHOR_TERMS = [
    "husband", "wife", "sister", "father", "mother", "son", "daughter", "brother",
    "fiancé", "fiance", "bride", "groom", "duke", "duchess", "family", "wedding",
    "married", "my lady", "my lord", "stepmother",
]
SLOW_OPEN_CAMERA_TERMS = ["establishing", "exterior", "wide shot of the", "slowly",
                          "landscape", "drone shot", "pan across", "panning across"]
OPENS_AS_REPLY = ("...", "yes", "no.", "no,", "because", "and ", "but ", "so ", "then ")
