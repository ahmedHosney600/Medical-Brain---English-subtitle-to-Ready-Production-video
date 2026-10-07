"""Quality gates on the script: translation fidelity, medical fidelity, medical truth, self-critique.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import json

from ..llm import call_llm
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import strip_json_fence


def translation_fidelity_auditor(state: PipelineState) -> dict:
    system_prompt = load_prompt("translation_fidelity_auditor")

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
        text = strip_json_fence(text)
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


def fidelity_auditor(state: PipelineState) -> dict:
    system_prompt = load_prompt("fidelity_auditor")

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
        text = strip_json_fence(text)
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


def medical_truth_verifier(state: PipelineState) -> dict:
    system_prompt = load_prompt("medical_truth_verifier")

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
        text = strip_json_fence(text)
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
    system_prompt = load_prompt("self_critique")

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
        text = strip_json_fence(text)
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
