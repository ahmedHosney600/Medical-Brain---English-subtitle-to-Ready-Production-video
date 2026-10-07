"""Post-production layers: transitions, text/overlay animations, B-roll prompts, production critique.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import json

from ..llm import call_llm
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import strip_json_fence


def transition_designer(state: PipelineState) -> dict:
    system_prompt = load_prompt("transition_designer")

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
    system_prompt = load_prompt("text_animation_overlay_designer")

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
    system_prompt = load_prompt("broll_prompt_generator")

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
    system_prompt = load_prompt("production_quality_critique")

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
        text = strip_json_fence(text)
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
