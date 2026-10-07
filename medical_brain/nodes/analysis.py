"""Analysis: read the English source, research search keywords, plan the Arabic rebuild.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import json

from ..llm import call_llm, call_llm_json
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import UnreadableAnswer, strip_json_fence
from ..utils.seo import fetch_autocomplete_grounding


def source_script_analyzer(state: PipelineState) -> dict:
    system_prompt = load_prompt("source_script_analyzer")

    user_prompt = f"""Analyze the following source script and produce the Metadata, Structural Map, and Medical Fact Ledger.

ORIGINAL SCRIPT:
{state.get("original_script", "")}

VIDEO ANALYSIS INTELLIGENCE (pre-curated insights for this specific video — use to extract the Editorial Intelligence Brief. If empty, skip the Editorial Brief):
{state.get("video_analysis", "")}

Do not rewrite or translate anything. Only analyze and extract. Every claim in the ledger must be traceable back to a specific point in the script above. Output ONLY the JSON object."""

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

    response = call_llm(system_prompt, user_prompt, temperature=0.5, max_tokens=5000)
    return {"strategy_plan": response}
