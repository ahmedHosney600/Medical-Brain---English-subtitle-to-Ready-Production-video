"""Analysis: read the English source, research search keywords, plan the Arabic rebuild.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import json

from ..llm import call_llm, call_llm_json
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import UnreadableAnswer, strip_json_fence
from ..utils.seo import fetch_autocomplete_grounding
from . import chapters


def source_script_analyzer(state: PipelineState) -> dict:
    system_prompt = load_prompt("source_script_analyzer")

    user_prompt = f"""Analyze the following source script and produce the Metadata, Structural Map, and Medical Fact Ledger.

ORIGINAL SCRIPT:
{state.get("original_script", "")}

VIDEO ANALYSIS INTELLIGENCE (pre-curated insights for this specific video — use to extract the Editorial Intelligence Brief. If empty, skip the Editorial Brief):
{state.get("video_analysis", "")}

Do not rewrite or translate anything. Only analyze and extract. Every claim in the ledger must be traceable back to a specific point in the script above. Output ONLY the JSON object."""
    if state.get("research_dossier"):
        user_prompt += """

SOURCE TYPE: a RESEARCH DOSSIER, not a video transcript. Every line is a fact already verified against the cited source [S#]. Build the Medical Fact Ledger from these facts, keeping their numbers, populations and [S#] citations exactly; there is no speaker or original video structure, so propose the structure from the outline and the viewer's journey. Nothing outside the dossier may enter the ledger."""

    try:
        data, response = call_llm_json(system_prompt, user_prompt, temperature=0.3, max_tokens=5000,
                                       required=["source_analysis"])
    except UnreadableAnswer as e:
        data, response = None, e.raw
    text = response.strip()
    try:
        if data is None:
            raise ValueError("unreadable JSON answer")
        
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
    variant_prompt = load_prompt("seo_keyword_researcher_variants")
    variant_user = f"Medical topic: {medical_topic}\n\nContext (for topic accuracy only):\n{source_analysis[:800]}"
    try:
        variants_raw = call_llm(variant_prompt, variant_user, temperature=0.5, max_tokens=300).strip()
        variants_raw = strip_json_fence(variants_raw)
        query_variants = json.loads(variants_raw.strip())
        if not isinstance(query_variants, list) or not query_variants:
            query_variants = [medical_topic]
    except Exception:
        query_variants = [medical_topic]

    autocomplete_grounding = fetch_autocomplete_grounding(query_variants)

    system_prompt = load_prompt("seo_keyword_researcher")

    user_prompt = f"""MEDICAL TOPIC: {medical_topic}

QUERY VARIANTS USED FOR AUTOCOMPLETE LOOKUP:
{json.dumps(query_variants, ensure_ascii=False)}

REAL YOUTUBE AUTOCOMPLETE COMPLETIONS (ground truth search behavior):
{autocomplete_grounding}

SOURCE SCRIPT ANALYSIS (for topic/claim accuracy — do not invent keywords beyond what this content actually supports):
{source_analysis}

Produce the keyword plan. Output ONLY the JSON object."""

    try:
        data, response = call_llm_json(system_prompt, user_prompt, temperature=0.4, max_tokens=1500,
                                       required=["primary_keyword"])
    except UnreadableAnswer as e:
        data, response = None, e.raw
    text = response.strip()
    try:
        if data is None:
            raise ValueError("unreadable JSON answer")
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


# A plan whose chapters add up to far less than its own duration would shrink the
# video while later steps still demanded the full time (a real run: blueprint
# target_minutes 12, one chapter of 330 words → a ~4-minute script). The planner
# gets ONE retry with the shortfall spelled out; if it is still short, a run without
# a user-pinned duration is sized down to what the plan actually covers.
PLANNER_SHORT_FRACTION = 0.6
PLANNER_DEFAULT_WPM = 138


def _planner_wpm(blueprint) -> float:
    """The blueprint's words_per_minute (138 when missing, unreadable or <= 0)."""
    try:
        wpm = float((blueprint or {}).get("words_per_minute", PLANNER_DEFAULT_WPM) or PLANNER_DEFAULT_WPM)
    except (TypeError, ValueError):
        wpm = float(PLANNER_DEFAULT_WPM)
    return wpm if wpm > 0 else float(PLANNER_DEFAULT_WPM)


def _planned_words(blueprint) -> int:
    """The blueprint's chapters' total word budget (bad values are ignored)."""
    total = 0
    for chapter in (blueprint or {}).get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        try:
            words = int(float(chapter.get("words", 0) or 0))
        except (TypeError, ValueError):
            continue
        if words > 0:
            total += words
    return total


def _planner_target_minutes(blueprint, strict_minutes: int) -> float:
    """The duration the sanity check measures against: the user's --minutes when set,
    otherwise the blueprint's own target_minutes (0 when neither is set)."""
    if strict_minutes > 0:
        return float(strict_minutes)
    try:
        return float((blueprint or {}).get("target_minutes", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _check_blueprint_size(system_prompt: str, user_prompt: str, response: str, blueprint, strict_minutes: int) -> tuple:
    """Retry the planner ONCE when the blueprint's chapters fall far short of the
    duration it claims; then size a non-strict run down to the content. Returns the
    (possibly retried) (response, blueprint), with the corrected blueprint ready to
    store in state (downstream reads state["blueprint"])."""

    def measure(bp):
        return _planned_words(bp), _planner_target_minutes(bp, strict_minutes) * _planner_wpm(bp)

    planned, expected = measure(blueprint)
    chapter_list = (blueprint or {}).get("chapters") or []
    if expected > 0 and (planned < PLANNER_SHORT_FRACTION * expected or not chapter_list):
        target = _planner_target_minutes(blueprint, strict_minutes)
        wpm = _planner_wpm(blueprint)
        correction = (
            f"\n\nYOUR PREVIOUS BLUEPRINT WAS INCOMPLETE: its chapters add up to {planned} words, "
            f"but {target:g} minutes × {wpm:g} words/minute ≈ {int(expected)} spoken words. "
            "Return the COMPLETE plan and blueprint again: chapters covering the whole arc, "
            f"adding up to about {int(expected)} words.")
        retry = call_llm(system_prompt, user_prompt + correction, temperature=0.5, max_tokens=9000)
        retried = chapters.parse_blueprint(retry)
        # An unreadable retry must not throw away the first (short but valid) plan.
        if chapters.valid_blueprint(retried) or not chapters.valid_blueprint(blueprint):
            response, blueprint = retry, retried
            planned, expected = measure(blueprint)

    if expected > 0 and planned < PLANNER_SHORT_FRACTION * expected and chapters.valid_blueprint(blueprint):
        if strict_minutes > 0:
            print(f"⚠️ strategy_planner: the plan only covers {planned} words for the {strict_minutes} min "
                  "target — keeping the requested length; later steps will ask for more")
        else:
            new_minutes = max(1, round(planned / _planner_wpm(blueprint), 1))
            blueprint["target_minutes"] = new_minutes
            print(f"⚠️ strategy_planner: the plan only covers {planned} words — video sized to {new_minutes} min")
    return response, blueprint


def strategy_planner(state: PipelineState) -> dict:
    system_prompt = load_prompt("strategy_planner")
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

    # Long-form runs may pin the duration: one "TARGET MINUTES" line drives the
    # planner's word budget, chapter split and the machine-readable blueprint.
    try:
        target_minutes = int(float(state.get("target_minutes", 0) or 0))
    except (TypeError, ValueError):
        target_minutes = 0
    if target_minutes > 0:
        user_prompt = f"TARGET MINUTES: {target_minutes}\n\n{user_prompt}"

    response = call_llm(system_prompt, user_prompt, temperature=0.5, max_tokens=9000)

    # The plan ends with the chapter blueprint; parse it now so the writers know
    # whether to work chapter by chapter. Invalid/absent ⇒ no chapter mode.
    blueprint = chapters.parse_blueprint(response)
    # A blueprint far shorter than its own duration gets one retry, then (unless the
    # user pinned the duration) its target_minutes is brought down to the content so
    # every later step agrees with it.
    response, blueprint = _check_blueprint_size(system_prompt, user_prompt, response, blueprint, target_minutes)
    if not chapters.valid_blueprint(blueprint):
        print("  🧭 strategy_planner: no valid script blueprint; script stays single-call")
        blueprint = {}
    return {"strategy_plan": response, "blueprint": blueprint}
