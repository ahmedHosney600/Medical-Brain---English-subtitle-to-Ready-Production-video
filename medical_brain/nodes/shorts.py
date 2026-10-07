"""Reels & Shorts: find moments, write the short scripts, timed captions, quality gate.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

from ..llm import call_llm, call_llm_json
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import UnreadableAnswer, pick


def shorts_moment_identifier(state: PipelineState) -> dict:
    system_prompt = load_prompt("shorts_moment_identifier")

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


def shorts_script_extractor(state: PipelineState) -> dict:
    revision_count = state.get("shorts_revision_count", 0)
    system_prompt = load_prompt("shorts_script_extractor")

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


def shorts_caption_packager(state: PipelineState) -> dict:
    system_prompt = load_prompt("shorts_caption_packager")

    user_prompt = f"""Generate the timed captions, highlight words, on-screen text overlays, and caption styling package for each of these short-form scripts.

REELS & SHORTS SCRIPTS:
{state.get("shorts_scripts", "")}

SHORTS MOMENTS & METADATA:
{state.get("shorts_moments", "")}

DIALECT REGISTER: {state.get("dialect_register", "")}
TARGET PLATFORM: {state.get("target_platform", "youtube")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.4, max_tokens=6000)
    return {"shorts_captions": response}


def shorts_quality_gate(state: PipelineState) -> dict:
    system_prompt = load_prompt("shorts_quality_gate")

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

    try:
        data, response = call_llm_json(system_prompt, user_prompt, temperature=0.4, max_tokens=5000,
                                       required=["shorts_quality_grade"])
    except UnreadableAnswer as e:
        data, response = None, e.raw
    text = response.strip()
    try:
        if data is None:
            raise ValueError("unreadable JSON answer")

        grade = str(pick(data, "shorts_quality_grade", "grade", default="")).strip().upper()
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
