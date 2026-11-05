# Quality Evaluation Report

**Run:** full Kuku input pack (50 episodes), rule-based scoring only.
**Command:** `python3 promo_finder.py --input input --output outputs --check-repro`
**Fingerprint:** `7e9df417611c8331` · **runtime:** ~7 s on one CPU core · log: `outputs/run_log.txt`

This report keeps three kinds of evidence apart:

| Kind | Who / what | Where |
|---|---|---|
| **Automated validation** | Code checks every promo | §1, `outputs/quality_report.json` |
| **AI-assisted review** | Claude (AI assistant) read the top 20 against the script text | §3 |
| **Human / editor evaluation** | **Not done yet.** No human labels exist. | §5: `outputs/review_sheet.csv` is ready for it |

No accuracy metric is reported, because there is no ground truth to measure against.

---

## 1. Automated validation: 15/15 checks pass

| Check | Result |
|---|---|
| All input files parsed | 50/50 |
| Episode numbers unique and contiguous | 1–50; header number = file-name number for every file |
| Block timestamps valid (start ≤ end, ≥ 0) | 0 bad of 3,727 blocks |
| Blocks inside their scene bounds (±0.5 s) | 0 outside |
| Parser warnings / malformed blocks | none; header count = timing-line count in every file |
| Every episode has dialogue | yes (1,487 lines in total) |
| Segment timestamps inside their episode | yes |
| Cuts on shot boundaries, never mid-dialogue | yes; also re-checked **independently from the raw text** (106/106 segments) |
| Total duration 20–60 s | yes (21.6–57.5 s, median 34.9 s) |
| Hook / cliffhanger / why present | 95/95 |
| No two promos share >15% of their screen time | yes |
| No two promos share the same opening (new) | yes |
| Every episode yields ≥1 promo | 50/50 |
| Reproducible: two full runs identical | `7e9df417611c8331` both times |

**Pipeline counts:**
* 12,127 single + 366 stitched = **12,493 candidates**, all listed in `outputs/all_candidates.csv`.
* 2,720 pass the hook and cliffhanger gates.
* **95 selected:** 18 post-first, 61 review, 16 weak.
* 14 multi-segment promos, 8 of them across episodes.

**Ablation: does the tool just read the writers' notes?** 22 scripts write the word "cliffhanger" into an SFX cue. With `--no-script-cues`, the **top 10 is identical**, 19 of the top 20 stay, and 88 promos are shared (95 vs 94 selected). The ranking doesn't depend on that annotation.

## 2. Audit of the previous version: what changed and why

The previous submission reported 50 episodes, 3,727 blocks, 12,494 candidates, 93 promos (18/58/17) and 14/14 checks. **Every one of those numbers reproduced exactly** before any change, with fingerprint `f3348624392b7e64`. The audit then found four genuine issues:

| Issue | Evidence | Fix |
|---|---|---|
| **Overlap measured per segment, not per clip** | A 3 s sting reused inside another promo counted as 100% overlap. In testing, this removed Episode 17's only promo. | Shared time is now divided by the shorter promo's total duration, as the docs always said. A unit test was added. |
| **Same opening reused** | After the overlap fix, the 4 s E15 sting "Stop! What are you doing?!" opened three top-20 promos. | Two promos may not share their first 3 s. This is the 15th check. |
| **Clip opens just after its own impact** | E24 started at 00:33.80, 1.3 s *after* the riding-crop strike (00:32.50), because the reaction line "absorbs the brutal strike" earned the physical-hook bonus. The window that included the strike lost by 0.003. | −0.15 if a physical event begins in the 2.5 s before the clip starts. E24 now starts at **00:30.80**, with the strike 1.7 s in. |
| **Stitch held together by a spoken name** | E37: a 3 s "The wedding?" sting from Catherine was joined to a scene that only *mentions* her. | The shared character must be **on screen** in both parts (a speaker or named in action/camera text). |

**Effect:**
* Top 12 unchanged.
* 85 of the previous 93 promos kept.
* 10 added and 8 dropped (E37 stitch, E24 late start, E18 sting that missed the door crash, and five lower-ranked promos).
* No segment is reused anywhere.

**Considered and not changed:** a pronoun check for standalone context. Without gender or coreference it would trade one false positive for another, so it stays a documented limitation (§4) for the LLM layer.

## 3. Promos to post first (top 10)

| # | Segments | Dur | Hook (first 3 s) | Cliffhanger |
|---|---|---|---|---|
| 1 | E36 02:43.00–03:20.75 | 37.8 s | Mother grabs his coat: "Alexander, you cannot go back there alone!" | "…their dead Duke is coming home." (episode end) |
| 2 | E15 00:07.50–00:11.80 + E15 00:49.50–01:26.00 | 40.8 s | Juliana bursts in: "Stop! What are you doing?!" | "You want to fight us, veteran?" (cut before the fight) |
| 3 | E28 01:10.50–01:15.30 + E28 01:47.80–02:18.50 | 35.5 s | Catherine yanks the locket off Juliana's neck | "…before he sends you to the gallows." |
| 4 | E32 01:59.50–02:34.38 | 34.9 s | Catherine raises a branding iron: "Silence!" | "…let's see how brave you are when it touches your skin!" |
| 5 | E29 00:56.60–01:36.10 | 39.5 s | "You are doing this because I saved the Duchess!" | Cell door slams; alone: "Mother…" |
| 6 | E16 00:14.30–00:44.20 | 29.9 s | A thug lunges with a weapon | Alexander: "Who sent you?" (answer withheld) |
| 7 | E13 00:45.80–01:34.00 | 48.2 s | "Juliana, stop!" as she crawls into the thorns | Still digging, bleeding; he screams her name |
| 8 | E11 00:58.00–01:21.30 | 23.3 s | Constable trashes the cottage: "Stop! Those are my mother's medicines!" | Gerald: "Happy?" |
| 9 | E27 02:02.80–02:29.20 | 26.4 s | "She admits she has been giving the Duchess poison!" | "Take her." (episode end) |
| 10 | E36 01:39.00–02:07.50 | 28.5 s | "They arrested her!" | "Juliana… is in a cell?" (answer withheld) |

**Why this order:** hook and cliffhanger carry the most weight (0.27 each). #1–#4 are strong at **both** ends: they open on physical action and close on spoken stakes. #5–#8 open just as hard but end softer (cliffhanger 0.45–0.58).

## 4. AI-assisted review of the top 20 (Claude, against the script; *not* human evaluation)

| Verdict | Ranks | Count |
|---|---|---|
| Would post as-is | 1, 2, 3, 4, 6, 9, 10, 18, 19 | 9 |
| Usable after a trim | 5, 7, 8, 11, 13, 14, 16, 20 | 8 |
| Should not be in the post-first tier | 12, 15, 17 | 3 |

### Failure cases (all from this run)

**Keyword out of context** (the main remaining failure mode):
* **#17 E38** gets a "shocking line" bonus for *"no one should **die** hungry"*, a guard secretly feeding Juliana. It also opens on "Why?", a reply to something the viewer never saw. A slow monologue that shouldn't be post-first.
* Found and fixed while building the first version: "beg" fired on "beggar's", "explo" on "explosive fury", "before" (in front of) counted as a deadline, "bursting into laughter" counted as a physical event, and a riding crop being *held* counted as a slap.

**Missing narrative context / weak standalone:**
* **#15 E30**: the conspirators plot *"we give them a reason to condemn **her**"*. "Her" is never seen or named, but the clip scores 0.85 on standalone, because the check counts names, not pronouns.
* **#12 E42+E43**: the main stretch is a Crown official reading records about "Lord Sterling", whom a stranger has never met.

**Start and end points:**
* **#8 E11** ends about 3 s early. The next line, *"Going to strike a gentleman in front of his wife?"*, is the real cliffhanger.
* **#7 E13**: the last 10 s repeat the same action (digging, screaming her name), so the ending adds no new question.
* **#16 E18**: opens strongly on the cottage in flames with Juliana sobbing, but runs 57.5 s with a slow embrace in the middle. Trim to about 35 s.
* The E24 late start is fixed in this version (§2).

**Unnecessary or incoherent stitching:** every one of the 14 selected stitched promos beats its main stretch alone, by 0.01–0.20, and six of those mains fail the hook gate without the sting. The smallest gain (0.01) is the weakest case for stitching at all. The one incoherent stitch (E37) was removed by the on-screen rule. #12 above is coherent but relies on context a stranger lacks.

**Weak-episode filler:** each episode returns its best moment even when it is weak. 16 promos are in the `weak` tier. Episodes **14, 17, 19, 49 and 50** have nothing above it; for example, #94 E19 is an informant's exposition dump. The tier is the safeguard: skip these rather than post them.

**Hook saturation:** the mean hook score is 0.63, but many "Stop!" or scream openings score 1.0, so the top of the list is separated mostly by the cliffhanger score. Two E36 promos also sit in the top 10 (#1 and #10).

## 5. How a human editor corrects selections

1. Mark each row of `outputs/review_sheet.csv` as `post` / `maybe` / `reject`, with optional `adjusted_start` / `adjusted_end`.
2. Copy rejected IDs to `"reject"` and wanted IDs (e.g. from `all_candidates.csv`) to `"pin"` in an overrides file (format in `examples/overrides.example.json`).
3. Rerun with `--overrides overrides.json`. Rejected slots refill with the next-best non-overlapping window; pinned promos are forced in.
4. If a reject pattern repeats, change one weight or lexicon entry in `config.py`. Every score's `evidence` field shows which rule produced it.

## 6. Improvements needed

1. **Run the LLM judge** (implemented, never run on a real model) on the top 40. Measure how often it disagrees with the rules, starting with #12, #15 and #17.
2. **Collect editor labels** through the review sheet and fit the five weights instead of hand-tuning them.
3. **Pronoun and role resolution** for standalone context (needs an LLM or coreference).
4. **End-point refinement:** try ±1–2 shots around each chosen end (that fixes #8).
5. **Top-N diversity:** allow one post-first promo per episode.
6. **Spoiler map:** flag clips that show the series' late reveals.
