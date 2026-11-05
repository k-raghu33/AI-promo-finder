#!/usr/bin/env python3
"""Build docs/solution.pdf (2 pages) from the latest outputs/. Needs reportlab.

    python3 docs/build_solution_pdf.py
"""
import json
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
ranked = json.loads((OUT / "ranked_promos.json").read_text())
report = json.loads((OUT / "quality_report.json").read_text())
raw = json.loads((OUT / "raw_candidates.json").read_text())
promos, dist, stats = ranked["promos"], report["distribution"], raw["stats"]
checks = report["input_checks"] + report["output_checks"]
n_pass = sum(c["pass"] for c in checks)
tiers = {}
for p in promos:
    tiers[p["tier"]] = tiers.get(p["tier"], 0) + 1

INK = colors.HexColor("#1d2230")
ACC = colors.HexColor("#c2410c")
MUTED = colors.HexColor("#5b6272")
RULE = colors.HexColor("#d9dce3")
TINT = colors.HexColor("#f4f5f8")

base = ParagraphStyle("b", fontName="Helvetica", fontSize=8.6, leading=11.2, textColor=INK, alignment=TA_LEFT)
small = ParagraphStyle("s", parent=base, fontSize=7.8, leading=9.8)
h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=18)
sub = ParagraphStyle("sub", parent=base, fontSize=8.4, textColor=MUTED, leading=11)
h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=9.4, leading=12,
                    textColor=ACC, spaceBefore=6, spaceAfter=2)
cell = ParagraphStyle("c", parent=base, fontSize=7.6, leading=9.4)
cellb = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold")


def P(t, st=base):
    return Paragraph(t, st)


def H(t):
    return Paragraph(t, h2)


def table(rows, widths, header=True, font=cell):
    data = [[P(c, cellb if (header and i == 0) else font) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
          ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
          ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6)]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), TINT))
    t.setStyle(TableStyle(st))
    return t


def arch_diagram(width):
    boxes = [("Parser", "blocks + timestamps"), ("Candidates", "shot-boundary cuts\n20-60 s, ~12k"),
             ("Scorer", "5 criteria\n+ evidence"), ("Stitcher", "hook sting\n+ main stretch"),
             ("Ranker", "gates, NMS,\n2 per episode"), ("LLM judge", "optional, top-40\nswappable"),
             ("QC + outputs", f"{len(checks)} checks\nJSON / CSV")]
    h = 17 * mm
    d = Drawing(width, h)
    gap = 3.2 * mm
    bw = (width - gap * (len(boxes) - 1)) / len(boxes)
    for i, (title, desc) in enumerate(boxes):
        x = i * (bw + gap)
        dashed = title == "LLM judge"
        d.add(Rect(x, 1.5 * mm, bw, h - 3 * mm, rx=2, ry=2, fillColor=TINT if not dashed else colors.white,
                   strokeColor=ACC if not dashed else MUTED, strokeWidth=0.7,
                   strokeDashArray=[2, 1.5] if dashed else None))
        d.add(String(x + bw / 2, h - 6.2 * mm, title, fontName="Helvetica-Bold", fontSize=7.4,
                     fillColor=INK, textAnchor="middle"))
        for j, ln in enumerate(desc.split("\n")):
            d.add(String(x + bw / 2, h - 9.6 * mm - j * 3 * mm, ln, fontName="Helvetica", fontSize=6.2,
                         fillColor=MUTED, textAnchor="middle"))
        if i < len(boxes) - 1:
            ax = x + bw + 0.4 * mm
            y = h / 2
            d.add(Line(ax, y, ax + gap - 1.4 * mm, y, strokeColor=INK, strokeWidth=0.7))
            d.add(Polygon([ax + gap - 0.8 * mm, y, ax + gap - 1.9 * mm, y + 0.8 * mm,
                           ax + gap - 1.9 * mm, y - 0.8 * mm], fillColor=INK, strokeColor=INK))
    return d


doc = SimpleDocTemplate(str(ROOT / "docs" / "solution.pdf"), pagesize=A4, leftMargin=14 * mm,
                        rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=11 * mm,
                        title="Kuku Promo Finder: Solution", author="Kuku AI Generalist Assignment")
W = A4[0] - 28 * mm
s = []

s += [P("Kuku Promo Finder", h1),
      P("AI Generalist Assignment · Problem 1: Find the Promos · a hybrid rule + (optional) LLM tool that reads "
        "microdrama screenplays and returns ranked 20-60 s promos with exact timestamps", sub), Spacer(1, 3)]

s += [H("1. Problem and why I picked it"),
      P("An editor today watches every episode to pick promo moments, one show at a time, while new shows ship every week. "
        "I chose this problem because the input pack is real, the output is checkable (timestamps either line up or they "
        "don't), and the business pain is direct: promos are how new viewers arrive. The screenplays already carry a "
        "timestamp for every shot, line and action, so most of the search can be done exactly, in code, "
        "with AI judgment saved for the part that needs it.")]

s += [H("2. Assumptions"),
      P("• The screenplay timestamps match the final cut, so a promo is defined by script timestamps (no video was available). "
        "• A clip may cut only on a camera-shot boundary and never mid-line, which is how an editor cuts. "
        "• \"First 3 seconds\" = blocks starting in the first 3.0 s of the first segment; the \"ending\" = the last ~6 s. "
        "• Multi-segment promos join a short hook sting to a later stretch, in story order (so the sting can't spoil the ending), "
        "from the same or previous episode, sharing a character. "
        "• \"Across all episodes\" means coverage: up to 2 promos per episode, each tagged with a confidence tier. "
        "• Delivery tags such as (Furious) or (Cold) are a usable proxy for performance intensity.")]

s += [H("3. Architecture"), arch_diagram(W)]

s += [H("4. How it works"),
      P("<b>Parse</b> each episode into typed blocks (CAMERA, ACTION, SOUND FX, dialogue with speaker and delivery tag), "
        "with seconds-precision times. <b>Generate</b> every valid start/end pair of 20-60 s "
        f"({stats['single_candidates']:,} singles), plus {stats['stitched_candidates']} stitched candidates for strong "
        "endings with weak openings. <b>Score</b> each on five criteria with logged evidence (e.g. \"+0.25 stakes in final "
        "line (gallows)\"). <b>Select</b>: hook and cliffhanger gates (≥0.30), then greedy non-maximum suppression "
        "(≤15% shared screen time, no two promos with the same opening, ≤2 per episode). <b>Optional LLM</b>: the top 40 are re-judged by OpenAI, Gemini or a local "
        "Ollama model (provider set by one environment variable; stdlib only, cached, falls back to rules on any error) and "
        "blended 50/50. <b>Explain</b>: hook, cliffhanger and \"why\" are templates filled with the actual script lines. "
        "No text is invented.")]

s += [H("5. Ranking methodology"),
      table([["Criterion", "Weight", "Main signals (all points logged as evidence)"],
             ["Hook (first 3 s)", "0.27", "physical event (slap, grab, lunge) · shocking line (married, poison, gallows) · "
              "explosive delivery · exclamation or question; minus for opening on a reply or an establishing shot"],
             ["Cliffhanger (last ~6 s)", "0.27", "question <b>whose answer exists in the script but is cut</b> · stakes or "
              "threat in the last line · trailing line · tension cue · arrival · cut on the episode's own ending; "
              "minus for resolution words, energy drop, dead air"],
             ["Emotional intensity", "0.16", "mean and peak delivery intensity (3-level lexicon from the 620 tag words in the "
              "pack) + physical beats"],
             ["Standalone", "0.15", "minus off-screen names and backstory phrases; plus relationship anchors "
              "(husband, sister, Duke)"],
             ["Narrative clarity", "0.15", "dialogue covers 35-85% of the clip, 2-3 speakers, turn-taking; minus scene changes"],
             ["Duration", "gate", "hard 20-60 s; ×1.0 inside 25-50 s, down to ×0.9 at the limits"]],
            [30 * mm, 12 * mm, W - 42 * mm]),
      P("Tiers: ≥0.75 <b>post-first</b>, ≥0.60 <b>review</b>, otherwise <b>weak</b>. Ties break on promo ID, "
        "so the ranking is deterministic.", small)]

# ---------------- page 2
top = promos[:6]


def segs(p):
    return " + ".join(f"E{x['episode']} {x['start']}-{x['end']}" for x in p["segments"])


short_hooks = {1: "Mother grabs his coat: \"You cannot go back there alone!\"",
               2: "Juliana bursts in: \"Stop! What are you doing?!\"",
               3: "Catherine yanks the locket off Juliana's neck",
               4: "Catherine raises a branding iron: \"Silence!\"",
               5: "\"You are doing this because I saved the Duchess!\"",
               6: "A thug lunges at Alexander with a weapon"}
short_cliffs = {1: "\"...their dead Duke is coming home.\" (episode end)",
                2: "\"You want to fight us, veteran?\" (cut before the fight)",
                3: "\"...before he sends you to the gallows.\"",
                4: "\"...let's see how brave you are when it touches your skin!\"",
                5: "Cell door slams; alone: \"Mother...\" (softer ending)",
                6: "\"Who sent you?\" (answer withheld)"}
rows = [["#", "Episode · segments", "Dur", "Score", "Hook", "Cliffhanger"]]
for p in top:
    rows.append([str(p["rank"]), segs(p), f"{p['total_duration_sec']:.0f}s", f"{p['score']:.2f}",
                 short_hooks.get(p["rank"], p["hook"][:70]), short_cliffs.get(p["rank"], p["cliffhanger"][:70])])

s += [PageBreak(), H("6. Actual results on the 50-episode pack (rules only; no LLM was reachable)"),
      P(f"{ranked['meta']['episodes']}/50 episodes parsed (3,727 blocks; 0 warnings after a fix) · "
        f"{stats['single_candidates'] + stats['stitched_candidates']:,} candidates · {stats['eligible']:,} pass the gates · "
        f"<b>{len(promos)} promos selected</b> ({tiers.get('post-first', 0)} post-first, {tiers.get('review', 0)} review, "
        f"{tiers.get('weak', 0)} weak), {dist['multi_segment']} multi-segment · durations "
        f"{dist['duration_min_median_max'][0]}-{dist['duration_min_median_max'][2]} s (median "
        f"{dist['duration_min_median_max'][1]}) · <b>{n_pass}/{len(checks)} automated checks pass</b>, including "
        f"two runs giving identical output · {ranked['meta']['runtime_sec']:.1f} s on one CPU core."),
      Spacer(1, 2),
      table(rows, [6 * mm, 50 * mm, 9 * mm, 10 * mm, (W - 75 * mm) / 2, (W - 75 * mm) / 2]),
      P("Post first: #1-#4 are the only promos strong at <b>both</b> ends (physical open, stakes spoken at the cut). "
        "#5-#8 open just as hard but end softer. Full list: outputs/ranked_promos.csv.", small)]

s += [H("7. Quality evaluation, failure cases and limitations"),
      table([["Evidence", "Result"],
             ["Automated validation", f"{n_pass}/{len(checks)} checks pass: timestamps inside episodes, cuts on shot boundaries "
              "and never mid-line (all 106 segments also re-checked against the raw text), 20-60 s, no shared openings, "
              "≤15% overlap, reproducible. <b>Ablation:</b> with the writers' \"cliffhanger\" SFX notes (in 22 scripts) "
              "ignored, the top 10 is unchanged and 88 promos are shared."],
             ["AI-assisted review", "Claude read the top 20 against the script: 9 post as-is, 8 usable after a trim, "
              "3 not post-first-worthy."],
             ["Human / editor review", "<b>Not done yet</b>; no human labels or accuracy figures exist. "
              "outputs/review_sheet.csv is ready for it."]],
            [34 * mm, W - 34 * mm]),
      Spacer(1, 3),
      table([["Failure / limitation", "Real example from this run"],
             ["Keyword out of context", "#17 E38 gets a \"shocking line\" bonus for \"no one should <b>die</b> hungry\", a guard's "
              "kindness; it also opens on a reply (\"Why?\")."],
             ["Standalone ignores pronouns", "#15 E30: \"we give them a reason to condemn <b>her</b>\"; \"her\" is never seen, yet "
              "it scores 0.85."],
             ["End point a beat early", "#8 E11 ends 3 s before the stronger line (\"Going to strike a gentleman...?\")."],
             ["Weak-episode filler", f"{tiers.get('weak', 0)} weak-tier promos; episodes 14, 17, 19, 49 and 50 have nothing "
              "above weak (e.g. E19 exposition)."],
             ["Hook saturation", "many \"Stop!\" or scream openings score 1.0, so the top is separated mainly by the cliffhanger."],
             ["Script, not footage", "performance, music and pacing are invisible; series-level spoilers are not modelled."]],
            [34 * mm, W - 34 * mm]),
      P("<b>Fixed in the final audit</b> (old numbers reproduced first): overlap was divided by segment instead of clip "
        "(a 3 s sting once removed Episode 17's only promo); one sting opened three top-20 promos; E24 began 1.3 s "
        "<i>after</i> its riding-crop strike; a stitch relied on a character who was only mentioned. <b>Human in the "
        "loop:</b> mark review_sheet.csv, list IDs to reject or pin in an overrides file, rerun with --overrides.", small)]

s += [H("8. Scaling to every show"),
      P("Rules cost about 0.14 s per episode on one core, so 1,000 episodes take about 2-3 minutes; shows run in parallel. "
        "The LLM sees only the shortlist: a measured ~570 input tokens per clip × 40 clips ≈ 25k tokens per show, "
        "cached, and free on local Ollama. The tool is format-driven, not show-driven: character names are learned "
        "per run, and a new show only needs --input. The editor's job shifts from watching every episode to approving "
        "about 20 post-first promos per show. Next steps: fit the weights to the editor's verdicts, add pronoun checks, "
        "try ±1-2 shots around each cut, allow one post-first promo per episode, and run the LLM judge on the shortlist.")]

s += [H("9. AI tools used, and one place they got it wrong"),
      P("<b>Claude</b> (Anthropic) helped design, write and test the code, read the script to check the top picks, and "
        "draft this document; I reviewed the decisions and ran the tool. <b>Where it went wrong:</b> its first keyword "
        "matcher used prefix matching, so \"beg\" fired on \"<b>beg</b>gar's pocket\", \"explo\" on \"<b>explo</b>sive "
        "fury\", \"before\" in \"scrub floors before these commoners\" counted as a deadline, and a riding crop that was only "
        "<i>held</i> scored as a slap. These inflated hook scores near the top of the list. Reading the top picks against "
        "the script exposed it; the fix was whole-word matching, events instead of props, and a regression test. It also "
        "missed that Episode 6 writes SCENE START: in capitals, until the quality checker flagged untimed scenes.")]

doc.build(s)
print("wrote", ROOT / "docs" / "solution.pdf")
