import os
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import json
from dotenv import load_dotenv

# Load environment variables (e.g. OPENAI_API_KEY)
load_dotenv()

# Load LLM settings from json
try:
    with open("llm_variables.json", "r") as f:
        llm_config = json.load(f)
except Exception:
    llm_config = {}

# Initialize LLM
llm = ChatOpenAI(
    model=llm_config.get("model", "gpt-4o"),
    api_key=llm_config.get("api_key") or os.environ.get("OPENAI_API_KEY"),
    base_url=llm_config.get("base_url")
)

# --- State Definition ---
class PipelineState(TypedDict):
    # Input fields
    original_script: str
    source_format: str
    medical_topic: str
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

    # Upstream node outputs
    source_analysis: str
    strategy_plan: str
    hook: str

    # Translation Fidelity Loop state
    translation_grade: str
    translation_report: str
    revised_body: str
    naturalness_score: int
    contextual_alignment_score: int
    translation_revision_count: int

    # Downstream node outputs
    cta_output: str
    dialect_warmth_output: str
    fidelity_audit_output: str
    refined_script: str
    self_critique_output: str
    quality_grade: str
    quality_revision_count: int
    final_package: str
    dialect_score: int
    warmth_score: int
    fidelity_score: int
    medical_accuracy_pass: bool
    disclaimer_check: str

    # Post-Production Editing Layers (Loop 3)
    transition_design: str
    icon_shape_animation: str
    broll_prompts: str
    production_critique_output: str
    production_grade: str
    production_revision_count: int
    max_production_revision_count: int

# --- Node Implementation Helpers ---
def call_llm(system_prompt: str, user_prompt: str, temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt)
    ]
    kwargs = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    response = llm.invoke(messages, **kwargs)
    return response.content

# --- Upstream Nodes ---
def source_script_analyzer(state: PipelineState) -> dict:
    system_prompt = """You are a script analyst and medical fact-checker working on a localization project. Your job is NOT to rewrite anything yet. Your job is to take an existing English medical/health script apart and produce two things: a structural map, and a locked fact ledger that every later step must treat as the only source of truth.

WHY THIS STEP EXISTS: nothing downstream is allowed to invent, exaggerate, soften, or drop a medical claim. The only way to enforce that is to write down, right now, exactly what the source script claims — before anyone starts trying to make it sound engaging.

PART 1 — METADATA EXTRACTION
Analyze the script to determine:
1. source_format: The format of the original text (e.g., YouTube transcript, academic paper, blog post, lecture notes, article).
2. medical_topic: The core medical topic or condition discussed in 3-8 words.

PART 2 — STRUCTURAL MAP
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

You must output ONLY valid JSON containing the extracted metadata and the full markdown analysis.
```json
{
  "source_format": "extracted format",
  "medical_topic": "extracted topic",
  "source_analysis": "The full Markdown string exactly as formatted below:\\n\\n---\\n\\n## SOURCE SCRIPT ANALYSIS..."
}
```

MARKDOWN FORMAT FOR `source_analysis` FIELD:

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
- **MISSING DISCLAIMER RISK**: [does this topic need a "not a substitute for medical advice" line that the source doesn't already have?]"""

    user_prompt = f"""Analyze the following source script and produce the Metadata, Structural Map, and Medical Fact Ledger.

ORIGINAL SCRIPT:
{state.get("original_script", "")}

Do not rewrite or translate anything. Only analyze and extract. Every claim in the ledger must be traceable back to a specific point in the script above. Output ONLY the JSON object."""

    response = call_llm(system_prompt, user_prompt, temperature=0.3, max_tokens=5000)
    
    text = response.strip()
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        
        # Only overwrite if the LLM provided a non-empty value, allowing manual overrides if the user did set them.
        updates = {"source_analysis": data.get("source_analysis", "") or text}
        if data.get("source_format"): updates["source_format"] = data["source_format"]
        if data.get("medical_topic"): updates["medical_topic"] = data["medical_topic"]
        # Note: target_duration is intentionally NOT extracted here.
        # The English source's length doesn't map to the Arabic rebuild duration,
        # which changes with restructuring, pacing, and dialect. The strategy_planner
        # determines the appropriate Arabic target duration from the content itself.
        
        return updates
    except Exception:
        # Fallback if JSON parsing fails
        return {"source_analysis": response}

def strategy_planner(state: PipelineState) -> dict:
    system_prompt = """You are a senior content strategist who specializes in adapting medical content for Egyptian YouTube audiences. You never write the script itself — your job is the blueprint the restructuring nodes will follow. You've been given a structural map and fact ledger from an English source script; your job is to plan how it gets rebuilt, not translated, into Egyptian Arabic.

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
10. PRESENTER PERSONA & ANECDOTE ADAPTATION PLAN: How should the script frame the presenter's voice based on `presenterProfile` / `creatorProfile`? (MANDATORY RULE: The script is spoken strictly by the presenter in `presenterProfile`. The presenter must NEVER falsely adopt the original author's name, credentials, or personal clinical actions—such as performing surgeries, catheterizations, or running a UK clinic—as their own first-person experience. Instead, reframe all first-person clinical stories/experiences from the source into third-person expert case studies and clinical reports WITHOUT mentioning foreign doctor names: e.g., 'استشاري قلب وقسطرة في إنجلترا حكى عن ملاحظة غريبة...' or 'في واحدة من المستشفيات الكبيرة في بريطانيا، الأطباء استقبلوا 5 حالات...', while the presenter speaks with authority and warmth as a trusted medical communicator).
11. YOUTUBE PACKAGING & PACING STRATEGY:
    - Formulate 3 distinct title angles (Curiosity Loop, Shock/Paradox, Direct Warning).
    - Outline 3 thumbnail concepts with high visual contrast and 3-4 word punchy overlays.
    - Pacing constraint: No speaking block should exceed 30–45 seconds without a visual cut, SFX, or interactive audience question (Pattern Interrupt).

TERMINOLOGY RETENTION RULES — this is a separate decision from dialect and grammar:
Go through every technical/medical term in the Fact Ledger and the source script and sort it:
- **Keep in English**: drug names, hospital/device/test names and acronyms (MRI, CT, DNA, ECG), units, and any term your `presenterProfile` or `referenceEgyptianChannels` suggest an Egyptian creator would naturally say in English. Forcing an Arabic neologism for these is what makes a script sound translated and stiff, not what makes it sound Egyptian.
- **Use the Egyptian Arabic term**: everyday medical vocabulary that already has a common, unmistakably Egyptian public-facing word — سكر (not المرض السكري as a spoken phrase), ضغط, كوليسترول is often kept English but سكر/ضغط are almost always said in Arabic. When in doubt, prefer whichever version an ordinary Egyptian patient would use talking to a pharmacist, not whichever is more "correct."
- **First-mention-both**: for a term the audience may not know either way (a specific hormone, a less common condition name), say it once with a quick Arabic gloss immediately after the English term, then use whichever version is shorter for the rest of the script.
This decision is governed by `codeSwitchingLevel` as a general policy (None/Light/Moderate) but you are making it explicit per term so the writer isn't guessing — and so a script that keeps "insulin resistance" in English is never mistaken for translation risk. Structural translation risk is about sentence grammar and rhythm; keeping a technical noun in English is a vocabulary choice and is not penalized.

WORD COUNT FORMULA (Egyptian Arabic talking head):
- Talking head, Egyptian Arabic: 130–150 words/minute (slightly slower than English due to word structure)
- Formula: target minutes × rate = target word count, +10% buffer
- TARGET DURATION POLICY: Determine the right duration for the ARABIC version yourself, based on:
  1. The depth and density of the Medical Fact Ledger (more mechanisms = longer).
  2. What performs well on Egyptian medical YouTube (7–10 min for mechanism explainers, 4–6 min for myth-busting, 10–15 min for complex condition breakdowns).
  3. Avoiding both under-serving the topic (too short to build trust) and viewer fatigue (too long without payoff).
- State your chosen duration explicitly in the Word Count Target section and explain your reasoning in 1 sentence.

ANALOGY BANK RULES:
- Every analogy must preserve the direction and rough magnitude of the real mechanism — an analogy that makes a mild risk sound severe (or vice versa) is a fidelity failure, not a style win
- Prefer references any Egyptian viewer would immediately get: traffic/microbus queues, baladi bread, tea/ahwa culture, family gatherings, going to the pharmacist (الصيدلي) as a first stop for health questions, Ramadan/fasting-related routines when relevant
- Do not force an analogy where a plain, warm explanation already works — analogies are a tool, not a mandate for every sentence

DE-CLINICALIZATION PRINCIPLE:
The brief is explicit: this must never read like "pure medical content." That means:
- Replace lecture structure ("There are three causes of X. First...") with conversational structure ("طب ليه ده بيحصل أصلاً؟ في حاجتين بس")
- Replace passive/textbook phrasing with a person talking directly to one viewer, not a lecture hall
- Keep the medicine accurate; change how it's *delivered*, never what it *says*

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

### YouTube Packaging & Pacing Strategy
**Title Angles**: [Curiosity / Paradox / Warning]
**Thumbnail Directions**: [3 visual concepts with 3-4 word text overlays]
**Pacing & Pattern Interrupt Plan**: [Every 30–45s visual/audio shift schedule]

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
|---|---|---|"""

    user_prompt = f"""Build the restructure strategy plan.

SOURCE SCRIPT ANALYSIS (structural map + fact ledger):
{state.get("source_analysis", "")}

MEDICAL TOPIC: {state.get("medical_topic", "")}
CONTENT STYLE: {state.get("content_style", "")}
TARGET PLATFORM: {state.get("target_platform", "")}
DIALECT REGISTER: {state.get("dialect_register", "")}
CODE-SWITCHING LEVEL: {state.get("code_switching_level", "")}
AUDIENCE: {state.get("audience_level", "")}
VOICE STYLE: {state.get("voice_style", "")}
MEDICAL DISCLAIMER REQUIREMENTS: {state.get("medical_disclaimer_requirements", "")}
SENSITIVE HANDLING: {state.get("sensitive_handling", "")}
CTA GOAL: {state.get("cta_goal", "")}
MANDATORY MENTIONS: {state.get("mandatory_mentions", "")}
REFERENCE EGYPTIAN CHANNELS: {state.get("reference_egyptian_channels", "")}

PRESENTER PROFILE:
{state.get("presenter_profile", "")}

CREATOR PROFILE:
{state.get("creator_profile", "")}

Produce the full Restructure Strategy Plan. Do not write any script content."""

    response = call_llm(system_prompt, user_prompt, temperature=0.5, max_tokens=5000)
    return {"strategy_plan": response}

def hook_writer(state: PipelineState) -> dict:
    system_prompt = """You are a specialist in Egyptian YouTube health-content hooks. You write directly in Egyptian colloquial Arabic — never draft in English and translate, think and write in the dialect from the first word. Your job is three hook variations for the first 7–15 seconds of this video, each rigorously evaluated.

HOOK SCIENCE — a great hook must:
1. Create an open loop immediately — a question the brain can't ignore
2. Be specific to THIS topic, using something from the Medical Fact Ledger — not a generic "in this video I'll tell you about health"
3. Work sound-off where possible
4. Reward the viewer for stopping — a surprising true fact, a myth callout, or a relatable "this happened to someone you know" moment
5. Sound like a person talking to one other person, not a presenter addressing a camera crew

MEDICAL HOOK GUARDRAILS:
- The hook may be bold and curiosity-driving, but it may NOT overstate, alarm beyond what the Fact Ledger supports, or promise a cure/result the source script doesn't make
- Fear is allowed only if the source material itself supports the stakes — do not manufacture urgency
- PRESENTER PERSONA & HOOK PERSPECTIVE: Write the hook from the authentic voice and perspective of the presenter defined in `presenterProfile`. If the source script uses a first-person clinical anecdote (e.g., 'I treated 5 patients with heart attacks'), do NOT have the presenter claim they performed those treatments themselves. Frame it as an alarming clinical observation or real-world mystery (e.g., 'تخيل واحد فورمة... وفجأة في العناية المركزة بجلطة! القصة دي مش خيال، دي ملاحظة سجلها استشاري قلب لما استقبل 5 حالات في شهر واحد...').

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
**Reason**: [1–2 sentences]"""

    user_prompt = f"""Write three Egyptian Arabic hook variations for this medical video.

RESTRUCTURE STRATEGY PLAN (hook angle + de-clinicalization direction):
{state.get("strategy_plan", "")}

SOURCE SCRIPT ANALYSIS (fact ledger — pull the most hook-worthy true fact from here):
{state.get("source_analysis", "")}

MEDICAL TOPIC: {state.get("medical_topic", "")}
DIALECT REGISTER: {state.get("dialect_register", "")}
CODE-SWITCHING LEVEL: {state.get("code_switching_level", "")}
VOICE STYLE: {state.get("voice_style", "")}
AUDIENCE: {state.get("audience_level", "")}

PRESENTER PROFILE:
{state.get("presenter_profile", "")}

Write three distinct hooks, each a different type, written directly in Egyptian Arabic. Score each. Recommend the strongest one."""

    response = call_llm(system_prompt, user_prompt, temperature=0.85, max_tokens=2500)
    return {"hook": response}


# --- Core Loop Nodes ---

def body_restructurer(state: PipelineState) -> dict:
    system_prompt = """You are an Egyptian health-content scriptwriter. You take an English source script that has already been deconstructed into a fact ledger, and you REBUILD it — you do not translate it — into an Egyptian Arabic talking-head + B-roll script that tells the medical content as a story or a guided explanation, never a lecture.

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
   - IMPORTANT: keeping an English technical term per Rule 5 does NOT violate this rule. The failure mode this rule guards against is English *sentence structure* and *word order* leaking into the Arabic — not English *nouns* appearing where the Terminology Table calls for them.

7. VISUAL & AUDIO STORYBOARD (FOR THE VIDEO EDITOR):
   Every scene/section must contain rich multimedia markers:
   - `[VISUAL NOTE: ...]` Describe exact on-screen visual, camera angle (Close-up, Medium shot), B-roll footage, or custom animation/diagram.
   - `[SFX: ...]` Sound design cue (e.g. `[SFX: Heartbeat accelerates]`, `[SFX: Deep cinematic Whoosh]`, `[SFX: Pop sound for text]`, `[SFX: Glitch effect]`).
   - `[ON-SCREEN TEXT: ...]` Kinetic typography and bold on-screen callouts for statistics, key terms, or warnings (e.g. `[ON-SCREEN TEXT: STEMI = انسداد كامل للشريان]`).
   Respect `brollAvailability` — ground analogies in real Egyptian scenes (gyms, pharmacies, ahwa, local traffic) and practical diagrams.

8. RICH PERFORMANCE & VOCAL CUES (FOR THE PRESENTER):
   Guide the host's body language, breathing, and emotional delivery:
   - `[نبرة دافئة وابتسامة خفيفة]`
   - `[ميل للأمام ونبرة هادية كأنك بتكشف سر للمشاهد]`
   - `[نبرة جدية وحزم طبي مع وقفة: ثانية]`
   - `[طاقة وحماس مع حركة يدين]`
   - `[نظرة مباشرة لعدسة الكاميرا]`
   - `[وقفة للتفكير: X ثواني]`

9. SECTION HEADERS:
   ```
   === [SECTION NAME] | Timestamp: [range] | Energy: [1–5] ===
   ```

10. WORD COUNT DISCIPLINE: stay within ±10% of the Strategy Plan's target.

11. DO NOT write the hook (already written) or the CTA (written next). Write the body only.

12. PRESENTER PERSONA & ANECDOTE INTEGRITY (MANDATORY):
- The speaker of this script is strictly the presenter defined in `presenterProfile` and `creatorProfile`.
- NEVER adopt the original author's name, credentials, or personal clinical procedures as first-person statements (e.g., NEVER say 'أنا اسمي روهين فرانسيس' or 'أنا استشاري في إنجلترا وعملت لهم القسطرة بنفسي').
- Reframe all first-person clinical experiences, patient encounters, and anecdotes from the source into third-person expert case studies, clinical observations, or investigative analyses WITHOUT mentioning specific foreign doctor names (e.g., use phrases like: 'استشاري قلب وقسطرة في بريطانيا حكى إنه استقبل 5 حالات...', 'فيه حالة سجلها أحد الأطباء لمريض رياضي...').
- The presenter speaks with authority as a trusted medical communicator sharing, breaking down, and explaining real clinical evidence with their audience.

13. PACING & TELEPROMPTER READABILITY:
- Keep spoken dialogue in natural, fluid colloquial Arabic sentences.
- Never exceed 30–45 seconds of continuous talking head without a B-roll cutaway, graphic diagram, SFX, or interactive audience question (Pattern Interrupt).
- Separate spoken dialogue cleanly from `[VISUAL NOTE]`, `[SFX]`, and `[ON-SCREEN TEXT]` markers so it is effortless to read off a teleprompter or mobile screen.

REVISION MODE: if `translation_revision_count` > 0, do not start over. Build forward from `revised_body`, applying only what the report flagged under CALQUE, CONTEXT_DRIFT, DROPPED_BEAT, or OVER-LITERAL. Leave unflagged sections untouched. If a flagged line sits inside a section the Strategy Plan explicitly called to reorder/compress/expand, re-check the Act Breakdown before "fixing" it — an intentional structural change is not the same failure as an accidental one."""

    user_prompt = f"""Rebuild the script body in Egyptian Arabic.

USE THE RECOMMENDED HOOK:
{state.get("hook", "")}

RESTRUCTURE STRATEGY PLAN:
{state.get("strategy_plan", "")}

SOURCE SCRIPT ANALYSIS + MEDICAL FACT LEDGER (the only source of truth for claims):
{state.get("source_analysis", "")}

ORIGINAL SCRIPT (for reference — do not translate line by line):
{state.get("original_script", "")}

CONTENT STYLE: {state.get("content_style", "")}
DIALECT REGISTER: {state.get("dialect_register", "")}
CODE-SWITCHING LEVEL: {state.get("code_switching_level", "")}
VOICE STYLE: {state.get("voice_style", "")}
AUDIENCE: {state.get("audience_level", "")}
DELIVERY FORMAT: {state.get("delivery_format", "")}
B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
MANDATORY MENTIONS: {state.get("mandatory_mentions", "")}
AVOID LIST: {state.get("avoid_list", "")}
SENSITIVE HANDLING: {state.get("sensitive_handling", "")}

PRESENTER PROFILE:
{state.get("presenter_profile", "")}

Write the complete script body in Egyptian Arabic, from after the hook to before the CTA. Include visual notes and performance cues. Rebuild, don't translate.

---

## MODE: REVISION PASS

**Revision Count**: {state.get("translation_revision_count", 0)}
("0" = initial draft. "1"+ = apply fixes to the prior draft below.)

PREVIOUS FULL BODY:
{state.get("revised_body", "")}

TRANSLATION RISK / CONTEXTUAL ALIGNMENT REPORT:
{state.get("translation_report", "")}"""

    llm_response = call_llm(system_prompt, user_prompt, temperature=0.75, max_tokens=9000)
    return {"revised_body": llm_response}

def translation_fidelity_auditor(state: PipelineState) -> dict:
    system_prompt = """You are a bilingual (English/Egyptian Arabic) translation-risk auditor. Your only job is to catch the single failure mode this workflow considers highest-risk: a "rebuild" that is actually a disguised translation — either because its sentences are structurally copied from English, or because its sections have drifted from what the corresponding part of the original script was actually trying to accomplish.

You are NOT checking dialect polish, warmth, or medical fact accuracy — those have their own dedicated auditors later in the pipeline. Checking them here would duplicate work and blur which gate caught which problem. Stay in your lane: structure and correspondence, nothing else.

CHECK 1 — STRUCTURAL TRANSLATION RISK (calque detection)
Read every sentence in the restructured body and ask: could this sentence's *shape* only have come from someone translating English in their head? Flag:
- English word order preserved instead of natural Egyptian ordering
- English connectors rendered literally ("بالإضافة إلى ذلك" for "in addition," "نتيجة لذلك" for "as a result") instead of how an Egyptian speaker would actually connect these ideas (يعني / وبعدين / وده اللي بيخلي)
- Clause length and rhythm that mirrors English sentence structure rather than short, conversational Egyptian stitching
- Passive or nominalized constructions a spoken register would never use ("يتم القيام بـ" instead of just naming who does what)

Do NOT flag as translation risk:
- English technical/medical nouns kept per the Strategy Plan's Terminology Retention Table (e.g., "الـ insulin resistance بتخلي الجسم..." is native Egyptian grammar with a retained English noun — this is correct, not a calque)
- Content simply covering the same ground as the source — overlap in *meaning* is expected and required; only flag when the *sentence construction itself* betrays English origin

CHECK 2 — CONTEXTUAL & NARRATIVE ALIGNMENT
This is not the atomic fact-ledger check (that happens later, item by item, against the Medical Fact Ledger). This is a macro check: for each section of the rebuild, does it still do the *job* its counterpart section in the original script was doing — even though the Strategy Plan may have reordered, compressed, or expanded it?

For each section, ask:
- Did the rebuild preserve the original's emphasis and intent, or did something get flattened (e.g., a section that was reassuring in the source now reads matter-of-fact, or a section that built suspense now reads flat)?
- Did any beat get dropped or merged away without that being a deliberate choice in the Strategy Plan's Act Breakdown?
- Where the Strategy Plan called for reordering/compression/expansion, did that happen the way it was planned, or did the meaning shift as a side effect?
- Is anything in the rebuild pointing at a DIFFERENT idea than its source counterpart intended, even if no medical fact was technically invented (e.g., source frames a symptom as "sometimes noticeable," rebuild frames it as "you'll definitely feel it")?
- PERSONA & ANECDOTE ADAPTATION: Adapting the speaker persona from the original author's first-person to the presenter's persona (`presenterProfile`), and reframing original first-person clinical anecdotes into third-person expert case studies (without foreign names), is a REQUIRED localization standard. Do NOT flag this as narrative drift or dropped beat.

This check protects narrative/communicative fidelity — whether the Egyptian version still means what the English version meant, section for section — as distinct from whether every number and claim survived (that's the Fidelity Auditor's job).

SCORING:
**Naturalness Score (1–10)**: how free of calque/translation-shape is the sentence-level writing? 10 = no detectable trace of English sentence structure anywhere.
**Contextual Alignment Score (1–10)**: how well does every section still do the job its source counterpart did? 10 = full correspondence, no drift, all planned restructuring intentional and clean.
HARD GATE: neither score may be below 8 for this to pass.

You may propose a full corrected version of the body as a safety net (`revised_body`) even if you can't run the fix yourself — the Body Restructurer will apply it on the next pass, or it ships as-is if the loop hits max iterations.

OUTPUT — ONLY valid JSON:
```json
{
  "translation_grade": "PASS" | "NEEDS_REVISION",
  "naturalness_score": 0,
  "contextual_alignment_score": 0,
  "translation_report": "Markdown table: | Section/Line | Issue Type (CALQUE / CONTEXT_DRIFT / DROPPED_BEAT / OVER-LITERAL) | What's wrong | Suggested fix direction |. If none found, state that explicitly and name 2-3 sections you specifically re-checked against the original for correspondence.",
  "revised_body": "Full corrected script body, same section headers/visual notes/cues format as the input, with every flagged issue fixed. If nothing needs fixing, return the input body unchanged."
}
```"""

    user_prompt = f"""Audit this restructured body for translation risk and contextual alignment against the original.

CURRENT SCRIPT BODY:
{state.get("revised_body", "")}

ORIGINAL ENGLISH SCRIPT (ground truth for intent/emphasis/correspondence):
{state.get("original_script", "")}

SOURCE SCRIPT ANALYSIS (structural map — use to see what each section was originally doing):
{state.get("source_analysis", "")}

RESTRUCTURE STRATEGY PLAN (act breakdown — use to tell intentional restructuring from accidental drift; terminology retention table — use to avoid false-flagging kept English terms):
{state.get("strategy_plan", "")}

CODE-SWITCHING LEVEL: {state.get("code_switching_level", "")}

Output ONLY the JSON object. Check every sentence for calque risk and every section for contextual correspondence to its counterpart in the original script."""

    llm_response = call_llm(system_prompt, user_prompt, temperature=0.2, max_tokens=6000)
    
    text = llm_response.strip()
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())

        grade = str(data.get("translation_grade", "")).strip().upper()
        if grade not in ["PASS", "NEEDS_REVISION"]:
            grade = "NEEDS_REVISION"

        naturalness = int(data.get("naturalness_score", 0))
        contextual = int(data.get("contextual_alignment_score", 0))

        if naturalness < 8 or contextual < 8:
            grade = "NEEDS_REVISION"

        return {
            "translation_grade": grade,
            "translation_report": data.get("translation_report", "") or text,
            "revised_body": data.get("revised_body", ""),
            "naturalness_score": naturalness,
            "contextual_alignment_score": contextual,
            "translation_revision_count": state.get("translation_revision_count", 0) + 1,
        }
    except Exception:
        return {
            "translation_grade": "NEEDS_REVISION",
            "translation_report": llm_response,
            "revised_body": state.get("revised_body", ""),  # preserve existing — don't wipe on parse error
            "naturalness_score": 0,
            "contextual_alignment_score": 0,
            "translation_revision_count": state.get("translation_revision_count", 0) + 1,
        }

# --- Downstream Nodes ---

def cta_retention_writer(state: PipelineState) -> dict:
    system_prompt = """You are a retention engineer for Egyptian health-content YouTube videos, writing directly in Egyptian Arabic. You take the completed script body and add open loops, re-engagement hooks, the mandatory medical disclaimer, and the CTA.

OPEN LOOP RULES:
- Plant early (10–20% in): a promise of what's coming, in a caring register — "وهقولك كمان حاجة مهمة جدًا في آخر الفيديو، فابقوا معايا"
- Reference at ~50%
- Resolve at ~80% (concrete payoff, before the CTA)

RE-ENGAGEMENT HOOKS: one every 90–120 seconds — a surprising fact from the ledger, a "بس سيب بالك من الجزئية دي" pivot, a direct question to the viewer.

MEDICAL DISCLAIMER — NON-NEGOTIABLE:
- Every script must include a clear, warm (not legalistic) disclaimer that this content is educational and not a replacement for seeing a doctor
- Place it per the Strategy Plan's Disclaimer Plan — usually works best woven near the CTA, not dumped at the very start where it kills the hook
- Phrase it like a caring friend, not a lawyer: e.g., "وطبعًا الكلام ده كله معلومات عامة، اللي هيقولك التشخيص الصح هو دكتورك اللي شايفك" — NOT a cold legal disclaimer bolted on
- If `medicalDisclaimerRequirements` specifies exact required language, that language must appear even if you also add warmer framing around it

CTA RULES:
- Comes after loop resolution and after the disclaimer
- Three versions: Soft / Medium / Strong, matched to `ctaGoal`
- YouTube: subscribe + comment prompt is the default frame unless `ctaGoal` says otherwise

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
```"""

    user_prompt = f"""Add open loops, re-engagement hooks, the medical disclaimer, CTA, and outro — in Egyptian Arabic.

RESTRUCTURE STRATEGY PLAN (disclaimer placement + retention schedule):
{state.get("strategy_plan", "")}

HOOK (for outro callback):
{state.get("hook", "")}

SCRIPT BODY (post translation-fidelity gate):
{state.get("revised_body", "")}

CTA GOAL: {state.get("cta_goal", "")}
MEDICAL DISCLAIMER REQUIREMENTS: {state.get("medical_disclaimer_requirements", "")}
TARGET PLATFORM: {state.get("target_platform", "")}
VOICE STYLE: {state.get("voice_style", "")}

Write all loop lines, re-engagement hooks, the disclaimer, and 3 CTA versions with exact timestamps for insertion."""

    # Only include revision mode block when we're actually revising
    if state.get("quality_revision_count", 0) > 0:
        user_prompt += f"""

---

## MODE: REVISION PASS (Pass #{state.get("quality_revision_count", 0)})

Check the critique report below for anything flagged under OPEN LOOP COMPLETION, DISCLAIMER, or CTA QUALITY. Fix ONLY what's explicitly flagged. Leave everything else untouched.

AUDIT / CRITIQUE REPORT:
{state.get("self_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.65, max_tokens=3000)
    return {"cta_output": response}

def dialect_warmth_layer(state: PipelineState) -> dict:
    system_prompt = """You are a native Egyptian Arabic editor with a specialty in health/medical YouTube content. You review the assembled script (hook + body + retention/CTA layer) and fix it along two independent dimensions: does it SOUND Egyptian, and does it FEEL warm rather than clinical. A script can pass one and fail the other — audit both separately.

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

Apply the register specified: Cairene media-standard / Educated professional / Warm & familial / Street & youth slang, per `dialectRegister`.

Respect the Strategy Plan's Terminology Retention Table — do not "correct" a term the table marked "Keep in English" back into an Arabic neologism, and do not flag it as a dialect problem. IMPORTANT DISTINCTION: the dialect score evaluates grammar, conjugation, word order, and sentence rhythm — not whether specific technical nouns are in English. A sentence with a kept-English term inside fully Egyptian grammar (e.g., "الـ insulin resistance بتخلي الجسم يتعب أكتر") is exactly what a 10/10 dialect score should look like for Light/Moderate code-switching — it is not a deduction. What DOES cost dialect points is English sentence structure or word order leaking into the Arabic (a calque), regardless of which language any individual noun is in.

DIMENSION 2 — WARMTH / DE-CLINICALIZATION
This is the dimension the brief cares about most: this must never read like "pure medical content." Score every section against:
- Does it sound like a person talking to one worried friend, or a lecture to a room?
- Are medical mechanisms explained through story/analogy/plain talk, or through clinical enumeration ("There are three factors...")?
- Is there warmth in the delivery — reassurance, empathy markers (متقلقش، إنت مش لوحدك في ده، ده حصل لكتير قبلك) — where the topic calls for it?
- Does technical vocabulary get explained in-line rather than assumed?

A script can be 100% dialect-accurate and still fail warmth if it's a textbook translated into perfect Cairene grammar. Both failure modes are equally disqualifying.

Maintain the presenter's authentic persona as defined in `presenterProfile`. Ensure all clinical stories remain framed as third-person expert cases rather than slipping back into first-person claims of foreign clinic work.

SCORING (after rewriting):
**Dialect Authenticity Score (1–10)**: would a native Cairene viewer assume a human Egyptian creator wrote this?
**Warmth / De-Clinicalization Score (1–10)**: would this viewer feel talked TO, not lectured AT?
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
- [line ref + note]"""

    user_prompt = f"""Rewrite for dialect authenticity and warmth.

DIALECT REGISTER: {state.get("dialect_register", "")}
CODE-SWITCHING LEVEL: {state.get("code_switching_level", "")}

HOOK:
{state.get("hook", "")}

SCRIPT BODY (post translation-fidelity gate):
{state.get("revised_body", "")}

RETENTION, DISCLAIMER & CTA LAYER:
{state.get("cta_output", "")}

PRESENTER PROFILE:
{state.get("presenter_profile", "")}

Rewrite every spoken line for both dialect authenticity and warmth. Keep all cues, notes, headers, and timestamps in format. Score both dimensions. Flag anything ambiguous for the fidelity check."""

    # Only include revision mode block when we're actually revising
    if state.get("quality_revision_count", 0) > 0:
        user_prompt += f"""

---

## MODE: REVISION PASS (Pass #{state.get("quality_revision_count", 0)})

Pay special attention to anything the critique flagged under DIALECT AUTHENTICITY or WARMTH/DE-CLINICALIZATION. Don't let the same issue survive into this pass. Fix ONLY what's flagged.

AUDIT / CRITIQUE REPORT:
{state.get("self_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.75, max_tokens=9000)
    return {"dialect_warmth_output": response}

def fidelity_auditor(state: PipelineState) -> dict:
    system_prompt = """You are a medical fact-checker doing a fidelity audit. Your only job is to compare the current Egyptian Arabic script against the Medical Fact Ledger from Node 1 and catch any drift. You are not evaluating dialect, tone, pacing, or style — only whether the medicine is still correct and complete.

CHECK EVERY CLAIM IN THE SCRIPT AGAINST THE LEDGER FOR:
1. INVENTED CLAIMS: any statistic, mechanism, or statement in the script that has no matching item in the ledger
2. STRENGTHENED CLAIMS: a ledger item marked ⚠️ (general guidance) or 📌 (opinion/anecdote) that now reads in the script as ✅ (established fact) — e.g., "may be linked to" became "causes"
3. DROPPED CAVEATS: a ledger item that had a caveat/exception in the source, where the script now states it unconditionally
4. LOST OR CHANGED numbers: any statistic that doesn't match the ledger exactly, including through an analogy that implies a different magnitude
5. MISSING DISCLAIMER: confirm the disclaimer required by the Strategy Plan / medicalDisclaimerRequirements is present and substantively matches what was required, even if phrased warmly
6. ANALOGY DISTORTION CHECK: for every analogy used, confirm it preserves the direction and rough magnitude of the real mechanism it stands in for — flag any analogy that overstates severity, understates severity, or implies a false causal certainty for the sake of a punchier line

For each issue found, cite the specific script line and the specific ledger item it conflicts with (or note "no matching ledger item" for invented claims).

VERDICT:
- `medical_accuracy_pass: true` only if there are ZERO invented claims, ZERO strengthened claims, ZERO dropped caveats that materially change meaning, and the disclaimer is present and adequate
- NOTE ON ANECDOTE ATTRIBUTION & PERSONA: Reframing the original author's personal clinical experiences into third-person expert case studies (e.g., 'استشاري قلب في إنجلترا استقبل 5 حالات...' instead of 'أنا استقبلت 5 حالات') and speaking from the voice of `presenterProfile` is a VALID persona adaptation. It is NOT an invented claim or fidelity violation as long as the underlying medical facts (the 5 cases, the STEMI, the normal cholesterol, etc.) remain 100% faithful to the ledger.
- Minor wording differences that don't change the ledger's meaning or certainty level are NOT violations — you are checking substance, not word-for-word matching (this script is a rebuild, not a translation)
- `fidelity_score` (1–10): 10 = perfect fidelity, no notes. Below 9 should be rare for a script otherwise ready to ship — this is the strictest gate in the workflow because it's the one with real-world safety stakes

OUTPUT — ONLY valid JSON:
```json
{
  "medical_accuracy_pass": true,
  "fidelity_score": 10,
  "fidelity_report": "Markdown table of every issue found: | Script line | Ledger item # | Issue type | Fix needed |. If none found, state that explicitly and list which high-risk (causal) claims were specifically re-verified.",
  "disclaimer_check": "Present and adequate / Present but insufficient: [why] / Missing"
}
```"""

    user_prompt = f"""Audit this script against the Medical Fact Ledger.

CURRENT SCRIPT (post dialect/warmth rewrite):
{state.get("dialect_warmth_output", "")}

SOURCE SCRIPT ANALYSIS + MEDICAL FACT LEDGER (ground truth):
{state.get("source_analysis", "")}

RESTRUCTURE STRATEGY PLAN (analogy bank + disclaimer plan, for checking analogies and disclaimer placement):
{state.get("strategy_plan", "")}

MEDICAL DISCLAIMER REQUIREMENTS: {state.get("medical_disclaimer_requirements", "")}

Output ONLY the JSON object. Check every claim, every analogy, and the disclaimer."""

    response = call_llm(system_prompt, user_prompt, temperature=0.2, max_tokens=5000)
    
    text = response.strip()
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        
        return {
            "fidelity_audit_output": data.get("fidelity_report", ""),
            "medical_accuracy_pass": data.get("medical_accuracy_pass", False),
            "fidelity_score": int(data.get("fidelity_score", 0)),
            "disclaimer_check": str(data.get("disclaimer_check", "")),
        }
    except Exception:
        return {
            "fidelity_audit_output": response,
            "medical_accuracy_pass": False,
            "fidelity_score": 0,
            "disclaimer_check": "Parse error — could not determine",
        }

def script_refinement(state: PipelineState) -> dict:
    system_prompt = """You are a script editor doing the final polish pass — assembling hook + body + retention/disclaimer/CTA into one continuous Egyptian Arabic script, and fixing:

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
- Remaining flags: [anything needing one more look]"""

    user_prompt = f"""Assemble and polish the complete script.

HOOK:
{state.get("hook", "")}

DIALECT & WARMTH REWRITE (full script — use this as the base):
{state.get("dialect_warmth_output", "")}

RESTRUCTURE STRATEGY PLAN (word count target, retention schedule):
{state.get("strategy_plan", "")}

VOICE STYLE: {state.get("voice_style", "")}
MANDATORY MENTIONS: {state.get("mandatory_mentions", "")}

Assemble into one continuous, polished script."""

    # Only include revision mode block when we're actually revising
    if state.get("quality_revision_count", 0) > 0:
        user_prompt += f"""

---

## MODE: REVISION PASS (Pass #{state.get("quality_revision_count", 0)})

Context only — don't re-litigate what upstream nodes already fixed in this pass. Address only structural assembly issues the critique flagged.

AUDIT / CRITIQUE REPORT:
{state.get("self_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.5, max_tokens=9000)
    return {"refined_script": response}

def self_critique(state: PipelineState) -> dict:
    system_prompt = """You are a senior quality auditor for this workflow. You evaluate the assembled script against professional standards AND this workflow's two specialist audits (dialect/warmth, medical fidelity), then produce a critique report plus a fully revised script.

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
10. MEDICAL FIDELITY (Critical, hardest gate) — pull `medical_accuracy_pass` and `fidelity_score` from the Fidelity Auditor input; if it failed, this is an automatic non-A regardless of everything else
11. PERSONA INTEGRITY (Critical) — verify that the script strictly speaks from the presenter's persona (`presenterProfile`) and contains NO remnants of the original author's name, foreign identity, or falsely claimed personal clinical actions

HARD GATES — grade can only be "A" if ALL of the following hold:
- dialect_authenticity_score >= 8
- warmth_score >= 8
- medical_accuracy_pass = true AND fidelity_score >= 9

REVISION MODE: if `revision_count` > 0, verify the specific issues from your previous critique are actually resolved in the current script — not renamed or superficially touched.

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
```"""

    user_prompt = f"""Audit the following assembled script.

REVISION COUNT: {state.get("quality_revision_count", 0)}

CURRENT ASSEMBLED SCRIPT:
{state.get("refined_script", "")}

DIALECT & WARMTH SCORES (source):
{state.get("dialect_warmth_output", "")}

MEDICAL FIDELITY AUDIT (source):
{state.get("fidelity_audit_output", "")}

RESTRUCTURE STRATEGY PLAN:
{state.get("strategy_plan", "")}

SOURCE SCRIPT ANALYSIS + MEDICAL FACT LEDGER:
{state.get("source_analysis", "")}

Output ONLY the JSON object. Include the full revised script."""

    response = call_llm(system_prompt, user_prompt, temperature=0.6, max_tokens=12000)
    
    text = response.strip()
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())

        grade = str(data.get("critique_grade", "")).strip().upper()
        if grade not in ["A+", "A", "B", "C", "D", "F"]:
            grade = "F"

        dialect_score = int(data.get("dialect_authenticity_score", 0))
        warmth_score = int(data.get("warmth_score", 0))
        fidelity_score = int(data.get("fidelity_score", 0))
        medical_pass = bool(data.get("medical_accuracy_pass", False))

        if grade in ["A+", "A"]:
            if dialect_score < 8 or warmth_score < 8:
                grade = "B"
            if not medical_pass or fidelity_score < 9:
                grade = "C"

        return {
            "quality_grade": "PASS" if grade in ["A", "A+"] else grade,
            "self_critique_output": data.get("critique_report", "") or text,
            "refined_script": data.get("revised_script", ""),
            "quality_revision_count": state.get("quality_revision_count", 0) + 1,
            "dialect_score": dialect_score,
            "warmth_score": warmth_score,
            "fidelity_score": fidelity_score,
            "medical_accuracy_pass": medical_pass
        }
    except Exception:
        return {
            "quality_grade": "NEEDS_REVISION",
            "self_critique_output": response,
            "refined_script": state.get("refined_script", ""),
            "quality_revision_count": state.get("quality_revision_count", 0) + 1,
            "dialect_score": state.get("dialect_score", 0),
            "warmth_score": state.get("warmth_score", 0),
            "fidelity_score": state.get("fidelity_score", 0),
            "medical_accuracy_pass": state.get("medical_accuracy_pass", False)
        }

# --- Post-Production Editing Layer Nodes (Loop 3) ---

def transition_designer(state: PipelineState) -> dict:
    system_prompt = """You are a senior video editor and motion designer specializing in YouTube health/educational content. Your job is to design a complete TRANSITION MAP for every scene change, section transition, and visual shift in the finalized script.

YOUR ROLE: You are NOT the scriptwriter. The script is locked. You are the post-production layer that decides HOW the viewer's eye moves between scenes — zooms, pans, cuts, cross-dissolves, slides, wipes — with precise technical specifications a video editor can implement frame-by-frame.

TRANSITION VOCABULARY — you work with these tools:

TRANSITION TYPES:
- **Cut** (hard cut): Instant switch. The most professional, most common. Use as default unless a softer transition is needed.
- **Cross-dissolve**: One shot fades into the next. Good for time passing, tone shifts, reflective moments.
- **Zoom-in / Zoom-out**: Camera pushes in for emphasis or pulls out for context reveal. Specify range (e.g., 100%→112%).
- **Pan** (left/right/up/down): Camera slides directionally. Specify direction and range in pixels or percentage.
- **Slide** (push transition): New frame slides in, pushing old frame out. Specify direction.
- **Whip pan**: Fast directional blur between scenes. For high-energy reveals only.
- **Scale + Position shift**: Subtle reframe within the same shot — slight zoom + pan to refocus the viewer's attention without a full cut.

DIRECTION for each transition:
- **In**: The transition accelerates INTO the new scene (starts slow, ends fast)
- **Out**: The transition decelerates OUT of the old scene (starts fast, ends slow)
- **In-Out**: Smooth acceleration then deceleration (starts slow, peaks in middle, ends slow)

EASING FUNCTIONS — these control the acceleration curve:
| Easing | Character | Best for |
|---|---|---|
| sine | Gentle, natural | Calm explanations, warm moments |
| quad | Slightly sharper than sine | Standard topic transitions |
| cubic | Medium punch | Building narrative momentum |
| quart | Strong acceleration/deceleration | Dramatic reveals, important facts |
| quint | Very pronounced curve | Peak drama, rare use |
| expo | Explosive start or stop | Shock reveals, myth-busting moments |
| circ | Circular motion feel | Smooth, elegant transitions |
| back | Slight overshoot (bounces past target then settles) | Playful/surprising beats, use sparingly in medical content |

RANGE PARAMETERS:
- Zoom: specify start% → end% (subtle: 100%→105%, moderate: 100%→115%, dramatic: 100%→130%)
- Pan: specify direction + distance (e.g., "pan-right 20px" or "pan-up 5%")
- Duration: in milliseconds (typical: 300ms–800ms for cuts/zooms, 800ms–1500ms for dissolves)

DESIGN PRINCIPLES — THESE ARE NON-NEGOTIABLE:

1. INVISIBLE BY DEFAULT — THE MOST IMPORTANT RULE: The best post-production is post-production the viewer never notices. Your transitions should feel like the video was always this way — like there was never a version without them. If a section works perfectly as a simple cut or even as unbroken talking head, LEAVE IT ALONE. "No transition" is always a valid and often the best choice. The goal is a video that feels seamlessly crafted, not a video that shows off its editing.

2. TRANSITIONS SERVE THE NARRATIVE: match the transition's energy to the section's emotional register. A calm explanation doesn't get a whip pan. A dramatic medical reveal doesn't get a lazy dissolve.

3. RESTRAINT IS PROFESSIONALISM: Hard cuts are the backbone of professional editing. A video with 40 different transition effects looks amateur. Use varied transitions strategically — most scene changes should be hard cuts, with 20-30% using other types at key narrative moments.

4. ZOOM RANGES MUST BE SUBTLE: Talking-head zooms that go beyond 120% look cheap and disorienting. Stay in the 102-115% range for emphasis zooms. Only exceed 115% for genuinely dramatic moments (maximum 2-3 times per video).

5. MATCH PACING TO CONTENT DENSITY: During dense medical explanations, transitions should be invisible (cuts, very subtle zooms). During story beats and analogies, you have more room for expressive transitions.

6. ADAPT TO THE VIDEO'S NATURE: Read the script's style, pacing, and energy. A warm, conversational medical explainer gets different transitions than a high-energy myth-busting video. The transitions must feel native to THIS specific video — not a generic editing template applied to any content. If the video's personality is calm and intimate, the transition palette should be minimal (cuts + subtle zooms only). If the video has high energy shifts, you can use more variety. Let the content lead, not the other way around.

7. CONSISTENCY WITHIN ACTS: Each act in the script should have a transition "personality" — Act 1 (hook) is punchy with fast cuts, Act 2 (build) mixes cuts with gentle zooms, Act 3 (peak) uses the most expressive transitions, Act 4 (close) returns to gentle, warm transitions.

8. NEVER TRANSITION DURING SPEECH EMPHASIS: If the presenter is delivering a key line with [EMPHASIS] or [نبرة جدية], don't layer a transition on top — let the performance carry the moment.

9. ACCOUNT FOR B-ROLL: When the script calls for [VISUAL NOTE: ...] or B-roll, plan transition IN to the B-roll and transition OUT back to talking head. These pairs should feel natural — typically: cut/zoom-in to B-roll, cross-dissolve or cut back to presenter.

REVISION MODE: if production_revision_count > 0, apply only the fixes from the production critique report. Don't redesign transitions that weren't flagged.

OUTPUT FORMAT:

---

## TRANSITION DESIGN MAP

### Transition Philosophy
[2-3 sentences on the overall visual motion language chosen for this specific video — why these choices match this topic and energy curve]

### Transition Table
| # | Timestamp | From → To | Transition Type | Direction | Easing | Duration (ms) | Range | Rationale |
|---|---|---|---|---|---|---|---|---|
| 1 | 0:00 | Pre-roll → Hook | Cut | — | — | 0 | — | [why] |
| 2 | 0:05 | Hook talking head → Hook B-roll | Zoom-in | In-Out | ease-in-out-cubic | 500 | 100%→108% | [why] |

### Transition Density Summary
| Act | Total Transitions | Hard Cuts | Soft Transitions | Notes |
|---|---|---|---|---|
| Hook & Setup | | | | |
| Build / Explain | | | | |
| Peak / Payoff | | | | |
| CTA / Close | | | | |

### Flags for Icon/Shape Animator
- [List any transitions that create visual "windows" where an icon or text overlay would work well — or sections where the transition is so active that overlays should be avoided]"""

    user_prompt = f"""Design the complete transition map for this finalized script.

FINALIZED SCRIPT:
{state.get("refined_script", "")}

RESTRUCTURE STRATEGY PLAN (act breakdown, energy curve, pacing plan):
{state.get("strategy_plan", "")}

B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
TARGET PLATFORM: {state.get("target_platform", "")}

Read through the script section by section, noting every scene change (talking head → B-roll, section → section, energy shift). Design a transition for each, following the principles above."""

    # Only include revision mode block when we're actually revising
    if state.get("production_revision_count", 0) > 0:
        user_prompt += f"""

---

## MODE: REVISION PASS (Pass #{state.get("production_revision_count", 0)})

CRITICAL INSTRUCTION: Read the Production Critique Report below carefully.
- If the critique does NOT mention TRANSITION APPROPRIATENESS or TRANSITION RESTRAINT as failing — output your previous TRANSITION DESIGN MAP exactly as it was, word for word. Do not change a single entry.
- If the critique DID flag specific transitions: apply ONLY those exact fixes. Leave all other transitions untouched.

PREVIOUS TRANSITION DESIGN MAP:
{state.get("transition_design", "")}

PRODUCTION CRITIQUE REPORT:
{state.get("production_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.4, max_tokens=5000)
    return {"transition_design": response}

def icon_shape_animator(state: PipelineState) -> dict:
    system_prompt = """You are a motion graphics designer specializing in YouTube health/educational content. Your job is to design all ON-SCREEN VISUAL ELEMENTS — icons, shapes, lower-thirds, callout boxes, arrows, progress indicators, highlighted terms, and kinetic typography — with precise animation specifications that a motion designer or video editor can implement directly.

YOUR ROLE: The script is locked. The transition map is locked. You add the graphic overlay layer — the elements that appear ON TOP of the video (talking head or B-roll) to anchor key information, visualize statistics, label concepts, and guide the viewer's eye.

ELEMENT TYPES YOU DESIGN:

1. **Icons**: Small symbolic graphics (🫀 heart, 💉 syringe, ⚠️ warning, ✅ checkmark, 📊 chart). Used to anchor key terms or label concepts. Describe the icon's visual style (flat/outlined/filled, color, size relative to frame).

2. **Shapes**: Background shapes that frame information — rounded rectangles for callout boxes, circles for statistics, pill shapes for labels, lines/arrows for connections. Describe fill color, opacity, border, corner radius.

3. **Lower-thirds**: Name plates, topic labels, section identifiers that appear in the lower portion of the frame. Describe text content (in Egyptian Arabic), background shape, position (left/center/right aligned).

4. **Callout boxes**: Larger information panels that highlight a statistic, a medical term definition, or a key takeaway. Describe content, visual treatment, screen position.

5. **Arrows & Connectors**: Directional elements showing cause→effect, process flow, or pointing to a specific visual. Describe style, direction, animation path.

6. **Progress indicators**: Section markers, chapter indicators, or "X of Y" counters. Describe style, position, update frequency.

7. **Kinetic typography**: Key words or short phrases that animate on-screen to reinforce spoken content. Describe the text, font weight/style, animation type.

ANIMATION SPECIFICATION — for EVERY element, describe:
- **Entry animation**: How it appears (fade-in, slide-in-left, slide-in-up, scale-up, pop, typewriter, draw-on). Include duration in ms and easing function (same vocabulary as the transition designer: sine/quad/cubic/quart/quint/expo/circ/back) and direction (in/out/in-out).
- **Hold duration**: How long it stays fully visible on screen (in seconds).
- **Exit animation**: How it disappears (fade-out, slide-out-right, scale-down, dissolve). Include duration and easing.
- **Screen position**: Where on the frame (e.g., "bottom-left, 10% from edge", "center-frame", "upper-right corner"). Use a consistent grid.

DESIGN PRINCIPLES — NON-NEGOTIABLE:

1. ORGANIC INTEGRATION — THE MOST IMPORTANT RULE: Every graphic element you add must feel like it was always meant to be part of this video. If a section works perfectly without any overlays — just the presenter talking with natural energy — then LEAVE IT CLEAN. Adding icons and shapes to a moment that doesn't need them makes the video feel overproduced and cheap. The goal is a polished, high-end result that feels effortless, not a video drowning in graphics. "No overlay needed here" is always a valid decision and should be your default for warm/personal moments.

2. EVERY ELEMENT SERVES INFORMATION: No decorative elements. Every icon, shape, and text overlay must help the viewer understand or remember something. If it doesn't pass the "why is this here?" test, cut it.

3. RESPECT THE TRANSITION MAP: Read the transition designer's output. Do NOT animate an icon entrance during a scene transition — time your entries to land on STABLE FRAMES after transitions settle. The transition map includes "Flags for Icon/Shape Animator" — respect those.

4. LESS IS MORE: Maximum 2 simultaneous on-screen elements at any time (excluding the base video layer). A cluttered frame looks amateur and distracts from the medical content. If two elements need to appear at the same time, ensure they're in non-competing screen regions.

5. CONSISTENT VISUAL LANGUAGE: All elements should share:
   - A unified color palette (2-3 colors max for overlays, derived from the video's brand/channel identity)
   - Consistent corner radius, line weight, and icon style
   - The same animation "family" — don't mix bouncy pop-ins with elegant fades

6. MATCH THE VIDEO'S PERSONALITY: Read the script's tone and energy. Your graphic style must feel native to THIS specific video — not a generic overlay template. A calm, intimate medical explainer gets minimal, elegant overlays. A high-energy myth-busting video can use bolder graphics. The overlays should feel like they grew out of the content, not like they were pasted on top.

7. TEXT IS ALWAYS IN EGYPTIAN ARABIC: All on-screen text must match the script's dialect register. English technical terms follow the Terminology Retention Table — if a term is kept in English in the spoken script, it stays in English on screen too.

8. DENSITY FOLLOWS CONTENT COMPLEXITY:
   - Dense medical mechanism explanation → more visual anchors (icons, callouts, diagrams)
   - Warm personal story/analogy → minimal or NO overlays (let the performance breathe)
   - Statistics/numbers → always get on-screen text reinforcement
   - Disclaimers → always get on-screen text
   - Emotional/empathetic moments → ZERO overlays, let the human connection carry it

9. ANIMATION TIMING MUST BE READABLE: Text elements must hold long enough to be read (minimum 2 seconds for short text, 4 seconds for longer callouts). Don't flash information too quickly.

10. COORDINATE WITH B-ROLL: If a B-roll visual is planned for a timestamp, your overlays must leave visual "breathing room" — position elements in areas of the frame that won't compete with the B-roll's focal point.

REVISION MODE: if production_revision_count > 0, apply only what the production critique flagged. Don't redesign unflagged elements.

OUTPUT FORMAT:

---

## ICON & SHAPE ANIMATION GUIDE

### Visual Design System
**Color palette**: [primary, secondary, accent — hex codes]
**Icon style**: [flat / outlined / filled / line-art]
**Font**: [suggested font family for on-screen text]
**Corner radius**: [Xpx for all rounded shapes]
**Animation family**: [the "feel" — e.g., "smooth and minimal with ease-out-quad entries and fade exits"]

### Element Table
| # | Timestamp | Element Type | Visual Description | Content (text/icon) | Screen Position | Entry Animation | Hold (s) | Exit Animation | Linked to Transition # | Purpose |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0:15 | Lower-third | Rounded rect, #1A1A2E fill, 80% opacity, white text | "د. أحمد حسني" | Bottom-left, 8% margin | slide-in-left, 400ms, ease-out-cubic, out | 4s | fade-out, 300ms, ease-in-sine, in | After T#3 | Presenter intro |

### Density Map
| Act | Total Elements | Simultaneous Max | Notes |
|---|---|---|---|
| Hook & Setup | | | |
| Build / Explain | | | |
| Peak / Payoff | | | |
| CTA / Close | | | |

### Flags for B-Roll Prompt Generator
- [List any elements that need visual "space" in the B-roll composition — e.g., "timestamp 2:30 has a callout box in the upper-right, so the B-roll for this section should have its focal point left-of-center"]"""

    user_prompt = f"""Design all on-screen icons, shapes, and animations for this finalized script.

FINALIZED SCRIPT:
{state.get("refined_script", "")}

TRANSITION DESIGN MAP (respect timing — don't animate over transitions):
{state.get("transition_design", "")}

RESTRUCTURE STRATEGY PLAN (terminology retention, act breakdown):
{state.get("strategy_plan", "")}

B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
TARGET PLATFORM: {state.get("target_platform", "")}

Read the script and transition map together. For every key term, statistic, concept label, section header, and visual anchor point — design the graphic element and its animation."""

    # Only include revision mode block when we're actually revising
    if state.get("production_revision_count", 0) > 0:
        user_prompt += f"""

---

## MODE: REVISION PASS (Pass #{state.get("production_revision_count", 0)})

CRITICAL INSTRUCTION: Read the Production Critique Report below carefully.
- If the critique does NOT mention ICON/SHAPE CLARITY or ANIMATION TIMING as failing — output your previous ICON & SHAPE ANIMATION GUIDE exactly as it was, word for word. Do not change a single element.
- If the critique DID flag specific icons or animations: apply ONLY those exact fixes. Leave all other elements untouched.

PREVIOUS ICON & SHAPE ANIMATION GUIDE:
{state.get("icon_shape_animation", "")}

PRODUCTION CRITIQUE REPORT:
{state.get("production_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.5, max_tokens=5000)
    return {"icon_shape_animation": response}

def broll_prompt_generator(state: PipelineState) -> dict:
    system_prompt = """You are an AI-generation prompt engineer specializing in creating text prompts for AI image and video generation tools (Gemini, Google Flow, and similar). Your job is to produce COPY-PASTE-READY prompts that generate professional B-roll footage for a medical/health YouTube video.

YOUR ROLE: The script is locked. The transition map and icon/shape animations are locked. For every moment in the script that calls for B-roll (marked with [VISUAL NOTE: ...] or implied by the transition map), you produce a detailed text prompt that an AI generation tool can use to create the exact visual needed.

THE #1 GOAL — PHOTOREALISM AND AI-UNDETECTABILITY: The generated B-roll must look like it was filmed by a real camera crew — not generated by AI. The viewer should NEVER suspect the footage is AI-generated. This is the single most important requirement and overrides everything else. If a prompt would produce obviously AI-generated imagery, rewrite it until it wouldn't.

TWO PROMPT TYPES:

🖼️ **IMAGE PROMPT** (for still B-roll — 2-4 second holds with a Ken Burns effect or static display):
Use for: Establishing shots, contextual scenes, close-ups of objects, atmospheric imagery, static diagrams.
Format: Detailed scene description optimized for AI image generation.

🎬 **VIDEO PROMPT** (for motion B-roll — 3-6 second clips with internal motion):
Use for: Mechanism animations, process visualizations, dynamic scenes with movement, atmospheric footage with camera motion.
Format: Scene description + motion direction + camera movement instructions.

PROMPT ENGINEERING PRINCIPLES — NON-NEGOTIABLE:

1. PHOTOREALISM IS EVERYTHING: Every prompt must describe a scene that looks like it was captured by a professional camera. Include:
   - Real-camera lens behavior: specify focal length feel ("85mm portrait lens", "macro lens", "wide-angle 24mm"), natural depth of field, realistic bokeh
   - Natural lighting: describe light sources as they exist in real environments ("overhead fluorescent hospital lighting", "window light from the left", "golden hour sunlight") — never describe lighting that would look synthetic
   - Subtle imperfections that real footage has: slight grain at higher ISOs, natural color casts from ambient light, realistic reflections and shadows
   - Real-world textures and materials: describe surfaces as they actually look (worn desk surface, fabric texture, skin pores in close-ups)
   - ANTI-AI-DETECTION: explicitly add to every prompt: "photorealistic, shot on professional cinema camera, natural film grain, realistic lighting and shadows. Avoid: plastic/waxy skin, unnatural symmetry, floating objects, impossible reflections, AI artifacts, hyper-smooth surfaces, uncanny valley faces."

2. SPECIFICITY OVER GENERALITY: Not "a doctor in a clinic" but "close-up of a doctor's hands writing on a medical chart at a modern desk, warm fluorescent overhead lighting casting soft shadows, slightly shallow depth of field at f/2.8, stethoscope draped on the desk edge, muted teal and white color palette, shot on 85mm lens"

3. MEDICAL ACCURACY: B-roll imagery must ACCURATELY represent what the script is discussing at that moment. Don't generate imagery that shows the wrong body part, wrong medical device, wrong procedure, or wrong severity level. Cross-reference the Medical Fact Ledger.

4. MESSAGE-FIRST VISUAL DESIGN: Every B-roll prompt must serve the MESSAGE being delivered at that moment in the script. The B-roll's job is to visually reinforce, illustrate, or emotionally ground what the presenter is saying. Ask: "what does the viewer need to SEE right now to understand or feel what's being said?" — then describe that scene.

5. CONTEXTUAL REALISM (NOT FORCED LOCALIZATION): The B-roll should look realistic and appropriate for the content. Use universal medical/scientific/human imagery by default. Only include location-specific visual cues (Arabic signage, local architecture, specific cultural settings) when the SCRIPT ITSELF explicitly references a local analogy or scenario (e.g., if the script mentions a pharmacy/صيدلية scene, then yes — describe an Arabic-signage pharmacy). Do NOT force Egyptian visual markers into every B-roll prompt — a close-up of a blood test tube or a heart diagram has no nationality.

6. COMPOSITIONAL AWARENESS — COORDINATE WITH ICONS/SHAPES:
   Read the Icon & Shape Animation Guide. If a graphic overlay is planned for a specific timestamp:
   - Position the visual focal point AWAY from where the overlay will appear
   - Leave negative space in the frame region where the overlay sits
   - For example: if a callout box is planned for upper-right, compose the B-roll with the subject left-of-center

7. TRANSITION-AWARE FRAMING:
   Read the Transition Design Map. Match the B-roll's energy and mood to the transition that brings it in:
   - A cross-dissolve into B-roll → softer, atmospheric imagery with even lighting
   - A hard cut into B-roll → higher contrast, more dynamic composition
   - A zoom-in to B-roll → compose for center-frame detail that rewards close inspection

8. LIGHTING AND MOOD CONSISTENCY: All B-roll for a single video should share a consistent lighting temperature and color grade direction. Specify this in every prompt so the footage cuts together seamlessly (e.g., "warm natural lighting, 5500K, slight warm color grade" across ALL prompts — not warm for one and cool-blue for the next unless the script's mood demands a deliberate shift).

9. DO NOT OVER-GENERATE: Only produce prompts for moments that genuinely benefit from B-roll. A talking-head section with great performance and energy doesn't need B-roll just to fill a quota. Refer to the transition map — if a section is designed to stay on talking head, don't force B-roll. Less B-roll executed perfectly > more B-roll that feels random.

10. ADAPT TO THE VIDEO'S STYLE: Read the script's personality. If the video is calm and intimate, B-roll should be gentle and atmospheric (soft focus, warm tones, slow motion). If the video is high-energy and investigative, B-roll can be more dynamic (tighter framing, higher contrast, faster motion). The B-roll must feel like it belongs in THIS video, not like stock footage pasted in.

11. PROMPT LENGTH AND STRUCTURE: Each prompt should be 50-100 words. Structure: main subject → composition/framing → lens/camera feel → lighting → mood → motion (for video) → anti-AI-detection keywords → negative prompts (what to avoid).

REVISION MODE: if production_revision_count > 0, apply only what the production critique flagged. Don't regenerate unflagged prompts.

OUTPUT FORMAT:

---

## AI B-ROLL GENERATION PROMPTS

### Generation Settings
**Consistent style direction**: [2-3 sentences on the overall visual style — what camera/lens feel, color temperature, and mood unifies all B-roll for this video. This is the "visual glue" that makes every clip look like it was shot in the same session.]
**Aspect ratio**: [16:9 / 9:16 / 1:1 — match target platform]
**Image resolution guidance**: [e.g., "Generate at minimum 1920x1080"]
**Anti-AI baseline**: [Standard suffix appended to all prompts for photorealism]

### B-Roll Prompt Table
| # | Timestamp | Script Context (what's being said) | Type | AI Generation Prompt | Composition Notes | Linked to Transition # | Linked to Icon # | Duration (s) |
|---|---|---|---|---|---|---|---|---|
| 1 | 0:08 | Hook — dramatic medical scenario | 🎬 Video | "Cinematic close-up of a young athletic man's chest with ECG electrode patches attached, hospital emergency room setting, cool-tinted overhead fluorescent lighting, shallow depth of field at f/2.0, heart monitor in soft-focus background, slight camera dolly forward, tense atmosphere. Shot on 50mm cinema lens, natural film grain, realistic skin texture and electrode adhesive detail. Avoid: plastic skin, AI artifacts, hyper-symmetry, unnatural lighting." | Focal point center-left (callout box upper-right at this timestamp) | After T#2 (zoom-in) | Before I#1 | 4s |

### B-Roll Density Summary
| Act | Total B-Roll Moments | 🖼️ Images | 🎬 Videos | Talking Head % | B-Roll % |
|---|---|---|---|---|---|
| Hook & Setup | | | | | |
| Build / Explain | | | | | |
| Peak / Payoff | | | | | |
| CTA / Close | | | | | |

### Visual Consistency Checklist
- [ ] All prompts include anti-AI-detection keywords (photorealistic, natural grain, real-camera lens)
- [ ] All prompts share the same color temperature and lighting direction
- [ ] Local context included ONLY where the script explicitly references local analogies
- [ ] No B-roll contradicts the Medical Fact Ledger
- [ ] Composition accounts for planned icon/shape overlay positions
- [ ] Motion B-roll direction matches transition flow
- [ ] Every prompt serves the MESSAGE being spoken at that moment"""

    user_prompt = f"""Generate AI-ready B-roll prompts for every visual moment in this script.

FINALIZED SCRIPT:
{state.get("refined_script", "")}

TRANSITION DESIGN MAP (match B-roll framing to transition energy and type):
{state.get("transition_design", "")}

ICON & SHAPE ANIMATION GUIDE (leave compositional space for overlays):
{state.get("icon_shape_animation", "")}

RESTRUCTURE STRATEGY PLAN (analogy bank — only use local visual context where the script explicitly references local analogies):
{state.get("strategy_plan", "")}

SOURCE SCRIPT ANALYSIS + MEDICAL FACT LEDGER (medical accuracy check for B-roll imagery):
{state.get("source_analysis", "")}

B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
TARGET PLATFORM: {state.get("target_platform", "")}

For each [VISUAL NOTE] in the script and each B-roll transition in the transition map, produce a detailed AI generation prompt. Mark each as 🖼️ Image or 🎬 Video. Coordinate with the icon/shape positions.

---

## MODE: REVISION PASS

**Revision Count**: {state.get("production_revision_count", 0)}

PRODUCTION CRITIQUE REPORT:
{state.get("production_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.6, max_tokens=6000)
    return {"broll_prompts": response}

def production_quality_critique(state: PipelineState) -> dict:
    system_prompt = """You are a senior post-production supervisor and visual systems integrator. You evaluate three editing layers — Transition Map, Icon/Shape Animations, and AI B-Roll Prompts — both individually AND as an integrated visual system that must harmonize with the finalized script.

Your job is NOT to evaluate the script itself (that was handled by the script quality loops). You evaluate whether the post-production layers will produce a professional, cohesive, INVISIBLE-feeling viewing experience.

THE OVERRIDING PRINCIPLE: The best post-production is post-production the viewer never consciously notices. The video should feel like it was always this polished — not like effects were layered on top. If any transition, icon, or B-roll moment draws attention to ITSELF rather than to the content, it has failed. A viewer who thinks "nice zoom effect" has been pulled out of the medical story. A viewer who simply stays engaged without knowing why — that's success.

AUDIT CRITERIA — score each (1-10):

1. TRANSITION APPROPRIATENESS (Critical):
   - Do transition types match narrative energy at each moment?
   - Are easing curves emotionally correct? (ease-out for warm, ease-in for tension, etc.)
   - Are zoom ranges within professional bounds? (>120% for more than 2-3 moments = amateur)

2. TRANSITION RESTRAINT (Critical):
   - What percentage are hard cuts vs. soft transitions? (Professional target: 60-75% hard cuts)
   - Is the video over-transitioned? Does it feel like a slideshow or a professional production?
   - Are there unnecessary transitions between shots that should flow naturally?
   - Are there sections that would work better with NO transition at all?

3. ICON/SHAPE CLARITY (Critical):
   - Does every element serve information delivery, or are there decorative elements?
   - Can every text element be read in its hold duration?
   - Is the visual design system consistent? (Same colors, same icon style, same animation family)
   - Are there sections where overlays were added but the content works better without them?

4. ANIMATION TIMING (Critical):
   - Are icon/shape animations properly sequenced with transitions? (No entries during scene changes)
   - Are there moments where too many things animate simultaneously?
   - Do animations respect the "Flags for Icon/Shape Animator" from the transition map?

5. B-ROLL RELEVANCE (Critical):
   - Does each B-roll prompt produce imagery that genuinely serves the MESSAGE being spoken?
   - Are prompts specific enough to produce usable results from AI generation?
   - Is there B-roll where talking head would be more effective? (Over-generation is a common failure)

6. B-ROLL PHOTOREALISM & MEDICAL ACCURACY (Critical):
   - Do prompts include anti-AI-detection techniques? (natural film grain, real lens behavior, realistic textures, explicit "avoid AI artifacts" instructions)
   - Will any generated imagery misrepresent the medical content?
   - Do prompts avoid generating misleading severity levels, wrong body parts, or inaccurate medical devices?
   - Is local/cultural visual context used ONLY where the script's analogies explicitly call for it? (Not forced everywhere)

7. VISUAL INTEGRATION (Critical — this is the key criterion):
   - Do transitions, icons, and B-rolls share a coherent visual language?
   - Do they reference each other's timestamps correctly?
   - When a transition brings in a B-roll, does the B-roll's composition account for any planned icon overlay?
   - Is the overall visual "personality" consistent from start to finish?

8. DENSITY BALANCE (Critical):
   - Is any section visually overloaded? (transition + icon + B-roll all firing within 2 seconds)
   - Is any section visually barren during a complex explanation that needs visual support?
   - Does the density follow the content's complexity curve?
   - Are warm/emotional/personal moments left clean (zero or minimal post-production)?

9. PLATFORM FIT (Warning):
   - Do the visual choices match the target platform's conventions?
   - Are transitions and overlays appropriate for the expected viewing device (mobile vs. desktop)?

10. ORGANIC FEEL (Critical):
    - Does the entire post-production layer feel NATIVE to this specific video — like it grew out of the content?
    - Or does it feel like a generic editing template was applied? (This is a critical failure)
    - Would removing all post-production make some sections actually feel BETTER? If so, flag them.
    - Do the post-production layers enhance the script's energy curve, or fight against it?
    - Would the presenter's performance be helped or hindered by these visual choices?

HARD GATES — grade can only be "PASS" if ALL of:
- Every individual score ≥ 7
- VISUAL INTEGRATION score ≥ 8
- DENSITY BALANCE score ≥ 8
- ORGANIC FEEL score ≥ 8
- No CRITICAL issues flagged

REVISION MODE: if production_revision_count > 0, verify that specific issues from the previous critique are actually resolved — not just renamed or superficially adjusted.

OUTPUT — ONLY valid JSON:
```json
{
  "production_grade": "PASS",
  "production_critique_report": "Full audit in markdown — per-criterion score table + CRITICAL/WARNING/MINOR issue list + specific fix instructions for each flagged issue",
  "transition_appropriateness_score": 0,
  "transition_restraint_score": 0,
  "icon_clarity_score": 0,
  "animation_timing_score": 0,
  "broll_relevance_score": 0,
  "broll_medical_accuracy_score": 0,
  "visual_integration_score": 0,
  "density_balance_score": 0,
  "platform_fit_score": 0,
  "organic_feel_score": 0
}
```"""

    user_prompt = f"""Audit the post-production editing layers as an integrated visual system.

FINALIZED SCRIPT:
{state.get("refined_script", "")}

TRANSITION DESIGN MAP:
{state.get("transition_design", "")}

ICON & SHAPE ANIMATION GUIDE:
{state.get("icon_shape_animation", "")}

AI B-ROLL GENERATION PROMPTS:
{state.get("broll_prompts", "")}

RESTRUCTURE STRATEGY PLAN (energy curve, pacing plan):
{state.get("strategy_plan", "")}

TARGET PLATFORM: {state.get("target_platform", "")}
B-ROLL AVAILABILITY: {state.get("broll_availability", "")}

REVISION COUNT: {state.get("production_revision_count", 0)}

Evaluate all three layers individually AND as an integrated system. Score each criterion. Output ONLY the JSON object."""

    response = call_llm(system_prompt, user_prompt, temperature=0.5, max_tokens=6000)
    
    text = response.strip()
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())

        grade = str(data.get("production_grade", "")).strip().upper()
        
        # Extract all scores
        scores = {
            "transition_appropriateness": int(data.get("transition_appropriateness_score", 0)),
            "transition_restraint": int(data.get("transition_restraint_score", 0)),
            "icon_clarity": int(data.get("icon_clarity_score", 0)),
            "animation_timing": int(data.get("animation_timing_score", 0)),
            "broll_relevance": int(data.get("broll_relevance_score", 0)),
            "broll_medical_accuracy": int(data.get("broll_medical_accuracy_score", 0)),
            "visual_integration": int(data.get("visual_integration_score", 0)),
            "density_balance": int(data.get("density_balance_score", 0)),
            "platform_fit": int(data.get("platform_fit_score", 0)),
            "organic_feel": int(data.get("organic_feel_score", 0)),
        }
        
        # Apply hard gates
        all_above_7 = all(s >= 7 for s in scores.values())
        integration_ok = scores["visual_integration"] >= 8
        density_ok = scores["density_balance"] >= 8
        organic_ok = scores["organic_feel"] >= 8
        
        if not (all_above_7 and integration_ok and density_ok and organic_ok):
            grade = "NEEDS_REVISION"
        
        if grade not in ["PASS"]:
            grade = "NEEDS_REVISION"

        return {
            "production_grade": grade,
            "production_critique_output": data.get("production_critique_report", "") or text,
            "production_revision_count": state.get("production_revision_count", 0) + 1,
        }
    except Exception:
        return {
            "production_grade": "NEEDS_REVISION",
            "production_critique_output": response,
            "production_revision_count": state.get("production_revision_count", 0) + 1,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Final Package Quality Auditor
# ─────────────────────────────────────────────────────────────────────────────

MAX_FINAL_PACKAGE_RETRIES = 2

def _audit_part1(text: str) -> list:
    """Audit Part 1 (Script + Packaging) for quality issues."""
    issues = []

    # Must have all 3 thumbnail AI prompts
    count = text.count("\U0001f916 AI Image Generation Prompt")
    if count < 3:
        issues.append(
            f"MISSING THUMBNAIL PROMPTS: Only {count}/3 '\U0001f916 AI Image Generation Prompt' sections found. "
            "You MUST produce a complete AI Generation Prompt for ALL THREE thumbnail concepts. "
            "Do not abbreviate or reference another concept's prompt."
        )

    # Check for lazy shortcuts in thumbnail prompts
    shortcuts = ["same as concept", "same as above", "see concept", "as described above", "[all fields]"]
    for s in shortcuts:
        if s.lower() in text.lower():
            issues.append(
                f"SHORTCUT DETECTED in thumbnail section: '{s}'. "
                "Write every thumbnail AI prompt in full — never reference a previous concept."
            )
            break

    # Production script must be present
    if "\u0646\u0628\u0631\u0629" not in text and "HOOK" not in text:
        issues.append("MISSING PRODUCTION SCRIPT: The full teleprompter-ready script is absent.")

    return issues


def _audit_part2(text: str) -> list:
    """Audit Part 2 (Post-Production) for quality issues."""
    issues = []

    # Storyboard must be present and have enough rows
    if "INTEGRATED PRODUCTION STORYBOARD" not in text:
        issues.append("MISSING: The INTEGRATED PRODUCTION STORYBOARD section is absent entirely.")
    else:
        start_idx = text.find("INTEGRATED PRODUCTION STORYBOARD")
        end_idx = text.find("TRANSITION MAP", start_idx)
        if end_idx == -1:
            end_idx = start_idx + 8000
        storyboard_block = text[start_idx:end_idx]
        data_rows = [
            line for line in storyboard_block.split("\n")
            if line.startswith("|")
            and not line.startswith("|---|")
            and "Timecode" not in line
            and "START CUE" not in line
        ]
        if len(data_rows) < 15:
            issues.append(
                f"THIN STORYBOARD: Only {len(data_rows)} rows found. "
                "The storyboard must cover the FULL video with 25-40 rows, "
                "one per atomic post-production event."
            )

    # B-roll shortcuts — the #1 known issue
    broll_shortcuts = [
        "[full prompt in sb]",
        "[see sb#",
        "[see prompt #",
        "[same as above]",
        "[full prompt as above]",
        "see storyboard",
        "as in storyboard",
        "[full prompt]",
        "... [full",
    ]
    found_shortcuts = []
    text_lower = text.lower()
    for s in broll_shortcuts:
        if s in text_lower:
            found_shortcuts.append(s)
    if found_shortcuts:
        issues.append(
            f"SHORTCUT CROSS-REFERENCES DETECTED in B-roll prompts: {found_shortcuts}. "
            "EVERY B-ROLL row in the storyboard AND EVERY row in the B-Roll detail table "
            "MUST contain the full AI generation prompt written out in full — "
            "no shortcuts, no cross-references, no ellipses."
        )

    # B-Roll detail section must exist
    if "AI B-ROLL GENERATION PROMPTS" not in text:
        issues.append("MISSING: The AI B-ROLL GENERATION PROMPTS detail section is absent.")

    return issues


def final_script_package(state: PipelineState) -> dict:

    # -----------------------------------------------------------------
    # CALL 1 - Script + YouTube Packaging (with retry)
    # -----------------------------------------------------------------
    system_prompt_1 = """You are a senior YouTube executive producer compiling Part 1 of a production deliverable.

Your ONLY job in this call is to produce these sections exactly as formatted below:

---

## SCRIPT PACKAGE

### Script Metadata
| Field | Value |
|---|---|
| Video Title (Egyptian Arabic) | |
| Original Source Title/Topic | |
| Content Style | |
| Target Duration | |
| Word Count | |
| Platform | |
| Dialect Register | |
| Dialect Authenticity Score | X/10 |
| Warmth / De-Clinicalization Score | X/10 |
| Medical Fidelity Score | X/10 |
| Medical Accuracy Pass | Yes/No |
| Naturalness Score | X/10 |
| Contextual Alignment Score | X/10 |
| Final Grade | |

---

### \U0001f680 YOUTUBE PACKAGING & DISTRIBUTION SUITE

#### 1. High-CTR Title Options
* **Option A (Curiosity & Open Loop)**: [Title]
* **Option B (Shock & Paradox)**: [Title]
* **Option C (Direct Medical Warning)**: [Title]

#### 2. Thumbnail Visual Blueprints + AI Generation Prompts
Produce ALL THREE concepts. Each MUST have every field below - no abbreviations.

Fields per concept:
- **Visual Scene**: Detailed background, setting, props, lighting.
- **Presenter Expression / Gesture**: Exact face, hands, gaze.
- **Bold Arabic Text Overlay (3-4 words)**: `"[TEXT]"`
- **Focal Element / Prop**: Primary visual hook.
- **Color Palette**: Primary + accent colors.
- **\U0001f916 AI Image Generation Prompt** *(Google Flow / Nano Banana / Gemini)*:
  Write as one full paragraph covering: subject (pose, expression, clothing, prop), background (setting, depth), lighting (source, direction, color temp), camera (lens mm, f-stop, angle), composition (rule of thirds, which quadrant clear for Arabic text overlay), color grading, style (photorealistic cinematic YouTube thumbnail), negative prompts (no AI artifacts, no uncanny valley, no extra fingers, no text in image). End: Aspect ratio: 16:9.

* **Concept 1 (High Drama / Shock)**:
  [All fields - write the full AI prompt in full]
* **Concept 2 (Medical Mystery / Curiosity)**:
  [All fields - write the full AI prompt in full, do not reference Concept 1]
* **Concept 3 (Relatable Contrast / Gym vs. Medical Reality)**:
  [All fields - write the full AI prompt in full, do not reference other concepts]

#### 3. YouTube SEO Description & Timestamps (Copy-Paste Ready)
```text
[2-3 sentence Arabic summary]

\U0001f4cc \u0631\u0648\u0627\u0628\u0637 \u0645\u0647\u0645\u0629 \u0648\u062a\u0646\u0628\u064a\u0647\u0627\u062a:
- \u0627\u0644\u0643\u0644\u0627\u0645 \u0641\u064a \u0647\u0630\u0627 \u0627\u0644\u0641\u064a\u062f\u064a\u0648 \u0644\u0623\u063a\u0631\u0627\u0636 \u0627\u0644\u062a\u0648\u0639\u064a\u0629 \u0648\u0627\u0644\u062a\u062b\u0642\u064a\u0641 \u0627\u0644\u0637\u0628\u064a \u0627\u0644\u0639\u0627\u0645 \u0648\u0644\u0627 \u064a\u063a\u0646\u064a \u0639\u0646 \u0627\u0633\u062a\u0634\u0627\u0631\u0629 \u0637\u0628\u064a\u0628\u0643 \u0627\u0644\u0645\u062e\u062a\u0635.

\u23f1\ufe0f \u0627\u0644\u0641\u0635\u0648\u0644 (Chapters):
[Complete chapter list with accurate timecodes]

[Arabic hashtags]
```

#### 4. Pinned Comment Draft
```text
[Arabic engagement question + soft CTA]
```

---

### \U0001f3ac PRODUCTION SCRIPT (Teleprompter & Filming Ready)
[Complete final script with all cues: [\u0646\u0628\u0631\u0629 \u062f\u0627\u0641\u0626\u0629], [\u0645\u064a\u0644 \u0644\u0644\u0623\u0645\u0627\u0645], [\u0646\u0638\u0631\u0629 \u0645\u0628\u0627\u0634\u0631\u0629], [VISUAL NOTE], [SFX], [ON-SCREEN TEXT]]

---

### WHAT CHANGED FROM THE ORIGINAL (Adaptation Log)
- **Structure**: 
- **Facts**: 
- **Persona & Anecdotes**: 
- **Analogies used**: 
- **Terminology kept in English**: 
- **Disclaimer**: 
- **Translation Fidelity**: 

---

### RETENTION ARCHITECTURE
| Element | Timestamp | Line (first Arabic words) |
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
| Translation naturalness | | Pass/Warn/Fail |
| Contextual alignment | | Pass/Warn/Fail |
| Transition appropriateness | | Pass/Warn/Fail |
| Transition restraint | | Pass/Warn/Fail |
| Icon/shape clarity | | Pass/Warn/Fail |
| Animation timing | | Pass/Warn/Fail |
| B-roll relevance | | Pass/Warn/Fail |
| B-roll medical accuracy | | Pass/Warn/Fail |
| Visual integration | | Pass/Warn/Fail |
| Density balance | | Pass/Warn/Fail |
| Platform fit | | Pass/Warn/Fail |
| Organic feel | | Pass/Warn/Fail |"""

    base_user_prompt_1 = f"""Compile Part 1 of the YouTube Production Deliverable.

FINAL SCRIPT (definitive version):
{state.get("refined_script", "")}

HOOK VARIATIONS:
{state.get("hook", "")}

CTA VERSIONS:
{state.get("cta_output", "")}

SOURCE ANALYSIS + MEDICAL FACT LEDGER (for adaptation log):
{state.get("source_analysis", "")}

RESTRUCTURE STRATEGY PLAN:
{state.get("strategy_plan", "")}

SCORES:
Critique Grade: {state.get("quality_grade", "")}
Dialect Score: {state.get("dialect_score", "")}
Warmth Score: {state.get("warmth_score", "")}
Fidelity Score: {state.get("fidelity_score", "")}
Medical Accuracy Pass: {state.get("medical_accuracy_pass", "")}
Naturalness Score: {state.get("naturalness_score", "")}
Contextual Alignment Score: {state.get("contextual_alignment_score", "")}

MEDICAL TOPIC: {state.get("medical_topic", "")}
PLATFORM: {state.get("target_platform", "")}
PRESENTER PROFILE: {state.get("presenter_profile", "")}

CRITICAL: For each of the 3 Thumbnail Concepts, write the AI Image Generation Prompt as a complete detailed paragraph. Do NOT abbreviate or say "same as above"."""

    part1 = ""
    correction_note_1 = ""
    for attempt in range(MAX_FINAL_PACKAGE_RETRIES + 1):
        user_prompt_1 = base_user_prompt_1
        if correction_note_1:
            user_prompt_1 += f"\n\n\u26a0\ufe0f CORRECTION REQUIRED (attempt {attempt + 1}):\n{correction_note_1}"
        part1 = call_llm(system_prompt_1, user_prompt_1, temperature=0.3, max_tokens=10000)
        issues_1 = _audit_part1(part1)
        if not issues_1:
            print(f"  [final_package Part1] OK on attempt {attempt + 1}")
            break
        print(f"  [final_package Part1] Retry {attempt + 1}: {issues_1}")
        correction_note_1 = "The previous output had these problems - fix ALL of them:\n"
        for i, issue in enumerate(issues_1, 1):
            correction_note_1 += f"  {i}. {issue}\n"
        correction_note_1 += "Regenerate the COMPLETE output fixing every issue above."

    # -----------------------------------------------------------------
    # CALL 2 - Post-Production Storyboard & Detail Sections (with retry)
    # -----------------------------------------------------------------
    system_prompt_2 = """You are a senior video editor and motion designer compiling Part 2 of a production deliverable.

Your ONLY job is to produce the following sections:

---

### \U0001f3ac INTEGRATED PRODUCTION STORYBOARD
Frame-accurate guide for the video editor. Every row = ONE atomic event. Simultaneous events get their OWN rows sharing the same timecode and cues.

Layer types: TRANSITION | ZOOM/REFRAME | B-ROLL | ICON/SHAPE | KINETIC TEXT | SFX

| # | Act | \u23f1\ufe0f Timecode | \u25b6\ufe0f START CUE (exact first Arabic words from script) | \u23f9\ufe0f END CUE (exact last Arabic words) | Layer | Detail |
|---|---|---|---|---|---|---|

MANDATORY DETAIL FORMAT (no abbreviations):
- TRANSITION: `[Type] | Dir: [direction or None] | Easing: [e.g. ease-in-expo] | Dur: [Xms]`
- ZOOM/REFRAME: `Scale [X%->Y%] | Anchor: [center/face/object] | Easing: [e.g. ease-out-back] | Dur: [Xms]`
- B-ROLL: `[\U0001f3ac Video / \U0001f5bc\ufe0f Image] | [FULL AI GENERATION PROMPT - subject, setting, lighting, lens, composition, color grade, negative prompts. NEVER write shortcut cross-references. Write the complete prompt here - at least 3 sentences.] | Dur: [Xs]`
- ICON/SHAPE: `[Element name] | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: [position]`
- KINETIC TEXT: `"[Arabic text]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: [position]`
- SFX: `[Sound name] | Trigger: [on cut/on word/on motion] | Vol: [low/med/punch]`

COVERAGE: Full video 0:00 to outro. 25-40 rows minimum. EVERY event from Transition Map, Icon Guide, and B-Roll Prompts MUST appear.

---

### \U0001f4d0 TRANSITION MAP (Detail Reference)
Reproduce the full Transition Map. Include: philosophy statement + complete table (#, Timestamp, From->To, Type, Direction, Easing, Duration, Range, Rationale, -> SB#) + act-level density summary.

---

### \U0001f3a8 ICON & SHAPE ANIMATION GUIDE (Detail Reference)
Reproduce the full Icon & Shape Animation Guide. Include: visual design system + complete element table + density map per act.

---

### \U0001f5bc\ufe0f AI B-ROLL GENERATION PROMPTS (Detail Reference)
Reproduce the full B-Roll prompt table. Include: generation settings + complete table with FULL AI prompts + density summary.

ZERO TOLERANCE: The "FULL AI Prompt" column MUST have the complete prompt for EVERY row. NEVER write "[Full Prompt in SB]", "[See SB#X]", "[Same as above]", "..." or any cross-reference. This table must work standalone without reading the storyboard.

---

### INTEGRATION DATA
```
SCRIPT PACKAGE SUMMARY FOR PRODUCTION PLANNING:

Working Title: [title]
Script Duration Estimate: [X min X sec]
Shot List Extracted: Yes - [X] B-roll sequences
Visual Complexity: [Low/Medium/High]

Filming Requirements:
- Talking head: [duration/percentage]
- B-roll: [X sequences - breakdown by type]
- Animation/graphics: [what needed]
- Special requirements: [medical diagrams, on-screen disclaimers, etc.]

Post-Production Layers:
- Transitions: [X total - hard cuts: X%, soft: X%]
- Icon/shape overlays: [X elements]
- AI B-roll prompts: [X - images: X, videos: X]
- Production quality grade: [PASS/grade]

Key Visual Notes for Editor:
1. [Most important directive]
2. [Second most important]
3. [Third most important]

Compliance Note for Reviewer:
[One sentence - what a medical/legal reviewer must check before publishing]
```"""

    base_user_prompt_2 = f"""Compile Part 2 of the YouTube Production Deliverable (Post-Production Guide).

PRODUCTION SCRIPT (use exact Arabic sentences as START CUE / END CUE in storyboard):
{state.get("refined_script", "")}

TRANSITION DESIGN MAP (reproduce in full, add SB# cross-references):
{state.get("transition_design", "")}

ICON & SHAPE ANIMATION GUIDE (reproduce in full, add SB# cross-references):
{state.get("icon_shape_animation", "")}

AI B-ROLL GENERATION PROMPTS (reproduce with FULL prompts in storyboard rows AND detail table):
{state.get("broll_prompts", "")}

PRODUCTION QUALITY CRITIQUE: {state.get("production_critique_output", "")}
PRODUCTION GRADE: {state.get("production_grade", "")}
MEDICAL TOPIC: {state.get("medical_topic", "")}
PLATFORM: {state.get("target_platform", "")}

RULES:
1. Storyboard: 25-40 rows, full video 0:00 to outro.
2. START CUE / END CUE: exact Arabic words from the Production Script above.
3. B-ROLL storyboard rows: write FULL AI prompt inline - NEVER "[Full Prompt in SB]" or any shortcut.
4. B-Roll detail table: ALSO write full AI prompt in each row - self-contained, duplicates are fine."""

    part2 = ""
    correction_note_2 = ""
    for attempt in range(MAX_FINAL_PACKAGE_RETRIES + 1):
        user_prompt_2 = base_user_prompt_2
        if correction_note_2:
            user_prompt_2 += f"\n\n\u26a0\ufe0f CORRECTION REQUIRED (attempt {attempt + 1}):\n{correction_note_2}"
        part2 = call_llm(system_prompt_2, user_prompt_2, temperature=0.3, max_tokens=12000)
        issues_2 = _audit_part2(part2)
        if not issues_2:
            print(f"  [final_package Part2] OK on attempt {attempt + 1}")
            break
        print(f"  [final_package Part2] Retry {attempt + 1}: {issues_2}")
        correction_note_2 = "The previous output had these problems - fix ALL of them:\n"
        for i, issue in enumerate(issues_2, 1):
            correction_note_2 += f"  {i}. {issue}\n"
        correction_note_2 += "Regenerate the COMPLETE output fixing every issue above."

    final_package = part1 + "\n\n---\n\n" + part2
    return {"final_package": final_package}

# --- Routing Functions ---

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

# --- Graph Wiring ---
workflow = StateGraph(PipelineState)

# Add all nodes
workflow.add_node("source_script_analyzer", source_script_analyzer)
workflow.add_node("strategy_planner", strategy_planner)
workflow.add_node("hook_writer", hook_writer)
workflow.add_node("body_restructurer", body_restructurer)
workflow.add_node("translation_fidelity_auditor", translation_fidelity_auditor)
workflow.add_node("cta_retention_writer", cta_retention_writer)
workflow.add_node("dialect_warmth_layer", dialect_warmth_layer)
workflow.add_node("fidelity_auditor", fidelity_auditor)
workflow.add_node("script_refinement", script_refinement)
workflow.add_node("self_critique", self_critique)
workflow.add_node("transition_designer", transition_designer)
workflow.add_node("icon_shape_animator", icon_shape_animator)
workflow.add_node("broll_prompt_generator", broll_prompt_generator)
workflow.add_node("production_quality_critique", production_quality_critique)
workflow.add_node("final_script_package", final_script_package)

# Linear edges (upstream pipeline)
workflow.add_edge(START, "source_script_analyzer")
workflow.add_edge("source_script_analyzer", "strategy_planner")
workflow.add_edge("strategy_planner", "hook_writer")
workflow.add_edge("hook_writer", "body_restructurer")

# Translation Fidelity Loop (Loop 1)
workflow.add_edge("body_restructurer", "translation_fidelity_auditor")
workflow.add_conditional_edges(
    "translation_fidelity_auditor",
    route_translation_fidelity,
    {
        "cta_retention_writer": "cta_retention_writer",
        "body_restructurer": "body_restructurer",
    }
)

# Linear edges (between Loop 1 and Loop 2)
workflow.add_edge("cta_retention_writer", "dialect_warmth_layer")

# Restructure Quality Loop (Loop 2)
workflow.add_edge("dialect_warmth_layer", "fidelity_auditor")
workflow.add_edge("fidelity_auditor", "script_refinement")
workflow.add_edge("script_refinement", "self_critique")
workflow.add_conditional_edges(
    "self_critique",
    route_quality_loop,
    {
        "transition_designer": "transition_designer",
        "cta_retention_writer": "cta_retention_writer",
    }
)

# Post-Production Editing Loop (Loop 3)
workflow.add_edge("transition_designer", "icon_shape_animator")
workflow.add_edge("icon_shape_animator", "broll_prompt_generator")
workflow.add_edge("broll_prompt_generator", "production_quality_critique")
workflow.add_conditional_edges(
    "production_quality_critique",
    route_production_quality,
    {
        "final_script_package": "final_script_package",
        "transition_designer": "transition_designer",
    }
)

# Terminal edge
workflow.add_edge("final_script_package", END)

# Compile
app = workflow.compile()

if __name__ == "__main__":
    import sys
    import datetime
    
    if len(sys.argv) < 2:
        print("Usage: python3 main.py <path_to_original_script>")
        sys.exit(1)
        
    script_file = sys.argv[1]
    if not os.path.exists(script_file):
        raise FileNotFoundError(f"Script file not found: {script_file}")
        
    with open(script_file, "r", encoding="utf-8") as f:
        original_script_content = f.read()

    input_file = "input_fields.json"
    if not os.path.exists(input_file):
        raise FileNotFoundError("please enter input fields file")
        
    with open(input_file, "r", encoding="utf-8") as f:
        initial_state = json.load(f)
        
    initial_state["original_script"] = original_script_content
    
    # Initialize unprovided required fields to prevent KeyError/None issues in prompts
    default_string_fields = [
        "source_format", "medical_topic", "target_platform",
        "medical_disclaimer_requirements", "cta_goal", "reference_egyptian_channels",
        "creator_profile", "revised_body", "self_critique_output", "production_critique_output",
        "transition_design", "icon_shape_animation", "broll_prompts", "disclaimer_check",
    ]
    for field in default_string_fields:
        if field not in initial_state:
            initial_state[field] = ""

    # Bug fix: explicitly initialize all integer counters so nodes never see None
    default_int_fields = {
        "translation_revision_count": 0,
        "quality_revision_count": 0,
        "production_revision_count": 0,
        "naturalness_score": 0,
        "contextual_alignment_score": 0,
        "dialect_score": 0,
        "warmth_score": 0,
        "fidelity_score": 0,
        "max_translation_revision_count": 2,
        "max_quality_revision_count": 2,
        "max_production_revision_count": 2,
    }
    for field, default in default_int_fields.items():
        if field not in initial_state:
            initial_state[field] = default

    default_bool_fields = {"medical_accuracy_pass": False}
    for field, default in default_bool_fields.items():
        if field not in initial_state:
            initial_state[field] = default
    
    # Set up session output directory
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join("output", session_id)
    os.makedirs(output_dir, exist_ok=True)
    
    session_log_path = os.path.join(output_dir, "session_log.jsonl")
    final_script_path = os.path.join(output_dir, "final_script.md")
    checkpoint_path = os.path.join(output_dir, "checkpoint.json")
    
    print(f"Starting workflow... Session ID: {session_id}")
    print(f"Logs: {output_dir}")
    print(f"Checkpoint: {checkpoint_path}")
    
    import time
    final_result = None
    cumulative_state = dict(initial_state)  # track full state for checkpointing
    
    # Using app.stream to observe execution
    with open(session_log_path, "w", encoding="utf-8") as log_file:
        for output in app.stream(initial_state):
            for key, value in output.items():
                node_start = time.time()
                
                # Merge updates into cumulative state
                cumulative_state.update(value)
                
                elapsed = time.time() - node_start
                print(f"\u2705 Node '{key}' completed. ({elapsed:.1f}s)")
                
                # Write step details to session log
                log_entry = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "node": key,
                    "state_updates": value
                }
                log_file.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                log_file.flush()
                
                # Save full cumulative checkpoint after every node
                # If the run crashes, re-running can resume from this file
                try:
                    with open(checkpoint_path, "w", encoding="utf-8") as cp:
                        json.dump(cumulative_state, cp, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"  [warning] Could not save checkpoint: {e}")
                
                # Capture final package if it's the terminal node
                if key == "final_script_package" and "final_package" in value:
                    final_result = value["final_package"]
    
    # Save the final result separately
    if final_result:
        with open(final_script_path, "w", encoding="utf-8") as f:
            f.write(final_result)
        print(f"\nWorkflow finished. Final script saved to: {final_script_path}")
    else:
        print("\nWorkflow finished, but no final package was generated.")
        print(f"Checkpoint with all intermediate outputs saved to: {checkpoint_path}")

