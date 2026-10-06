import os
import re
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, START, END
from llm_providers import LLMRouter, load_llm_config, track_node
import editing_workbook as ew
import json
import urllib.request
import urllib.parse
from dotenv import load_dotenv

# Load environment variables (e.g. OPENAI_API_KEY)
load_dotenv()

# LLM settings (see llm_providers.py and setup_models.py): main model, one
# strong model, or hybrid. The mode is chosen at startup (menu or --provider).
llm_config = load_llm_config("llm_variables.json")
llm_router = LLMRouter(llm_config)

# --- State Definition ---
class PipelineState(TypedDict):
    # Input fields
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
    video_analysis: str
    
    max_translation_revision_count: int
    max_quality_revision_count: int

    # Upstream node outputs
    source_analysis: str
    strategy_plan: str
    hook: str

    # SEO Keyword Research (grounds packaging in real search data)
    seo_primary_keyword: str
    seo_secondary_keywords: str
    seo_search_intent: str
    seo_research_output: str

    # Packaging Loop state (Loop 5 — titles, thumbnails, honesty/CTR gate)
    packaging_brainstorm_output: str
    title_options: str
    thumbnail_concepts: str
    packaging_critique_output: str
    packaging_grade: str
    packaging_revision_count: int
    max_packaging_revision_count: int

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
    truth_verification_report: str
    truth_score: int
    truth_pass: bool

    # Post-Production Editing Layers (Loop 3)
    transition_design: str
    text_animation_overlay: str
    broll_prompts: str
    production_critique_output: str
    production_grade: str
    production_revision_count: int
    max_production_revision_count: int

    # Reels/Shorts Extraction Pipeline (Loop 4)
    shorts_moments: str
    shorts_scripts: str
    shorts_captions: str
    shorts_quality_output: str
    shorts_quality_grade: str
    shorts_revision_count: int
    max_shorts_revision_count: int

# --- Node Implementation Helpers ---
def call_llm(system_prompt: str, user_prompt: str, temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
    return llm_router.call(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)

def fetch_youtube_autocomplete(query: str, hl: str = "ar", gl: str = "EG") -> list:
    """
    Hits YouTube's public autocomplete endpoint (no API key required) and returns
    the raw completion strings for a query. This is the grounding data that turns
    seo_keyword_researcher from an LLM guess into evidence-based keyword research —
    it's what real people are actually typing into YouTube search.

    Fails soft: any network or parsing error returns an empty list so the pipeline
    never crashes on a flaky connection or a blocked domain. The LLM is instructed
    to proceed with best-effort reasoning if grounding data is thin or empty.
    """
    try:
        params = urllib.parse.urlencode({
            "client": "youtube",
            "ds": "yt",
            "hl": hl,
            "gl": gl,
            "q": query,
        })
        url = f"https://suggestqueries.google.com/complete/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        # Response is a JSON array: ["query", [["suggestion", 0, [...]], ...]]
        data = json.loads(raw)
        suggestions = []
        for item in data[1]:
            text = item[0] if isinstance(item, list) else item
            # Strip any HTML bolding tags Google sometimes includes
            text = re.sub(r"<[^>]+>", "", str(text)).strip()
            if text and text not in suggestions:
                suggestions.append(text)
        return suggestions
    except Exception:
        return []

def fetch_autocomplete_grounding(queries: list) -> str:
    """Runs fetch_youtube_autocomplete across several phrasings and formats the
    combined, deduplicated result as a plain-text block for the LLM prompt."""
    all_suggestions = []
    for q in queries:
        for s in fetch_youtube_autocomplete(q):
            if s not in all_suggestions:
                all_suggestions.append(s)
    if not all_suggestions:
        return "(No live autocomplete data retrieved — network unavailable or no results. Proceed using judgment and the topic/fact ledger alone, and note this in seo_research_output.)"
    return "\n".join(f"- {s}" for s in all_suggestions[:40])

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
- **MISSING DISCLAIMER RISK**: [does this topic need a "not a substitute for medical advice" line that the source doesn't already have?]

PART 3 — EDITORIAL INTELLIGENCE BRIEF (only if Video Analysis is provided)
If a Video Analysis is provided in the user prompt, extract and log the following as a SEPARATE section — do NOT mix these into the Medical Fact Ledger:
1. CULTURAL CONTEXT ADDITIONS: Egyptian references, events, or cultural phenomena mentioned in the analysis that do NOT exist in the source script. These are APPROVED creative additions for the Egyptian rebuild — not medical claims, but contextual framing. Log each one clearly.
2. EMPHASIS REWEIGHTING: Which points from the source should be AMPLIFIED as core themes in the Egyptian version (even if they were minor in the source), and which should be COMPRESSED.
3. RISK GUARDRAILS: Specific pitfalls flagged in the analysis (e.g., "avoid fear-mongering around athletic exertion"). These are MANDATORY constraints for all downstream nodes.
4. RECOMMENDED ANALOGIES: Any Egyptian analogies suggested in the analysis.
5. ADAPTATION NOTES: Verdict, scores, and any reframing directives from the analysis.

These items are NOT medical claims and must NOT be added to the Medical Fact Ledger. They are editorial direction that governs creative decisions downstream.

If Video Analysis is provided, add to the JSON output: "editorial_brief": "Full markdown of the Editorial Intelligence Brief"

And add to the `source_analysis` markdown output:
### Editorial Intelligence Brief
[contents extracted above]

If no Video Analysis is provided, omit the Editorial Intelligence Brief entirely."""

    user_prompt = f"""Analyze the following source script and produce the Metadata, Structural Map, and Medical Fact Ledger.

ORIGINAL SCRIPT:
{state.get("original_script", "")}

VIDEO ANALYSIS INTELLIGENCE (pre-curated insights for this specific video — use to extract the Editorial Intelligence Brief. If empty, skip the Editorial Brief):
{state.get("video_analysis", "")}

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

def seo_keyword_researcher(state: PipelineState) -> dict:
    """
    Grounds YouTube SEO in real search-completion data instead of an LLM guess.
    Runs BEFORE strategy_planner so the primary keyword can also inform the hook
    (spoken keywords help YouTube's caption/ASR-based indexing, not just the
    title and description text) and the narrative structure.
    """
    medical_topic = state.get("medical_topic", "").strip()
    source_analysis = state.get("source_analysis", "")

    # Build a few phrasing variants of the topic to query autocomplete with.
    # We ask the LLM for query variants first because the raw medical_topic
    # string alone (3-8 words, English-flavored) is a poor search query —
    # real Egyptian viewers search in colloquial Arabic phrasings.
    variant_prompt = """You generate YouTube search-box query variants for autocomplete grounding.
Given a medical topic, output 4-6 short phrasings (2-5 words each) an Egyptian Arabic speaker would actually type into YouTube's search box about this topic — mix formal and colloquial phrasings, mix question and statement forms. Output ONLY a JSON array of strings, nothing else."""
    variant_user = f"Medical topic: {medical_topic}\n\nContext (for topic accuracy only):\n{source_analysis[:800]}"
    try:
        variants_raw = call_llm(variant_prompt, variant_user, temperature=0.5, max_tokens=300).strip()
        if variants_raw.startswith("```"):
            variants_raw = variants_raw.split("```")[1]
            if variants_raw.startswith("json"):
                variants_raw = variants_raw[4:]
        query_variants = json.loads(variants_raw.strip())
        if not isinstance(query_variants, list) or not query_variants:
            query_variants = [medical_topic]
    except Exception:
        query_variants = [medical_topic]

    autocomplete_grounding = fetch_autocomplete_grounding(query_variants)

    system_prompt = """You are an SEO researcher for a new, zero-authority Egyptian Arabic medical YouTube channel. Your job is to turn real search-completion data into an actionable keyword plan — NOT to invent keywords from the topic string alone. A new channel's biggest edge is ranking for specific long-tail phrases it can actually win, not competing head-on for broad terms already owned by established channels.

YOU ARE GIVEN real YouTube autocomplete completions gathered from several phrasings of this video's topic. Treat these as ground truth for what people actually type — even incomplete or oddly-phrased suggestions tell you something about real search behavior. If the grounding data is thin or empty, say so explicitly and fall back to informed judgment, but never present a guess as if it were grounded data.

YOUR OUTPUT MUST DETERMINE:
1. ONE primary keyword/phrase — the single best phrase to build the title and hook around. For a new channel, prefer a specific, winnable long-tail phrase over a broad, saturated head-term, unless the autocomplete data shows a head-term with clear, high-volume signal and no realistic alternative.
2. 6-10 secondary/long-tail keyword variants — phrases to weave naturally into the description, not stuffed.
3. Search intent — what is the person actually trying to find out or solve when they type this? (symptom-checking, myth-verification, "should I worry", treatment-seeking, curiosity/general-knowledge)
4. One explicit note on why the primary keyword was chosen over the alternatives, given this is a starting channel.

OUTPUT — ONLY valid JSON:
```json
{
  "primary_keyword": "the single best phrase",
  "secondary_keywords": ["phrase 1", "phrase 2", "..."],
  "search_intent": "1-2 sentence description of what the searcher wants",
  "research_notes": "Markdown-formatted reasoning: what the autocomplete data showed, why this primary keyword over alternatives, and any gaps in the grounding data"
}
```"""

    user_prompt = f"""MEDICAL TOPIC: {medical_topic}

QUERY VARIANTS USED FOR AUTOCOMPLETE LOOKUP:
{json.dumps(query_variants, ensure_ascii=False)}

REAL YOUTUBE AUTOCOMPLETE COMPLETIONS (ground truth search behavior):
{autocomplete_grounding}

SOURCE SCRIPT ANALYSIS (for topic/claim accuracy — do not invent keywords beyond what this content actually supports):
{source_analysis}

Produce the keyword plan. Output ONLY the JSON object."""

    response = call_llm(system_prompt, user_prompt, temperature=0.4, max_tokens=1500)
    text = response.strip()
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        secondary = data.get("secondary_keywords", [])
        secondary_str = ", ".join(secondary) if isinstance(secondary, list) else str(secondary)
        return {
            "seo_primary_keyword": data.get("primary_keyword", "") or medical_topic,
            "seo_secondary_keywords": secondary_str,
            "seo_search_intent": data.get("search_intent", ""),
            "seo_research_output": data.get("research_notes", "") or text,
        }
    except Exception:
        return {
            "seo_primary_keyword": medical_topic,
            "seo_secondary_keywords": "",
            "seo_search_intent": "",
            "seo_research_output": response,
        }

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
11. PACING STRATEGY:
    - Pacing constraint: No speaking block should exceed 30–45 seconds without a visual cut, SFX, or interactive audience question (Pattern Interrupt).
    - NOTE: Title and thumbnail packaging are NOT decided here. That work now happens downstream, after the script is fully locked, by a dedicated packaging team who can see the finished script and real SEO keyword data. Your only packaging-adjacent job is the Hook Angle above (the spoken opening) and, if useful, noting in your reasoning whether the SEO Primary Keyword below fits naturally into that spoken hook.

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
- ACT-BY-ACT WORD COUNT BUDGET (MANDATORY):
  You must calculate and mandate an explicit word count target for EACH ACT so downstream nodes do not compress a 9-minute video into a 5-minute summary.
  Example for 9 minutes (~1,250–1,350 words total at 140 wpm):
  * Hook: 80–100 words (0:00–0:45)
  * Act 1 (The Relatable Hook & Human Stakes): 200–250 words (0:45–2:00)
  * Act 2 (The Underlying Biological Machinery): 350–450 words (2:00–4:30)
  * Act 3 (The Scientific Mystery & Competing Hypotheses): 400–450 words (4:30–7:30)
  * Act 4 (The Clinical Evidence, Verdict & Solution): 250–300 words (7:30–8:45)
  * Outro & Interactive CTA: 70–90 words (8:45–9:00)

ANALOGY BANK RULES:
- Every analogy must preserve the direction and rough magnitude of the real mechanism — an analogy that makes a mild risk sound severe (or vice versa) is a fidelity failure, not a style win.
- REGISTER & TONE (العامية المصرية المثقفة البيضاء): Use clear, elegant, and universally understood Egyptian analogies that any viewer across Egypt can relate to without confusion.
- STRICTLY FORBIDDEN: Vulgar street slang (ألفاظ سوقية) and mechanic-shop / car electrical jargon (لغة ورش وميكانيكا). Never use analogies like: 'فيوزات تضرب', 'فيوز', 'تجنزر', 'تصدي', 'الماكينة والكونتاكت', 'ماس كهربائي في الضفيرة', 'تفرك'.
- PREFERRED ANALOGIES: Universal, clean everyday concepts: زرار النور، جرس الإنذار، فرامل العضلة، إشارة المرور، كارت الشحن، طابور العيش، الصيدلي.
- Do not force an analogy where a plain, warm explanation already works — analogies are a tool, not a mandate for every sentence.

SCIENTIFIC MASTERY & CONVERSATIONAL WARMTH (THE BALANCED COMMUNICATOR):
The brief requires an engaging, conversational Egyptian delivery, BUT it must NEVER dumb down the medicine or skip vital biological steps.
- Dr. Ahmed Hosney is a respected physician and master science communicator. He explains the full cascade of physiology (receptors, cellular energy, nerve reflexes, neurotransmitters, clinical trials) with absolute mastery and depth.
- Conversational delivery means changing *how* it's delivered (using relatable, dignified Egyptian analogies and conversational pacing in fluent العامية المثقفة البيضاء), NEVER stripping out the science or replacing it with vague hand-waving.
- Do NOT just say 'السلك هنج وخلاص' — explain the physiological mechanism (e.g. what sensory organ stopped inhibiting the reflex, why fatigue triggers it, what the studies prove).
- Conversational warmth does NOT mean cheap sensationalism or biological hostility. The human body and its organs must NEVER be framed as treacherous backstabbers or enemies ('جسمك بيخونك', 'المرض جه غدر', 'خيانة الأعضاء'). Medical conditions develop through physiological mechanisms, not moral malice.

OUTPUT FORMAT:

---

## RESTRUCTURE STRATEGY PLAN

### Narrative Structure
**Source structure**: [what Node 1 found]
**Structure for the Egyptian version**: [same or reordered — state which, and why]

### Word Count Target
**Target duration**: [X minutes]
**Target word count**: [X words] (range [X–X])
**Act-by-Act Word Count Budget**:
- Hook: [X words]
- Act 1: [X words]
- Act 2: [X words]
- Act 3: [X words]
- Act 4: [X words]
- Outro & CTA: [X words]

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

### Pacing & Chapters Strategy
**Pacing & Pattern Interrupt Plan**: [Every 30–45s visual/audio shift schedule]
**YouTube Chapters Plan**: Each chapter timestamp MUST align with a real content start cue / main point from the script (will be used for YouTube segments). Plan chapter names and approximate timestamps that correspond to the actual narrative beats and section transitions.
CHAPTER NAME RULES (these become the YouTube chapters viewers click on): viewer-facing, clear, and keyword-bearing — 2–7 Arabic words (max 45 characters) that say what that part answers (e.g., "ليه رسم القلب ممكن يطلع سليم؟"). Never production labels such as مقدمة / هوك / خاتمة / دعوة للاشتراك / CTA / Act / الجزء الأول — even the first chapter (0:00) names the question the video opens with, and the last names its takeaway. At least 3 chapters, first at 0:00, each at least 10 seconds. Use the SEO primary or a secondary keyword naturally in 1–2 chapter names.
| Chapter # | Approx. Timestamp | Chapter Name (Arabic) | Main Cue / Point from Script |
|---|---|---|---|
| 1 | 0:00 | [chapter name] | [the start cue or main point this chapter marks] |

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

VIDEO ANALYSIS INTEGRATION (when provided):
- The Editorial Intelligence Brief in the source analysis (extracted from the Video Analysis) is your PRIMARY creative direction. It contains Egyptian cultural context, emphasis reweighting directives, approved analogies, and risk guardrails that the source script does NOT have.
- The Egyptian angle from the Video Analysis is your PRIMARY hook angle and framing direction — build your hook angle, analogy bank, and cultural sensitivity notes around it.
- Risk-to-pre-solve items from the Video Analysis are MANDATORY guardrails — they must appear in your Cultural Sensitivity Notes and inform your De-Clinicalization Priority List.
- If the analysis suggests emphasis reweighting (amplify X, compress Y), reflect this in your Act Breakdown word count allocations.
- Cultural references from the analysis (Egyptian events, local phenomena) that don't exist in the source script are APPROVED for inclusion — add them to the Analogy Bank and Hook Angle sections. These are editorial additions, not medical claims.
- RECOMMENDED ANALOGIES from the Editorial Brief should be seeded into your Analogy Bank and adapted as needed."""

    user_prompt = f"""Build the restructure strategy plan.

SOURCE SCRIPT ANALYSIS (structural map + fact ledger + Editorial Intelligence Brief if available):
{state.get("source_analysis", "")}

VIDEO ANALYSIS INTELLIGENCE (AUTHORITATIVE creative brief — the Egyptian angle, risks, and emphasis directives below should be treated as editorial mandates, not optional suggestions. They contain cultural context that the source script does NOT have. If empty, rely on source analysis alone):
{state.get("video_analysis", "")}

MEDICAL TOPIC: {state.get("medical_topic", "")}
SEO PRIMARY KEYWORD (from real YouTube search data — consider whether it fits naturally into the hook angle; do not force it): {state.get("seo_primary_keyword", "")}
SEO SEARCH INTENT: {state.get("seo_search_intent", "")}
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

AVOID LIST:
{state.get("avoid_list", "")}

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
- STRICT BAN ON BODY-ANTAGONISM & CHEAP MELODRAMA: Never frame involuntary medical events or normal physiology as betrayal, treason, or malice (STRICTLY FORBIDDEN: 'جسمك بيخونك', 'القلب بيغدر بصاحبه', 'طعنة من جسمك', 'خيانة الأعضاء'). Hooks must arouse curiosity through genuine scientific paradoxes, surprising clinical facts, or bust misconceptions — NOT soap-opera melodrama.
- STRICT BAN ON LOW-BROW STREET SLANG & FORCED COLLOQUIALISMS: The presenter (Dr. Ahmed Hosney) is an educated physician speaking in natural, moderate Egyptian Arabic. Strictly avoid vulgar street slang or coarse idioms (e.g. 'كلبشت', 'قفشت', 'ناشف'). Use natural, context-appropriate phrasing (e.g., 'شَدّت عليك فجأة', 'انقباض مفاجئ ومؤلم').
- SEO NOTE: If an SEO Primary Keyword is provided below and it fits naturally into one of the three hooks without sounding forced, prefer working it into that hook's spoken words — YouTube's caption/transcript-based indexing weighs what's actually said, not just the title and description. This is a bonus, never a requirement: a hook that sounds natural without the keyword beats one that sounds stiff with it.

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

VIDEO ANALYSIS INTELLIGENCE (contains a pre-curated Egyptian angle and scroll-stop factor — use as primary creative direction for hook construction. The Egyptian cultural references here may NOT exist in the source script but are approved for use. If empty, rely on source analysis alone):
{state.get("video_analysis", "")}

MEDICAL TOPIC: {state.get("medical_topic", "")}
SEO PRIMARY KEYWORD (work in naturally if it fits — never force it): {state.get("seo_primary_keyword", "")}
DIALECT REGISTER: {state.get("dialect_register", "")}
CODE-SWITCHING LEVEL: {state.get("code_switching_level", "")}
VOICE STYLE: {state.get("voice_style", "")}
AUDIENCE: {state.get("audience_level", "")}

PRESENTER PROFILE:
{state.get("presenter_profile", "")}

AVOID LIST:
{state.get("avoid_list", "")}

Write three distinct hooks, each a different type, written directly in Egyptian Arabic. Score each. Recommend the strongest one."""

    response = call_llm(system_prompt, user_prompt, temperature=0.85, max_tokens=2500)
    return {"hook": response}


# --- Core Loop Nodes ---

def body_restructurer(state: PipelineState) -> dict:
    system_prompt = """You are an Egyptian health-content scriptwriter. You take an English source script that has already been deconstructed into a fact ledger, and you REBUILD it — you do not translate it — into an Egyptian Arabic talking-head + B-roll script that tells the medical content as a story or a guided explanation, never a lecture.

THE ONE RULE THAT OVERRIDES EVERYTHING ELSE: every medical claim in your output must be traceable to an item in the Medical Fact Ledger. You may reorder, re-explain, use an analogy, or cut something for time. You may NOT add a new statistic, a new causal claim, a new mechanism, or strengthen/soften a claim's certainty. If you want to say something the ledger doesn't support, don't say it.

EDITORIAL INTELLIGENCE BRIEF ADDITIONS (when provided):
The source analysis may contain an Editorial Intelligence Brief with APPROVED cultural context additions — Egyptian events, local references, and cultural framing that do NOT exist in the source script or fact ledger. These are NOT medical claims — they are contextual framing approved by the editorial process. You MAY and SHOULD incorporate them into the script as:
- Cultural anchoring (e.g., referencing an Egyptian footballer's collapse when discussing sudden cardiac death)
- Local analogies (e.g., comparing ECG screening to annual car inspection)
- Audience-relevant framing (e.g., addressing Egyptian-specific health anxieties)
These additions must NEVER introduce new medical claims, statistics, or causal mechanisms. They are storytelling and cultural framing elements only. Any medical fact you state must still trace to the Medical Fact Ledger.
Risk guardrails from the Editorial Brief are MANDATORY — if the brief says "avoid fear-mongering around athletic exertion," that constraint is as binding as any medical fidelity rule.

REBUILD PRINCIPLES:

1. THIS IS A REBUILD, NOT A TRANSLATION: Do not go sentence by sentence through the English script rendering each line in Arabic. Work from the Restructure Strategy Plan's act breakdown and analogy bank. Some English sections may compress into one Egyptian line; a single English stat may expand into a short story beat using the analogy bank. The order of ideas may change from the source if the Strategy Plan calls for it.

2. WRITE IN EGYPTIAN ARABIC FROM THE START — never draft in English internally and convert. Think in the dialect.

3. SCIENTIFIC DEPTH WITH WARM CONVERSATIONAL DELIVERY: Every item on the Strategy Plan's De-Clinicalization Priority List gets rebuilt with clarity. But NEVER dumb down the science or skip physiological steps. The presenter (Dr. Ahmed Hosney) explains the real biological mechanisms (reflexes, receptors, cell energy, clinical data) with deep medical authority, using warm, clever Egyptian analogies (السهل الممتنع). De-clinicalization does NOT mean sensationalist hostility towards the human body ('غدر / خيانة / بيخونك') and does NOT mean vague colloquial hand-waving ('السلك هنج وخلاص'). Explain the 'how' and 'why' thoroughly so the audience learns real, empowering science.

4. USE THE ANALOGY BANK, DON'T OVER-USE IT: pull analogies from the Strategy Plan where they were mapped to a specific fact. Don't force an analogy onto every sentence — plain warm Arabic works fine for simple points; save the analogies for the genuinely hard-to-grasp mechanisms.

5. FOLLOW THE TERMINOLOGY RETENTION TABLE: for every technical/medical term, use the exact decision the Strategy Plan made — keep it in English, Arabize it, or say it first-mention-both. Do not re-decide this term by term as you write; the table already did that work. A term marked "Keep in English" should sit in English inside an otherwise fully Egyptian sentence, exactly the way an Egyptian doctor or health creator would actually say it (e.g., "الـ insulin resistance بتخلي الجسم..." is correct Egyptian Arabic with an English term inside it — it is NOT translation risk).

6. SPOKEN EGYPTIAN ARABIC GRAMMAR & REGISTER (العامية المصرية المثقفة البيضاء المعتدلة):
   - Clear, dignified, highly articulate, effortless to understand and pronounce for any native speaker (واضحة، معتدلة، مفهومة للجميع، وسهلة النطق بدون تكلف).
   - Mutual understanding & respect: The viewer must feel peer-to-peer warmth from an educated doctor who speaks naturally and accessibly, WITHOUT feeling like they are sitting with someone from the street using low-brow, coarse slang (بدون كلام سوقي زيادة عن اللزوم).
   - Context-appropriate vocabulary without unnecessary slang stuffing (الكلمات مناسبة تماماً للسياق بدون حشو ألفاظ عامية فجة أو إقحام تشبيهات شارع ركيكة لمجرد إثبات العامية).
   - No فصحى conjugation or vocabulary, ever, for the surrounding Arabic (بيعمل not يفعل، هروح not سأذهب، عايز/محتاج not أريد، دلوقتي not الآن، بس not لكن، إزاي not كيف، ده/دي not هذا/هذه، أيوه not نعم).
   - Use discourse markers naturally: يعني / طب / خلاص / بصراحة / أصل / على فكرة.
   - STRICTLY FORBIDDEN: Vulgar street slang (ألفاظ سوقية), coarse street idioms, and mechanic-shop jargon:
     * Never use: "كلبشت" / "بتكلبش" / "تكلبش" / "قفشت" (use: "شَدِّت فجأة", "قفلت", "انقبضت ومبتفكش", "شَدّت عليك فجأة").
     * Never use: "موضوع ناشف" / "كلام ناشف" (use: "موضوع معقد وممل", "كلام أكاديمي جامد", "شرح نظري معقد").
     * Never use: "بتغلس علينا" (use: "بتتعبنا", "بتشد فجأة بدون سبب واضح").
     * Never use: "خناقة كروية بين العلماء" (use: "انقسام في الآراء الطبية", "خلاف ونقاش علمي بين العلماء").
     * Never use: "العصب بيتجنن" (use: "العصب بيستثار بزيادة", "بيفرط في إرسال الإشارات").
     * Avoid over-repeating "بتهنج" (use: "بتعطل", "بتفقد التوازن", "الإشارات بتتلخبط").
     * Never use: "حتة لحمة" (use: "كتلة عضلية عادية", "نسيج عضلي بسيط").
     * Never use: "وجع رخم" (use: "ألم مفاجئ ومزعج", "وجع شديد").
     * Never use: "مابتخرفش" (use: "بتشتغل بدقة عالية", "من غير أي خلل").
     * Never use: "قرصت عليها في التمرين" (use: "أجهدتها بزيادة", "حملت عليها في التمرين").
     * Never use mechanic jargon: "فيوزات تضرب", "فيوز", "تجنزر", "تصدي", "ماس كهربائي في الضفيرة", "تفرك".
   - Short clauses. Conversational stitching, not essay structure.
   - IMPORTANT: keeping an English technical term per Rule 5 does NOT violate this rule. The failure mode this rule guards against is English *sentence structure* and *word order* leaking into the Arabic — not English *nouns* appearing where the Terminology Table calls for them.

7. VISUAL & AUDIO STORYBOARD (FOR THE VIDEO EDITOR):
   Every scene/section must contain rich multimedia markers:
   - `[VISUAL NOTE: ...]` Describe exact on-screen visual, camera angle (Close-up, Medium shot), B-roll footage, or custom animation/diagram.
   - `[SFX: ...]` Sound design cue (e.g. `[SFX: Heartbeat accelerates]`, `[SFX: Deep cinematic Whoosh]`, `[SFX: Pop sound for text]`, `[SFX: Glitch effect]`).

   TEXT OVERLAY MARKERS — use these 5 specific types (the text animation designer and editor depend on them):
   - `[TOP-RIGHT POPUP: English Term → Arabic translation or clarification]` — appears as a small designed popup in the top-right corner. Use for EVERY English technical/medical term the first time it appears in each section, showing the English word with its Arabic translation or brief clarification. Also use for notes or clarifications. Example: `[TOP-RIGHT POPUP: Cramp → شد عضلي / تقلص لا إرادي]`, `[TOP-RIGHT POPUP: ATP → أدينوسين ثلاثي الفوسفات (وقود الخلية)]`.
   - `[TEXT OVERLAY TITLE: Arabic headline text]` — full-width title in center of screen with a dark semi-transparent overlay behind it over the video. Use for important section headlines and chapter transitions. Must be in Arabic. Example: `[TEXT OVERLAY TITLE: غرفة المحركات: إزاي العضلة بتنقبض؟]`.
   - `[WARNING BOX: Arabic warning text]` — designed warning/alert box with ⚠️ icon, appears at center-bottom of screen. Use for medical disclaimers, red-flag symptoms, and safety warnings. Example: `[WARNING BOX: ⚠️ لو الشد العضلي بيتكرر بشكل غير طبيعي — لازم تزور دكتور متخصص]`.
   - `[QUOTE BOX: Arabic quote text]` — designed elegant quote callout box at center-bottom. Use sparingly for the most impactful, memorable sentences — the lines a viewer would screenshot. Example: `[QUOTE BOX: "جسمك محتاج طاقة عشان يرتاح... بنفس القدر اللي محتاجه عشان يتحرك"]`.
   - `[KINETIC TEXT: text]` — animated kinetic typography for key terms, statistics, equations, and emphasis phrases. Example: `[KINETIC TEXT: Risk Factors ≠ Direct Cause]`, `[KINETIC TEXT: ATP = كارت الشحن]`.

   MANDATORY: Go through the Terminology Retention Table and ensure EVERY English term that appears in each section gets a `[TOP-RIGHT POPUP]` marker the FIRST time it appears in that section. This is how the viewer learns what each English term means.

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

10. ACT-BY-ACT WORD COUNT DISCIPLINE & SUBSTANTIVE DEPTH:
   - You MUST fulfill the word count budget specified for each Act in the Strategy Plan.
   - Do NOT write brief, high-level summaries or rush through the explanation.
   - Dive deep into the medical nuances, physiological steps, and case studies so each act achieves its target word count (e.g., Act 1: 200–250 words, Act 2: 350–450 words, Act 3: 400–450 words, Act 4: 250–300 words).
   - Total spoken dialogue across all acts must realistically match the target duration at ~135–145 words/minute (e.g., ~1,200–1,350 words for 9 minutes).

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

VIDEO ANALYSIS INTELLIGENCE (contains APPROVED cultural context to incorporate and MANDATORY risk guardrails. If empty, rely on source analysis and strategy plan alone):
{state.get("video_analysis", "")}

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
- SENSATIONALIST IDIOM TRANSCREATION CHECK: Watch out for English idioms like 'when your body turns on you', 'out of the blue', or 'the silent killer' being transcreated into sensationalist Egyptian betrayal melodrama ('جسمك بيخونك', 'المرض جه غدر', 'طعنة في الضهر'). These must be rendered through natural clinical reality (e.g., 'من غير أعراض واضحة', 'فجأة ومن غير ما تحس'), NOT biological antagonism.

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

AVOID LIST:
{state.get("avoid_list", "")}

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
            "revised_body": (data.get("revised_body", "") or "").strip() or state.get("revised_body", ""),
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
AVOID LIST: {state.get("avoid_list", "")}

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

REGISTER REQUIREMENTS (العامية المصرية المثقفة البيضاء المعتدلة):
- Apply Educated, Moderate Conversational Egyptian per `dialectRegister`. The language must be dignified, articulate, universally understood, and effortless to pronounce for any native speaker (سهلة الفهم، واضحة، وسلسة النطق بدون تكلف).
- The viewer must feel mutual understanding and respect from an educated doctor who speaks naturally and accessibly, without feeling like they are sitting with someone from the street using coarse, vulgar slang (بدون كلام سوقي زيادة عن اللزوم).
- Context-appropriate vocabulary without unnecessary slang stuffing (الكلمات مناسبة تماماً للسياق بدون حشو ألفاظ عامية فجة أو إقحام تشبيهات شارع ركيكة لمجرد إثبات العامية).
- STRICTLY FORBIDDEN: Vulgar street slang (ألفاظ سوقية), coarse street idioms, and mechanic-shop jargon:
  * "كلبشت" / "بتكلبش" / "تكلبش" / "قفشت" -> استبدلها بـ: "شَدّت فجأة" / "قفلت" / "انقبضت ومبتفكش" / "شَدّت عليك فجأة"
  * "موضوع ناشف" / "كلام ناشف" -> استبدلها بـ: "موضوع معقد وممل" / "شرح أكاديمي جامد" / "كلام نظري معقد"
  * "بتغلس علينا" -> استبدلها بـ: "بتتعبنا" / "بتشد فجأة بدون سبب واضح"
  * "خناقة كروية بين العلماء" -> استبدلها بـ: "انقسام في الآراء الطبية" / "خلاف ونقاش علمي بين العلماء"
  * "العصب بيتجنن" -> استبدلها بـ: "العصب بيستثار بزيادة" / "بيفرط في إرسال الإشارات"
  * "حتة لحمة" -> استبدلها بـ: "كتلة عضلية عادية" / "نسيج عضلي بسيط"
  * "وجع رخم" -> استبدلها بـ: "ألم مفاجئ ومزعج" / "وجع شديد"
  * "مابتخرفش" -> استبدلها بـ: "بتشتغل بدقة عالية" / "من غير أي خلل"
  * "قرصت عليها في التمرين" -> استبدلها بـ: "أجهدتها بزيادة" / "حملت عليها في التمرين"
  * تجنب الإفراط في كلمة "بتهنج" -> استبدلها بـ: "بتعطل" / "بيحصل فيها خلل" / "بتفقد التوازن"
  * مصطلحات ورش وميكانيكا السيارات: ممنوع "فيوزات تضرب"، "فيوز"، "تجنزر"، "تصدي"، "ماس كهربائي في الضفيرة"، "تفرك".
  * التشبيهات البديلة المعتمدة: "زرار النور علّق"، "جرس الإنذار شغال ومش راضي يسكت"، "فرامل العضلة"، "إشارة المرور".

Respect the Strategy Plan's Terminology Retention Table — do not "correct" a term the table marked "Keep in English" back into an Arabic neologism, and do not flag it as a dialect problem. IMPORTANT DISTINCTION: the dialect score evaluates grammar, conjugation, word order, and sentence rhythm — not whether specific technical nouns are in English. A sentence with a kept-English term inside fully Egyptian grammar (e.g., "الـ insulin resistance بتخلي الجسم يتعب أكتر") is exactly what a 10/10 dialect score should look like for Light/Moderate code-switching — it is not a deduction. What DOES cost dialect points is English sentence structure or word order leaking into the Arabic (a calque), regardless of which language any individual noun is in.

DIMENSION 2 — THE BALANCE OF SCIENTIFIC MASTERY & CONVERSATIONAL WARMTH
This is the core identity of Dr. Ahmed Hosney: an authoritative medical professional and elite science communicator who speaks like a warm, smart friend chatting with you over coffee.

AVOID TWO OPPOSING FAILURE MODES:
1. The Cold Academic (التقرير الأكاديمي الجاف): Lecture-hall enumerations ("هناك 3 أسباب... أولاً"), stiff textbook phrasing, passive voice, or clinical detachment.
2. The Superficial Dumb-Down (التسطيح المخل): Dumbing down the medicine, omitting key biological mechanisms, or replacing science with vague colloquial hand-waving (e.g., just saying "السلك مهنج وخلاص" without explaining the actual physiological reflex, feedback loop, or fatigue mechanism).

THE GOLDEN STANDARD (السهل الممتنع):
- Keep the full, sophisticated scientific mechanism intact (explain the receptors, the calcium release, the inhibitory reflexes, the clinical studies) so the viewer feels enlightened and respects the presenter's deep medical mastery.
- Deliver that sophisticated science through warm, effortless Egyptian spoken storytelling, sharp cultural analogies, and genuine doctor-patient camaraderie.
- Reassurance and empathy markers (متقلقش، إنت مش لوحدك في ده، تعالوا نفهم الحكاية) where appropriate.
- NO SENSATIONALIST MELODRAMA OR BETRAYAL TROPES: Warmth means camaraderie and reassurance, NOT sensationalist tabloid drama. Actively weed out and penalize clickbait phrases like 'جسمك بيخونك', 'المرض جه غدر', 'خيانة الأعضاء', 'طعنة'. If the script portrays organs as enemies or traitors, deduct points on Warmth/De-clinicalization and rewrite those lines into objective, empathetic medical science.

A script can be 100% dialect-accurate and still fail warmth if it's a textbook translated into perfect Cairene grammar, and it fails authority if it strips away the science. Both failure modes are equally disqualifying.

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

AVOID LIST:
{state.get("avoid_list", "")}

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
7. SENSATIONALIST DISTORTION & BODY-ANTAGONISM CHECK: Flag any lines that introduce melodramatic anthropomorphism or hostility towards the body (e.g. 'جسمك بيخونك', 'غدر وخيانة', 'طعنة', 'قلبه يخذله'). The script must communicate scientific mechanism, not biological malice. Any such wording must be flagged for correction and costs fidelity points if present.

For each issue found, cite the specific script line and the specific ledger item it conflicts with (or note "no matching ledger item" for invented claims).

VERDICT:
- `medical_accuracy_pass: true` only if there are ZERO invented claims, ZERO strengthened claims, ZERO dropped caveats that materially change meaning, ZERO sensationalist body-antagonism distortions, and the disclaimer is present and adequate
- NOTE ON ANECDOTE ATTRIBUTION & PERSONA: Reframing the original author's personal clinical experiences into third-person expert case studies (e.g., 'استشاري قلب في إنجلترا استقبل 5 حالات...' instead of 'أنا استقبلت 5 حالات') and speaking from the voice of `presenterProfile` is a VALID persona adaptation. It is NOT an invented claim or fidelity violation as long as the underlying medical facts (the 5 cases, the STEMI, the normal cholesterol, etc.) remain 100% faithful to the ledger.
- Minor wording differences that don't change the ledger's meaning or certainty level are NOT violations — you are checking substance, not word-for-word matching (this script is a rebuild, not a translation)
- `fidelity_score` (1–10): 10 = perfect fidelity, no notes. Below 9 should be rare for a script otherwise ready to ship — this is the strictest gate in the workflow because it's the one with real-world safety stakes

EDITORIAL INTELLIGENCE BRIEF EXEMPTIONS:
If the source analysis contains an Editorial Intelligence Brief, the following are NOT fidelity violations:
- Egyptian cultural references, events, or local phenomena used as contextual framing (e.g., "Ahmed Refaat's collapse" as an anchor for sudden cardiac death discussion) — these are approved editorial additions, not invented medical claims
- Local analogies approved in the brief (e.g., comparing ECG screening to annual car inspection)
- Emphasis reweighting that amplifies a minor source point into a major Egyptian theme
These are editorial/creative additions, not medical claims. Only flag them if they introduce a NEW medical claim, statistic, or causal mechanism that isn't in the fact ledger. Cultural storytelling framing ≠ medical claim invention.

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

VIDEO ANALYSIS INTELLIGENCE (if present, cultural references from this are approved editorial additions — do not flag as invented claims):
{state.get("video_analysis", "")}

RESTRUCTURE STRATEGY PLAN (analogy bank + disclaimer plan, for checking analogies and disclaimer placement):
{state.get("strategy_plan", "")}

MEDICAL DISCLAIMER REQUIREMENTS: {state.get("medical_disclaimer_requirements", "")}

AVOID LIST:
{state.get("avoid_list", "")}

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
4. WORD COUNT & DEPTH: total spoken dialogue must realistically achieve the word count budget from the Strategy Plan (~135–145 words/minute). Do NOT compress or truncate scientific mechanisms into brief summaries.
5. CONSISTENCY & REGISTER: one voice throughout (Dr. Ahmed Hosney — authoritative physician with warm, natural Egyptian conversational delivery in Educated Moderate Egyptian Arabic / العامية المثقفة البيضاء المعتدلة). The script must sound natural and direct without forced slang stuffing (بدون حشو ألفاظ عامية فجة). Cleanse any low-brow street slang or coarse idioms (like كلبشت، قفشت، موضوع ناشف، بتغلس، بيتجنن، حتة لحمة، وجع رخم، مابتخرفش، قرصت عليها) and mechanic-shop metaphors (فيوزات، تجنزر، تصدي).
6. MANDATORY MENTIONS present
7. RETENTION MECHANIC CHECK: verify that the open loop planted in the hook is explicitly mentioned/referenced in Act 3 before resolution in Act 4; disclaimer present; CTA placed correctly. All retention cues MUST exist as spoken dialogue in the text.
8. VISUAL NOTE CHECK: notes describe what's SHOWN, not what's said

Do NOT re-litigate dialect, warmth, or medical fidelity — those were handled by dedicated nodes. Only fix flow, pacing, and structural assembly.

OUTPUT:

```
SCRIPT: [Video Title in Egyptian Arabic]
Duration Target: [X min]
Word Count: [actual spoken words] / [target words] ([X%])
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

def medical_truth_verifier(state: PipelineState) -> dict:
    system_prompt = """You are an uncompromising Chief Medical Officer (CMO) and Senior Scientific Fact-Checker. Your sole, vital mission is to audit the complete assembled Egyptian Arabic script and verify the ABSOLUTE SCIENTIFIC TRUTH, FACTUAL CORRECTNESS, and MEDICAL ACCURACY of every single claim, number, mechanism, practical advice, and analogy.

You evaluate the script against two sources of ground truth:
1. The Medical Fact Ledger from Node 1 (ensuring no claims were invented, altered, or exaggerated).
2. Established Clinical Medical Science & Physiology (ensuring everything stated is objectively true, biologically accurate, and evidence-based in medical science).

AUDIT DIMENSIONS (CHECK EVERY LINE & SECTION):

1. PHYSIOLOGICAL & BIOCHEMICAL MECHANISMS:
   - Are the biological cascades accurate? (e.g., Actin/Myosin binding, Troponin/Tropomyosin gatekeeping, Calcium release from sarcoplasmic reticulum and ATP-dependent reuptake).
   - Are the neurological reflexes explained with clinical truth? (e.g., muscle fatigue triggering Golgi tendon organ disinhibition, leading to alpha motor neuron hyper-excitability and involuntary sustained contraction).
   - Flag any biological hand-waving, vague pseudoscientific explanations, or misattributed organ functions.

2. EMPIRICAL PRECISION & NUMERICAL INTEGRITY:
   - Verify every statistic, clinical case study (e.g., the 3cm intramuscular hematoma case in the UK), study cohort, and meta-analysis mentioned.
   - Confirm that numbers are exact and zero fabricated data or exaggerated figures exist.

3. SCIENTIFIC TRUTH OF MYTH-BUSTING (NO NEW MYTHS):
   - When debunking popular myths (e.g., that nocturnal cramps are purely caused by dehydration or potassium deficiency from bananas), ensure the debunking is scientifically sound and nuanced.
   - Acknowledge that while dehydration/electrolytes can be minor contributors in extreme endurance exercise, sports medicine and clinical trials show they are NOT the root cause of standard nocturnal cramps.
   - Ensure the alternative scientific explanation (neuromuscular fatigue theory) is stated with peer-reviewed accuracy.

4. FACTUAL VALIDITY OF EVERY ANALOGY:
   - Does each analogy accurately reflect biological reality?
   - Reject any analogy that misrepresents physiology, implies biological malice (like 'body betrayal'), or uses absurd mechanical falsehoods.

5. ACTIONABLE ADVICE & CLINICAL SAFETY:
   - Is all practical advice (e.g., passive acute stretching, resistance training, conditioning) medically evidence-based and safe?
   - Are the red flags and medical disclaimers present, accurate, and prominent? (e.g., recurrent non-exercise cramps requiring urgent evaluation for neuropathy, diabetic complications, liver disease, or ALS).
   - Is the magnesium meta-analysis finding accurately conveyed? (i.e., multiple Cochrane reviews / clinical trials found magnesium is no better than placebo for idiopathic nocturnal cramps in general adults, despite popular marketing).

6. ABSOLUTE ZERO TOLERANCE FOR HALLUCINATIONS OR VAGUE COLLOQUIAL DISTORTIONS:
   - Check that no scientific claim was diluted, distorted, or invented during dialect adaptation.

EDITORIAL CONTEXT VS. MEDICAL CLAIMS:
Cultural references, Egyptian events, and local analogies that were added as contextual framing (not medical claims) from an Editorial Intelligence Brief are NOT subject to medical truth verification. Only verify the medical/scientific substance of claims, not the cultural storytelling wrapper around them. For example, if the script mentions "Ahmed Refaat's collapse" as a cultural anchor for sudden cardiac death, you are not verifying the details of that event — you are verifying the medical claims about sudden cardiac death itself.

SCORING & VERDICT:
- `truth_score` (1–10):
  * 10 = Every single claim, number, mechanism, and piece of advice is 100% verified, scientifically true, and evidence-based.
  * 9 = Flawless scientific accuracy with only minor nuances in phrasing needed.
  * < 9 = Contains inaccurate biology, exaggerated claims, wrong numbers, or unverified folk science.
- `truth_pass`: `true` ONLY if `truth_score >= 9` AND there are ZERO false or unverified medical statements.
- `verified_script`: If any minor inaccuracies, exaggerated numbers, or imprecise physiological explanations are found, provide the surgically corrected script with all facts healed to 100% truth while preserving the warm Egyptian spoken dialect. If flawless, return the input script unchanged.

OUTPUT — ONLY valid JSON:
```json
{
  "truth_pass": true,
  "truth_score": 10,
  "truth_verification_report": "Markdown table: | Section / Line | Medical / Scientific Claim | Truth Status (✅ Verified True / ⚠️ Needs Nuance / ❌ Inaccurate) | Scientific Evidence / Reality Check | Action Taken / Correction |. Followed by a concise clinical summary.",
  "verified_script": "The full script with every factual inaccuracy corrected to 100% scientific truth. Same act headers, cues, and dialogue format."
}
```"""

    user_prompt = f"""Rigorously verify the scientific correctness and factual truth of every claim in this assembled script.

CURRENT ASSEMBLED SCRIPT:
{state.get("refined_script", "")}

SOURCE SCRIPT ANALYSIS + MEDICAL FACT LEDGER (source truth):
{state.get("source_analysis", "")}

ORIGINAL SOURCE SCRIPT:
{state.get("original_script", "")}

RESTRUCTURE STRATEGY PLAN:
{state.get("strategy_plan", "")}

PRESENTER PROFILE:
{state.get("presenter_profile", "")}

MEDICAL DISCLAIMER REQUIREMENTS:
{state.get("medical_disclaimer_requirements", "")}

AVOID LIST:
{state.get("avoid_list", "")}

Examine every single line, claim, mechanism, number, study, analogy, and advice. Output ONLY valid JSON."""

    response = call_llm(system_prompt, user_prompt, temperature=0.2, max_tokens=12000)
    
    text = response.strip()
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        
        truth_score = int(data.get("truth_score", 0))
        truth_pass = bool(data.get("truth_pass", False))
        verified_script = (data.get("verified_script", "") or "").strip()
        
        return {
            "truth_verification_report": data.get("truth_verification_report", "") or text,
            "truth_score": truth_score,
            "truth_pass": truth_pass and (truth_score >= 9),
            "refined_script": verified_script if verified_script else state.get("refined_script", "")
        }
    except Exception:
        return {
            "truth_verification_report": response,
            "truth_score": 7,
            "truth_pass": False,
            "refined_script": state.get("refined_script", "")
        }

def self_critique(state: PipelineState) -> dict:
    system_prompt = """You are a senior quality auditor for this workflow. You evaluate the assembled script against professional standards AND this workflow's three specialist audits (dialect/warmth, medical fidelity, scientific truth verifier), then produce a critique report plus a fully revised script.

AUDIT CRITERIA — score each (1–10), flag CRITICAL / WARNING / MINOR:

1. HOOK QUALITY (Critical) — grabs attention fast, open loop, specific, works sound-off
2. NARRATIVE ARC (Critical) — clear structure, no filler, matches the planned energy curve
3. SPOKEN LANGUAGE (Critical) — natural aloud, no written/translated patterns
4. REAL WORD COUNT & PACING (Critical) — Check the actual length of the spoken script. Do NOT trust the header label blindly. For an 8–10 minute video, the script must contain ~1,150–1,350 words of actual spoken dialogue across all acts. A ~700–800 word script is only ~5 minutes and is a severe failure of pacing and duration! If under-length, expand the physiological explanations, clinical nuances, and case study details in revised_script.
5. OPEN LOOP COMPLETION (Critical) — Confirm that the open loop planted in the hook (e.g. the 3cm hematoma case) is explicitly sustained/referenced in Act 3 before being resolved in Act 4. Both the plant, the mid-video reference, and the resolution must actually be present as spoken dialogue in the script text.
6. DISCLAIMER (Critical) — present, adequate, appropriately placed and warmly phrased
7. CTA QUALITY (Warning) — natural, after loop resolution, matches ctaGoal
8. DIALECT AUTHENTICITY & REGISTER (Critical) — pull the score from the Dialect & Warmth Layer input; re-verify it holds in the final assembly. Ensure the dialect is Educated Moderate Egyptian (العامية المثقفة البيضاء المعتدلة) that is smooth, easy to understand, and effortless to pronounce for any native listener. Deduct heavily if there is any vulgar/coarse street slang (e.g., 'كلبشت', 'قفشت', 'ناشف', 'بتغلس', 'بيتجنن', 'حتة لحمة', 'وجع رخم', 'مابتخرفش', 'قرصت عليها') or mechanic-shop jargon (e.g. 'فيوزات', 'تجنزر', 'تصدي', 'ماس كهربائي في الضفيرة').
9. WARMTH & HUMAN CAMARADERIE (Critical) — verify doctor-patient warmth, reassurance, and empathy
10. MEDICAL FIDELITY (Critical, hardest gate) — pull `medical_accuracy_pass` and `fidelity_score` from the Fidelity Auditor input; if it failed, this is an automatic non-A regardless of everything else
11. PERSONA INTEGRITY (Critical) — verify that the script strictly speaks from the presenter's persona (`presenterProfile`) and contains NO remnants of the original author's name, foreign identity, or falsely claimed personal clinical actions
12. AVOID LIST & ANTI-SENSATIONALISM (Critical) — verify the script strictly honors the avoid_list. Confirm there is NO sensationalist melodrama or body-antagonism (e.g., 'جسمك بيخونك', 'غدر', 'خيانة', 'طعنة', 'قلبه يخذله') AND NO low-brow street slang or mechanic-shop jargon (e.g., 'كلبشت', 'قفشت', 'موضوع ناشف', 'بتغلس', 'بيتجنن', 'حتة لحمة', 'وجع رخم', 'مابتخرفش', 'قرصت عليها', 'فيوزات', 'تجنزر', 'تصدي'). Any occurrence of these tropes is an automatic failure for Warmth and requires immediate rewriting in revised_script.
13. SCIENTIFIC MASTERY & DEPTH BALANCE (Critical) — verify the script strikes Dr. Ahmed Hosney's balance: authoritative, precise medical science (receptors, reflexes, biochemical cascades) delivered through warm Egyptian storytelling and clever analogies (السهل الممتنع). Deduct points if the medicine is stripped away or over-simplified into vague colloquial hand-waving.
14. SCIENTIFIC TRUTH & FACT VERIFICATION (Critical, hardest gate) — pull `truth_pass` and `truth_score` from the Medical Truth Verifier audit below. If `truth_pass` is false or `truth_score` < 9, this is an automatic non-A regardless of everything else. Check the Truth Verification Report to ensure all claims, mechanisms, numbers, and advice are confirmed true and accurate.

HARD GATES — grade can only be "A" if ALL of the following hold:
- dialect_authenticity_score >= 8
- warmth_score >= 8
- medical_accuracy_pass = true AND fidelity_score >= 9
- truth_pass = true AND truth_score >= 9 (Zero unverified, false, or exaggerated scientific claims)
- strictly zero occurrences of prohibited sensationalist tropes ('بيخونك', 'غدر وخيانة', etc.), low-brow street slang ('كلبشت', 'قفشت', 'موضوع ناشف', 'بتغلس', 'بيتجنن', 'حتة لحمة', 'وجع رخم', 'مابتخرفش', 'قرصت عليها'), or mechanic-shop slang ('فيوزات', 'تجنزر', 'تصدي', etc.) in revised_script
- actual spoken script length >= 85% of target (minimum 1,150 words for an 8–10 minute target video)
- open loop planted in hook is explicitly referenced in Act 3 and resolved in Act 4 in the script text

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

SCIENTIFIC TRUTH & FACT VERIFICATION AUDIT (source):
{state.get("truth_verification_report", "")}
Truth Score: {state.get("truth_score", 0)}/10 | Truth Pass: {state.get("truth_pass", False)}

DIALECT & WARMTH SCORES (source):
{state.get("dialect_warmth_output", "")}

MEDICAL FIDELITY AUDIT (source):
{state.get("fidelity_audit_output", "")}

RESTRUCTURE STRATEGY PLAN:
{state.get("strategy_plan", "")}

SOURCE SCRIPT ANALYSIS + MEDICAL FACT LEDGER:
{state.get("source_analysis", "")}

AVOID LIST:
{state.get("avoid_list", "")}

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
        truth_score = int(state.get("truth_score", 10))
        truth_pass = bool(state.get("truth_pass", True))

        if grade in ["A+", "A"]:
            if dialect_score < 8 or warmth_score < 8:
                grade = "B"
            if not medical_pass or fidelity_score < 9 or not truth_pass or truth_score < 9:
                grade = "C"

        return {
            "quality_grade": "PASS" if grade in ["A", "A+"] else grade,
            "self_critique_output": data.get("critique_report", "") or text,
            "refined_script": (data.get("revised_script", "") or "").strip() or state.get("refined_script", ""),
            "quality_revision_count": state.get("quality_revision_count", 0) + 1,
            "dialect_score": dialect_score,
            "warmth_score": warmth_score,
            "fidelity_score": fidelity_score,
            "medical_accuracy_pass": medical_pass,
            "truth_score": truth_score,
            "truth_pass": truth_pass
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
            "medical_accuracy_pass": state.get("medical_accuracy_pass", False),
            "truth_score": state.get("truth_score", 0),
            "truth_pass": state.get("truth_pass", False)
        }

# --- Packaging Loop Nodes (Loop 5 — titles, thumbnails, honesty/CTR gate) ---
# Runs AFTER the script is locked (self_critique PASS), so packaging is built
# against what the video actually delivers, not guessed at before it exists.

def packaging_creative_director(state: PipelineState) -> dict:
    system_prompt = """You are a YouTube packaging creative director for a starting Egyptian Arabic medical channel. The script for this video is finished and locked — your job is to brainstorm WIDE before anything gets narrowed down to finalists.

WHY THIS STEP EXISTS: the old process defaulted to the same 3 title flavors (Curiosity / Shock-Paradox / Warning) on every single video, which caps creativity and makes the channel feel formulaic. Your job is to break that by pulling from a much wider set of proven hook mechanisms and grounding every one of them in something the finished script actually contains.

BRAINSTORM 6-8 CONCEPTS, each pulling from a DIFFERENT mechanism. Do not default to the same 2-3 mechanisms every time — rotate across this bank based on what actually fits this video's content:
- Curiosity gap / open loop ("what happens when...")
- Hyper-specificity (exact numbers, exact timeframes — "3 علامات... خلال 10 دقايق")
- Second-person direct address ("لو بتعمل كذا... وقف")
- Contrarian / myth-bust ("اللي فاكره... غلط")
- Transformation / before-after
- Insider-info framing (what doctors know that patients don't)
- Relatable story open (a patient scenario from the script)
- Bold, medically-honest claim/warning

THE ONE RULE THAT OVERRIDES EVERYTHING: every single concept must cite the EXACT fact, moment, or line from the finished script that it is honestly built on, plus a one-line "promise" of what the viewer will actually learn or see resolved. A concept that can't point to a real anchor in the script gets discarded here, before it ever reaches a title. This is what keeps creative expansion from turning into overpromising.

For each concept also give: the mechanism it uses, and a rough visual idea for how it could look as a thumbnail (not a full prompt yet — just the core image idea).

OUTPUT FORMAT:

---

## PACKAGING BRAINSTORM (6-8 concepts)

### Concept 1 — [Mechanism name]
**Anchor in the script**: [exact fact/line/moment this is built on]
**Promise to the viewer**: [one sentence — what they'll actually learn/see]
**Rough title direction**: [a working phrase, not final]
**Rough visual idea**: [core thumbnail image idea, 1-2 sentences]

[Repeat for all 6-8 concepts, each a genuinely different mechanism]

### Strongest 3-5 for the Generator
[List which concepts should move forward and a one-line reason each — prioritize a mix of mechanisms, not 3 variations on the same one]"""

    user_prompt = f"""Brainstorm packaging concepts for this finished video.

FINAL LOCKED SCRIPT:
{state.get("refined_script", "")}

MEDICAL TOPIC: {state.get("medical_topic", "")}
SEO PRIMARY KEYWORD: {state.get("seo_primary_keyword", "")}
SEO SEARCH INTENT: {state.get("seo_search_intent", "")}

RESTRUCTURE STRATEGY PLAN (for tone/audience context):
{state.get("strategy_plan", "")}

AVOID LIST:
{state.get("avoid_list", "")}

Brainstorm 6-8 concepts across distinct mechanisms, each anchored to something real in the script above. Recommend the strongest 3-5 to carry forward."""

    response = call_llm(system_prompt, user_prompt, temperature=0.9, max_tokens=3000)
    return {"packaging_brainstorm_output": response}


def packaging_generator(state: PipelineState) -> dict:
    system_prompt = """You are a senior YouTube packaging producer. You take a wide creative brainstorm and narrow it into 3-5 finalist titles plus 3 fully-specified thumbnail concepts, ready for production.

TITLE CONSTRAINTS (non-negotiable):
- Put the payoff and, where it fits naturally, the SEO primary keyword within the first ~60 characters. YouTube's hard cap is 100 characters, but search results and suggested-video tiles typically only display 50-70 characters before truncating (mobile is the tighter end of that range), so anything after that point is often invisible at the moment someone decides whether to click.
- STRICTLY FREE of sensationalist body-antagonism or melodrama tropes ('بيخونك', 'غدر', 'خيانة', 'يخذله', 'طعنة'). If a "shock/paradox" mechanism is used, it must be a genuine counter-intuitive medical reality or myth-bust — never betrayal-by-the-body framing.
- Each finalist title must be traceable to a DIFFERENT concept from the brainstorm — do not submit 3-5 minor rewordings of the same idea.

THUMBNAIL CONSTRAINTS (non-negotiable):
- Produce exactly 3 thumbnail concepts, each fully specified — no abbreviations, no "same as concept 1", no placeholder fields.
- Each thumbnail's text overlay must carry DIFFERENT information from its paired title, not repeat it. Redundant text/title pairs waste the curiosity-gap mechanism — the two should combine to create curiosity, not duplicate each other.
- Actively AVOID the standard medical-channel visual clichés unless a specific concept is genuinely the strongest option for that idea: glowing red heart, hand clutching chest, doctor pointing at an X-ray/scan, red arrows overlaid on a body part, generic DNA helix, stethoscope close-up. If you do use one of these, justify explicitly why it's the strongest choice here, not just the default.
- Every thumbnail must include a legibility note: confirm the text overlay and focal element are readable at small size (mobile feed thumbnail size), not just at full resolution.

OUTPUT FORMAT:

---

## PACKAGING FINALISTS

### Title Options (3-5, each from a different brainstorm concept)
| # | Title | Character Count | Mechanism | Anchored fact from script |
|---|---|---|---|---|
| 1 | | | | |

### Thumbnail Concepts (exactly 3)
Each MUST have every field below — no abbreviations, no cross-references to another concept.

- **Visual Scene**: Detailed background, setting, props, lighting.
- **Presenter Expression / Gesture**: Exact face, hands, gaze.
- **Bold Arabic Text Overlay (3-4 words)**: `"[TEXT]"` — must carry different information from the paired title.
- **Focal Element / Prop**: Primary visual hook.
- **Color Palette**: Primary + accent colors.
- **Cliché Check**: [Which standard medical-thumbnail trope, if any, was considered and avoided/justified]
- **Legibility at Small Size**: [Confirm text and focal element remain clear at mobile thumbnail size]
- **🤖 AI Image Generation Prompt** *(Google Flow / Nano Banana / Gemini)*:
  One full paragraph covering: subject (pose, expression, clothing, prop), background (setting, depth), lighting (source, direction, color temp), camera (lens mm, f-stop, angle), composition (rule of thirds, which quadrant is clear for Arabic text overlay), color grading, style (photorealistic cinematic YouTube thumbnail), negative prompts (no AI artifacts, no uncanny valley, no extra fingers, no text in image). End: Aspect ratio: 16:9.

* **Concept 1**: [Which title # it pairs with] — [all fields in full]
* **Concept 2**: [Which title # it pairs with] — [all fields in full]
* **Concept 3**: [Which title # it pairs with] — [all fields in full]"""

    correction_note = state.get("packaging_critique_output", "") if state.get("packaging_revision_count", 0) > 0 else ""

    user_prompt = f"""Narrow this brainstorm into finalists.

PACKAGING BRAINSTORM:
{state.get("packaging_brainstorm_output", "")}

FINAL LOCKED SCRIPT (for fact-checking anchors):
{state.get("refined_script", "")}

SEO PRIMARY KEYWORD: {state.get("seo_primary_keyword", "")}
SEO SECONDARY KEYWORDS: {state.get("seo_secondary_keywords", "")}

REVISION COUNT: {state.get("packaging_revision_count", 0)}
{"PREVIOUS CRITIQUE — YOU MUST RESOLVE THESE SPECIFIC ISSUES:" + chr(10) + correction_note if correction_note else ""}

Produce 3-5 finalist titles and exactly 3 fully-specified thumbnail concepts."""

    response = call_llm(system_prompt, user_prompt, temperature=0.7, max_tokens=4000)
    # Split the two sections out for downstream nodes that only need one or the other.
    title_section, _, thumb_section = response.partition("### Thumbnail Concepts")
    return {
        "title_options": title_section.strip(),
        "thumbnail_concepts": ("### Thumbnail Concepts" + thumb_section).strip() if thumb_section else response,
    }


def packaging_honesty_ctr_auditor(state: PipelineState) -> dict:
    system_prompt = """You are the honesty and CTR quality gate for YouTube packaging on a medical channel. Your job is the mechanical, evidence-based version of "don't fake it" — not a banned-word list, but a hard check on whether each title's implied promise is actually paid off by the finished script.

SCORE EACH FINALIST TITLE 1-10 ON:
1. CURIOSITY STRENGTH — does it create a real open loop or specific enough claim that the brain wants closed?
2. SPECIFICITY — numbers, timeframes, named mechanisms beat vague claims
3. SEO KEYWORD PRESENCE — is the primary keyword (or a close natural variant) present, ideally in the first ~60 characters?
4. TITLE/THUMBNAIL COMPLEMENTARITY — for the paired thumbnail, does the overlay text add NEW information rather than repeat the title?
5. VISUAL DIFFERENTIATION — does the paired thumbnail avoid generic medical-channel clichés, or justify using one?
6. PROMISE-DELIVERY MATCH (hard gate) — read the finished script. Does it actually contain, and pay off by roughly when implied, whatever the title claims? A title promising "3 signs" needs 3 signs in the script. A title implying a surprising twist needs that twist to actually land. This is the core anti-overpromising check — score it strictly.

HARD GATE: if PROMISE-DELIVERY MATCH < 9 for ANY finalist that would otherwise be your top pick, the packaging is NEEDS_REVISION regardless of every other score. An overpromising title/thumbnail combination gets the click but tanks average view duration and triggers "not interested" signals — for a channel still building algorithmic trust, that costs more than a weaker but honest title.

ALSO CHECK (fail if violated):
- Any finalist title contains sensationalist body-antagonism/melodrama tropes ('بيخونك', 'غدر', 'خيانة', 'يخذله', 'طعنة') → NEEDS_REVISION
- No finalist title exceeds ~100 characters, and at least the top recommended title fits its payoff within ~60 characters
- All 3 thumbnail concepts are fully specified (no abbreviations, no cross-references)

REVISION MODE: if packaging_revision_count > 0, verify the SPECIFIC issues from your previous critique were actually fixed, not superficially reworded.

OUTPUT — ONLY valid JSON:
```json
{
  "packaging_grade": "PASS",
  "packaging_critique_report": "Full audit in markdown: per-title score table (all 6 criteria), the Promise-Delivery Match reasoning for the top title against the actual script, thumbnail complementarity/cliché notes, and a specific fix list for anything below threshold",
  "recommended_title": "The single strongest finalist title, to carry forward as the primary Video Title",
  "top_promise_delivery_score": 0
}
```"""

    correction_note = f"\n\nREVISION COUNT: {state.get('packaging_revision_count', 0)} — verify prior issues were actually fixed." if state.get("packaging_revision_count", 0) > 0 else ""

    user_prompt = f"""Audit these packaging finalists against the actual finished script.

TITLE OPTIONS:
{state.get("title_options", "")}

THUMBNAIL CONCEPTS:
{state.get("thumbnail_concepts", "")}

FINAL LOCKED SCRIPT (the ground truth for Promise-Delivery Match):
{state.get("refined_script", "")}

SEO PRIMARY KEYWORD: {state.get("seo_primary_keyword", "")}
{correction_note}

Score every finalist. Apply the hard gates. Output ONLY the JSON object."""

    response = call_llm(system_prompt, user_prompt, temperature=0.3, max_tokens=3000)
    text = response.strip()
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())

        grade = str(data.get("packaging_grade", "")).strip().upper()
        top_pd_score = int(data.get("top_promise_delivery_score", 0))

        # Hard gate: Promise-Delivery Match must be >= 9 regardless of the LLM's self-reported grade
        if top_pd_score < 9:
            grade = "NEEDS_REVISION"
        if grade != "PASS":
            grade = "NEEDS_REVISION"

        return {
            "packaging_grade": grade,
            "packaging_critique_output": data.get("packaging_critique_report", "") or text,
            "packaging_revision_count": state.get("packaging_revision_count", 0) + 1,
        }
    except Exception:
        return {
            "packaging_grade": "NEEDS_REVISION",
            "packaging_critique_output": response,
            "packaging_revision_count": state.get("packaging_revision_count", 0) + 1,
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

### Flags for Text Animation/Overlay Designer
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

def text_animation_overlay_designer(state: PipelineState) -> dict:
    system_prompt = """You are a text animation and overlay designer specializing in educational medical YouTube content. Your job is to design all ON-SCREEN TEXT-BASED ELEMENTS — kinetic typography, popup callout boxes, lower-thirds, text overlays, animated list reveals, highlight/underline animations, arrows/connectors, progress indicators — and CONDITIONAL DRAWING ANIMATIONS (whiteboard sketches, progressive diagram builds) with precise animation specifications that a motion designer or video editor can implement directly.

YOUR ROLE: The script is locked. The transition map is locked. You add the text overlay and animation layer — the elements that appear ON TOP of the video (talking head or B-roll) to anchor key information, reinforce medical terms, visualize statistics, and guide the viewer's comprehension.

THE 5 PRIMARY OVERLAY TYPES — every text element you design MUST be classified as one of these:

1. **TOP-RIGHT POPUP** (English Term Translation / Clarification / Notes):
   - A compact, designed popup box in the TOP-RIGHT corner of the screen.
   - Purpose: Show English technical/medical terms alongside their Arabic translation, or provide brief clarifications and notes.
   - Visual treatment: Rounded rect with semi-transparent dark background, white text, accent-colored English term, smaller Arabic translation/note below.
   - MANDATORY RULE: Every English technical term that appears in the script MUST get a TOP-RIGHT POPUP the first time it appears in each section. Pull terms from the Terminology Retention Table in the Strategy Plan. This is how the audience learns what each English term means.
   - Example: English term "Cramp" → popup shows: "Cramp" (bold, accent color) / "شد عضلي / تقلص لا إرادي" (Arabic explanation).
   - Example: English term "ATP" → popup shows: "ATP" / "أدينوسين ثلاثي الفوسفات (وقود الخلية)".

2. **TEXT OVERLAY TITLE** (Section Headlines / Chapter Markers):
   - Full-width title in CENTER of screen with a dark semi-transparent overlay behind it covering the video.
   - Purpose: Mark important sections, chapters, and narrative transitions. Must be in Arabic.
   - Visual treatment: Large bold Arabic text, centered, with dark overlay (70-80% opacity) spanning the full width behind the text. Clean, cinematic feel.
   - Use at the start of each major script section/chapter.
   - Example: "غرفة المحركات: إزاي العضلة بتنقبض؟"

3. **WARNING / ALERT BOX** (Medical Warnings / Disclaimers / Red Flags):
   - Designed warning box at CENTER-BOTTOM of screen with ⚠️ icon.
   - Purpose: Medical disclaimers, red-flag symptom alerts, safety warnings, contraindications.
   - Visual treatment: Rounded rect with amber/red accent border, ⚠️ icon on the left, Arabic warning text. Semi-transparent dark background.
   - Example: "⚠️ لو الشد العضلي بيتكرر بشكل غير طبيعي — لازم تزور دكتور متخصص"

4. **QUOTE BOX** (Impactful Sentences / Key Takeaways):
   - Designed elegant quote callout box at CENTER-BOTTOM of screen.
   - Purpose: Highlight the most memorable, screenshot-worthy sentences — the lines that capture the video's key insight.
   - Visual treatment: Rounded rect with quotation mark accent (「」or "), subtle accent-colored left border, Arabic quote text in slightly larger font. Elegant, not loud.
   - Use SPARINGLY — maximum 2-3 per video. Only for genuinely impactful lines.
   - Example: "جسمك محتاج طاقة عشان يرتاح... بنفس القدر اللي محتاجه عشان يتحرك"

5. **KINETIC TEXT** (Animated Terms / Stats / Equations / Emphasis):
   - Animated kinetic typography that appears on-screen to reinforce spoken words.
   - Purpose: Visually anchor key statistics, medical equations, term definitions, and emphasis phrases.
   - Visual treatment: Bold text with dynamic entry animation (pop, typewriter, scale-up), accent color for key terms, complementary color for supporting text.
   - Example: "Risk Factors ≠ Direct Cause", "ATP = كارت الشحن", "3 سنتيمتر نزيف داخلي"

ADDITIONAL ELEMENT TYPES (use alongside the 5 primary types):

6. **Lower-Thirds**: Speaker credentials, section labels. Bottom-left or bottom-right.

7. **Animated List Reveals**: Step-by-step processes appearing one item at a time.

8. **Arrows & Connectors**: Cause→effect flow indicators.

9. **Progress Indicators**: Section markers, chapter counters.

9. **Drawing Animations (CONDITIONAL — only when needed)**: Whiteboard-style sketch reveals, progressive diagram builds, sketch overlay annotations, animated handwriting. Use these ONLY when the medical mechanism being explained is genuinely difficult to follow verbally — when "making the invisible visible" meaningfully aids comprehension. NOT every script needs drawing animations.

   When to include: Complex multi-step medical mechanisms, cause-effect chains with 3+ steps, anatomical processes invisible to the eye, healthy vs. unhealthy comparisons.
   When NOT to include: Simple concepts explained clearly with words, warm/emotional moments, sections where B-roll or text overlays already provide adequate visual support.

   For each drawing animation, provide HIGHLY DETAILED specs:
   - **Draw style**: whiteboard / sketch overlay / handwriting / progressive diagram
   - **Subject description**: exactly what is being drawn — specific organ, process, or concept, in enough detail that an animator or AI generator can reproduce it without guessing
   - **Build sequence**: step-by-step what draws first, second, third — each step tied to a specific narration beat with the exact spoken line quoted
   - **Visual elements list**: enumerate every line, shape, label, arrow, and annotation with relative positions (e.g., "heart outline center-screen → left ventricle fills with red → arrow from LV to aorta labeled 'blood flow' → plaque buildup drawn on artery wall in yellow")
   - **Duration**: total draw time per step + hold time before next step + total animation duration
   - **Color palette**: specific hex colors for each element (e.g., "arteries: #E63946, veins: #457B9D, labels: white on dark background")
   - **Placement & size**: full-screen whiteboard moment vs. corner overlay on talking head, exact screen region and approximate size ratio
   - **Reference description**: plain-language description of what the finished drawing should look like, as if describing it to someone who can't see it
   - **Whiteboard image prompt** (English, for Google Flow image generation): plain white whiteboard, clean black marker line art, at most 2 accent colors, the FINISHED composition, and NO text, letters, numbers or labels anywhere in the image
   - **Draw-on video prompt** (English, image-to-video in Google Flow with the image above as the start frame): a hand with a black marker draws the elements in the build-sequence order, static camera, white background, no text appears, ~8 seconds
   - **Labels to add in Premiere**: every label/annotation as on-screen text (Arabic or English) with the exact cue words where it appears — labels are never part of the AI image

ANIMATION SPECIFICATION — for EVERY text element, describe:
- **Entry animation**: How it appears (fade-in, slide-in-left, slide-in-up, scale-up, pop, typewriter, draw-on). Include duration in ms and easing function (sine/quad/cubic/quart/quint/expo/circ/back) and direction (in/out/in-out).
- **Hold duration**: How long it stays fully visible on screen (in seconds).
- **Exit animation**: How it disappears (fade-out, slide-out-right, scale-down, dissolve). Include duration and easing.
- **Screen position**: Where on the frame (e.g., "bottom-left, 10% from edge", "center-frame", "upper-right corner").

DESIGN PRINCIPLES — NON-NEGOTIABLE:

1. EVERY ELEMENT SERVES COMPREHENSION: No decorative elements. Every text overlay, animation, and callout must help the viewer understand or remember the medical content. If it doesn't pass the "why is this here?" test, cut it.

2. ORGANIC INTEGRATION: Every element must feel native to this video. If a section works perfectly without any overlays — just the presenter talking with natural energy — then LEAVE IT CLEAN. Adding overlays to a moment that doesn't need them makes the video feel overproduced.

3. RESPECT THE TRANSITION MAP: Do NOT animate entries during scene transitions — time your entries to land on STABLE FRAMES after transitions settle.

4. LESS IS MORE: Maximum 2 simultaneous on-screen elements at any time (excluding the base video layer). A cluttered frame distracts from the medical content.

5. CONSISTENT VISUAL LANGUAGE: All elements share a unified color palette (2-3 colors max), consistent corner radius/line weight, and the same animation "family."

6. TEXT IS ALWAYS IN EGYPTIAN ARABIC: All on-screen text must match the script's dialect register. English technical terms follow the Terminology Retention Table.

7. DENSITY FOLLOWS CONTENT COMPLEXITY:
   - Dense medical mechanism explanation → more visual anchors (callouts, kinetic text, potentially drawing animations)
   - Warm personal story/analogy → minimal or NO overlays (let the performance breathe)
   - Statistics/numbers → always get on-screen text reinforcement
   - Disclaimers → always get on-screen text
   - Emotional/empathetic moments → ZERO overlays

8. DRAWING ANIMATIONS ARE CONDITIONAL: Only include them when the medical mechanism genuinely benefits from visual clarification. When included, specs must be detailed enough for an animator or AI tool to execute without ambiguity.

9. ANIMATION TIMING MUST BE READABLE: Text elements must hold long enough to be read (minimum 2 seconds for short text, 4 seconds for longer callouts).

10. COORDINATE WITH B-ROLL: If a B-roll visual is planned for a timestamp, position elements in areas of the frame that won't compete with the B-roll's focal point.

REVISION MODE: if production_revision_count > 0, apply only what the production critique flagged. Don't redesign unflagged elements.

OUTPUT FORMAT:

---

## TEXT ANIMATION & OVERLAY GUIDE

### Visual Design System
**Color palette**: [primary, secondary, accent — hex codes]
**Font**: [suggested font family for on-screen text]
**Corner radius**: [Xpx for all rounded shapes]
**Animation family**: [the "feel" — e.g., "smooth and minimal with ease-out-quad entries and fade exits"]

### Text Element Table
| # | Timestamp | Element Type | Content (text) | ▶️ START CUE (Arabic) | ⏹️ END CUE (Arabic) | Screen Position | Entry Animation | Hold (s) | Exit Animation | Linked to Transition # | Purpose |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0:48 | Lower-third | "د. أحمد حسني" | أهلاً بيكم، أنا دكتور | أنا دكتور أحمد حسني | Bottom-left, 8% margin | slide-in-left, 400ms, ease-out-cubic | 4s | fade-out, 300ms | After T#3 | Presenter intro |
| 2 | 0:55 | TOP-RIGHT POPUP | "Cramp → شد عضلي / تقلص لا إرادي" | الشد العضلي أو الـ Cramp | مَسِلِمش منه | Top-right, 5% margin | slide-in-right, 300ms, ease-out-cubic | 3s | fade-out, 200ms | — | English term translation |

### Drawing Animations (if applicable)
[For each drawing animation, provide the full detailed spec as described above. If no drawing animations are needed for this script, state: "No drawing animations needed — the medical concepts in this script are adequately served by text overlays and B-roll."]

### Density Map
| Act | Total Elements | Simultaneous Max | Drawing Animations | Notes |
|---|---|---|---|---|
| Hook & Setup | | | | |
| Build / Explain | | | | |
| Peak / Payoff | | | | |
| CTA / Close | | | | |

### Flags for B-Roll Prompt Generator
- [List any elements that need visual "space" in the B-roll composition]"""

    user_prompt = f"""Design all on-screen text animations, overlays, and conditional drawing animations for this finalized script.

FINALIZED SCRIPT:
{state.get("refined_script", "")}

TRANSITION DESIGN MAP (respect timing — don't animate over transitions):
{state.get("transition_design", "")}

RESTRUCTURE STRATEGY PLAN (terminology retention, act breakdown):
{state.get("strategy_plan", "")}

B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
TARGET PLATFORM: {state.get("target_platform", "")}

Read the script and transition map together. Classify every element as one of the 5 primary types: TOP-RIGHT POPUP, TEXT OVERLAY TITLE, WARNING/ALERT BOX, QUOTE BOX, or KINETIC TEXT. For every English technical term in the script, generate a TOP-RIGHT POPUP with its Arabic translation. For every section/chapter transition, generate a TEXT OVERLAY TITLE. For medical disclaimers and warnings, use WARNING/ALERT BOX. For the most impactful sentences, use QUOTE BOX. For statistics and key terms, use KINETIC TEXT. Include START CUE and END CUE (exact first/last Arabic words from the script that trigger each element). For complex medical mechanisms that are hard to follow verbally, consider whether a drawing animation would genuinely help comprehension."""

    # Only include revision mode block when we're actually revising
    if state.get("production_revision_count", 0) > 0:
        user_prompt += f"""

---

## MODE: REVISION PASS (Pass #{state.get("production_revision_count", 0)})

CRITICAL INSTRUCTION: Read the Production Critique Report below carefully.
- If the critique does NOT mention TEXT ANIMATION & OVERLAY CLARITY or DRAWING ANIMATION EFFECTIVENESS as failing — output your previous guide exactly as it was, word for word. Do not change a single element.
- If the critique DID flag specific elements: apply ONLY those exact fixes. Leave all other elements untouched.

PREVIOUS TEXT ANIMATION & OVERLAY GUIDE:
{state.get("text_animation_overlay", "")}

PRODUCTION CRITIQUE REPORT:
{state.get("production_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.5, max_tokens=7000)
    return {"text_animation_overlay": response}


def broll_prompt_generator(state: PipelineState) -> dict:
    system_prompt = """You are an AI-generation prompt engineer specializing in creating text prompts for AI image and video generation tools (Gemini, Google Flow, and similar). Your job is to produce COPY-PASTE-READY prompts that generate professional B-roll footage for a medical/health YouTube video.

YOUR ROLE: The script is locked. The transition map and text animation overlays are locked. For every moment in the script that calls for B-roll (marked with [VISUAL NOTE: ...] or implied by the transition map), you produce a detailed text prompt that an AI generation tool can use to create the exact visual needed.

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

5. B-ROLL CONTENT SCOPE — PREFER EDUCATIONAL/MEDICAL, BUT NOT RESTRICTED TO IT:
   B-roll should preferably serve educational and medical comprehension, but is not limited to clinical imagery. Everyday life scenes are welcome when they ground an analogy or support the narrative. The test is RELEVANCE to the message, not strict medical context.
   - PREFERRED: Anatomical diagrams, medical mechanism animations, clinical/hospital/pharmacy settings, medical equipment close-ups, data visualizations, Egyptian everyday health scenes
   - ALLOWED: Broader contextual scenes (everyday Egyptian life, food, workplace) when they ground an analogy or support the narrative
   - AVOID unless directly relevant: Generic lifestyle/cinematic stock that doesn't connect to the medical message
   - NEVER: Abstract/artistic visuals with no connection to the content

6. PEOPLE IN B-ROLL — PREFER MALE SUBJECTS:
   When B-roll includes people (patients, everyday scenes, demonstrations), prefer male subjects. Avoid generating female figures unless the medical topic specifically requires it (e.g., pregnancy, breast cancer, gynecological conditions).

7. COMPOSITIONAL AWARENESS — COORDINATE WITH TEXT OVERLAYS:
   Read the Text Animation & Overlay Guide. If a text overlay or callout is planned for a specific timestamp:
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

12. PROMPT LENGTH AND STRUCTURE: Each prompt should be 50-100 words. Structure: main subject → composition/framing → lens/camera feel → lighting → mood → motion (for video) → anti-AI-detection keywords → negative prompts (what to avoid).

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
| # | Timestamp | ▶️ START CUE (exact first Arabic words) | ⏹️ END CUE (exact last Arabic words) | Script Context (what's being said) | Type | Main Cue (most important visual element that MUST be present) | AI Generation Prompt | Composition Notes | Linked to Transition # | Linked to Overlay # | Duration (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0:08 | [exact first Arabic words from script at this moment] | [exact last Arabic words when B-roll ends] | Hook — dramatic medical scenario | 🎬 Video | "Cinematic close-up of a young athletic man's chest with ECG electrode patches attached, hospital emergency room setting, cool-tinted overhead fluorescent lighting, shallow depth of field at f/2.0, heart monitor in soft-focus background, slight camera dolly forward, tense atmosphere. Shot on 50mm cinema lens, natural film grain, realistic skin texture and electrode adhesive detail. Avoid: plastic skin, AI artifacts, hyper-symmetry, unnatural lighting." | Focal point center-left (callout box upper-right at this timestamp) | After T#2 (zoom-in) | Before I#1 | 4s |

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
- [ ] Composition accounts for planned text animation/overlay positions
- [ ] Motion B-roll direction matches transition flow
- [ ] Every prompt serves the MESSAGE being spoken at that moment
- [ ] People in B-roll are preferably male unless topic requires otherwise
- [ ] B-roll preferably serves educational/medical comprehension"""

    user_prompt = f"""Generate AI-ready B-roll prompts for every visual moment in this script.

FINALIZED SCRIPT:
{state.get("refined_script", "")}

TRANSITION DESIGN MAP (match B-roll framing to transition energy and type):
{state.get("transition_design", "")}

TEXT ANIMATION & OVERLAY GUIDE (leave compositional space for overlays):
{state.get("text_animation_overlay", "")}

RESTRUCTURE STRATEGY PLAN (analogy bank — only use local visual context where the script explicitly references local analogies):
{state.get("strategy_plan", "")}

SOURCE SCRIPT ANALYSIS + MEDICAL FACT LEDGER (medical accuracy check for B-roll imagery):
{state.get("source_analysis", "")}

B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
TARGET PLATFORM: {state.get("target_platform", "")}

For each [VISUAL NOTE] in the script and each B-roll transition in the transition map, produce a detailed AI generation prompt. Mark each as 🖼️ Image or 🎬 Video. Coordinate with text overlay positions. Remember: prefer educational/medical contexts for B-roll, and use male subjects when people appear.

PRODUCTION CRITIQUE REPORT:
{state.get("production_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.6, max_tokens=6000)
    return {"broll_prompts": response}

def production_quality_critique(state: PipelineState) -> dict:
    system_prompt = """You are a senior post-production supervisor and visual systems integrator. You evaluate three editing layers — Transition Map, Text Animations/Overlays/Drawing Animations, and AI B-Roll Prompts — both individually AND as an integrated visual system that must harmonize with the finalized script.

Your job is NOT to evaluate the script itself (that was handled by the script quality loops). You evaluate whether the post-production layers will produce a professional, cohesive, INVISIBLE-feeling viewing experience.

THE OVERRIDING PRINCIPLE: The best post-production is post-production the viewer never consciously notices. The video should feel like it was always this polished — not like effects were layered on top. If any transition, text animation, or B-roll moment draws attention to ITSELF rather than to the content, it has failed. A viewer who thinks "nice zoom effect" has been pulled out of the medical story. A viewer who simply stays engaged without knowing why — that's success.

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

3. TEXT ANIMATION & OVERLAY CLARITY (Critical):
   - Does every text element serve information delivery, or are there decorative elements?
   - Can every text element be read in its hold duration?
   - Is the visual design system consistent? (Same colors, same animation family)
   - Are there sections where overlays were added but the content works better without them?
   - Are drawing animations (if present) genuinely clarifying complex mechanisms, or decorative?

4. ANIMATION TIMING (Critical):
   - Are text animation/overlay elements properly sequenced with transitions? (No entries during scene changes)
   - Are there moments where too many things animate simultaneously?
   - Do animations respect the "Flags for Text Animation/Overlay Designer" from the transition map?

5. B-ROLL RELEVANCE & EDUCATIONAL FOCUS (Critical):
   - Does each B-roll prompt produce imagery that genuinely serves the MESSAGE being spoken?
   - Are prompts specific enough to produce usable results from AI generation?
   - Is B-roll preferably oriented toward educational/medical contexts?
   - Are people in B-roll preferably male (unless topic requires otherwise)?
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
  "text_animation_clarity_score": 0,
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

TEXT ANIMATION & OVERLAY GUIDE:
{state.get("text_animation_overlay", "")}

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
            "text_animation_clarity": int(data.get("text_animation_clarity_score", data.get("icon_clarity_score", 0))),
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
    """Audit Part 1 (Script + Packaging) for quality issues.

    NOTE: title/thumbnail quality (count, completeness, anti-cliché, honesty)
    is no longer checked here — that's the job of packaging_honesty_ctr_auditor,
    which gates the packaging BEFORE it ever reaches this assembly step. This
    audit now only needs to confirm those approved sections were copied through
    and that the production script itself is clean.
    """
    issues = []

    # Approved packaging sections must actually be present (copy-through check,
    # not a quality check — quality was already gated upstream).
    if "\U0001f916 AI Image Generation Prompt" not in text:
        issues.append(
            "MISSING APPROVED THUMBNAIL CONCEPTS: The approved thumbnail concepts (with AI Image Generation Prompts) "
            "were not copied through from the packaging loop output. Copy them through in full, unmodified."
        )

    # Production script must be present
    if "\u0646\u0628\u0631\u0629" not in text and "HOOK" not in text:
        issues.append("MISSING PRODUCTION SCRIPT: The full teleprompter-ready script is absent.")

    # Extract only the production script content to check forbidden terms.
    # Title/thumbnail wording is no longer generated here (it's copied through
    # already-approved packaging output), so this is now purely a safety net
    # on the SPOKEN script — the same content self_critique already checked,
    # re-verified here in case anything drifted during final assembly.
    content_lines = []
    in_script = False
    for line in text.splitlines():
        line_strip = line.strip()
        if "### 🎬 PRODUCTION SCRIPT" in line or "### PRODUCTION SCRIPT" in line:
            in_script = True
            continue
        elif in_script and line_strip.startswith("###"):
            in_script = False
        
        if in_script:
            content_lines.append(line)

    # Filter out compliance explanations, disclaimers, or echoed rules
    clean_lines = [
        l for l in content_lines 
        if not any(k in l.lower() for k in ["free of", "strictly forbidden", "forbidden", "title rules", "rules:", "قواعد", "خالي من", "تنبيه"])
    ]
    target_dialogue = "\n".join(clean_lines) if clean_lines else text

    # Check for forbidden body-antagonism / melodrama clichés in the script
    forbidden_terms = ["بيخونك", "يخونك", "غدر", "خيانة", "طعنة", "يخذله", "بيطعنك"]
    found_forbidden = [t for t in forbidden_terms if t in target_dialogue]
    if found_forbidden:
        issues.append(
            f"SENSATIONALIST BODY-ANTAGONISM DETECTED: Found forbidden melodrama/betrayal term(s): {found_forbidden}. "
            "Never frame the human body or organs as backstabbers or traitors. "
            "Replace with grounded scientific paradoxes, surprising clinical facts, or myth-busting."
        )

    # Check for forbidden mechanic-shop jargon or low-brow street slang
    slang_forbidden = [
        "كلبشت", "بتكلبش", "تكلبش", "قفشت", "موضوع ناشف", "طبي ناشف", "كلام ناشف",
        "بتغلس", "بيتجنن", "تتجنن", "مابتخرفش", "بتخرف", "حتة لحمة", "وجع رخم", "قرصت عليها",
        "فيوزات", "فيوز", "تجنزر", "يجنزر", "يصدي", "تصدي", "تفرك"
    ]
    found_slang = [s for s in slang_forbidden if s in target_dialogue]
    if found_slang:
        issues.append(
            f"VULGAR / STREET SLANG DETECTED: Found inappropriate street slang or mechanic term(s): {found_slang}. "
            "The presenter is Dr. Ahmed Hosney — use Educated, Moderate Egyptian Arabic (العامية المصرية المثقفة البيضاء المعتدلة). "
            "Do NOT use low-brow street slang like 'كلبشت' or 'موضوع ناشف' or mechanic jargon like 'فيوزات'. "
            "Replace with clean, natural phrasing (e.g., 'شَدّت فجأة', 'قفلت', 'انقبضت ومبتفكش', 'موضوع معقد وممل', 'زرار النور علّق', 'جرس الإنذار شغال')."
        )

    # Check retention architecture table desync with normalized Arabic matching
    try:
        script_match = re.search(r"###\s*🎬?\s*PRODUCTION SCRIPT.*?\n(.*?)(\n###\s*WHAT CHANGED|\Z)", text, re.DOTALL)
        if script_match and "### RETENTION ARCHITECTURE" in text:
            script_body = script_match.group(1)
            
            def _normalize_ar(s: str) -> str:
                s = re.sub(r"[أإآ]", "ا", s)
                s = re.sub(r"ة", "ه", s)
                s = re.sub(r"ى", "ي", s)
                return re.sub(r"\s+", " ", s).strip()

            clean_script = re.sub(r"\[.*?\]", " ", script_body)
            clean_script = re.sub(r"[\.…\"'“”«»\(\)\[\]،؛؟!:ـ\-\n]", " ", clean_script)
            normalized_script = _normalize_ar(clean_script)

            ret_block = text.split("### RETENTION ARCHITECTURE")[1].split("###")[0]
            missing_cues = []
            for line in ret_block.split("\n"):
                if line.startswith("|") and not line.startswith("|---|") and "Timestamp" not in line and "Element" not in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4:
                        elem, cue = parts[1], parts[3]
                        clean_cue = re.sub(r"[\.…\"'“”«»\(\)\[\]،؛؟!:ـ\-]", " ", cue)
                        norm_cue = _normalize_ar(clean_cue)
                        words = norm_cue.split()
                        
                        found = False
                        if len(words) >= 2:
                            two_word = " ".join(words[:2])
                            three_word = " ".join(words[:3]) if len(words) >= 3 else two_word
                            if two_word in normalized_script or three_word in normalized_script:
                                found = True
                            else:
                                for i in range(len(words) - 1):
                                    if " ".join(words[i:i+2]) in normalized_script:
                                        found = True
                                        break
                        elif len(words) == 1:
                            if words[0] in normalized_script:
                                found = True
                                
                        if not found and words:
                            missing_cues.append(f"{elem}: '{cue}'")
            if missing_cues:
                issues.append(
                    f"RETENTION ARCHITECTURE DESYNC: The following lines in the Retention table do not appear in the Production Script text: {missing_cues}. "
                    "Every hook, open loop reference, disclaimer, and loop resolution in the Retention table MUST be present as actual spoken dialogue in the script."
                )
    except Exception:
        pass

    # Check spoken script length against target duration
    try:
        script_match = re.search(r"###\s*🎬?\s*PRODUCTION SCRIPT.*?\n(.*?)(\n###\s*WHAT CHANGED|\Z)", text, re.DOTALL)
        if script_match:
            script_body = script_match.group(1)
            cleaned = re.sub(r"\[.*?\]", "", script_body)
            cleaned = re.sub(r"\*\*\(.*?\)\*\*", "", cleaned)
            cleaned = re.sub(r"===.*?===", "", cleaned)
            cleaned = re.sub(r"\*\*\[.*?\]\*\*", "", cleaned)
            spoken_words = len(cleaned.split())
            
            duration_match = re.search(r"Target Duration\s*\|\s*(\d+)", text, re.IGNORECASE)
            target_min = int(duration_match.group(1)) if duration_match else 0
            if target_min >= 8 and spoken_words < 1000:
                issues.append(
                    f"PRODUCTION SCRIPT TOO SHORT: Spoken dialogue has only {spoken_words} words (~{spoken_words // 140} minutes), but the target duration is {target_min} minutes (needs ~1,150–1,350 words). "
                    "You must expand the biological explanations, physiological mechanisms, and clinical case study details."
                )
    except Exception:
        pass

    # VIDEO SECTIONS = YouTube chapters: titles must be usable as chapters, and
    # the start/end sentences must really be in the script (the editor searches for them).
    sections = ew.parse_video_sections(text)
    issues.extend(f"CHAPTER TITLE: {p}" for p in ew.check_chapter_titles(sections))
    cue_problems = ew.check_cue_rows(
        [(f"VIDEO SECTIONS row {s['num']}", s["start"], s["end"]) for s in sections],
        ew.production_script(text), min_words=1,
    )
    if cue_problems:
        issues.append(
            "VIDEO SECTIONS SENTENCES NOT EXACT: " + "; ".join(cue_problems[:12]) +
            ". Copy every Start/End Sentence character-for-character from the spoken words of the PRODUCTION SCRIPT."
        )

    return issues


def _audit_part2(text: str, script: str = "") -> list:
    """Audit Part 2 (Post-Production) for quality issues. `script` is the
    production script the cues must come from."""
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

    rows = ew.parse_storyboard(text)

    # Cues: the editor finds every event by its words, so they must be exact.
    if script and rows:
        cue_problems = ew.check_cue_rows(
            [(f"SB#{r['num']} ({r['layer']})", r["start"], r["end"]) for r in rows], script
        )
        if cue_problems:
            issues.append(
                f"STORYBOARD CUES NOT EXACT ({len(cue_problems)}): " + "; ".join(cue_problems[:20]) +
                ". Every START/END CUE must be 4-8 consecutive spoken words copied exactly from the PRODUCTION SCRIPT and unique in it."
            )

    # Whiteboard drawings must be producible in Google Flow.
    for r in rows:
        if "DRAWING" in r["layer"]:
            d = r["detail"].upper()
            if "IMAGE PROMPT" not in d or "DRAW-ON PROMPT" not in d:
                issues.append(
                    f"DRAWING ANIM SB#{r['num']} is missing its IMAGE PROMPT and/or DRAW-ON PROMPT "
                    "(see the DRAWING ANIM detail format)."
                )

    return issues


def final_script_package(state: PipelineState) -> dict:

    system_prompt_1 = """You are a senior YouTube executive producer compiling Part 1 of a production deliverable.

TITLE & THUMBNAIL SOURCING RULE — READ CAREFULLY:
- The title options and thumbnail concepts have ALREADY been created, brainstormed, narrowed, and quality-gated by a dedicated upstream packaging team (creative director → generator → honesty/CTR auditor). They are provided to you below as APPROVED, FINAL content.
- Your job here is to ASSEMBLE them into this deliverable's format, not to rewrite, regenerate, or second-guess them. Copy the approved title options and thumbnail concepts through with only light reformatting to match the section structure below — do not alter the wording of titles, text overlays, or AI image prompts.
- Use the approved "recommended_title" (provided below) as the Video Title in the Script Metadata table.
- IMPORTANT: Do NOT include any compliance notes, checklists, or rule explanations in your markdown output (e.g., NEVER output '*TITLE RULES:*' or write 'All titles are free of...'). Output ONLY the deliverable sections directly.

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
| Scientific Truth Score | X/10 |
| Scientific Truth Pass | Yes/No |
| Naturalness Score | X/10 |
| Contextual Alignment Score | X/10 |
| Final Grade | |

---

### \U0001f680 YOUTUBE PACKAGING & DISTRIBUTION SUITE

#### 1. High-CTR Title Options
Copy the approved finalist titles through exactly as provided in TITLE OPTIONS below (reformat into a simple list, do not reword). Mark the approved recommended title clearly as the primary one.

#### 2. Thumbnail Visual Blueprints + AI Generation Prompts
Copy the approved thumbnail concepts through exactly as provided in THUMBNAIL CONCEPTS below, for all 3 concepts, with every field intact — do not shorten, reword, or drop any field (especially the full AI Image Generation Prompt paragraphs).

#### 3. YouTube SEO Description & Timestamps (Copy-Paste Ready)
```text
[2-3 sentence Arabic summary — the SEO PRIMARY KEYWORD below MUST appear naturally within these first 1-2 lines, since that's the part visible before "Show more" and the part search and suggested-video ranking weighs most heavily]

\U0001f4cc \u0631\u0648\u0627\u0628\u0637 \u0645\u0647\u0645\u0629 \u0648\u062a\u0646\u0628\u064a\u0647\u0627\u062a:
- \u0627\u0644\u0643\u0644\u0627\u0645 \u0641\u064a \u0647\u0630\u0627 \u0627\u0644\u0641\u064a\u062f\u064a\u0648 \u0644\u0623\u063a\u0631\u0627\u0636 \u0627\u0644\u062a\u0648\u0639\u064a\u0629 \u0648\u0627\u0644\u062a\u062b\u0642\u064a\u0641 \u0627\u0644\u0637\u0628\u064a \u0627\u0644\u0639\u0627\u0645 \u0648\u0644\u0627 \u064a\u063a\u0646\u064a \u0639\u0646 \u0627\u0633\u062a\u0634\u0627\u0631\u0629 \u0637\u0628\u064a\u0628\u0643 \u0627\u0644\u0645\u062e\u062a\u0635.

\u23f1\ufe0f \u0627\u0644\u0641\u0635\u0648\u0644 (Chapters):
[One line per VIDEO SECTIONS row below, in the form "M:SS <the exact same Section Title>", first line at 0:00. (This list is rebuilt automatically from the VIDEO SECTIONS table, so make the table right.)]

[Arabic hashtags — include at least 2-3 built from the SEO SECONDARY KEYWORDS below]
```
DESCRIPTION SEO RULE: weave the SEO SECONDARY KEYWORDS below naturally through the description body (not just the summary line and not stuffed as a keyword list) — these came from real YouTube search-completion data, so using them as written (or a close natural variant) matters more than paraphrasing them into something else.

#### 4. Pinned Comment Draft
```text
[Arabic engagement question + soft CTA]
```

---

### 🎬 PRODUCTION SCRIPT (Teleprompter & Filming Ready)
- Deep, complete spoken dialogue matching the target duration (~1,200–1,350 words for 9 minutes). Do NOT compress into high-level summaries.
- Every open loop planted in the hook MUST be explicitly sustained/referenced in Act 3 and resolved in Act 4 as spoken dialogue.
- Dialect Register: Strictly use Educated, Moderate Egyptian Arabic (العامية المصرية المثقفة البيضاء المعتدلة). Smooth, clear, dignified, and effortless to understand and pronounce for all viewers. Context-appropriate vocabulary without unnecessary slang stuffing (بدون حشو ألفاظ عامية فجة وخلاص). The audience must feel peer-to-peer warmth from an educated doctor, NOT that they are sitting with someone from the street using low-brow slang. Strictly ZERO vulgar slang (ممنوع: كلبشت، بتكلبش، قفشت، موضوع ناشف، بتغلس، بيتجنن، حتة لحمة، وجع رخم، مابتخرفش، قرصت عليها) and ZERO mechanic-shop jargon (ممنوع: فيوزات، تجنزر، تصدي، ماس كهربائي في الضفيرة). Use clean, natural phrasing (شَدّت فجأة، قفلت، انقبضت، ألم مفاجئ، موضوع معقد، زرار النور، جرس الإنذار، فرامل العضلة).
[Complete final script with all cues: [نبرة دافئة], [ميل للأمام], [نظرة مباشرة], [VISUAL NOTE], [SFX], [ON-SCREEN TEXT]]

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
*MANDATORY SYNCHRONIZATION RULE*: In the 'Line (first Arabic words)' column, write the exact first 3 to 5 Arabic words that literally start that spoken sentence in the PRODUCTION SCRIPT above. Do NOT combine multiple phrases together or paraphrase.

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
| Text animation/overlay clarity | | Pass/Warn/Fail |
| Animation timing | | Pass/Warn/Fail |
| B-roll relevance | | Pass/Warn/Fail |
| B-roll medical accuracy | | Pass/Warn/Fail |
| Visual integration | | Pass/Warn/Fail |
| Density balance | | Pass/Warn/Fail |
| Platform fit | | Pass/Warn/Fail |
| Organic feel | | Pass/Warn/Fail |

---

### 📹 VIDEO SECTIONS (YouTube Editing Guide)
A section-by-section breakdown of the entire video. Each row is one content section AND one YouTube chapter: the Section Title is published as-is as the chapter title.

SECTION TITLE RULES (= YouTube chapter titles):
- Viewer-facing, clear and keyword-bearing: 2–7 Arabic words, max 45 characters, saying what this part answers for the viewer. Good: "ليه رسم القلب ممكن يطلع سليم؟", "الفرق بين قلب الرياضي والمرض". Bad: "المقدمة والهوك", "الخاتمة والدعوة للاشتراك".
- NEVER production labels: مقدمة، هوك، خاتمة، دعوة للاشتراك، اشترك، CTA، Act، الجزء الأول. The first row (0:00) names the question the video opens with; the last row names the takeaway.
- At least 3 rows; every section at least 10 seconds. Use the SEO primary or a secondary keyword naturally in 1–2 titles.
- Duration Estimate in the form "Xm Ys".

SENTENCE RULES: Start/End Sentence must be copied character-for-character from the PRODUCTION SCRIPT above (spoken words only — never stage directions in parentheses or [cues]). If a sentence appears more than once in the script, add the neighbouring words so it is unique.

| # | Section Title (Arabic) | ▶️ Start Sentence (exact first Arabic sentence of this section from the script) | ⏹️ End Sentence (exact last Arabic sentence of this section from the script) | Duration Estimate | Notes for Editor |
|---|---|---|---|---|---|
| 1 | [viewer-facing chapter title] | [exact first spoken sentence] | [exact last spoken sentence before next section] | [Xm Ys] | [any special notes] |"""

    base_user_prompt_1 = f"""Compile Part 1 of the YouTube Production Deliverable.

FINAL SCRIPT (definitive version):
{state.get("refined_script", "")}

HOOK VARIATIONS:
{state.get("hook", "")}

CTA VERSIONS:
{state.get("cta_output", "")}

APPROVED TITLE OPTIONS (from the packaging loop — copy through, do not rewrite):
{state.get("title_options", "")}

APPROVED THUMBNAIL CONCEPTS (from the packaging loop — copy through, do not rewrite):
{state.get("thumbnail_concepts", "")}

PACKAGING AUDIT (contains the recommended_title to use as the primary Video Title):
{state.get("packaging_critique_output", "")}

SEO PRIMARY KEYWORD: {state.get("seo_primary_keyword", "")}
SEO SECONDARY KEYWORDS: {state.get("seo_secondary_keywords", "")}

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
Scientific Truth Score: {state.get("truth_score", "")}/10
Scientific Truth Pass: {state.get("truth_pass", "")}
Naturalness Score: {state.get("naturalness_score", "")}
Contextual Alignment Score: {state.get("contextual_alignment_score", "")}

MEDICAL TOPIC: {state.get("medical_topic", "")}
PLATFORM: {state.get("target_platform", "")}
PRESENTER PROFILE: {state.get("presenter_profile", "")}
AVOID LIST: {state.get("avoid_list", "")}

CRITICAL: Copy the APPROVED TITLE OPTIONS and APPROVED THUMBNAIL CONCEPTS through in full, including every AI Image Generation Prompt paragraph in full. Do NOT abbreviate, reword, or say "same as above" — these were already written and quality-gated upstream."""

    part1 = ""
    best_1 = None  # (issue_count, text): a retry can come back worse, so keep the best
    correction_note_1 = ""
    for attempt in range(MAX_FINAL_PACKAGE_RETRIES + 1):
        user_prompt_1 = base_user_prompt_1
        if correction_note_1:
            user_prompt_1 += f"\n\n\u26a0\ufe0f CORRECTION REQUIRED (attempt {attempt + 1}):\n{correction_note_1}"
        part1 = call_llm(system_prompt_1, user_prompt_1, temperature=0.3, max_tokens=10000)
        issues_1 = _audit_part1(part1)
        if best_1 is None or len(issues_1) < best_1[0]:
            best_1 = (len(issues_1), part1)
        if not issues_1:
            print(f"  [final_package Part1] OK on attempt {attempt + 1}")
            break
        print(f"  [final_package Part1] Retry {attempt + 1}: {issues_1}")
        correction_note_1 = "The previous output had these problems - fix ALL of them:\n"
        for i, issue in enumerate(issues_1, 1):
            correction_note_1 += f"  {i}. {issue}\n"
        correction_note_1 += "Regenerate the COMPLETE output fixing every issue above."
    part1 = best_1[1]
    # The description's chapter list is always rebuilt from VIDEO SECTIONS, so
    # the chapter titles can never drift from the section titles.
    part1 = ew.rebuild_description_chapters(part1)
    # Part 2's cues must come from the script the presenter actually reads.
    filmed_script = ew.production_script(part1) or state.get("refined_script", "")

    # -----------------------------------------------------------------
    # CALL 2 - Post-Production Storyboard & Detail Sections (with retry)
    # -----------------------------------------------------------------
    system_prompt_2 = """You are a senior video editor and motion designer compiling Part 2 of a production deliverable.

Your ONLY job is to produce the following sections:

---

### \U0001f3ac INTEGRATED PRODUCTION STORYBOARD
Frame-accurate guide for the video editor. Every row = ONE atomic event. Simultaneous events get their OWN rows sharing the same timecode and cues.

Layer types: TRANSITION | ZOOM/REFRAME | B-ROLL | TOP-RIGHT POPUP | TEXT OVERLAY TITLE | WARNING/ALERT BOX | QUOTE BOX | KINETIC TEXT | LOWER-THIRD | ICON/SHAPE | DRAWING ANIM | SFX

CUE RULES (the editor finds every event by searching for its cue words, so they must be exact):
- START CUE and END CUE = 4–8 consecutive spoken words copied character-for-character from the PRODUCTION SCRIPT (spoken words only — never stage directions in parentheses, [bracket cues], or placeholders like "(بداية الفيديو)", "(Start)", "(Black)").
- Each cue must occur only ONCE in the script; if the words repeat elsewhere, add neighbouring words until unique.
- For an event at the very start, the START CUE is the first spoken words of the script.

| # | Act | \u23f1\ufe0f Timecode | \u25b6\ufe0f START CUE (exact first Arabic words from script) | \u23f9\ufe0f END CUE (exact last Arabic words) | Layer | Detail |
|---|---|---|---|---|---|---|

MANDATORY DETAIL FORMAT (no abbreviations):
- TRANSITION: `[Type] | Dir: [direction or None] | Easing: [e.g. ease-in-expo] | Dur: [Xms]`
- ZOOM/REFRAME: `Scale [X%->Y%] | Anchor: [center/face/object] | Easing: [e.g. ease-out-back] | Dur: [Xms]`
- B-ROLL: `[\U0001f3ac Video / \U0001f5bc\ufe0f Image] | [FULL AI GENERATION PROMPT - subject, setting, lighting, lens, composition, color grade, negative prompts. NEVER write shortcut cross-references. Write the complete prompt here - at least 3 sentences.] | Dur: [Xs]`
- TOP-RIGHT POPUP: `"[English Term → Arabic translation]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Top-right`
- TEXT OVERLAY TITLE: `"[Arabic headline]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Center-frame (dark overlay)`
- WARNING/ALERT BOX: `"⚠️ [Arabic warning text]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Center-bottom`
- QUOTE BOX: `"[Arabic quote text]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Center-bottom`
- KINETIC TEXT: `"[Arabic text]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: [position]`
- LOWER-THIRD: `"[text]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Bottom-left/right`
- SFX: `[Sound name] | Trigger: [on cut/on word/on motion] | Vol: [low/med/punch]`
- ICON/SHAPE (optional): `[icon or shape, e.g. heart icon / circle highlight / arrow] | Entry: [anim, Xms] | Hold: [Xs] | Exit: [anim, Xms] | Pos: [position]`
- DRAWING ANIM: `[what is drawn] | IMAGE PROMPT: [English prompt for a still whiteboard image: plain white whiteboard, clean black marker line art, at most 2 accent colors, the FINISHED drawing composition, NO text, letters, numbers or labels anywhere in the image] | DRAW-ON PROMPT: [English image-to-video prompt using that image as the start frame: a hand with a black marker draws the elements in this order: ..., static camera, white background, no text appears, ~8s] | LABELS IN PREMIERE: [each label text (Arabic/English) + the cue words where it appears] | Dur: [Xs]`
  (Generated in Google Flow: image first, then image-to-video. AI cannot draw Arabic or anatomy labels reliably, so labels are always added as text in Premiere.)

COVERAGE: Full video 0:00 to outro. 25-40 rows minimum. EVERY event from Transition Map, Text Animation & Overlay Guide, and B-Roll Prompts MUST appear.

---

### \U0001f4d0 TRANSITION MAP (Detail Reference)
Reproduce the full Transition Map. Include: philosophy statement + complete table (#, Timestamp, From->To, Type, Direction, Easing, Duration, Range, Rationale, -> SB#) + act-level density summary.

---

### \U0001f3a8 TEXT ANIMATION & OVERLAY GUIDE (Detail Reference)
Reproduce the full Text Animation & Overlay Guide. Include: visual design system + complete element table with columns: #, Timestamp, Element Type (one of: TOP-RIGHT POPUP / TEXT OVERLAY TITLE / WARNING/ALERT BOX / QUOTE BOX / KINETIC TEXT / Lower-third), Content (text), ▶️ START CUE (Arabic), ⏹️ END CUE (Arabic), Screen Position, Entry Animation, Hold, Exit Animation, SB#. Include drawing animations specs + density map per act.

MANDATORY: Every English technical term in the script MUST have a TOP-RIGHT POPUP entry with its Arabic translation. Every section headline should have a TEXT OVERLAY TITLE. Medical disclaimers use WARNING/ALERT BOX. Very impactful sentences use QUOTE BOX (max 2-3 per video). Stats and key terms use KINETIC TEXT.

---

### \U0001f5bc\ufe0f AI B-ROLL GENERATION PROMPTS (Detail Reference)
Reproduce the full B-Roll prompt table. Include: generation settings + complete table with columns: #, Timestamp, ▶️ START CUE (exact first Arabic words), ⏹️ END CUE (exact last Arabic words), Type, Main Cue (the single most important visual element that MUST be present), FULL AI Generation Prompt, SB# + density summary.

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
- Text animation/overlay elements: [X elements]
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

PRODUCTION SCRIPT (copy START CUE / END CUE words exactly from here — spoken words only):
{filmed_script}

TRANSITION DESIGN MAP (reproduce in full, add SB# cross-references):
{state.get("transition_design", "")}

TEXT ANIMATION & OVERLAY GUIDE (reproduce in full, add SB# cross-references):
{state.get("text_animation_overlay", "")}

AI B-ROLL GENERATION PROMPTS (reproduce with FULL prompts in storyboard rows AND detail table):
{state.get("broll_prompts", "")}

PRODUCTION QUALITY CRITIQUE: {state.get("production_critique_output", "")}
PRODUCTION GRADE: {state.get("production_grade", "")}
MEDICAL TOPIC: {state.get("medical_topic", "")}
PLATFORM: {state.get("target_platform", "")}

RULES:
1. Storyboard: 25-40 rows, full video 0:00 to outro.
2. START CUE / END CUE: 4-8 consecutive spoken words copied exactly from the Production Script above, unique in the script, never stage directions or placeholders.
3. B-ROLL storyboard rows: write FULL AI prompt inline - NEVER "[Full Prompt in SB]" or any shortcut.
4. B-Roll detail table: ALSO write full AI prompt in each row - self-contained, duplicates are fine."""

    part2 = ""
    best_2 = None
    correction_note_2 = ""
    for attempt in range(MAX_FINAL_PACKAGE_RETRIES + 1):
        user_prompt_2 = base_user_prompt_2
        if correction_note_2:
            user_prompt_2 += f"\n\n⚠️ CORRECTION REQUIRED (attempt {attempt + 1}):\n{correction_note_2}"
        part2 = call_llm(system_prompt_2, user_prompt_2, temperature=0.3, max_tokens=12000)
        issues_2 = _audit_part2(part2, filmed_script)
        if best_2 is None or len(issues_2) < best_2[0]:
            best_2 = (len(issues_2), part2)
        if not issues_2:
            print(f"  [final_package Part2] OK on attempt {attempt + 1}")
            break
        print(f"  [final_package Part2] Retry {attempt + 1}: {issues_2}")
        correction_note_2 = "The previous output had these problems - fix ALL of them:\n"
        for i, issue in enumerate(issues_2, 1):
            correction_note_2 += f"  {i}. {issue}\n"
        correction_note_2 += "Regenerate the COMPLETE output fixing every issue above."
    part2 = best_2[1]

    final_package = part1 + "\n\n---\n\n" + part2
    return {"final_package": final_package}


# ─────────────────────────────────────────────────────────────────────────────
# Node 16: shorts_moment_identifier
# ─────────────────────────────────────────────────────────────────────────────
def shorts_moment_identifier(state: PipelineState) -> dict:
    system_prompt = """You are a short-form content strategist specializing in medical/educational YouTube Shorts and Facebook Reels. You scan a finalized long-form medical script and identify 3–5 moments that would work as standalone 30–60 second clips — each designed to stop scrolling AND create enough curiosity to drive viewers to the full video.

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

[Repeat for each moment (3-5 moments total)]

### Recommended Extraction Order
1. [Moment #] — [reason this should be made first]
2. [Moment #] — [reason]
3. [Moment #] — [reason]"""

    user_prompt = f"""Identify 3-5 viral-worthy moments from this finalized medical YouTube script for extraction as Facebook Reels and YouTube Shorts.

FINAL PRODUCTION SCRIPT:
{state.get("refined_script", "")}

FINAL DELIVERABLE PACKAGE (for reference on title, thumbnail, structure):
{state.get("final_package", "")[:3000]}

STRATEGY PLAN & FACT LEDGER:
{state.get("strategy_plan", "")}

SOURCE ANALYSIS:
{state.get("source_analysis", "")}

TARGET PLATFORM: {state.get("target_platform", "youtube")}
PRESENTER PROFILE: {state.get("presenter_profile", "")}
MEDICAL TOPIC: {state.get("medical_topic", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.6, max_tokens=4000)
    return {"shorts_moments": response}


# ─────────────────────────────────────────────────────────────────────────────
# Node 17: shorts_script_extractor (Loop 4 Sub-Node 1)
# ─────────────────────────────────────────────────────────────────────────────
def shorts_script_extractor(state: PipelineState) -> dict:
    revision_count = state.get("shorts_revision_count", 0)
    system_prompt = """You are a short-form content editor specializing in medical/educational Reels and Shorts. You take identified viral-worthy moments from a long-form Egyptian Arabic medical script and re-edit each into a standalone 30–60 second clip optimized for Facebook Reels and YouTube Shorts.

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

PERSONA INTEGRITY:
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

[Repeat for each short]"""

    user_prompt = f"""Extract and re-edit standalone 30-60 second scripts for each identified viral moment.

IDENTIFIED SHORTS MOMENTS:
{state.get("shorts_moments", "")}

FINAL LONG-FORM PRODUCTION SCRIPT:
{state.get("refined_script", "")}

STRATEGY PLAN (Terminology Retention Table):
{state.get("strategy_plan", "")}

SOURCE ANALYSIS:
{state.get("source_analysis", "")}

PRESENTER PROFILE: {state.get("presenter_profile", "")}
DIALECT REGISTER: {state.get("dialect_register", "")}
VOICE STYLE: {state.get("voice_style", "")}
TARGET PLATFORM: {state.get("target_platform", "youtube")}
SHORTS REVISION COUNT: {revision_count}"""

    if revision_count > 0 and state.get("shorts_quality_output"):
        user_prompt += f"""

⚠️ PREVIOUS QUALITY GATE AUDIT & FIX INSTRUCTIONS:
{state.get("shorts_quality_output", "")}

Apply the fix instructions above. Only modify the clips that had issues; preserve clips that passed."""

    response = call_llm(system_prompt, user_prompt, temperature=0.7, max_tokens=8000)
    return {"shorts_scripts": response}


# ─────────────────────────────────────────────────────────────────────────────
# Node 18: shorts_caption_packager (Loop 4 Sub-Node 2)
# ─────────────────────────────────────────────────────────────────────────────
def shorts_caption_packager(state: PipelineState) -> dict:
    system_prompt = """You are a caption and subtitle specialist for Arabic short-form video content. You take finalized Reels/Shorts scripts and produce professional, timed caption packages optimized for vertical, sound-off, mobile-first viewing on Facebook Reels and YouTube Shorts.

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

[Repeat for each short]"""

    user_prompt = f"""Generate the timed captions, highlight words, on-screen text overlays, and caption styling package for each of these short-form scripts.

REELS & SHORTS SCRIPTS:
{state.get("shorts_scripts", "")}

SHORTS MOMENTS & METADATA:
{state.get("shorts_moments", "")}

DIALECT REGISTER: {state.get("dialect_register", "")}
TARGET PLATFORM: {state.get("target_platform", "youtube")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.4, max_tokens=6000)
    return {"shorts_captions": response}


# ─────────────────────────────────────────────────────────────────────────────
# Node 19: shorts_quality_gate (Loop 4 Gate)
# ─────────────────────────────────────────────────────────────────────────────
def shorts_quality_gate(state: PipelineState) -> dict:
    system_prompt = """You are a senior short-form content quality auditor for medical/educational Reels and Shorts. You evaluate each extracted clip against strict quality criteria, ensuring every clip is genuinely scroll-stopping, medically responsible, and drives viewers to the full video.

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
```"""

    user_prompt = f"""Audit the extracted Reels & Shorts scripts and caption packages.

SHORTS SCRIPTS:
{state.get("shorts_scripts", "")}

SHORTS CAPTIONS:
{state.get("shorts_captions", "")}

ORIGINAL IDENTIFIED MOMENTS:
{state.get("shorts_moments", "")}

FULL PRODUCTION SCRIPT (for context & medical fact checking):
{state.get("refined_script", "")}

SOURCE ANALYSIS & MEDICAL FACT LEDGER:
{state.get("source_analysis", "")}

TARGET PLATFORM: {state.get("target_platform", "youtube")}
SHORTS REVISION COUNT: {state.get("shorts_revision_count", 0)}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.4, max_tokens=5000)
    
    text = response.strip()
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())

        grade = str(data.get("shorts_quality_grade", "")).strip().upper()
        clips = data.get("per_clip_scores", [])
        
        all_clips_pass = True
        if not clips:
            all_clips_pass = False
        else:
            for clip in clips:
                impact = int(clip.get("standalone_impact", 0))
                scroll = int(clip.get("scroll_stop_power", 0))
                gap = int(clip.get("curiosity_gap", 0))
                med_resp = str(clip.get("medical_responsibility", "")).strip().lower()
                if impact < 8 or scroll < 8 or gap < 8 or med_resp != "pass":
                    all_clips_pass = False
                    break
        
        if not all_clips_pass or grade != "PASS":
            grade = "NEEDS_REVISION"

        return {
            "shorts_quality_grade": grade,
            "shorts_quality_output": data.get("shorts_quality_report", "") or text,
            "shorts_revision_count": state.get("shorts_revision_count", 0) + 1,
        }
    except Exception:
        return {
            "shorts_quality_grade": "NEEDS_REVISION",
            "shorts_quality_output": response,
            "shorts_revision_count": state.get("shorts_revision_count", 0) + 1,
        }


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
    quality_pass = (
        state.get("quality_grade") == "PASS"
        and state.get("truth_pass", False)
        and state.get("truth_score", 0) >= 9
    )
    max_iterations = state.get("max_quality_revision_count", MAX_QUALITY_ITERATIONS)
    hit_max = state.get("quality_revision_count", 0) >= max_iterations

    if quality_pass or hit_max:
        return "packaging_creative_director"
    else:
        return "cta_retention_writer"

MAX_PACKAGING_ITERATIONS = 2

def route_packaging_quality(state: PipelineState) -> str:
    packaging_pass = (state.get("packaging_grade") == "PASS")
    max_iterations = state.get("max_packaging_revision_count", MAX_PACKAGING_ITERATIONS)
    hit_max = state.get("packaging_revision_count", 0) >= max_iterations

    if packaging_pass or hit_max:
        return "transition_designer"
    else:
        return "packaging_generator"

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


# --- Graph Wiring ---
workflow = StateGraph(PipelineState)

# Add all 24 nodes
workflow.add_node("source_script_analyzer", track_node("source_script_analyzer", source_script_analyzer))
workflow.add_node("seo_keyword_researcher", track_node("seo_keyword_researcher", seo_keyword_researcher))
workflow.add_node("strategy_planner", track_node("strategy_planner", strategy_planner))
workflow.add_node("hook_writer", track_node("hook_writer", hook_writer))
workflow.add_node("body_restructurer", track_node("body_restructurer", body_restructurer))
workflow.add_node("translation_fidelity_auditor", track_node("translation_fidelity_auditor", translation_fidelity_auditor))
workflow.add_node("cta_retention_writer", track_node("cta_retention_writer", cta_retention_writer))
workflow.add_node("dialect_warmth_layer", track_node("dialect_warmth_layer", dialect_warmth_layer))
workflow.add_node("fidelity_auditor", track_node("fidelity_auditor", fidelity_auditor))
workflow.add_node("script_refinement", track_node("script_refinement", script_refinement))
workflow.add_node("medical_truth_verifier", track_node("medical_truth_verifier", medical_truth_verifier))
workflow.add_node("self_critique", track_node("self_critique", self_critique))
workflow.add_node("packaging_creative_director", track_node("packaging_creative_director", packaging_creative_director))
workflow.add_node("packaging_generator", track_node("packaging_generator", packaging_generator))
workflow.add_node("packaging_honesty_ctr_auditor", track_node("packaging_honesty_ctr_auditor", packaging_honesty_ctr_auditor))
workflow.add_node("transition_designer", track_node("transition_designer", transition_designer))
workflow.add_node("text_animation_overlay_designer", track_node("text_animation_overlay_designer", text_animation_overlay_designer))
workflow.add_node("broll_prompt_generator", track_node("broll_prompt_generator", broll_prompt_generator))
workflow.add_node("production_quality_critique", track_node("production_quality_critique", production_quality_critique))
workflow.add_node("final_script_package", track_node("final_script_package", final_script_package))
workflow.add_node("shorts_moment_identifier", track_node("shorts_moment_identifier", shorts_moment_identifier))
workflow.add_node("shorts_script_extractor", track_node("shorts_script_extractor", shorts_script_extractor))
workflow.add_node("shorts_caption_packager", track_node("shorts_caption_packager", shorts_caption_packager))
workflow.add_node("shorts_quality_gate", track_node("shorts_quality_gate", shorts_quality_gate))

# Linear edges (upstream pipeline)
workflow.add_edge(START, "source_script_analyzer")
workflow.add_edge("source_script_analyzer", "seo_keyword_researcher")
workflow.add_edge("seo_keyword_researcher", "strategy_planner")
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
workflow.add_edge("script_refinement", "medical_truth_verifier")
workflow.add_edge("medical_truth_verifier", "self_critique")
workflow.add_conditional_edges(
    "self_critique",
    route_quality_loop,
    {
        "packaging_creative_director": "packaging_creative_director",
        "cta_retention_writer": "cta_retention_writer",
    }
)

# Packaging Loop (Loop 5) — runs once the script is locked, before post-production
workflow.add_edge("packaging_creative_director", "packaging_generator")
workflow.add_edge("packaging_generator", "packaging_honesty_ctr_auditor")
workflow.add_conditional_edges(
    "packaging_honesty_ctr_auditor",
    route_packaging_quality,
    {
        "transition_designer": "transition_designer",
        "packaging_generator": "packaging_generator",
    }
)

# Post-Production Editing Loop (Loop 3)
workflow.add_edge("transition_designer", "text_animation_overlay_designer")
workflow.add_edge("text_animation_overlay_designer", "broll_prompt_generator")
workflow.add_edge("broll_prompt_generator", "production_quality_critique")
workflow.add_conditional_edges(
    "production_quality_critique",
    route_production_quality,
    {
        "final_script_package": "final_script_package",
        "transition_designer": "transition_designer",
    }
)

# Bridge from final_script_package to Reels/Shorts Pipeline
workflow.add_edge("final_script_package", "shorts_moment_identifier")
workflow.add_edge("shorts_moment_identifier", "shorts_script_extractor")

# Reels/Shorts Extraction Loop (Loop 4)
workflow.add_edge("shorts_script_extractor", "shorts_caption_packager")
workflow.add_edge("shorts_caption_packager", "shorts_quality_gate")
workflow.add_conditional_edges(
    "shorts_quality_gate",
    route_shorts_quality,
    {
        END: END,
        "shorts_script_extractor": "shorts_script_extractor",
    }
)

# Compile
app = workflow.compile()


def export_broll_prompt_files(text: str, fallback_broll: str = "", output_dir: str = "") -> tuple:
    """
    Extract AI B-roll image and video prompts and save them into two dedicated text files:
    - broll_images.txt
    - broll_videos.txt

    Format per user specification:
    text prompt 1(one line or multiline)

    text prompt 2(one line or multiline)
    """
    if not output_dir:
        return [], []

    images = []
    videos = []

    # Strategy 1: AI B-ROLL GENERATION PROMPTS detail section
    match = re.search(r"###\s*🖼️?\s*AI B-ROLL GENERATION PROMPTS.*?\n(.*?)(?=\n###|\Z)", text, re.DOTALL)
    block = match.group(1) if match else ""

    # Strategy 2: fallback to fallback_broll if detail section is absent or empty
    if not block and fallback_broll:
        block = fallback_broll

    # Strategy 3: fallback to storyboard if still empty
    if not block:
        match_sb = re.search(r"###\s*🎬?\s*INTEGRATED PRODUCTION STORYBOARD.*?\n(.*?)(?=\n###|\Z)", text, re.DOTALL)
        if match_sb:
            block = match_sb.group(1)

    for line in block.splitlines():
        line_clean = line.strip()
        if (
            line_clean.startswith("|")
            and not line_clean.startswith("|---")
            and "DRAWING" not in line_clean.upper()  # whiteboard rows have their own files
            and not any(h in line_clean.upper() for h in ["FULL AI GENERATION PROMPT", "AI GENERATION PROMPT", "TIMECODE", "START CUE", "LAYER"])
        ):
            parts = [p.strip() for p in line_clean.split("|") if p.strip()]
            if not parts:
                continue
            longest = max(parts, key=len)
            if len(longest) > 30:
                cleaned_prompt = longest.replace('\\"', '"').strip('"\'`')
                cleaned_prompt = re.sub(r"^(?:🎬|🖼️)?\s*(?:Video|Image)\s*\|?\s*", "", cleaned_prompt, flags=re.IGNORECASE).strip()
                line_lower = line_clean.lower()
                if "🖼" in line_clean or "image" in line_lower:
                    images.append(cleaned_prompt)
                elif "🎬" in line_clean or "video" in line_lower:
                    videos.append(cleaned_prompt)

    img_path = os.path.join(output_dir, "broll_images.txt")
    vid_path = os.path.join(output_dir, "broll_videos.txt")

    if images:
        with open(img_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(images).strip() + "\n")
        print(f"🖼️ B-Roll Image prompts saved to: {img_path} ({len(images)} prompts)")

    if videos:
        with open(vid_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(videos).strip() + "\n")
        print(f"🎬 B-Roll Video prompts saved to: {vid_path} ({len(videos)} prompts)")

    return images, videos


def export_whiteboard_prompt_files(text: str, output_dir: str = "") -> tuple:
    """
    Saves the whiteboard (DRAWING ANIM) prompts for the Google Flow automator:
    - whiteboard_images.txt : still whiteboard image per drawing (generate first)
    - whiteboard_videos.txt : draw-on image-to-video prompt per drawing, in the
                              same order, to run with the matching image as start frame
    Same format as the B-roll files: prompts separated by a blank line.
    """
    if not output_dir:
        return [], []
    images, videos = [], []
    for r in ew.parse_storyboard(text):
        if "DRAWING" not in r["layer"]:
            continue
        img = re.search(r"IMAGE PROMPT:\s*(.*?)(?=\s*\|\s*(?:DRAW-ON PROMPT|LABELS IN PREMIERE|Dur)\b|$)", r["detail"], re.IGNORECASE | re.DOTALL)
        vid = re.search(r"DRAW-ON PROMPT:\s*(.*?)(?=\s*\|\s*(?:LABELS IN PREMIERE|Dur)\b|$)", r["detail"], re.IGNORECASE | re.DOTALL)
        if img and vid:
            images.append(img.group(1).strip(" []`\""))
            videos.append(vid.group(1).strip(" []`\""))
    for name, prompts, label in (("whiteboard_images.txt", images, "image"), ("whiteboard_videos.txt", videos, "draw-on video")):
        if prompts:
            path = os.path.join(output_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(prompts).strip() + "\n")
            print(f"✏️ Whiteboard {label} prompts saved to: {path} ({len(prompts)} prompts)")
    return images, videos


if __name__ == "__main__":
    import sys
    import datetime
    
    # Optional: --provider main|single|hybrid skips the startup menu
    # (old names: gemini = main, claude = single)
    cli_provider = None
    if "--provider" in sys.argv:
        i = sys.argv.index("--provider")
        if i + 1 >= len(sys.argv):
            print("--provider needs a value: main, single or hybrid")
            sys.exit(1)
        cli_provider = sys.argv[i + 1]
        del sys.argv[i:i + 2]

    if len(sys.argv) < 2:
        print("Usage: python3 main.py <path_to_original_script> [path_to_video_analysis] [--provider main|single|hybrid]")
        print("Choose models first with: python3 setup_models.py")
        sys.exit(1)

    # Model setup for this run: menu when interactive, otherwise flag / env / config
    if not cli_provider and not os.environ.get("LLM_PROVIDER") and sys.stdin.isatty():
        print("\nWhich model setup for this run?  (change the models with: python3 setup_models.py)")
        print(f"  1) Main model for the whole workflow ({llm_config['main_model']})")
        print(f"  2) One strong model for the whole workflow ({llm_config['single_model']})")
        print(f"  3) Hybrid (recommended): {llm_config['main_model']} writes, judges check:")
        for _node, _model in llm_config["hybrid_routes"].items():
            print(f"       {_node} → {_model}")
        print("     Any step whose model fails runs on the main model instead.")
        while True:
            _choice = input("Choose 1-3 [3]: ").strip() or "3"
            if _choice in ("1", "2", "3"):
                break
            print("Please type 1, 2 or 3.")
        cli_provider = {"1": "main", "2": "single", "3": "hybrid"}[_choice]
    llm_router = LLMRouter(llm_config, provider=cli_provider)
        
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
    
    # Load optional video analysis file (second CLI argument)
    if len(sys.argv) >= 3:
        analysis_file = sys.argv[2]
        if not os.path.exists(analysis_file):
            raise FileNotFoundError(f"Video analysis file not found: {analysis_file}")
        with open(analysis_file, "r", encoding="utf-8") as f:
            initial_state["video_analysis"] = f.read()
        print(f"📋 Video analysis loaded from: {analysis_file}")
    
    # Initialize unprovided required fields to prevent KeyError/None issues in prompts
    default_string_fields = [
        "source_format", "medical_topic", "target_duration", "target_platform",
        "medical_disclaimer_requirements", "cta_goal", "reference_egyptian_channels",
        "creator_profile", "video_analysis", "revised_body", "self_critique_output", "production_critique_output",
        "transition_design", "text_animation_overlay", "broll_prompts", "disclaimer_check",
        "shorts_moments", "shorts_scripts", "shorts_captions", "shorts_quality_output", "shorts_quality_grade",
        "seo_primary_keyword", "seo_secondary_keywords", "seo_search_intent", "seo_research_output",
        "packaging_brainstorm_output", "title_options", "thumbnail_concepts",
        "packaging_critique_output", "packaging_grade",
    ]
    for field in default_string_fields:
        if field not in initial_state:
            initial_state[field] = ""

    # Bug fix: explicitly initialize all integer counters so nodes never see None
    default_int_fields = {
        "translation_revision_count": 0,
        "quality_revision_count": 0,
        "production_revision_count": 0,
        "shorts_revision_count": 0,
        "naturalness_score": 0,
        "contextual_alignment_score": 0,
        "dialect_score": 0,
        "warmth_score": 0,
        "fidelity_score": 0,
        "max_translation_revision_count": 2,
        "max_quality_revision_count": 2,
        "max_production_revision_count": 2,
        "max_shorts_revision_count": 2,
        "packaging_revision_count": 0,
        "max_packaging_revision_count": 2,
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
    
    # Quick check of every model this run uses (never blocks; failures fall back)
    llm_router.select()

    print(f"Starting workflow... Session ID: {session_id}")
    print(f"Logs: {output_dir}")
    print(f"Checkpoint: {checkpoint_path}")
    
    import time
    final_result = None
    cumulative_state = dict(initial_state)  # track full state for checkpointing
    
    # Using app.stream to observe execution
    with open(session_log_path, "w", encoding="utf-8") as log_file:
        step_start = time.time()
        for output in app.stream(initial_state):
            for key, value in output.items():
                # Merge updates into cumulative state
                cumulative_state.update(value)

                elapsed = time.time() - step_start
                step_start = time.time()
                _model = llm_router.model_for_node(key)
                print(f"✅ Node '{key}' completed. ({elapsed:.0f}s" + (f", {_model})" if _model else ")"))
                
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

    # Record which model(s) actually produced this run
    provider_report = llm_router.report()
    with open(os.path.join(output_dir, "llm_provider.json"), "w", encoding="utf-8") as pf:
        json.dump(provider_report, pf, ensure_ascii=False, indent=2)
    print(f"\n🔀 LLM calls this run ({provider_report['mode']}): {provider_report['calls']}")

    # Save the final deliverables
    longform_deliverable = cumulative_state.get("final_package", "")
    shorts_scripts_content = cumulative_state.get("shorts_scripts", "")
    shorts_captions_content = cumulative_state.get("shorts_captions", "")
    shorts_moments_content = cumulative_state.get("shorts_moments", "")

    shorts_section = ""
    if shorts_scripts_content:
        shorts_section += "\n\n---\n\n# 📱 REELS & SHORTS PACKAGE\n\n"
        if shorts_moments_content:
            shorts_section += "## IDENTIFIED MOMENTS\n\n" + shorts_moments_content + "\n\n---\n\n"
        shorts_section += "## REELS & SHORTS SCRIPTS\n\n" + shorts_scripts_content + "\n\n---\n\n"
        if shorts_captions_content:
            shorts_section += "## TIMED CAPTIONS & ON-SCREEN OVERLAYS\n\n" + shorts_captions_content

    if longform_deliverable:
        # Reorder into the editing workflow (pure Python, nothing is retyped).
        # If the package has an unexpected shape, keep it as generated.
        try:
            longform_document = ew.build_editing_workbook(longform_deliverable, cumulative_state)
        except Exception as wb_err:
            print(f"  ⚠️ Could not reorder the package into editing steps ({wb_err}); saving it as generated.")
            longform_document = longform_deliverable
        complete_output = longform_document + shorts_section
        with open(final_script_path, "w", encoding="utf-8") as f:
            f.write(complete_output)
        print(f"\nWorkflow finished. Final complete package saved to: {final_script_path}")
        
        # Also write standalone shorts package file for easy access
        if shorts_section:
            shorts_path = os.path.join(output_dir, "shorts_package.md")
            with open(shorts_path, "w", encoding="utf-8") as sf:
                sf.write(shorts_section.strip())
            print(f"Dedicated Reels & Shorts package saved to: {shorts_path}")

        # Automatically export dedicated B-Roll prompt files (Images & Videos)
        export_broll_prompt_files(
            text=longform_deliverable + shorts_section,
            fallback_broll=cumulative_state.get("broll_prompts", ""),
            output_dir=output_dir
        )
        export_whiteboard_prompt_files(longform_deliverable, output_dir=output_dir)

        # Automatically generate responsive HTML, PDF, and DOCX exports
        try:
            from convert_script import convert_markdown_file
            print("\n🚀 Exporting final script to Responsive HTML, PDF, and DOCX...")
            res = convert_markdown_file(final_script_path)
            html_out = res[0]
            pdf_out = res[1]
            docx_out = res[2] if len(res) > 2 else None
            print(f"  📄 HTML export saved to: {html_out}")
            if pdf_out:
                print(f"  📑 PDF export saved to:  {pdf_out}")
            if docx_out:
                print(f"  📝 DOCX export saved to: {docx_out}")
            
            # Also export shorts package if generated
            if shorts_section and os.path.exists(shorts_path):
                convert_markdown_file(shorts_path)
        except Exception as conv_err:
            print(f"  ⚠️ Automatic HTML/PDF/DOCX conversion warning: {conv_err}")
    else:
        print("\nWorkflow finished, but no final package was generated.")
        print(f"Checkpoint with all intermediate outputs saved to: {checkpoint_path}")
