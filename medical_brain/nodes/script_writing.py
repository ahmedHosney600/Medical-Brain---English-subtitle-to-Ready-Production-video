"""Writing the Arabic script: hook, body, CTA, dialect/warmth layer, refinement.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

from ..llm import call_llm
from ..prompts import load_prompt
from ..state import PipelineState


def hook_writer(state: PipelineState) -> dict:
    system_prompt = load_prompt("hook_writer")

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


def body_restructurer(state: PipelineState) -> dict:
    system_prompt = load_prompt("body_restructurer")

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


def cta_retention_writer(state: PipelineState) -> dict:
    system_prompt = load_prompt("cta_retention_writer")

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

Check the critique report below for anything flagged under OPEN LOOP COMPLETION, DISCLAIMER, or CTA QUALITY. Fix ONLY what's explicitly flagged in YOUR PREVIOUS OUTPUT below. Leave everything else in it untouched (same wording).

YOUR PREVIOUS OUTPUT:
{state.get("cta_output", "")}

AUDIT / CRITIQUE REPORT:
{state.get("self_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.65, max_tokens=3000)
    return {"cta_output": response}


def dialect_warmth_layer(state: PipelineState) -> dict:
    system_prompt = load_prompt("dialect_warmth_layer")

    revising = state.get("quality_revision_count", 0) > 0 and state.get("refined_script", "")
    if revising:
        # Later passes start from the fact-checked script, so the medical truth
        # verifier's corrections survive the rewrite.
        script_block = f"""CURRENT SCRIPT (already fact-checked — keep every medical correction; use this as the base):
{state.get("refined_script", "")}

REVISED RETENTION, DISCLAIMER & CTA LINES (from this pass — apply them where the critique flagged open loops, the disclaimer or the CTA):
{state.get("cta_output", "")}"""
    else:
        script_block = f"""HOOK:
{state.get("hook", "")}

SCRIPT BODY (post translation-fidelity gate):
{state.get("revised_body", "")}

RETENTION, DISCLAIMER & CTA LAYER:
{state.get("cta_output", "")}"""

    user_prompt = f"""Rewrite for dialect authenticity and warmth.

DIALECT REGISTER: {state.get("dialect_register", "")}
CODE-SWITCHING LEVEL: {state.get("code_switching_level", "")}

{script_block}

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

Pay special attention to anything the critique flagged under DIALECT AUTHENTICITY or WARMTH/DE-CLINICALIZATION. Don't let the same issue survive into this pass. Fix ONLY what's flagged. Never reintroduce a claim the truth verification or fidelity audit below corrected or flagged.

AUDIT / CRITIQUE REPORT:
{state.get("self_critique_output", "")}

MEDICAL TRUTH VERIFICATION REPORT (score {state.get("truth_score", 0)}/10):
{state.get("truth_verification_report", "")}

MEDICAL FIDELITY AUDIT:
{state.get("fidelity_audit_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.75, max_tokens=9000)
    return {"dialect_warmth_output": response}


def script_refinement(state: PipelineState) -> dict:
    system_prompt = load_prompt("script_refinement")

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
