# Plan: patient-value research → trustworthy long-form video (v2)

Status: proposed, 2026-10-09. Builds on the research pipeline as it stands at commit 14183bb.

## How the dossier is made today

1. **Collected:** sources come from PubMed, Europe PMC, MedlinePlus and the trusted sites (Mayo Clinic, Hopkins,
   NHS, Cleveland Clinic).
2. **Extracted by an LLM, checked by code:** the LLM writes each fact from the source's own text and must attach
   an exact quote. Code checks the quote word for word against the source text, and every number in the claim
   must appear in the quote.
3. **Verified by a second LLM:** does the claim say exactly what the quote says, is it current, and does it
   contradict other facts?
4. **Section summaries are LLM-written**, but code deletes any sentence that doesn't cite a verified fact.

**The gap:** the video workflow receives all of this (`dossier.video_source()`: 175 facts, ~16k words) as one
block. Nothing selects, merges or orders it for a patient, so the script writers must do research editing and
writing at once.

## Goal

| Layer | What it is | Who uses it |
|---|---|---|
| **1. Evidence Library** (today's dossier, cleaned up) | every verified fact with its exact quote and source | you, for checking; the audit trail |
| **2. Patient Guide** (new) | the organised, patient-valued version: action card, sections in viewer-question order, evidence strength labels, a bonus evidence-based complementary medicine part; every sentence cites Layer 1 fact ids | **the input to the video workflow** |
| **3. Script Sources Guide** (new) | the sources the finished script actually uses, numbered, with what each supports | end of the script document + YouTube description |

The video workflow also learns to handle **long scripts (15–30 min)** by writing and checking chapter by chapter
instead of the whole script in one model call.

---

## Requirement A: works for any medical topic, not just cramps

Nothing in prompts or code may assume a topic. The muscle-cramp run is one test case among several.

1. **Topic type first.** `topic_planner` classifies the topic and picks the matching question spine:

   | Topic type | Example | Viewer-question spine (planner adapts wording) |
   |---|---|---|
   | Symptom | cramps, headache, dizziness | what to do now · is it dangerous · causes · prevention · what works/doesn't · when to see a doctor / ER |
   | Disease / condition | diabetes, migraine, knee OA | what it is · am I at risk · signs · diagnosis · treatment options compared · living with it · complications · when to escalate |
   | Medicine / supplement | statins, vitamin D | who needs it · benefits (numbers) · side effects (real vs feared) · interactions · how to take · stopping it · alternatives |
   | Test / procedure | colonoscopy, MRI, CBC | why it's done · preparation · what happens · risks · reading the result · next steps |
   | Lifestyle / nutrition | fasting, salt, sleep | what evidence shows · who benefits / who must be careful · how much / how to · myths |
   | Prevention / screening | vaccines, cancer screening | who and when · benefit vs harm (numbers) · myths · access in Egypt |
   | Mental health | anxiety, insomnia | what it is · when it's a disorder · self-help that works · treatments · stigma/myths · when urgent |

   Mixed topics combine spines (e.g. "statins and muscle pain" = medicine + symptom).
2. **Sections that don't apply are dropped.** A test/procedure topic gets no "home remedies" section; the
   complementary-medicine part (Phase 2) is included only when people actually use such remedies for that topic,
   and the planner says why.
3. **Topic-specific lists come from the planner, not from code:** "classic questions/remedies to check", regional
   remedies, special groups and red flags are generated per topic and logged in the plan, so you can see and edit
   them in `review.md`.
4. **Regression topic bank.** `tests/topics.md` lists 7 topics, one per type (muscle cramps, migraine, statins,
   colonoscopy preparation, intermittent fasting, HPV vaccine, insomnia). After each phase, an "eval run" executes
   research on 2–3 of them, rotating, and produces a short scorecard: facts kept, % cited by patient pages,
   off-topic facts found by the critic, classic questions answered, run time, cost. The result is compared with the
   `v1-baseline-research-web` tag. A phase only lands if it doesn't make another topic type worse.
5. **Unit tests** use fixtures from more than one topic type, so the code isn't tuned to cramps.

## Requirement B: the script works as one whole

Long scripts are built in chapters for reliability, but the viewer must experience one coherent story. That
coherence is designed in, not hoped for:

1. **One story spine before any chapter is written.** `strategy_planner` produces a *script blueprint*: the
   central promise (what the viewer will be able to do by the end), the narrative arc (hook → stakes →
   understanding → action → reassurance → call to action), the chapter list with each chapter's job, and the
   **through-lines**: recurring example/patient story, key terms, the one number to remember.
2. **A shared "script memory" passed to every chapter writer:**
   - the blueprint and style sheet (dialect, tone, how the doctor speaks, words to avoid)
   - terms already defined and how they were phrased (so a term isn't redefined or named differently)
   - facts already used (no repetition; later chapters can refer back: "زي ما قلنا…")
   - open loops: questions raised that must be answered later, and promises to pay off
   - the last ~150 words of the previous chapter, so the handover sentence flows
3. **Chapters are written in order when they depend on each other** (story chapters); independent ones
   (e.g. "myths", "complementary options") can run in parallel and get their bridges written afterwards.
4. **Whole-script integrity pass, after the chapters.** One model call over a compact view: per chapter, the
   summary, first and last paragraph, the facts used, and the terms. It checks for repetition, contradiction,
   broken promises or open loops, tone drift, pacing against target minutes, and missing transitions. It returns
   targeted edits per chapter (not a rewrite), which code applies; then only the touched chapters are re-checked.
5. **Final read-through for medium-length videos (≤15 min):** if the full script fits comfortably in context, one
   global coherence pass reads it all; above that, step 4's compact view is used. Either way the output is
   edits, never a full rewrite, so length can't cause a cut-off.
6. **Integrity checks in code:** every open loop is closed; no fact id is used in two chapters unless marked as a
   callback; the chapter word counts sum to target ±10%; every chapter has an entry and exit bridge; the hook's
   promise appears again in the conclusion.

---

## Phase 1: Better research (Layer 1)

1. **Plan around viewer questions** (topic_planner prompt), using the topic-type spine from Requirement A
   (for a symptom like cramps: *what do I do right now · is it dangerous · why me · prevention · what works /
   doesn't / is risky · special cases · when to see a doctor / ER*). 6–8 sections instead of 10.
2. **"So what?" filter** (extractor + verifier prompts): keep a fact only if it changes what a viewer would do or
   understand. Drop niche populations (dialysis, military, rare syndromes) unless the topic is about them. Lower
   `max_facts_per_section` from 20 to 12.
3. **Always check the classic questions:** the planner lists the remedies, medicines and beliefs people commonly
   ask about for this topic (for cramps: quinine, magnesium, B vitamins, potassium, pickle juice, stretching,
   compression stockings), and searches target guidelines and Cochrane reviews for each.
4. **Evidence-strength labels** (verifier prompt): every fact gets `proven` | `probably helps` | `weak / disputed`
   | `no benefit` | `harmful`, with a one-line reason (study type, size, consistency).
5. **Merge duplicates** (code): when two sources say the same thing (Mayo + MedlinePlus), keep one fact citing
   both. Agreement between sources is shown, not repeated.

## Phase 2: Bonus, evidence-based complementary & traditional medicine

A dedicated research section, with strict rules so nothing unproven is presented as helpful:

- **Sources:** NCCIH (nccih.nih.gov, the US National Center for Complementary and Integrative Health), Cochrane
  reviews, and PubMed systematic reviews/RCTs. NCCIH is added to the trusted sites and the site search.
- **What counts as "helps":** at least one systematic review or well-designed RCT showing benefit, and no
  important safety signal. One small trial = `possibly helps (limited evidence)`, said exactly that way.
- **Regional remedies are checked, not assumed:** the planner lists remedies common in Egypt and the Gulf for the
  topic (e.g. black seed, honey, herbal teas, cupping, specific foods), and each gets a verdict:
  `helps` · `possibly helps` · `not proven` · `harmful / interacts with medicines`.
- **In the Patient Guide:** a "Complementary options: what the evidence says" part listing only `helps` and
  `possibly helps`, each with its strength label and any safety warning. A "Popular but unproven" box covers the
  rest, respectfully worded, so viewers hear an honest answer about remedies they already know. That builds
  trust without endorsing anything unproven.
- If nothing qualifies, the section says so in one line ("no complementary approach has good evidence yet for
  X"). That honesty is part of the value.

## Phase 3: Patient Guide (Layer 2), the version that feeds the video

New research step `guide_editor` after the coverage loop (prompt `prompts/research/patient_guide.md`):

- **Input:** the verified facts (Layer 1) with ids, strength labels and sources.
- **Output (JSON → `patient_guide.md/.html`):**
  1. **Action card:** Do this now · Tonight / before exercise · Prevention that works · Doesn't work or is risky
     · See a doctor if · Go to the ER if.
  2. **Sections in viewer-question order**, each a short plain-language explanation built from selected facts.
  3. **Complementary options** (Phase 2) and **Myths vs evidence**.
  4. **Key numbers** worth saying on camera.
  5. **Open questions / where evidence is weak**, so the script doesn't overclaim.
- **Rules enforced by code (as for dossier summaries today):** every sentence must cite real verified fact ids
  `[F12]`; uncited sentences are removed; a strength label travels with each claim. Nothing new is introduced:
  the guide only selects, merges, orders and simplifies.
- **Review step stays:** you review `patient_guide.md` (plus the library if you want) in `review.md` before the
  video is built.
- `video_source()` switches from "all facts" to the Patient Guide. The full library stays available to the
  `medical_truth_verifier` for checking.

## Phase 4: Script Sources Guide (Layer 3)

- **Citation tags travel with the script:** the script writers keep invisible source tags per claim
  (e.g. `⟦F12⟧`) in the working script. Production nodes strip them from the spoken text and the teleprompter
  view, so they're never read aloud.
- **`medical_truth_verifier`** checks each tagged claim against its fact (and flags untagged medical claims).
- **Final package appendix "Sources for this video":** numbered list grouped by script chapter. Each entry gives
  the source (title, organisation/journal, year, link), its evidence level, and the claim(s) it supports in
  the script, in Arabic, with the English original title.
- **YouTube description:** a short version (top 5–8 sources + a link line).
- Only sources actually used in the final script are listed; the full library stays in the research folder.

## Phase 5: Long-form scripts (15–30 min) without getting stuck

**Why it gets stuck today:** body_restructurer, dialect_warmth_layer and script_refinement each rewrite the
**whole** script in one call (9,000-token cap); the quality judges read the whole script (12,000); and
final_script_package Part 1/2 regenerates everything with tables (10,000/12,000). A 20-minute Arabic script
(~2,700–3,000 spoken words), plus thinking tokens and table markup, overflows these caps. That causes cut-off
answers, retries with bigger limits, then fallbacks: slow, and sometimes stuck.

**Design: chapter-wise pipeline**

1. `target_minutes` in input_fields.json (default 10, up to 30). `strategy_planner` outputs a **chapter plan**:
   chapters (≈3–5 min each), each with its goal, its Patient Guide sections/fact ids, and a word budget
   (≈135 Arabic words/min).
2. **Writers work per chapter:** body, dialect warmth and refinement run once per chapter, given the chapter plan
   + a running summary of previous chapters (not their full text) + the hook. Chapters are written in parallel
   where they don't depend on each other.
3. **Judges work per chapter plus one global pass:** fidelity and medical truth per chapter (with that chapter's
   facts); self-critique gets the chapter summaries + transitions for flow; the existing loops re-run only the
   failing chapter.
4. **Production per chapter:** transitions, overlays and B-roll per chapter; final package tables built chapter
   by chapter and stitched by code (no single giant answer).
5. **Token budgets from length:** max_tokens per call computed from the chapter's word budget (with room for
   thinking), not fixed numbers. The length check uses `target_minutes` (today it's hard-coded "≥8 min → 1,000
   words").
6. **Checkpoint per chapter:** a crash or a cut-off resumes at the failed chapter, never from the start.

Short videos (≤10 min) become a single chapter, so they behave as today.

---

## Order of work, effort, and how we'll check it

| Phase | Work | Check |
|---|---|---|
| 0 | topic bank + eval scorecard script; baseline scores at tag `v1-baseline-research-web` | scorecard for 3 topic types recorded |
| 1 | topic-type spines + filters (prompts, me) + fact merge + labels (DeepSeek) | eval on cramps + 2 other types: fewer off-topic facts, classic questions answered, no type worse |
| 2 | NCCIH source + complementary section rules (me + DeepSeek) | complementary verdicts with labels; skipped for topics where it doesn't apply (e.g. colonoscopy prep) |
| 3 | `guide_editor` step, guide export, review flow, video input switch | read each guide as a patient: action card right; every sentence cites |
| 4 | citation tags through the script, verifier check, sources appendix | every medical claim in the final script maps to a listed source |
| 5 | blueprint + script memory + chapter pipeline + integrity pass + checkpoints | 10-, 20- and 30-min scripts finish without cut-offs; integrity checks pass; a blind read finds no repetition or seams |

Phases 1–3 make the research better; 4 adds credibility; 5 is the biggest code change and can start in parallel
with 3–4 since it touches the video side.
Coding goes to DeepSeek (Flash for most, V4 Pro for the chapter-pipeline core); prompts and reviews by Claude.
DeepSeek balance ≈ $3 covers this comfortably; each full research + video test run costs cents.
