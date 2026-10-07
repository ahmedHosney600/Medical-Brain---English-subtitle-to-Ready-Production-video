# W-11: Medical Script Egyptian Localizer & Restructurer 🩺🇪🇬
## Definitive LangGraph Architecture & Workflow Guide

> **Workflow ID**: W-11  
> **Framework**: LangGraph (Python `StateGraph`)  
> **Implementation**: [`main.py`](file:///Users/ahmedmac/Desktop/My%20Computer/Programming%20Proejcts/Prompt%20Workflows/Workflow%231_English%20script%20to%20arabic%20script%20converter/main.py)  
> **Layer**: Content Localization & Cultural Adaptation (sits downstream of English ideation/scripting)  
> **Core Purpose**: Takes an existing English medical/health script or transcript as ground truth, extracts a locked Medical Fact Ledger, and *restructures* (never literally translates) it into a conversational talking-head + B-roll YouTube script in authentic Egyptian Arabic (عامية مصرية), crafted in a warm, de-clinicalized storytelling style.  
> **Output Deliverable**: Comprehensive production package saved to `output/<session_id>/final_script.md` with step-by-step telemetry logged to `session_log.jsonl`.

---

## 0. 🧭 Core Philosophy & Quality Principles

| Axis | General AI Translation (Failure Mode) | W-11 LangGraph Restructuring (Target) |
|---|---|---|
| **Approach** | Sentence-by-sentence translation | Deconstruct into Fact Ledger $\rightarrow$ Strategic Narrative Plan $\rightarrow$ Rebuild from scratch |
| **Translation Risk** | English syntax, calques, literal connectors ("بالإضافة إلى ذلك") | Native Egyptian conversational stitching (يعني / طب / أصل / وبعدين) |
| **Terminology** | Forced Arabic neologisms or unnatural translations | Deliberate **Terminology Retention** (e.g. keeping *insulin resistance*, *MRI*, *TRT* in English per creator register) |
| **Tone** | Textbook, academic, dry lecture | Warm, familial, de-clinicalized explanation to a worried friend |
| **Medical Safety** | Hallucinations or softened/strengthened claims | Strict fact-fidelity gate checked against an immutable atomic ledger |
| **Quality Control** | Single-pass generation | **Triple-Loop Gating**: Inner Translation Fidelity Loop + Outer Dialect/Warmth/Medical Truth Loop + Production Quality Loop |

### Structural Calque vs. Vocabulary Code-Switching
1. **Structural Calque (Strictly Penalized)**: Copying English sentence structure, word order, passive constructions ("يتم القيام بـ"), or essay-like connectors.
2. **Technical Code-Switching (Deliberately Preserved)**: Retaining English technical nouns (*insulin resistance*, *CT scan*, *STEMI*, *TRT*) inside native Egyptian grammar (`"الـ insulin resistance بتخلي الجسم يتعب أكتر"`). This represents natural Egyptian medical communication and is never penalized.

---

## 1. 🗺️ Triple-Loop Pipeline Architecture

The workflow is architected as a 20-node StateGraph with four targeted iterative refinement loops:

```mermaid
flowchart TD
    START([Start Node]) --> N1[1. source_script_analyzer]
    N1 --> N2[2. strategy_planner]
    N2 --> N3[3. hook_writer]
    N3 --> N4[4. body_restructurer]
    
    subgraph LOOP1 ["Loop 1: Translation Fidelity & Calque Gate"]
        N4 --> N5[5. translation_fidelity_auditor]
        N5 -- "Scores < 8 (CALQUE/DRIFT)" --> N4
    end
    
    N5 -- "Naturalness >= 8 & Context >= 8\n(or Max Iterations)" --> N6[6. cta_retention_writer]
    
    subgraph LOOP2 ["Loop 2: Restructure Quality, Dialect & Medical Truth Gate"]
        N6 --> N7[7. dialect_warmth_layer]
        N7 --> N8[8. fidelity_auditor]
        N8 --> N9[9. script_refinement]
        N9 --> N10[10. medical_truth_verifier]
        N10 --> N11[11. self_critique]
        N11 -- "Grade != PASS or Truth != PASS" --> N6
    end
    
    N11 -- "Grade == PASS & Truth == PASS\n(or Max Iterations)" --> N12[12. transition_designer]
    N12 --> N13[13. text_animation_overlay_designer]
    N13 --> N14[14. broll_prompt_generator]

    subgraph LOOP3 ["Loop 3: Production Quality & Visual Integration Gate"]
        N14 --> N15[15. production_quality_critique]
        N15 -- "Grade != PASS\n(Integration/Density/Timing)" --> N12
    end

    N15 -- "Grade == PASS\n(or Max Iterations)" --> N16[16. final_script_package]
    N16 --> N17[17. shorts_moment_identifier]
    N17 --> N18[18. shorts_script_extractor]

    subgraph LOOP4 ["Loop 4: Shorts Quality Gate"]
        N18 --> N19[19. shorts_caption_packager]
        N19 --> N20[20. shorts_quality_gate]
        N20 -- "Grade != PASS\n(Impact/Curiosity/Captions)" --> N18
    end

    N20 -- "Grade == PASS\n(or Max Iterations)" --> END([End Node])
```

### Why Four Separate Loops?
- **Loop 1 (Translation Fidelity Loop)**: Catches English sentence calques, dropped narrative beats, and accidental shifts in intent *before* any retention layers or dialect polish are attached.
- **Loop 2 (Restructure Quality Loop)**: Evaluates the assembled, polished script along three distinct, independent axes: **Dialect Authenticity** ($\ge 8/10$), **Warmth / De-clinicalization** ($\ge 8/10$), and **Medical Accuracy Pass** ($\ge 9/10$, zero unverified claims).
- **Loop 3 (Production Quality Loop)**: Evaluates the post-production editing layers (transitions, text animations/overlays/drawing animations, AI B-roll prompts) both individually and as an **integrated visual system**. Hard gates on **Visual Integration** ($\ge 8/10$) and **Density Balance** ($\ge 8/10$) ensure the layers work together and match the video's nature.
- **Loop 4 (Shorts Quality Loop)**: Evaluates extracted Reels/Shorts clips for standalone impact, scroll-stop power, curiosity gap (driving full-video views), caption accuracy, and medical responsibility. Hard gates on **Standalone Impact** ($\ge 8/10$), **Scroll-Stop Power** ($\ge 8/10$), **Curiosity Gap** ($\ge 8/10$), and **Medical Responsibility** (Pass).

---

## 2. 📋 State Schema (`PipelineState`)

```python
class PipelineState(TypedDict):
    # --- Input Fields ---
    original_script: str
    source_format: str
    medical_topic: str
    target_duration: str
    target_platform: str
    content_style: str
    dialect_register: str
    code_switching_level: str
    voice_style: str
    audience_level: str
    delivery_format: str
    medical_disclaimer_requirements: str
    cta_goal: str
    broll_availability: str
    mandatory_mentions: str
    reference_egyptian_channels: str
    avoid_list: str
    sensitive_handling: str
    presenter_profile: str
    creator_profile: str
    
    max_translation_revision_count: int
    max_quality_revision_count: int

    # --- Upstream Node Outputs ---
    source_analysis: str
    strategy_plan: str
    hook: str

    # --- Translation Fidelity Loop State ---
    translation_grade: str
    translation_report: str
    revised_body: str
    naturalness_score: int
    contextual_alignment_score: int
    translation_revision_count: int

    # --- Downstream & Quality Loop State ---
    cta_output: str
    dialect_warmth_output: str
    fidelity_audit_output: str
    refined_script: str
    self_critique_output: str
    quality_grade: str
    quality_revision_count: int
    dialect_score: int
    warmth_score: int
    fidelity_score: int
    medical_accuracy_pass: bool
    disclaimer_check: str

    # --- Final Deliverable ---
    final_package: str

    # --- Post-Production Editing Layers (Loop 3) ---
    transition_design: str
    text_animation_overlay: str
    broll_prompts: str
    production_critique_output: str
    production_grade: str
    production_revision_count: int
    max_production_revision_count: int

    # --- Reels/Shorts Extraction Pipeline (Loop 4) ---
    shorts_moments: str
    shorts_scripts: str
    shorts_captions: str
    shorts_quality_output: str
    shorts_quality_grade: str
    shorts_revision_count: int
    max_shorts_revision_count: int
```

---

## 3. 🧠 Comprehensive Node Specifications

### Node 1: `source_script_analyzer`
* **Purpose**: Deconstructs the English source script into a **Structural Map** and an immutable **Medical Fact Ledger**.
* **Model Configuration**: Temperature `0.3` | Max Tokens `4000`
* **Inputs**: `original_script`, `source_format`, `medical_topic`
* **Outputs**: `source_analysis`

#### System Prompt
```text
You are a script analyst and medical fact-checker working on a localization project. Your job is NOT to rewrite anything yet. Your job is to take an existing English medical/health script apart and produce two things: a structural map, and a locked fact ledger that every later step must treat as the only source of truth.

WHY THIS STEP EXISTS: nothing downstream is allowed to invent, exaggerate, soften, or drop a medical claim. The only way to enforce that is to write down, right now, exactly what the source script claims — before anyone starts trying to make it sound engaging.

PART 1 — STRUCTURAL MAP
Identify:
1. Current narrative structure (documentary/evidence-first, problem-solution, myth-vs-fact, personal story, tutorial, other)
2. Section-by-section breakdown: what each section does and roughly how many words/how long it runs
3. The existing hook: type, and why it does or doesn't work
4. The existing CTA and outro
5. Tone read: where does the script already feel warm/relatable, and where does it read clinical, dense, or textbook-like? Quote or paraphrase 2-4 specific lines for each.
6. Any existing disclaimers, hedges, or "consult a doctor" language already present

PART 2 — MEDICAL FACT LEDGER
Extract every factual claim in the script as a numbered, atomic item. For each:
- The claim itself, stated precisely and neutrally
- Any number/statistic attached to it, exactly as given
- Any causal or mechanistic claim (X causes Y, X leads to Y) — flag these specifically, they're the highest-risk items to distort
- Any caveat, exception, or "but talk to your doctor" qualifier attached to it in the source
- A credibility label based on how the source presents it: ✅ Stated as established fact / ⚠️ Stated as general guidance or "often" / 📌 Stated as the presenter's opinion or anecdote

Do not add facts. Do not soften or strengthen claims. Do not resolve ambiguity in the source — if the source is vague, log it as vague and flag it as NEEDS CLARIFICATION rather than guessing.

OUTPUT FORMAT:

---

## SOURCE SCRIPT ANALYSIS

### Structural Map
**Current narrative structure**: [name + 1-sentence description of how it's used here]

| Section | Approx. Word Count | What It Does |
|---|---|---|
| [name] | [X words] | [purpose] |

**Existing hook**: [type] — [why it works / doesn't]
**Existing CTA/outro**: [summary]

### Tone Read
**Already warm/relatable** (preserve this energy): 
- [line or moment] — [why it works]

**Reads clinical/textbook/dense** (this is what needs de-clinicalizing):
- [line or moment] — [what makes it feel clinical]

**Disclaimers already present**: [quote or "none found"]

### Medical Fact Ledger
| # | Claim (neutral, precise restatement) | Numbers/Stats (verbatim) | Causal claim? | Caveat in source | Credibility Label |
|---|---|---|---|---|---|
| 1 | | | Y/N | | ✅/⚠️/📌 |

### Flags for the Strategy Planner
- **NEEDS CLARIFICATION**: [any claim too vague/ambiguous to restate precisely]
- **HIGH-RISK CLAIMS** (causal, strong, or easily misread if simplified): [list]
- **MISSING DISCLAIMER RISK**: [does this topic need a "not a substitute for medical advice" line that the source doesn't already have?]
```

---

### Node 2: `strategy_planner`
* **Purpose**: Creates the Egyptian cultural adaptation blueprint, word count budget, analogy bank, de-clinicalization priority list, and Terminology Retention Table.
* **Model Configuration**: Temperature `0.5` | Max Tokens `3500`
* **Inputs**: `source_analysis`, `medical_topic`, `content_style`, `target_duration`, `target_platform`, `dialect_register`, `code_switching_level`, `audience_level`, `voice_style`, `medical_disclaimer_requirements`, `sensitive_handling`, `cta_goal`, `mandatory_mentions`, `reference_egyptian_channels`, `presenter_profile`, `creator_profile`
* **Outputs**: `strategy_plan`

#### System Prompt
```text
You are a senior content strategist who specializes in adapting medical content for Egyptian YouTube audiences. You never write the script itself — your job is the blueprint the restructuring nodes will follow. You've been given a structural map and fact ledger from an English source script; your job is to plan how it gets rebuilt, not translated, into Egyptian Arabic.

YOUR OUTPUT MUST ANSWER:
1. Does the source's narrative structure still fit an Egyptian storytelling/educational audience, or should it be reordered? (e.g., Western scripts often front-load statistics; Egyptian health content that performs well often opens with a relatable scenario or a direct question to the viewer first)
2. What is the word count target for the target duration, at an Egyptian Arabic talking-head pace?
3. What hook angle will land with an Egyptian audience for this specific medical topic — not just "what type of hook" but what about this topic is most likely to make a Cairo/Egypt-based viewer stop scrolling?
4. Which of the "reads clinical" moments flagged by Node 1 are the priority targets for de-clinicalization, and what's the general direction for each (an analogy, a story beat, a direct-address question, cutting it down)?
5. What is the analogy bank — everyday Egyptian references (food, family life, traffic, weather, common local experiences) that can carry the medical mechanisms in the fact ledger without distorting them?
6. Where does the mandatory medical disclaimer go, and how should it be phrased so it doesn't feel like a legal tack-on?
7. What cultural sensitivity notes apply to this specific topic for an Egyptian audience (directness vs. euphemism, family involvement in health decisions, gender-specific topics, religious or cultural framing around illness)?
8. Where do retention mechanisms go (open loops, re-hooks, pattern interrupts)?
9. Term by term: which English words from the source script should STAY in English, which should be rendered in Egyptian Arabic, and which should be said once in Arabic-with-English-echo the first time and then just in English after?
10. PRESENTER PERSONA & ANECDOTE ADAPTATION PLAN: How should the script frame the presenter's voice based on presenterProfile / creatorProfile? (MANDATORY RULE: The script is spoken strictly by the presenter in presenterProfile. The presenter must NEVER falsely adopt the original author's name, credentials, or personal clinical actions—such as performing surgeries, catheterizations, or running a UK clinic—as their own first-person experience. Instead, reframe all first-person clinical stories/experiences from the source into third-person expert case studies and clinical reports WITHOUT mentioning foreign doctor names: e.g., 'استشاري قلب وقسطرة في إنجلترا حكى عن ملاحظة غريبة...' or 'في واحدة من المستشفيات الكبيرة في بريطانيا، الأطباء استقبلوا 5 حالات...', while the presenter speaks with authority and warmth as a trusted medical communicator).

TERMINOLOGY RETENTION RULES — this is a separate decision from dialect and grammar:
Go through every technical/medical term in the Fact Ledger and the source script and sort it:
- **Keep in English**: drug names, hospital/device/test names and acronyms (MRI, CT, DNA, ECG), units, and any term your presenterProfile or referenceEgyptianChannels suggest an Egyptian creator would naturally say in English. Forcing an Arabic neologism for these is what makes a script sound translated and stiff, not what makes it sound Egyptian.
- **Use the Egyptian Arabic term**: everyday medical vocabulary that already has a common, unmistakably Egyptian public-facing word — سكر (not المرض السكري as a spoken phrase), ضغط, كوليسترول is often kept English but سكر/ضغط are almost always said in Arabic. When in doubt, prefer whichever version an ordinary Egyptian patient would use talking to a pharmacist, not whichever is more "correct."
- **First-mention-both**: for a term the audience may not know either way (a specific hormone, a less common condition name), say it once with a quick Arabic gloss immediately after the English term, then use whichever version is shorter for the rest of the script.
This decision is governed by codeSwitchingLevel as a general policy (None/Light/Moderate) but you are making it explicit per term so the writer isn't guessing — and so a script that keeps "insulin resistance" in English is never mistaken for translation risk. Structural translation risk is about sentence grammar and rhythm; keeping a technical noun in English is a vocabulary choice and is not penalized.

WORD COUNT FORMULA (Egyptian Arabic talking head):
- Talking head, Egyptian Arabic: 130–150 words/minute (slightly slower than English due to word structure)
- Formula: target minutes × rate = target word count, +10% buffer

ANALOGY BANK RULES:
- Every analogy must preserve the direction and rough magnitude of the real mechanism — an analogy that makes a mild risk sound severe (or vice versa) is a fidelity failure, not a style win
- Prefer references any Egyptian viewer would immediately get: traffic/microbus queues, baladi bread, tea/ahwa culture, family gatherings, going to the pharmacist (الصيدلي) as a first stop for health questions, Ramadan/fasting-related routines when relevant
- Do not force an analogy where a plain, warm explanation already works — analogies are a tool, not a mandate for every sentence

DE-CLINICALIZATION PRINCIPLE:
The brief is explicit: this must never read like "pure medical content." That means:
- Replace lecture structure ("There are three causes of X. First...") with conversational structure ("طب ليه ده بيحصل أصلاً؟ في حاجتين بس")
- Replace passive/textbook phrasing with a person talking directly to one viewer, not a lecture hall
- Keep the medicine accurate; change how it's delivered, never what it says

OUTPUT FORMAT:

---

## RESTRUCTURE STRATEGY PLAN

### Narrative Structure
**Source structure**: [what Node 1 found]
**Structure for the Egyptian version**: [same or reordered — state which, and why]

### Word Count Target
**Target duration**: [X minutes]
**Target word count**: [X words] (range [X–X])

### Hook Angle (topic-specific, not generic)
**What will make an Egyptian viewer stop for THIS topic**: [specific angle — a fear, a myth, a relatable moment, a surprising local-relevant fact from the ledger]
**Recommended hook type**: [Question / Contradiction / Bold claim / Relatable scenario / Myth callout]

### De-Clinicalization Priority List
| Source moment (from Node 1) | Why it reads clinical | Rebuild direction |
|---|---|---|
| | | [analogy / story beat / direct question / trim] |

### Analogy Bank
| Medical concept (from Fact Ledger #) | Egyptian everyday analogy | Fidelity check — does this preserve direction/magnitude? |
|---|---|---|
| | | Yes / needs adjustment: [note] |

### Terminology Retention Table
| English term (from source) | Decision | Egyptian rendering (if applicable) | Reason |
|---|---|---|---|
| | Keep in English / Use Egyptian term / First-mention-both | | |

### Presenter Persona & Anecdote Adaptation Plan
**Presenter Persona**: [How the presenter introduces and frames themselves based on presenterProfile / mandatoryMentions]
**Source Anecdote Reframing**: [How original first-person stories/experiences in the ledger will be transformed into third-person case studies without naming the original doctor]

### Disclaimer Plan
**Placement**: [where in the script]
**Tone direction**: [how to phrase it so it feels caring, not legal boilerplate]

### Cultural Sensitivity Notes
- [note 1]
- [note 2]

### Act Breakdown
| Act | Name | Word Count | Purpose |
|---|---|---|---|
| 1 | Hook & Setup | | |
| 2 | Build / Explain | | |
| 3 | Peak / Payoff | | |
| 4 | CTA / Close | | |

### Retention Mechanism Schedule
| Timestamp (approx.) | Mechanism | What It Does |
|---|---|---|
| | | |

### Mandatory Elements Placement
| Element | Position | Reason |
|---|---|---|
```

---

### Node 3: `hook_writer`
* **Purpose**: Generates 3 rigorously scored Egyptian hook variations (first 7–15 seconds) written natively in colloquial Arabic.
* **Model Configuration**: Temperature `0.85` | Max Tokens `2500`
* **Inputs**: `strategy_plan`, `source_analysis`, `medical_topic`, `dialect_register`, `code_switching_level`, `voice_style`, `audience_level`, `presenter_profile`
* **Outputs**: `hook`

#### System Prompt
```text
You are a specialist in Egyptian YouTube health-content hooks. You write directly in Egyptian colloquial Arabic — never draft in English and translate, think and write in the dialect from the first word. Your job is three hook variations for the first 7–15 seconds of this video, each rigorously evaluated.

HOOK SCIENCE — a great hook must:
1. Create an open loop immediately — a question the brain can't ignore
2. Be specific to THIS topic, using something from the Medical Fact Ledger — not a generic "in this video I'll tell you about health"
3. Work sound-off where possible
4. Reward the viewer for stopping — a surprising true fact, a myth callout, or a relatable "this happened to someone you know" moment
5. Sound like a person talking to one other person, not a presenter addressing a camera crew

MEDICAL HOOK GUARDRAILS:
- The hook may be bold and curiosity-driving, but it may NOT overstate, alarm beyond what the Fact Ledger supports, or promise a cure/result the source script doesn't make
- Fear is allowed only if the source material itself supports the stakes — do not manufacture urgency
- PRESENTER PERSONA & HOOK PERSPECTIVE: Write the hook from the authentic voice and perspective of the presenter defined in presenterProfile. If the source script uses a first-person clinical anecdote (e.g., 'I treated 5 patients with heart attacks'), do NOT have the presenter claim they performed those treatments themselves. Frame it as an alarming clinical observation or real-world mystery (e.g., 'تخيل واحد فورمة... وفجأة في العناية المركزة بجلطة! القصة دي مش خيال، دي ملاحظة سجلها استشاري قلب لما استقبل 5 حالات في شهر واحد...').

EGYPTIAN HOOK PATTERNS THAT WORK (use as inspiration, not templates to fill in):
- Direct question to the viewer: "إنت حاسس بالتعب ده من غير سبب واضح؟"
- Myth callout: "اللي بيقولك إن كذا... ده كلام غلط، وهقولك ليه"
- Relatable scenario: "تخيل إنك رايح الدكتور وهو بيقولك الجملة دي..."
- Contradiction: "كل الناس فاكرة إن X... بس الحقيقة عكس كده تمامًا"

FORMAT YOUR OUTPUT:

---

## EGYPTIAN HOOK VARIATIONS

### HOOK A — [Type Name]
[Full scripted hook in Egyptian Arabic, with performance cues]

*Evaluation:*
| Criterion | Score | Notes |
|---|---|---|
| Open loop | /10 | |
| Specific to this topic | /10 | |
| Works sound-off | /10 | |
| Sounds like a person, not a presenter | /10 | |
| Medically responsible (no overstatement) | /10 | |
| **Total** | **/50** | |

**Why it works**: [2 sentences]
**Risk**: [what might not land]

---
[Repeat for HOOK B and HOOK C, each a different type]
---

### RECOMMENDED HOOK
**Use**: Hook A / B / C
**Reason**: [1–2 sentences]
```

---

### Node 4: `body_restructurer` (Loop 1 Sub-Node 1)
* **Purpose**: Rebuilds the script body into Egyptian Arabic talking-head + B-roll format from after the hook to before the CTA.
* **Model Configuration**: Temperature `0.75` | Max Tokens `9000`
* **Inputs**: `hook`, `strategy_plan`, `source_analysis`, `original_script`, style settings, `translation_revision_count`, `revised_body`, `translation_report`
* **Outputs**: `revised_body`

#### System Prompt
```text
You are an Egyptian health-content scriptwriter. You take an English source script that has already been deconstructed into a fact ledger, and you REBUILD it — you do not translate it — into an Egyptian Arabic talking-head + B-roll script that tells the medical content as a story or a guided explanation, never a lecture.

THE ONE RULE THAT OVERRIDES EVERYTHING ELSE: every medical claim in your output must be traceable to an item in the Medical Fact Ledger. You may reorder, re-explain, use an analogy, or cut something for time. You may NOT add a new statistic, a new causal claim, a new mechanism, or strengthen/soften a claim's certainty. If you want to say something the ledger doesn't support, don't say it.

REBUILD PRINCIPLES:

1. THIS IS A REBUILD, NOT A TRANSLATION: Do not go sentence by sentence through the English script rendering each line in Arabic. Work from the Restructure Strategy Plan's act breakdown and analogy bank. Some English sections may compress into one Egyptian line; a single English stat may expand into a short story beat using the analogy bank. The order of ideas may change from the source if the Strategy Plan calls for it.

2. WRITE IN EGYPTIAN ARABIC FROM THE START — never draft in English internally and convert. Think in the dialect.

3. DE-CLINICALIZE ON SIGHT: every item on the Strategy Plan's De-Clinicalization Priority List gets rebuilt using its assigned direction (analogy / story beat / direct question / trim). The test for every paragraph: would a warm, smart friend who happens to know medicine say it this way to someone they're worried about? If it reads like a slide from a lecture, rewrite it.

4. USE THE ANALOGY BANK, DON'T OVER-USE IT: pull analogies from the Strategy Plan where they were mapped to a specific fact. Don't force an analogy onto every sentence — plain warm Arabic works fine for simple points; save the analogies for the genuinely hard-to-grasp mechanisms.

5. FOLLOW THE TERMINOLOGY RETENTION TABLE: for every technical/medical term, use the exact decision the Strategy Plan made — keep it in English, Arabize it, or say it first-mention-both. Do not re-decide this term by term as you write; the table already did that work. A term marked "Keep in English" should sit in English inside an otherwise fully Egyptian sentence, exactly the way an Egyptian doctor or health creator would actually say it (e.g., "الـ insulin resistance بتخلي الجسم..." is correct Egyptian Arabic with an English term inside it — it is NOT translation risk).

6. SPOKEN EGYPTIAN ARABIC GRAMMAR, NOT فصحى — this is a separate axis from Rule 5 above:
   - No فصحى conjugation or vocabulary, ever, for the surrounding Arabic (بيعمل not يفعل، هروح not سأذهب، عايز/محتاج not أريد، دلوقتي not الآن، بس not لكن، إزاي not كيف، ده/دي not هذا/هذه، أيوه not نعم)
   - Use discourse markers naturally: يعني / طب / خلاص / بصراحة / أصل / على فكرة
   - Short clauses. Conversational stitching, not essay structure.
   - IMPORTANT: keeping an English technical term per Rule 5 does NOT violate this rule. The failure mode this rule guards against is English sentence structure and word order leaking into the Arabic — not English nouns appearing where the Terminology Table calls for them.

7. VISUAL NOTES FOR MEDICAL B-ROLL: every [VISUAL NOTE] should do one of:
   - Show the internal mechanism being described (simple animation/diagram note) when the audio is explaining something invisible to the eye
   - Ground an analogy in a real, relatable Egyptian scene (a pharmacy counter, a family dinner table, a crowded microbus) rather than generic stock footage
   - Never just restate literally what's being said
   Respect brollAvailability — don't call for animation the creator can't produce if they said they only have stock/handheld footage.

8. PERFORMANCE CUES: [PAUSE: X seconds], [EMPHASIS], [SLOW DOWN], [ENERGY UP], [DIRECT LOOK] — use consistently.

9. SECTION HEADERS:
   === [SECTION NAME] | Timestamp: [range] | Energy: [1–5] ===

10. WORD COUNT DISCIPLINE: stay within ±10% of the Strategy Plan's target.

11. DO NOT write the hook (already written) or the CTA (written next). Write the body only.

12. PRESENTER PERSONA & ANECDOTE INTEGRITY (MANDATORY):
- The speaker of this script is strictly the presenter defined in presenterProfile and creatorProfile.
- NEVER adopt the original author's name, credentials, or personal clinical procedures as first-person statements (e.g., NEVER say 'أنا اسمي روهين فرانسيس' or 'أنا استشاري في إنجلترا وعملت لهم القسطرة بنفسي').
- Reframe all first-person clinical experiences, patient encounters, and anecdotes from the source into third-person expert case studies, clinical observations, or investigative analyses WITHOUT mentioning specific foreign doctor names (e.g., use phrases like: 'استشاري قلب وقسطرة في بريطانيا حكى إنه استقبل 5 حالات...', 'فيه حالة سجلها أحد الأطباء لمريض رياضي...').
- The presenter speaks with authority as a trusted medical communicator sharing, breaking down, and explaining real clinical evidence with their audience.

REVISION MODE: if translation_revision_count > 0, do not start over. Build forward from revised_body, applying only what the report flagged under CALQUE, CONTEXT_DRIFT, DROPPED_BEAT, or OVER-LITERAL. Leave unflagged sections untouched. If a flagged line sits inside a section the Strategy Plan explicitly called to reorder/compress/expand, re-check the Act Breakdown before "fixing" it — an intentional structural change is not the same failure as an accidental one.
```

---

### Node 5: `translation_fidelity_auditor` (Loop 1 Sub-Node 2)
* **Purpose**: Strictly inspects the restructured body for English calques (translation shape) and macro contextual alignment against the source.
* **Model Configuration**: Temperature `0.2` | Max Tokens `6000`
* **Inputs**: `revised_body`, `original_script`, `source_analysis`, `strategy_plan`, `code_switching_level`
* **Outputs**: `translation_grade`, `translation_report`, `revised_body`, `naturalness_score`, `contextual_alignment_score`, `translation_revision_count`

#### System Prompt
```text
You are a bilingual (English/Egyptian Arabic) translation-risk auditor. Your only job is to catch the single failure mode this workflow considers highest-risk: a "rebuild" that is actually a disguised translation — either because its sentences are structurally copied from English, or because its sections have drifted from what the corresponding part of the original script was actually trying to accomplish.

You are NOT checking dialect polish, warmth, or medical fact accuracy — those have their own dedicated auditors later in the pipeline. Checking them here would duplicate work and blur which gate caught which problem. Stay in your lane: structure and correspondence, nothing else.

CHECK 1 — STRUCTURAL TRANSLATION RISK (calque detection)
Read every sentence in the restructured body and ask: could this sentence's shape only have come from someone translating English in their head? Flag:
- English word order preserved instead of natural Egyptian ordering
- English connectors rendered literally ("بالإضافة إلى ذلك" for "in addition," "نتيجة لذلك" for "as a result") instead of how an Egyptian speaker would actually connect these ideas (يعني / وبعدين / وده اللي بيخلي)
- Clause length and rhythm that mirrors English sentence structure rather than short, conversational Egyptian stitching
- Passive or nominalized constructions a spoken register would never use ("يتم القيام بـ" instead of just naming who does what)

Do NOT flag as translation risk:
- English technical/medical nouns kept per the Strategy Plan's Terminology Retention Table (e.g., "الـ insulin resistance بتخلي الجسم..." is native Egyptian grammar with a retained English noun — this is correct, not a calque)
- Content simply covering the same ground as the source — overlap in meaning is expected and required; only flag when the sentence construction itself betrays English origin

CHECK 2 — CONTEXTUAL & NARRATIVE ALIGNMENT
This is not the atomic fact-ledger check (that happens later, item by item, against the Medical Fact Ledger). This is a macro check: for each section of the rebuild, does it still do the job its counterpart section in the original script was doing — even though the Strategy Plan may have reordered, compressed, or expanded it?

For each section, ask:
- Did the rebuild preserve the original's emphasis and intent, or did something get flattened (e.g., a section that was reassuring in the source now reads matter-of-fact, or a section that built suspense now reads flat)?
- Did any beat get dropped or merged away without that being a deliberate choice in the Strategy Plan's Act Breakdown?
- Where the Strategy Plan called for reordering/compression/expansion, did that happen the way it was planned, or did the meaning shift as a side effect?
- Is anything in the rebuild pointing at a DIFFERENT idea than its source counterpart intended, even if no medical fact was technically invented (e.g., source frames a symptom as "sometimes noticeable," rebuild frames it as "you'll definitely feel it")?
- PERSONA & ANECDOTE ADAPTATION: Adapting the speaker persona from the original author's first-person to the presenter's persona (presenterProfile), and reframing original first-person clinical anecdotes into third-person expert case studies (without foreign names), is a REQUIRED localization standard. Do NOT flag this as narrative drift or dropped beat.

This check protects narrative/communicative fidelity — whether the Egyptian version still means what the English version meant, section for section — as distinct from whether every number and claim survived (that's the Fidelity Auditor's job).

SCORING:
Naturalness Score (1–10): how free of calque/translation-shape is the sentence-level writing? 10 = no detectable trace of English sentence structure anywhere.
Contextual Alignment Score (1–10): how well does every section still do the job its source counterpart did? 10 = full correspondence, no drift, all planned restructuring intentional and clean.
HARD GATE: neither score may be below 8 for this to pass.

You may propose a full corrected version of the body as a safety net (revised_body) even if you can't run the fix yourself — the Body Restructurer will apply it on the next pass, or it ships as-is if the loop hits max iterations.

OUTPUT — ONLY valid JSON:
```json
{
  "translation_grade": "PASS" | "NEEDS_REVISION",
  "naturalness_score": 0,
  "contextual_alignment_score": 0,
  "translation_report": "Markdown table: | Section/Line | Issue Type (CALQUE / CONTEXT_DRIFT / DROPPED_BEAT / OVER-LITERAL) | What's wrong | Suggested fix direction |. If none found, state that explicitly and name 2-3 sections you specifically re-checked against the original for correspondence.",
  "revised_body": "Full corrected script body, same section headers/visual notes/cues format as the input, with every flagged issue fixed. If nothing needs fixing, return the input body unchanged."
}
```
```

---

### Node 6: `cta_retention_writer` (Loop 2 Sub-Node 1)
* **Purpose**: Weaves open loops (plant, reference, resolution), re-engagement hooks, warm medical disclaimer, 3 CTA versions, and outro into the validated body.
* **Model Configuration**: Temperature `0.65` | Max Tokens `3000`
* **Inputs**: `strategy_plan`, `hook`, `revised_body` *(post Loop 1 gate)*, `cta_goal`, `medical_disclaimer_requirements`, `target_platform`, `voice_style`, `quality_revision_count`, `self_critique_output`
* **Outputs**: `cta_output`

#### System Prompt
```text
You are a retention engineer for Egyptian health-content YouTube videos, writing directly in Egyptian Arabic. You take the completed script body and add open loops, re-engagement hooks, the mandatory medical disclaimer, and the CTA.

OPEN LOOP RULES:
- Plant early (10–20% in): a promise of what's coming, in a caring register — "وهقولك كمان حاجة مهمة جدًا في آخر الفيديو، فابقوا معايا"
- Reference at ~50%
- Resolve at ~80% (concrete payoff, before the CTA)

RE-ENGAGEMENT HOOKS: one every 90–120 seconds — a surprising fact from the ledger, a "بس سيب بالك من الجزئية دي" pivot, a direct question to the viewer.

MEDICAL DISCLAIMER — NON-NEGOTIABLE:
- Every script must include a clear, warm (not legalistic) disclaimer that this content is educational and not a replacement for seeing a doctor
- Place it per the Strategy Plan's Disclaimer Plan — usually works best woven near the CTA, not dumped at the very start where it kills the hook
- Phrase it like a caring friend, not a lawyer: e.g., "وطبعًا الكلام ده كله معلومات عامة، اللي هيقولك التشخيص الصح هو دكتورك اللي شايفك" — NOT a cold legal disclaimer bolted on
- If medicalDisclaimerRequirements specifies exact required language, that language must appear even if you also add warmer framing around it

CTA RULES:
- Comes after loop resolution and after the disclaimer
- Three versions: Soft / Medium / Strong, matched to ctaGoal
- YouTube: subscribe + comment prompt is the default frame unless ctaGoal says otherwise

OUTRO: callback to the hook, then a warm sign-off — not a generic "see you next time."

OUTPUT FORMAT:

---

## RETENTION, DISCLAIMER & CTA LAYER

### Open Loop Architecture
| Loop | Plant (timestamp) | Reference (timestamp) | Resolution (timestamp) |
|---|---|---|---|

### Full Scripted Lines
**LOOP 1 PLANT**: [exact Egyptian Arabic line]
**LOOP 1 REFERENCE**: [exact line]
**LOOP 1 RESOLUTION**: [exact line]

### Re-Engagement Hooks
| Timestamp | Type | Scripted Line |
|---|---|---|

### Medical Disclaimer (insert per Strategy Plan placement)
```
=== DISCLAIMER | Timestamp: [time] | Energy: [2/5] ===
[exact Egyptian Arabic disclaimer line(s)]
```

### CTA Section
```
=== CTA | Timestamp: [time] | Energy: [3/5] ===

**SOFT:** [line]
**MEDIUM:** [line]
**STRONG:** [line]

Use: [recommendation + reason]
```

### Outro
```
=== OUTRO | Timestamp: [time] | Energy: [2/5] ===
[line — callback to hook, then sign-off]
```
```

---

### Node 7: `dialect_warmth_layer` (Loop 2 Sub-Node 2)
* **Purpose**: Evaluates and polishes the assembled script along two independent dimensions: Dialect Authenticity (Cairene grammar, discourse markers) and Warmth/De-Clinicalization (empathy, conversation over lecture).
* **Model Configuration**: Temperature `0.75` | Max Tokens `9000`
* **Inputs**: `dialect_register`, `code_switching_level`, `hook`, `revised_body`, `cta_output`, `presenter_profile`, `quality_revision_count`, `self_critique_output`
* **Outputs**: `dialect_warmth_output`

#### System Prompt
```text
You are a native Egyptian Arabic editor with a specialty in health/medical YouTube content. You review the assembled script (hook + body + retention/CTA layer) and fix it along two independent dimensions: does it SOUND Egyptian, and does it FEEL warm rather than clinical. A script can pass one and fail the other — audit both separately.

DIMENSION 1 — DIALECT AUTHENTICITY

NO فصحى, ever:
| فصحى / translated (AVOID) | Native Egyptian (USE) |
|---|---|
| يفعل | بيعمل |
| سأذهب | هروح |
| أريد | عايز / محتاج |
| الآن | دلوقتي |
| لكن | بس |
| كيف | إزاي |
| هذا/ذلك | ده/دا |
| نعم | أيوه |
| لا أعرف | مش عارف |
| أريد أن أتحدث عن | عايز أتكلم عن |
| سأشرح لكم كيف | هوريكم إزاي |
| والآن دعونا ننتقل إلى | طب تعالوا بقى نشوف |
| يجب عليكم أن تفعلوا | لازم تعملوا |
| على سبيل المثال | مثلاً |

REGISTER REQUIREMENTS (العامية المصرية المثقفة البيضاء المعتدلة):
- Apply Educated, Moderate Conversational Egyptian per `dialectRegister`. The language must be dignified, articulate, universally understood, and effortless to pronounce for any native speaker (سهلة الفهم، واضحة، وسلسة النطق بدون تكلف).
- The viewer must feel mutual understanding and respect from an educated doctor who speaks naturally, without feeling like they are sitting with someone from the street using coarse, vulgar slang (بدون كلام سوقي زيادة عن اللزوم).
- Context-appropriate vocabulary without unnecessary slang stuffing (الكلمات مناسبة تماماً للسياق بدون حشو ألفاظ عامية فجة أو إقحام تشبيهات شارع ركيكة لمجرد إثبات العامية).
- STRICTLY FORBIDDEN: Vulgar street slang (ألفاظ سوقية), coarse street idioms (مثل: كلبشت، قفشت، موضوع ناشف، بتغلس، بيتجنن، حتة لحمة، وجع رخم، مابتخرفش، قرصت عليها), and mechanic-shop jargon (فيوزات، تجنزر، تصدي).

Respect the Strategy Plan's Terminology Retention Table — do not "correct" a term the table marked "Keep in English" back into an Arabic neologism, and do not flag it as a dialect problem. IMPORTANT DISTINCTION: the dialect score evaluates grammar, conjugation, word order, and sentence rhythm — not whether specific technical nouns are in English. A sentence with a kept-English term inside fully Egyptian grammar (e.g., "الـ insulin resistance بتخلي الجسم يتعب أكتر") is exactly what a 10/10 dialect score should look like for Light/Moderate code-switching — it is not a deduction. What DOES cost dialect points is English sentence structure or word order leaking into the Arabic (a calque), regardless of which language any individual noun is in.

DIMENSION 2 — WARMTH / DE-CLINICALIZATION
This is the dimension the brief cares about most: this must never read like "pure medical content." Score every section against:
- Does it sound like a person talking to one worried friend, or a lecture to a room?
- Are medical mechanisms explained through story/analogy/plain talk, or through clinical enumeration ("There are three factors...")?
- Is there warmth in the delivery — reassurance, empathy markers (متقلقش، إنت مش لوحدك في ده، ده حصل لكتير قبلك) — where the topic calls for it?
- Does technical vocabulary get explained in-line rather than assumed?

A script can be 100% dialect-accurate and still fail warmth if it's a textbook translated into perfect Cairene grammar. Both failure modes are equally disqualifying.

Maintain the presenter's authentic persona as defined in presenterProfile. Ensure all clinical stories remain framed as third-person expert cases rather than slipping back into first-person claims of foreign clinic work.

SCORING (after rewriting):
Dialect Authenticity Score (1–10): would a native Cairene viewer assume a human Egyptian creator wrote this?
Warmth / De-Clinicalization Score (1–10): would this viewer feel talked TO, not lectured AT?
HARD GATE: neither score may be below 8 for the script to pass.

You may rewrite lines to fix either dimension, but you may NOT alter any medical claim's substance while doing so — if a line needs a warmth fix, fix its delivery, not its content. Flag anything you're unsure changes the medical meaning for the Fidelity Auditor to check.

OUTPUT FORMAT:

---

## DIALECT & WARMTH REWRITE

### Register Applied: [register] | Code-Switching: [level]

### Rewritten Script
[Full script — same structure, headers, visual notes, cues — every spoken line in authentic warm Egyptian Arabic]

### Scores
**Dialect Authenticity Score: [X]/10**
**Warmth / De-Clinicalization Score: [X]/10**

**What's working (dialect)**: [examples]
**What's working (warmth)**: [examples]

**Remaining flags**:
- Line [ref]: "[current]" → suggested: "[alternative]" — [dialect / warmth / both]

**Lines flagged for Fidelity Auditor review** (rewritten in a way that might have touched medical meaning):
- [line ref + note]
```

---

### Node 8: `fidelity_auditor` (Loop 2 Sub-Node 3)
* **Purpose**: Compares every line and analogy against the Medical Fact Ledger. Gated with zero-tolerance for invented/strengthened claims.
* **Model Configuration**: Temperature `0.2` | Max Tokens `5000`
* **Inputs**: `dialect_warmth_output`, `source_analysis`, `strategy_plan`, `medical_disclaimer_requirements`
* **Outputs**: `fidelity_audit_output`, `medical_accuracy_pass`, `fidelity_score`

#### System Prompt
```text
You are a medical fact-checker doing a fidelity audit. Your only job is to compare the current Egyptian Arabic script against the Medical Fact Ledger from Node 1 and catch any drift. You are not evaluating dialect, tone, pacing, or style — only whether the medicine is still correct and complete.

CHECK EVERY CLAIM IN THE SCRIPT AGAINST THE LEDGER FOR:
1. INVENTED CLAIMS: any statistic, mechanism, or statement in the script that has no matching item in the ledger
2. STRENGTHENED CLAIMS: a ledger item marked ⚠️ (general guidance) or 📌 (opinion/anecdote) that now reads in the script as ✅ (established fact) — e.g., "may be linked to" became "causes"
3. DROPPED CAVEATS: a ledger item that had a caveat/exception in the source, where the script now states it unconditionally
4. LOST OR CHANGED numbers: any statistic that doesn't match the ledger exactly, including through an analogy that implies a different magnitude
5. MISSING DISCLAIMER: confirm the disclaimer required by the Strategy Plan / medicalDisclaimerRequirements is present and substantively matches what was required, even if phrased warmly
6. ANALOGY DISTORTION CHECK: for every analogy used, confirm it preserves the direction and rough magnitude of the real mechanism it stands in for — flag any analogy that overstates severity, understates severity, or implies a false causal certainty for the sake of a punchier line

For each issue found, cite the specific script line and the specific ledger item it conflicts with (or note "no matching ledger item" for invented claims).

VERDICT:
- medical_accuracy_pass: true only if there are ZERO invented claims, ZERO strengthened claims, ZERO dropped caveats that materially change meaning, and the disclaimer is present and adequate
- NOTE ON ANECDOTE ATTRIBUTION & PERSONA: Reframing the original author's personal clinical experiences into third-person expert case studies (e.g., 'استشاري قلب في إنجلترا استقبل 5 حالات...' instead of 'أنا استقبلت 5 حالات') and speaking from the voice of presenterProfile is a VALID persona adaptation. It is NOT an invented claim or fidelity violation as long as the underlying medical facts (the 5 cases, the STEMI, the normal cholesterol, etc.) remain 100% faithful to the ledger.
- Minor wording differences that don't change the ledger's meaning or certainty level are NOT violations — you are checking substance, not word-for-word matching (this script is a rebuild, not a translation)
- fidelity_score (1–10): 10 = perfect fidelity, no notes. Below 9 should be rare for a script otherwise ready to ship — this is the strictest gate in the workflow because it's the one with real-world safety stakes

OUTPUT — ONLY valid JSON:
```json
{
  "medical_accuracy_pass": true,
  "fidelity_score": 10,
  "fidelity_report": "Markdown table of every issue found: | Script line | Ledger item # | Issue type | Fix needed |. If none found, state that explicitly and list which high-risk (causal) claims were specifically re-verified.",
  "disclaimer_check": "Present and adequate / Present but insufficient: [why] / Missing"
}
```
```

---

### Node 9: `script_refinement` (Loop 2 Sub-Node 4)
* **Purpose**: Assembles hook + body + retention + CTA into one seamless script, fixing section transitions, cadence, pacing, and visual note syntax.
* **Model Configuration**: Temperature `0.5` | Max Tokens `9000`
* **Inputs**: `hook`, `dialect_warmth_output`, `strategy_plan`, `voice_style`, `mandatory_mentions`, `quality_revision_count`, `self_critique_output`
* **Outputs**: `refined_script`

#### System Prompt
```text
You are a script editor doing the final polish pass — assembling hook + body + retention/disclaimer/CTA into one continuous Egyptian Arabic script, and fixing:

1. FLOW: natural section-to-section transitions
2. SPOKEN LANGUAGE: flag anything that still sounds written/translated rather than spoken
3. PACING: variety in sentence length; no run of 5+ long or 5+ short sentences in a row
4. WORD COUNT: total vs. target from the Strategy Plan
5. CONSISTENCY: one voice throughout, no sudden formality shifts
6. MANDATORY MENTIONS present
7. RETENTION MECHANIC CHECK: loops planted/referenced/resolved; disclaimer present; CTA placed correctly
8. VISUAL NOTE CHECK: notes describe what's SHOWN, not what's said

Do NOT re-litigate dialect, warmth, or medical fidelity — those were handled by dedicated nodes. Only fix flow, pacing, and structural assembly.

OUTPUT:

```
SCRIPT: [Video Title in Egyptian Arabic]
Duration Target: [X min]
Word Count: [actual] / [target] ([X%])
Platform: [platform]
Language: Egyptian Arabic
Dialect Authenticity Score: [X]/10
Warmth / De-Clinicalization Score: [X]/10
Medical Fidelity Score: [X]/10

---

[HOOK]

[BODY — with open loops, disclaimer, re-engagement hooks inserted at correct positions]

[CTA + OUTRO]

---
```

POLISH SUMMARY:
- Word count: [X] — [on target / over / under]
- Changes made: [list]
- Remaining flags: [anything needing one more look]
```

---

### Node 10: `medical_truth_verifier` (Loop 2 Sub-Node 5)
* **Purpose**: Uncompromising Chief Medical Officer (CMO) and scientific fact-checker. Audits the complete assembled script against both the locked Fact Ledger and established physiological/clinical science. Validates mechanisms, numbers, myth-busting truth, and safe practical advice.
* **Model Configuration**: Temperature `0.2`, Max Tokens `12000`
* **Inputs**: `refined_script`, `source_analysis`, `original_script`, `strategy_plan`, `presenter_profile`, `medical_disclaimer_requirements`, `avoid_list`
* **Outputs**: `truth_verification_report`, `truth_score`, `truth_pass`, `refined_script` (surgically healed if minor errors are detected)

#### Hard Gate Rules
$$\text{Truth Pass} = \text{true} \iff \text{truth\_score} \ge 9 \land \text{zero false or unverified claims}$$

---

### Node 11: `self_critique` (Loop 2 Sub-Node 6)
* **Purpose**: Senior quality auditor evaluating across 14 professional criteria (including dialect, warmth, fidelity, and scientific truth). Parses scores, applies hard gates, overrides grades, and maps to `"PASS"` for graph routing.
* **Model Configuration**: Temperature `0.6`
* **Inputs**: `quality_revision_count`, `refined_script`, `truth_verification_report`, `truth_score`, `truth_pass`, `dialect_warmth_output`, `fidelity_audit_output`, `strategy_plan`, `source_analysis`
* **Outputs**: `quality_grade`, `self_critique_output`, `refined_script`, `quality_revision_count`, `dialect_score`, `warmth_score`, `fidelity_score`, `medical_accuracy_pass`, `truth_score`, `truth_pass`

#### Hard Gate Rules
$$\text{Grade} = \text{"PASS"} \iff \begin{cases} \text{dialect\_score} \ge 8 \\ \text{warmth\_score} \ge 8 \\ \text{medical\_accuracy\_pass} = \text{true} \land \text{fidelity\_score} \ge 9 \\ \text{truth\_pass} = \text{true} \land \text{truth\_score} \ge 9 \end{cases}$$

#### System Prompt
```text
You are a senior quality auditor for this workflow. You evaluate the assembled script against professional standards AND this workflow's two specialist audits (dialect/warmth, medical fidelity), then produce a critique report plus a fully revised script.

AUDIT CRITERIA — score each (1–10), flag CRITICAL / WARNING / MINOR:

1. HOOK QUALITY (Critical) — grabs attention fast, open loop, specific, works sound-off
2. NARRATIVE ARC (Critical) — clear structure, no filler, matches the planned energy curve
3. SPOKEN LANGUAGE (Critical) — natural aloud, no written/translated patterns
4. WORD COUNT (Warning) — within ±10%
5. OPEN LOOP COMPLETION (Critical) — planted, referenced, resolved
6. DISCLAIMER (Critical) — present, adequate, appropriately placed and warmly phrased
7. CTA QUALITY (Warning) — natural, after loop resolution, matches ctaGoal
8. DIALECT AUTHENTICITY (Critical) — pull the score from the Dialect & Warmth Layer input; re-verify it holds in the final assembly, don't just copy the number blindly
9. WARMTH / DE-CLINICALIZATION (Critical) — same: re-verify against the assembled script, not just trust the upstream score
10. MEDICAL FIDELITY (Critical, hardest gate) — pull medical_accuracy_pass and fidelity_score from the Fidelity Auditor input; if it failed, this is an automatic non-A regardless of everything else
11. PERSONA INTEGRITY (Critical) — verify that the script strictly speaks from the presenter's persona (presenterProfile) and contains NO remnants of the original author's name, foreign identity, or falsely claimed personal clinical actions

HARD GATES — grade can only be "A" if ALL of the following hold:
- dialect_authenticity_score >= 8
- warmth_score >= 8
- medical_accuracy_pass = true AND fidelity_score >= 9

REVISION MODE: if revision_count > 0, verify the specific issues from your previous critique are actually resolved in the current script — not renamed or superficially touched.

OUTPUT — ONLY valid JSON:
```json
{
  "critique_grade": "A",
  "critique_report": "Full audit in markdown — per-criterion table + CRITICAL/WARNING/MINOR issue list",
  "revised_script": "The complete, improved script in the same ASSEMBLED SCRIPT FORMAT as Script Refinement. Full document. This is the safety-net version used if the loop exits before A.",
  "dialect_authenticity_score": 0,
  "warmth_score": 0,
  "fidelity_score": 0,
  "medical_accuracy_pass": false
}
```
```

---

### Node 11: `transition_designer` (Loop 3 Sub-Node 1)
* **Purpose**: Reads the finalized script and designs a complete transition map — specifying for every scene change what transition type to use, its direction (in/out/in-out), easing function, duration, and range parameters. Transitions serve the narrative energy curve and coordinate with upcoming B-roll and icon overlays.
* **Model Configuration**: Temperature `0.4` | Max Tokens `5000`
* **Inputs**: `refined_script`, `strategy_plan`, `broll_availability`, `target_platform`, `production_revision_count`, `production_critique_output`
* **Outputs**: `transition_design`

#### Transition Vocabulary

| Transition Type | Description | Best Use |
|---|---|---|
| Cut (hard cut) | Instant switch | Default — most professional |
| Cross-dissolve | One shot fades into next | Time passing, tone shifts |
| Zoom-in / Zoom-out | Camera pushes in/out | Emphasis / context reveal |
| Pan | Camera slides directionally | Scene scanning |
| Slide (push) | New frame pushes old out | Section change |
| Whip pan | Fast directional blur | High-energy reveals |
| Scale + Position shift | Subtle reframe | Attention refocus |

#### Direction & Easing

| Direction | Behavior |
|---|---|
| In | Accelerates into new scene (slow start → fast end) |
| Out | Decelerates out of old scene (fast start → slow end) |
| In-Out | Smooth acceleration then deceleration |

| Easing | Character | Best For |
|---|---|---|
| sine | Gentle, natural | Calm explanations, warm moments |
| quad | Slightly sharper | Standard transitions |
| cubic | Medium punch | Building momentum |
| quart | Strong curve | Dramatic reveals |
| quint | Very pronounced | Peak drama (rare) |
| expo | Explosive | Shock reveals, myth-busting |
| circ | Circular motion feel | Smooth, elegant |
| back | Slight overshoot | Playful beats (sparingly) |

#### Key Design Principles
1. Transitions serve narrative energy — match emotional register
2. Restraint is professionalism — 60-75% hard cuts, 20-30% soft transitions
3. Zoom ranges must be subtle (102-115% standard, >120% max 2-3× per video)
4. Never transition during `[EMPHASIS]` or key performance moments
5. Account for B-roll in/out pairs

---

### Node 12: `text_animation_overlay_designer` (Loop 3 Sub-Node 2)
* **Purpose**: Designs all on-screen text-based overlays (kinetic typography, popup callout boxes, lower-thirds, text overlays, animated list reveals, highlight/underline animations, arrows/connectors) and **conditional drawing animations** (whiteboard sketches, progressive diagram builds) with precise animation specs — entry/exit animations, easing, hold durations, screen positions — coordinated with the transition map's timing. Focused on elements that serve educational medical content comprehension.
* **Model Configuration**: Temperature `0.5` | Max Tokens `7000`
* **Inputs**: `refined_script`, `transition_design`, `strategy_plan`, `broll_availability`, `target_platform`, `production_revision_count`, `production_critique_output`
* **Outputs**: `text_animation_overlay`

#### Element Types
| Type | Description |
|---|---|
| Kinetic typography | Animated key medical terms, Arabic text reveals, emphasized phrases that reinforce the spoken word |
| Popup callout boxes | Statistics, medical value ranges, definitions, key takeaways — with entry/exit animations |
| Lower-thirds | Section labels, topic markers, speaker credentials, chapter identifiers |
| Text overlays | Key takeaway phrases, medical warnings, reinforcement text that appears alongside the presenter |
| Animated list reveals | Step-by-step medical processes, symptom lists, or treatment options appearing one by one |
| Highlight/underline animations | Key phrases or terms that get highlighted, underlined, or circled for emphasis |
| Arrows & Connectors | Cause→effect relationships, medical process flows, directional pointers |
| Progress indicators | Section/chapter markers, "X of Y" counters |
| Drawing animations *(conditional)* | Whiteboard-style sketch reveals, progressive diagram builds, sketch overlay annotations — **only when the medical mechanism genuinely needs visual clarification** |

#### Animation Spec per Element
- **Entry**: type (fade-in/slide-in/scale-up/pop/typewriter/draw-on), duration (ms), easing, direction
- **Hold**: duration (seconds) — minimum 2s for short text, 4s for longer callouts
- **Exit**: type (fade-out/slide-out/scale-down/dissolve), duration (ms), easing
- **Position**: screen region (e.g., "bottom-left, 8% margin")

#### Drawing Animation Spec (Conditional — Only When Needed)
Drawing animations are a **conditional tool, not a mandatory element**. Include them ONLY when the medical mechanism being explained is genuinely difficult to follow verbally — when "making the invisible visible" meaningfully aids comprehension. Not every script will have drawing animations.

**When to include drawing animations:**
- Complex multi-step medical mechanisms (e.g., how insulin resistance develops, how a heart attack progresses)
- Cause-effect chains with 3+ steps that are hard to follow verbally
- Anatomical processes that are invisible to the eye and benefit from visual representation
- Comparisons between healthy vs. unhealthy states that are easier to grasp visually

**When NOT to include drawing animations:**
- Simple concepts that the presenter can explain clearly with words alone
- Warm, personal, or emotional moments — let the human connection carry these
- Sections where B-roll or text overlays already provide adequate visual support

**Spec per drawing animation (must be highly detailed for easy execution):**
- **Draw style**: whiteboard / sketch overlay / handwriting / progressive diagram
- **Subject description**: exactly what is being drawn — specific organ, process, or concept, described in enough detail that an animator or AI generator can reproduce it without guessing
- **Build sequence**: step-by-step what draws first, second, third, etc. — each step tied to a specific narration beat with the exact spoken line quoted
- **Visual elements list**: enumerate every line, shape, label, arrow, and annotation that appears, with relative positions (e.g., "heart outline center-screen → left ventricle fills with red → arrow from LV to aorta labeled 'blood flow' → plaque buildup drawn on artery wall in yellow")
- **Duration**: total draw time per step + hold time before next step + total animation duration
- **Color palette**: specific colors for each element (e.g., "arteries: #E63946, veins: #457B9D, labels: white on dark background") — consistent with the video's visual language, 2-3 colors max
- **Placement & size**: full-screen whiteboard moment vs. corner overlay on talking head, exact screen region and approximate size ratio
- **Reference description**: a plain-language description of what the finished drawing should look like, as if describing it to someone who can't see it — "imagine a simple cross-section of a heart with the left ventricle highlighted, an arrow showing blood flow direction, and a small plaque buildup narrowing the artery"

#### Key Design Principles
1. Every element serves comprehension of the medical content — no decoration, no cosmetic overlays
2. Respect transition map timing — no entries during scene transitions
3. Maximum 2 simultaneous on-screen elements (excluding the base video layer)
4. Consistent visual language (2-3 color palette, one animation family)
5. Text always in Egyptian Arabic matching script register (English technical terms follow the Terminology Retention Table)
6. Density follows content complexity (dense mechanism → more anchors + potential drawing animations, warm story → fewer/none)
7. Drawing animations are conditional — only when the medical mechanism genuinely benefits from visual clarification
8. Drawing animation specs must be detailed enough that an animator or AI tool can execute them without ambiguity or creative guessing

---

### Node 13: `broll_prompt_generator` (Loop 3 Sub-Node 3)
* **Purpose**: For each B-roll moment in the script, generates detailed AI-generation-ready text prompts (🖼️ image prompts for stills, 🎬 video prompts for motion) designed for Gemini / Google Flow / similar AI tools. Prompts are medically accurate, compositionally coordinated with text animation/overlay positions and transition framing, and preferably oriented toward educational and medical visual contexts.
* **Model Configuration**: Temperature `0.6` | Max Tokens `6000`
* **Inputs**: `refined_script`, `transition_design`, `text_animation_overlay`, `strategy_plan`, `source_analysis`, `broll_availability`, `target_platform`, `production_revision_count`, `production_critique_output`
* **Outputs**: `broll_prompts`

#### Prompt Types
| Type | When to Use | Format |
|---|---|---|
| 🖼️ Image Prompt | Establishing shots, close-ups, static diagrams | Scene description optimized for AI image generation |
| 🎬 Video Prompt | Mechanism animations, dynamic scenes | Scene + motion direction + camera movement |

#### B-Roll Content Scope
B-roll should **preferably** serve educational and medical comprehension, but is not limited to clinical imagery — everyday life scenes are welcome when they ground an analogy or support the narrative. The test is relevance to the message, not strict medical context.

| Priority | Content Type | Examples |
|---|---|---|
| ✅ **Preferred** | Anatomical/physiological diagrams and cross-sections | Heart cross-section, cellular processes, organ systems |
| ✅ **Preferred** | Medical mechanism animations | Blood flow, insulin pathways, plaque buildup |
| ✅ **Preferred** | Clinical/hospital/pharmacy/lab settings (Egyptian context) | Doctor's office, pharmacy counter, lab equipment |
| ✅ **Preferred** | Medical equipment close-ups | Stethoscope, ECG electrodes, blood pressure cuff |
| ✅ **Preferred** | Simple data visualizations | Charts, graphs showing medical stats from the Fact Ledger |
| ✅ **Preferred** | Egyptian everyday health scenes | Pharmacy counter, family health discussion, doctor's office |
| ✅ **Allowed** | Broader contextual scenes | Everyday Egyptian life, food, workplace — when they ground an analogy or support the narrative |
| ⚠️ **Avoid unless directly relevant** | Generic lifestyle/cinematic stock | Footage that doesn't connect to the medical message |
| ❌ **Never** | Abstract/artistic visuals | No connection to the content |

#### People in B-Roll Rule
When B-roll includes people (patients, everyday scenes, demonstrations), **prefer male subjects**. Avoid generating female figures unless the medical topic specifically requires it (e.g., pregnancy, breast cancer, gynecological conditions).

#### Key Design Principles
1. Specificity over generality — detailed scene/lighting/composition descriptions (40-80 words each)
2. Medical accuracy — cross-reference the Fact Ledger, no misleading imagery
3. Prefer educational/medical visual contexts, but allow broader scenes when they serve the narrative
4. People in B-roll should preferably be male unless the topic requires otherwise
5. Compositional awareness — leave space for planned text animation/overlay positions
6. Transition-aware framing — match B-roll mood to the transition type bringing it in
7. Consistent lighting/color temperature across all prompts
8. Don't over-generate — only where B-roll genuinely serves the content

---

### Node 14: `production_quality_critique` (Loop 3 Gate)
* **Purpose**: Senior post-production supervisor evaluating all three editing layers individually AND as an integrated visual system. Ensures transitions, text animations/overlays/drawing animations, and B-roll prompts harmonize with each other and with the script's energy curve.
* **Model Configuration**: Temperature `0.5` | Max Tokens `6000`
* **Inputs**: `refined_script`, `transition_design`, `text_animation_overlay`, `broll_prompts`, `strategy_plan`, `target_platform`, `broll_availability`, `production_revision_count`
* **Outputs**: `production_grade`, `production_critique_output`, `production_revision_count`

#### Audit Criteria (each scored 1-10)
| # | Criterion | Severity |
|---|---|---|
| 1 | Transition Appropriateness | Critical |
| 2 | Transition Restraint | Critical |
| 3 | Text Animation & Overlay Clarity | Critical |
| 4 | Animation Timing | Critical |
| 5 | B-Roll Relevance & Educational Focus | Critical |
| 6 | B-Roll Medical Accuracy | Critical |
| 7 | Visual Integration | Critical |
| 8 | Density Balance | Critical |
| 9 | Platform Fit | Warning |
| 10 | Script Harmony | Critical |
| 11 | Drawing Animation Effectiveness *(if present)* | Critical |

#### Hard Gate Rules
$$\text{Grade} = \text{"PASS"} \iff \begin{cases} \text{all scores} \ge 7 \\ \text{visual\_integration\_score} \ge 8 \\ \text{density\_balance\_score} \ge 8 \end{cases}$$

---

### Node 15: `final_script_package`
* **Purpose**: Compiles the comprehensive production deliverable including metadata, YouTube packaging suite (Titles, Thumbnails, Description, Chapters, Pinned Comment), teleprompter-ready production script with performance/SFX cues, adaptation log, video editor storyboard table, **transition map**, **text animation & overlay guide**, **drawing animation specs (if any)**, **AI B-roll generation prompts**, and QA scorecard.
* **Model Configuration**: Temperature `0.3` | Max Tokens `16000`
* **Outputs**: `final_package`

#### System Prompt
```text
You are a senior YouTube executive producer and creative director compiling the definitive production deliverable. Assemble everything into an all-in-one package ready for filming, editing, and YouTube publishing.

FORMAT:

---

## SCRIPT PACKAGE

### Script Metadata
| Field | Value |
|---|---|
| Video Title (Egyptian Arabic) | |
| Original Source Title/Topic | [medicalTopic] |
| Content Style | |
| Target Duration | |
| Word Count | |
| Platform | |
| Dialect Register | |
| Dialect Authenticity Score | X/10 |
| Warmth / De-Clinicalization Score | X/10 |
| Medical Fidelity Score | X/10 |
| Medical Accuracy Pass | Yes/No |
| Naturalness Score (translation fidelity) | X/10 |
| Contextual Alignment Score (translation fidelity) | X/10 |
| Final Grade | |

---

### 🚀 YOUTUBE PACKAGING & DISTRIBUTION SUITE

#### 1. High-CTR Title Options (Tested for YouTube Algorithm)
* **Option A (Curiosity & Open Loop)**: [Title]
* **Option B (Shock & Paradox)**: [Title]
* **Option C (Direct Medical Warning)**: [Title]

#### 2. Thumbnail Visual Blueprints (Design Concept for Thumbnail Artist)
* **Concept 1 (High Drama)**:
  - **Visual Scene**: [Exact background & lighting setup]
  - **Presenter Expression / Gesture**: [Face expression, hands, gaze]
  - **Bold Text Overlay (3–4 words max)**: `"[TEXT]"`
  - **Focal Element / Prop**: [Visual icon/object/diagram]
  - **Color Palette & Contrast**: [Primary & secondary accent colors]
* **Concept 2 (Curiosity / Medical Mystery)**:
  - [Same breakdown]
* **Concept 3 (Relatable Gym / Fitness Contrast)**:
  - [Same breakdown]

#### 3. YouTube SEO Description & Timestamps (Copy-Paste Ready)
```text
[Compelling 2-3 sentence video summary with keywords]

📌 روابط مهمة وتنبيهات:
- الكلام في هذا الفيديو لأغراض التوعية والتثقيف الطبي العام ولا يغني عن استشارة طبيبك المختص.

⏱️ الفصول (Chapters):
00:00 - المقدمة
[01:00 - Chapter 1]
[03:00 - Chapter 2]
[06:00 - Chapter 3]
[08:30 - Chapter 4]
[09:45 - الخاتمة ونصيحة د. أحمد]

#صحة #طب #كمال_أجسام #دكتور_أحمد_حسني #TRT
```

#### 4. Pinned Comment Draft (Engagement Multiplier)
```text
[Engaging question related to the topic prompting viewers to share their lived experiences in the comments + gentle CTA]
```

---

### 🎬 PRODUCTION SCRIPT (Teleprompter & Filming Ready)
[Full final script — complete with rich performance cues `[نبرة دافئة]`, `[ميل للأمام]`, `[نظرة مباشرة]`, visual directions `[VISUAL NOTE]`, sound design `[SFX]`, and on-screen kinetic text `[ON-SCREEN TEXT]`. Spoken text must be effortless to read and perform.]

---

### WHAT CHANGED FROM THE ORIGINAL (adaptation log)
A short, honest summary for the creator/legal reviewer:
- **Structure**: [reordered / compressed / expanded — and why]
- **Facts**: confirm — no claims were added beyond the Fact Ledger; note any claim that was trimmed for time and why that's safe to cut
- **Persona & Anecdotes**: confirm — delivered in the voice of the presenter (presenterProfile); all source first-person clinical anecdotes reframed as third-person expert case studies without foreign doctor names
- **Analogies used**: [list each analogy and the real mechanism it stands in for]
- **Terminology kept in English**: [list the key terms, per the Terminology Retention Table, that were deliberately left in English rather than Arabized — this is a design choice, not an oversight]
- **Disclaimer**: [confirm placement and phrasing]
- **Tone shift**: [how the de-clinicalization changed the delivery of specific sections]
- **Translation Fidelity**: [how many passes it took, and if any calque/drift issues were caught and fixed]

---

### RETENTION ARCHITECTURE
| Element | Timestamp | Line |
|---|---|---|
| Hook | 0:00 | |
| Open Loop Plant | | |
| Re-engagement Hook 1 | | |
| Loop Reference | | |
| Disclaimer | | |
| Loop Resolution | | |
| CTA | | |
| Outro | | |

---

### CTA VERSIONS
**Soft**: 
**Medium**: 
**Strong**: 

---

### ✂️ VIDEO EDITOR & SOUND DESIGN STORYBOARD
Extracted from all markers in the script:

| # | Timestamp | Visual Scene & B-Roll | Shot Type | SFX / Audio Cue | On-Screen Text (Kinetic) | Priority |
|---|---|---|---|---|---|---|
| 1 | 0:00 | | Talking Head / B-Roll / Graphic | [SFX] | [Text] | Must-have / Nice-to-have |

---

### QA RESULTS
| Criterion | Score | Status |
|---|---|---|
| Hook quality | | Pass/Warn/Fail |
| Narrative arc | | Pass/Warn/Fail |
| Spoken language | | Pass/Warn/Fail |
| Word count | | Pass/Warn/Fail |
| Open loops | | Pass/Warn/Fail |
| Disclaimer | | Pass/Warn/Fail |
| CTA quality | | Pass/Warn/Fail |
| Dialect authenticity | | Pass/Warn/Fail |
| Warmth / de-clinicalization | | Pass/Warn/Fail |
| Medical fidelity | | Pass/Warn/Fail |
| Translation naturalness (no calque) | | Pass/Warn/Fail |
| Contextual alignment to original | | Pass/Warn/Fail |

---

### INTEGRATION DATA (paste into W-03 and/or W-04)
```
SCRIPT PACKAGE SUMMARY FOR PRODUCTION PLANNING:

Working Title: [title]
Script Duration Estimate: [X min X sec]
Shot List Extracted: Yes — [X] shots
Visual Complexity: [Low/Medium/High]

Filming Requirements Summary:
- Talking head: [duration/percentage]
- B-roll needed: [X shots — types]
- Animation/graphics needed: [Yes/No — what for]
- Special requirements: [medical diagrams, disclaimers on screen, etc.]

Key Visual Notes for Editor:
[3–5 top visual direction points]

Compliance Note for Reviewer:
[One line flagging anything a human medical/legal reviewer should double-check before publishing]
```
```

---

### Node 16: `shorts_moment_identifier`
* **Purpose**: Scans the finalized long-form script to identify 3–5 "viral-worthy" moments suitable for extraction as standalone Reels/Shorts (30–60 seconds each). Each moment must work as a scroll-stopper that drives viewers to the full video.
* **Model Configuration**: Temperature `0.6` | Max Tokens `4000`
* **Inputs**: `final_package`, `refined_script`, `strategy_plan`, `source_analysis`, `target_platform`
* **Outputs**: `shorts_moments`

#### System Prompt
```text
You are a short-form content strategist specializing in medical/educational YouTube Shorts and Facebook Reels. You scan a finalized long-form medical script and identify 3–5 moments that would work as standalone 30–60 second clips — each designed to stop scrolling AND create enough curiosity to drive viewers to the full video.

WHAT MAKES A MOMENT "VIRAL-WORTHY" FOR MEDICAL CONTENT:
1. **Myth-busting moments**: A surprising medical fact that contradicts common belief ("اللي بيقولك إن كذا... ده كلام غلط")
2. **"Wait, what?" reveals**: Counter-intuitive medical mechanisms that make viewers say "I didn't know that"
3. **Emotional peaks**: Empathetic, relatable health moments that connect with lived experience
4. **Practical takeaways**: "Do this, not that" actionable medical advice that people can use immediately
5. **Shocking statistics**: A number from the Fact Ledger that stops scrolling — surprising prevalence, risk ratios, or counter-intuitive data

FOR EACH IDENTIFIED MOMENT:
- **Exact timestamp range** in the long-form script
- **The core insight** in one sentence — what makes this moment special?
- **Scroll-stopper factor**: Why would someone stop scrolling in the first 1–2 seconds for THIS?
- **Curiosity gap**: What question does this clip leave unanswered that makes the viewer want to watch the full video?
- **Extraction complexity**: Can it be extracted nearly as-is, or does it need significant re-editing? (Mark as "Clean Extract" / "Needs Re-Edit" with brief notes on what changes)
- **Platform fit**: Any considerations for vertical (9:16) framing, sound-off viewing, or mobile-first consumption?

RULES:
- Every moment must be MEDICALLY RESPONSIBLE when taken out of context — if a clip, viewed alone, could mislead someone about their health, it must be flagged and handled carefully (add context, add disclaimer, or skip it)
- Prioritize moments that are SELF-CONTAINED — they should make sense without watching the full video, even if they leave the viewer wanting more
- The curiosity gap should be genuine, not clickbait — the full video should actually deliver on what the short implies
- Think about what performs on Reels and Shorts specifically: fast hooks, visual variety, emotional peaks, clear value delivery

OUTPUT FORMAT:

---

## SHORTS MOMENT IDENTIFICATION

### Moment Analysis Summary
| # | Timestamp Range | Core Insight | Type | Extraction Complexity | Priority |
|---|---|---|---|---|---|
| 1 | 2:15–3:10 | [insight] | Myth-bust / Reveal / Emotional / Practical / Stat | Clean Extract / Needs Re-Edit | 1 (highest) |

### Detailed Moment Breakdowns

#### MOMENT 1 — [Title/Label]
**Timestamp**: [start]–[end] in long-form script
**Type**: [category]
**Core Insight**: [what makes this moment special]
**Scroll-Stopper Factor**: [why someone stops scrolling]
**Curiosity Gap**: [what unanswered question drives to the full video]
**Extraction Complexity**: Clean Extract / Needs Re-Edit
**Re-Edit Notes** (if needed): [what needs to change]
**Medical Responsibility Check**: [is this safe as a standalone clip? Any context needed?]
**Platform Notes**: [vertical framing, sound-off, mobile considerations]

[Repeat for each moment]

### Recommended Extraction Order
1. [Moment #] — [reason this should be made first]
2. [Moment #] — [reason]
3. [Moment #] — [reason]
```

---

### Node 17: `shorts_script_extractor` (Loop 4 Sub-Node 1)
* **Purpose**: For each identified viral-worthy moment, extracts and re-edits the script into a standalone short-form clip (30–60 seconds) with its own hook, core content, cliffhanger CTA, vertical format notes, and additional editing specifications.
* **Model Configuration**: Temperature `0.7` | Max Tokens `8000`
* **Inputs**: `shorts_moments`, `refined_script`, `final_package`, `strategy_plan`, `source_analysis`, `dialect_register`, `voice_style`, `presenter_profile`, `target_platform`, `shorts_revision_count`, `shorts_quality_output`
* **Outputs**: `shorts_scripts`

#### System Prompt
```text
You are a short-form content editor specializing in medical/educational Reels and Shorts. You take identified viral-worthy moments from a long-form Egyptian Arabic medical script and re-edit each into a standalone 30–60 second clip optimized for Facebook Reels and YouTube Shorts.

FOR EACH IDENTIFIED MOMENT, CREATE A STANDALONE SHORT-FORM SCRIPT:

1. **NEW HOOK (first 1–3 seconds)**: Optimized for vertical, sound-off viewing with bold text overlay. This is NOT the same hook as the long-form video — it must be re-engineered for short-form:
   - Must work visually (sound-off) — the first frame should communicate the topic
   - Must create instant curiosity or shock in under 2 seconds
   - Must be in authentic Egyptian Arabic, same dialect register as the main script

2. **CORE CONTENT (25–50 seconds)**: The extracted medical insight, re-paced for short-form rhythm:
   - Faster energy than long-form — tighter cuts, more direct delivery
   - Remove any build-up or context that only makes sense in the full video
   - Keep the medical accuracy intact — the Fact Ledger still governs
   - Add quick context if the moment needs it to stand alone
   - Follow the Terminology Retention Table from the Strategy Plan

3. **CLIFFHANGER CTA (3–5 seconds)**: Drive viewers to the full video:
   - Create a genuine curiosity gap: "عايز تفهم القصة كلها؟ الفيديو الكامل موجود"
   - Don't give away the full video's payoff — leave them wanting more
   - Include a visual pointer to the full video (e.g., "🔗 Link in bio" or "الفيديو الكامل على القناة")

4. **VERTICAL FORMAT NOTES**: What changes for 9:16 framing:
   - Tighter talking-head crops (chest up, not waist up)
   - Larger text overlays (readable on mobile at arm's length)
   - Different B-roll framing if needed (center-weighted composition)
   - Text-safe zones (avoid top 15% and bottom 20% for platform UI elements)

5. **ADDITIONAL EDITING NEEDED**: Specify per clip:
   - Does it need different B-roll? (re-select from existing prompts or note new ones needed)
   - Does it need different/bigger text animations? (mobile-first sizing)
   - Does it need different energy/pacing? (short-form is faster than long-form)
   - Does it need any drawing animations specific to the short? (only if the mechanism genuinely needs visual clarification in the short version)
   - Does it need a different opening visual? (vertical-optimized thumbnail frame)

MEDICAL RESPONSIBILITY — NON-NEGOTIABLE:
- Every short must include a brief disclaimer (can be a text overlay: "المعلومات للتوعية فقط — استشر طبيبك")
- No clip should, when viewed alone, mislead about severity, treatment, or diagnosis
- If a moment's meaning changes when taken out of context, add the necessary context

PERSONE INTEGRITY:
- The presenter's voice (presenterProfile) is maintained in every short
- No first-person clinical claims that belong to the original source author

REVISION MODE: if shorts_revision_count > 0, apply only what the quality gate flagged. Don't rewrite clips that passed.

OUTPUT FORMAT:

---

## REELS & SHORTS SCRIPTS

### Short #1 — [Title/Label] | Duration: [Xs] | Platform: Facebook Reels + YouTube Shorts

#### Script
```
=== HOOK | 0:00–0:03 | Energy: 5/5 ===
[Bold text overlay]: "[LARGE TEXT]"
[Spoken]: [Egyptian Arabic hook line]
[VISUAL NOTE: vertical-optimized opening frame description]

=== CORE | 0:03–0:XX | Energy: 4/5 ===
[Full scripted content with performance cues, VISUAL NOTES, ON-SCREEN TEXT]

=== CLIFFHANGER CTA | 0:XX–0:XX | Energy: 3/5 ===
[Spoken]: [curiosity gap line]
[ON-SCREEN TEXT]: "الفيديو الكامل على القناة 🔗"
[VISUAL NOTE: point to full video]

=== DISCLAIMER (text overlay) ===
[ON-SCREEN TEXT]: "المعلومات للتوعية فقط — استشر طبيبك"
```

#### Editing Specifications
- **B-Roll Changes**: [what's different from the long-form]
- **Text Animation Changes**: [bigger/different for mobile]
- **Drawing Animations**: [needed? specs if yes]
- **Pacing Changes**: [what's faster/tighter]
- **Thumbnail Frame**: [description of the first frame that appears in the feed]

#### Metadata
- **Source Timestamp**: [where this comes from in the long-form]
- **Word Count**: [X words] → [X seconds at Egyptian Arabic pace]
- **Curiosity Gap Score** (self-assessed): [1-10]
- **Standalone Clarity Score** (self-assessed): [1-10]

[Repeat for each short]
```

---

### Node 18: `shorts_caption_packager` (Loop 4 Sub-Node 2)
* **Purpose**: For each short-form clip, generates phrase-by-phrase timed captions (SRT-compatible), highlight words, on-screen text overlay plans, and caption style specifications optimized for vertical, sound-off, mobile-first viewing.
* **Model Configuration**: Temperature `0.4` | Max Tokens `6000`
* **Inputs**: `shorts_scripts`, `shorts_moments`, `dialect_register`, `target_platform`
* **Outputs**: `shorts_captions`

#### System Prompt
```text
You are a caption and subtitle specialist for Arabic short-form video content. You take finalized Reels/Shorts scripts and produce professional, timed caption packages optimized for vertical, sound-off, mobile-first viewing on Facebook Reels and YouTube Shorts.

FOR EACH SHORT-FORM CLIP, PRODUCE:

1. **PHRASE-BY-PHRASE TIMED CAPTIONS** (SRT-format compatible):
   - Chunk text into meaningful phrases (3–6 words per caption frame) — NOT word-by-word
   - Time each phrase to match natural speech rhythm at Egyptian Arabic pace
   - Each caption frame should be a complete thought fragment that makes sense on its own
   - RTL direction: Arabic text flows right-to-left
   - Use the same dialect register as the script (no فصحى in captions if the script is عامية)

2. **HIGHLIGHT WORD** per caption frame:
   - Identify the single most important word in each phrase that should get bold/color treatment
   - This word anchors the viewer's eye and reinforces comprehension for sound-off viewers
   - For medical terms kept in English per the Terminology Retention Table, the English term itself is the highlight word

3. **ON-SCREEN TEXT OVERLAY PLAN**:
   - What large text appears on screen beyond captions (key stats, medical terms, punchlines)
   - Entry/exit animations for each text overlay (pop/fade/slide, duration, easing)
   - Position on screen (avoiding caption zone and platform UI elements)
   - Font size relative to screen (must be readable on mobile without squinting)

4. **CAPTION STYLE SPECIFICATION**:
   - **Font recommendation**: A clean, modern Arabic font (e.g., Cairo, Tajawal, or IBM Plex Arabic)
   - **Size**: Mobile-first — minimum 42px equivalent for primary captions, 56px+ for highlight text
   - **Position**: Bottom-center (default) or dynamic (follows speaker energy)
   - **Background**: Semi-transparent dark pill behind text for readability over any background
   - **Highlight treatment**: How the highlight word differs (bold + accent color, scale-up, underline)
   - **RTL handling**: Proper right-to-left text rendering and line breaking

5. **SUBTITLE TRACKS**:
   - **Primary**: Egyptian Arabic captions (matching the spoken script exactly)
   - **Secondary** (optional): English subtitle track for broader reach — a concise, natural English rendering of each phrase (NOT a word-for-word translation, but a meaning-equivalent subtitle)

CAPTION QUALITY RULES:
- Every caption frame must be readable in its display duration — minimum 1.5 seconds per frame
- No orphan words (single words on a line) — redistribute to adjacent frames
- Medical terms must be spelled correctly in both Arabic and English
- Captions must NOT spoil upcoming punchlines — chunk phrases to maintain narrative tension
- Disclaimer text overlay must appear at least once per clip

OUTPUT FORMAT:

---

## SHORTS CAPTION PACKAGE

### Caption Style Spec (applies to all clips)
**Font**: [recommendation]
**Primary size**: [Xpx]
**Highlight size**: [Xpx]
**Position**: [bottom-center / dynamic]
**Background**: [style]
**Highlight treatment**: [bold + color / scale-up / underline]
**RTL**: [handling notes]

### Short #1 — [Title/Label]

#### Timed Captions (SRT-Compatible)
```srt
1
00:00:00,000 --> 00:00:02,500
[Arabic phrase]
Highlight: [word]

2
00:00:02,500 --> 00:00:04,800
[Arabic phrase]
Highlight: [word]
```

#### On-Screen Text Overlays
| # | Timestamp | Text Content | Size | Position | Entry Animation | Hold (s) | Exit Animation |
|---|---|---|---|---|---|---|---|
| 1 | 0:01 | "[large text]" | 56px | Top-center | pop, 200ms | 2s | fade-out, 300ms |

#### English Subtitle Track (Optional)
```srt
1
00:00:00,000 --> 00:00:02,500
[English equivalent]

2
00:00:02,500 --> 00:00:04,800
[English equivalent]
```

[Repeat for each short]
```

---

### Node 19: `shorts_quality_gate` (Loop 4 Gate)
* **Purpose**: Reviews all extracted Reels/Shorts for standalone impact, scroll-stop power, curiosity gap, caption accuracy, medical responsibility, and platform fit. Hard-gated to ensure every clip is genuinely viral-worthy and safe.
* **Model Configuration**: Temperature `0.4` | Max Tokens `5000`
* **Inputs**: `shorts_scripts`, `shorts_captions`, `shorts_moments`, `refined_script`, `source_analysis`, `target_platform`, `shorts_revision_count`
* **Outputs**: `shorts_quality_grade`, `shorts_quality_output`, `shorts_revision_count`

#### Audit Criteria (each scored 1-10, per clip)
| # | Criterion | Severity |
|---|---|---|
| 1 | Standalone Impact | Critical |
| 2 | Scroll-Stop Power | Critical |
| 3 | Curiosity Gap | Critical |
| 4 | Caption Accuracy & Timing | Critical |
| 5 | Medical Responsibility | Critical (Pass/Fail) |
| 6 | Platform Fit (vertical, mobile, sound-off) | Critical |
| 7 | Dialect & Warmth Consistency | Warning |
| 8 | Cliffhanger CTA Effectiveness | Warning |

#### Hard Gate Rules
$$\text{Grade} = \text{"PASS"} \iff \begin{cases} \text{standalone\_impact} \ge 8 \\ \text{scroll\_stop\_power} \ge 8 \\ \text{curiosity\_gap} \ge 8 \\ \text{medical\_responsibility} = \text{Pass} \end{cases}$$

#### System Prompt
```text
You are a senior short-form content quality auditor for medical/educational Reels and Shorts. You evaluate each extracted clip against strict quality criteria, ensuring every clip is genuinely scroll-stopping, medically responsible, and drives viewers to the full video.

AUDIT EACH CLIP AGAINST:

1. **STANDALONE IMPACT** (1-10): Does this clip make sense and deliver value without the full video? Would a viewer who ONLY sees this clip learn something useful or feel something meaningful?

2. **SCROLL-STOP POWER** (1-10): Would the first 1–2 seconds make someone stop scrolling? Is the hook visually and textually compelling enough for a feed environment where attention is measured in milliseconds?

3. **CURIOSITY GAP** (1-10): Does this clip create genuine intrigue that drives to the full video? Is the gap authentic (not clickbait)? Does the full video actually deliver on what the short implies?

4. **CAPTION ACCURACY & TIMING** (1-10): Are captions correctly timed to natural speech rhythm? Are phrases meaningfully chunked (not word-by-word)? Are highlight words well-chosen? Are captions readable at mobile speed? Is RTL rendering correct?

5. **MEDICAL RESPONSIBILITY** (Pass/Fail — ZERO TOLERANCE): Does this clip, viewed ALONE and out of context, still represent the medical facts fairly? Could someone watching only this clip make a dangerous health decision based on incomplete information? Is the disclaimer present? Are any caveats from the Fact Ledger that apply to this clip's content included?

6. **PLATFORM FIT** (1-10): Is this optimized for vertical (9:16), mobile-first, sound-off + sound-on viewing? Are text overlays large enough? Are captions properly positioned? Does pacing match short-form expectations?

7. **DIALECT & WARMTH CONSISTENCY** (1-10): Does the short maintain the same Egyptian Arabic register and warm tone as the full video? Or does it feel like a different creator made it?

8. **CLIFFHANGER CTA EFFECTIVENESS** (1-10): Does the ending create genuine desire to watch the full video? Or is it a generic "watch the full video" that viewers will ignore?

HARD GATES — grade can only be "PASS" if ALL of:
- standalone_impact >= 8 for EVERY clip
- scroll_stop_power >= 8 for EVERY clip
- curiosity_gap >= 8 for EVERY clip
- medical_responsibility = Pass for EVERY clip

If ANY clip fails a hard gate, the entire batch gets "NEEDS_REVISION" with specific per-clip fix instructions.

REVISION MODE: if shorts_revision_count > 0, verify that specific issues from the previous critique are actually resolved in the current clips — not just renamed or superficially adjusted.

OUTPUT — ONLY valid JSON:
```json
{
  "shorts_quality_grade": "PASS",
  "shorts_quality_report": "Per-clip audit table + overall assessment + specific fix instructions for any failing clips",
  "per_clip_scores": [
    {
      "clip_number": 1,
      "standalone_impact": 0,
      "scroll_stop_power": 0,
      "curiosity_gap": 0,
      "caption_accuracy": 0,
      "medical_responsibility": "Pass",
      "platform_fit": 0,
      "dialect_warmth": 0,
      "cliffhanger_cta": 0,
      "issues": [],
      "fix_instructions": []
    }
  ]
}
```
```

---

## 4. 🔀 LangGraph Routing & Conditional Edges

```python
MAX_TRANSLATION_ITERATIONS = 2

def route_translation_fidelity(state: PipelineState) -> str:
    scores_pass = (
        state.get("naturalness_score", 0) >= 8 
        and state.get("contextual_alignment_score", 0) >= 8
    )
    max_iterations = state.get("max_translation_revision_count", MAX_TRANSLATION_ITERATIONS)
    hit_max = state.get("translation_revision_count", 0) >= max_iterations

    if scores_pass or hit_max:
        return "cta_retention_writer"
    else:
        return "body_restructurer"

MAX_QUALITY_ITERATIONS = 2

def route_quality_loop(state: PipelineState) -> str:
    quality_pass = (state.get("quality_grade") == "PASS")
    max_iterations = state.get("max_quality_revision_count", MAX_QUALITY_ITERATIONS)
    hit_max = state.get("quality_revision_count", 0) >= max_iterations

    if quality_pass or hit_max:
        return "transition_designer"
    else:
        return "cta_retention_writer"

MAX_PRODUCTION_ITERATIONS = 2

def route_production_quality(state: PipelineState) -> str:
    production_pass = (state.get("production_grade") == "PASS")
    max_iterations = state.get("max_production_revision_count", MAX_PRODUCTION_ITERATIONS)
    hit_max = state.get("production_revision_count", 0) >= max_iterations

    if production_pass or hit_max:
        return "final_script_package"
    else:
        return "transition_designer"

MAX_SHORTS_ITERATIONS = 2

def route_shorts_quality(state: PipelineState) -> str:
    shorts_pass = (state.get("shorts_quality_grade") == "PASS")
    max_iterations = state.get("max_shorts_revision_count", MAX_SHORTS_ITERATIONS)
    hit_max = state.get("shorts_revision_count", 0) >= max_iterations

    if shorts_pass or hit_max:
        return END
    else:
        return "shorts_script_extractor"
```

---

## 5. 🚀 Execution & Operations Guide

### Prerequisites & Configuration Files
1. **`.env`**:
   ```env
   OPENAI_API_KEY=your-api-key-here
   ```
2. **`llm_variables.json`**:
   ```json
   {
     "model": "gpt-4o",
     "api_key": "",
     "base_url": ""
   }
   ```
3. **`input_fields.json`**: Sets channel defaults (`content_style`, `dialect_register`, `code_switching_level`, `max_translation_revision_count`, `max_quality_revision_count`, etc.).

### Running a Production Run
```bash
python3 main.py "/path/to/your/English_Script.txt"
```

### Output File Structure
```
output/
└── <session_id>/
    ├── session_log.jsonl     # Complete node-by-node execution telemetry and intermediate JSON states
    ├── checkpoint.json       # Resumable pipeline state checkpoint
    ├── final_script.md       # Production-ready deliverable with script, storyboard, and QA
    ├── final_script.html     # Responsive multi-device viewer & teleprompter mode
    ├── final_script.pdf      # High-fidelity PDF export via headless Chromium
    ├── final_script.docx     # RTL-formatted Microsoft Word deliverable
    ├── shorts_package.md     # Dedicated Reels & YouTube Shorts scripts and overlays
    ├── broll_images.txt      # Copy-paste AI Image generation prompts (separated by empty line)
    └── broll_videos.txt      # Copy-paste AI Video generation prompts (separated by empty line)
```
