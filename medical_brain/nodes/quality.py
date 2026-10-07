"""Quality gates on the script: translation fidelity, medical fidelity, medical truth, self-critique.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import re

from ..llm import call_llm_json
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import UnreadableAnswer, pick


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

    try:
        data, llm_response = call_llm_json(system_prompt, user_prompt, temperature=0.2, max_tokens=6000,
                                           required=["translation_grade", "naturalness_score", "contextual_alignment_score"])
    except UnreadableAnswer as e:
        data, llm_response = None, e.raw
    text = llm_response.strip()
    try:
        if data is None:
            raise ValueError("unreadable JSON answer")

        grade = str(pick(data, "translation_grade", "grade", default="")).strip().upper()
        if grade not in ["PASS", "NEEDS_REVISION"]:
            grade = "NEEDS_REVISION"

        naturalness = pick(data, "naturalness_score", default=0, kind=int)
        contextual = pick(data, "contextual_alignment_score", default=0, kind=int)

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

    try:
        data, response = call_llm_json(system_prompt, user_prompt, temperature=0.2, max_tokens=5000,
                                       required=["fidelity_score", "medical_accuracy_pass"])
    except UnreadableAnswer as e:
        data, response = None, e.raw
    try:
        if data is None:
            raise ValueError("unreadable JSON answer")
        
        return {
            "fidelity_audit_output": data.get("fidelity_report", ""),
            "medical_accuracy_pass": pick(data, "medical_accuracy_pass", default=False, kind=bool),
            "fidelity_score": pick(data, "fidelity_score", default=0, kind=int),
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
    if state.get("research_dossier"):
        user_prompt += """

The ORIGINAL SOURCE above is a verified RESEARCH DOSSIER with citations [S#]. Every factual claim in the script must trace to one of its facts with the same numbers and population; flag (and remove in verified_script) any claim, number or study that is not in the dossier, even if you believe it is true."""

    try:
        data, response = call_llm_json(system_prompt, user_prompt, temperature=0.2, max_tokens=12000,
                                       required=["truth_score", "truth_pass"])
    except UnreadableAnswer as e:
        data, response = None, e.raw
    text = response.strip()
    try:
        if data is None:
            raise ValueError("unreadable JSON answer")
        
        truth_score = pick(data, "truth_score", default=0, kind=int)
        truth_pass = pick(data, "truth_pass", default=False, kind=bool)
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
Fidelity Score: {state.get("fidelity_score", 0)}/10 | Medical Accuracy Pass: {state.get("medical_accuracy_pass", False)}

RESTRUCTURE STRATEGY PLAN:
{state.get("strategy_plan", "")}

SOURCE SCRIPT ANALYSIS + MEDICAL FACT LEDGER:
{state.get("source_analysis", "")}

AVOID LIST:
{state.get("avoid_list", "")}

Output ONLY the JSON object. Include the full revised script."""

    try:
        data, response = call_llm_json(system_prompt, user_prompt, temperature=0.6, max_tokens=12000,
                                       required=[("critique_grade", "grade"), ("dialect_authenticity_score", "dialect_score"), ("warmth_score", "warmth"), ("fidelity_score", "fidelity"), ("medical_accuracy_pass", "medical_pass")])
    except UnreadableAnswer as e:
        data, response = None, e.raw
    try:
        if data is None:
            raise ValueError("unreadable JSON answer")

        return _critique_result(state, _letter_grade(pick(data, "critique_grade", "grade", default="")), data, response)
    except Exception:
        # Unreadable answer: the scores this round are the independent judges' only.
        return _critique_result(state, "NEEDS_REVISION", None, response)


def _letter_grade(value) -> str:
    """'A+', 'A', 'A-' → A+/A/A; 'B+' → B; anything unreadable → F."""
    text = str(value or "").strip().strip("*").strip().upper()
    if text.startswith("PASS"):
        return "A"
    m = re.match(r"([A-F])\s*(\+?)(?![A-Z])", text)
    if not m:
        return "F"
    return "A+" if m.group(1) == "A" and m.group(2) else m.group(1)


def _critique_result(state: PipelineState, grade: str, data, raw: str) -> dict:
    """Combines self_critique's answer with the independent judges' verdicts, and
    keeps the best round: the loop never ends on a worse script than one it already had."""
    data = data or {}
    # A score the critique didn't give is unknown, not 0: fall back to the last
    # known value, and never let a missing value fail the gate on its own.
    dialect = pick(data, "dialect_authenticity_score", "dialect_score", "dialect", kind=int)
    warmth = pick(data, "warmth_score", "warmth", kind=int)
    dialect = dialect if dialect is not None else state.get("dialect_score") or None
    warmth = warmth if warmth is not None else state.get("warmth_score") or None

    # The critique also rewrote the script, so it may lower the independent
    # fidelity auditor's verdict but never raise it.
    auditor_fidelity = state.get("fidelity_score")
    auditor_pass = state.get("medical_accuracy_pass")
    critique_fidelity = pick(data, "fidelity_score", "fidelity", kind=int)
    critique_pass = pick(data, "medical_accuracy_pass", "medical_pass", kind=bool)
    known_fidelity = [v for v in (auditor_fidelity, critique_fidelity) if v is not None]
    fidelity = min(known_fidelity) if known_fidelity else 0
    known_pass = [v for v in (auditor_pass, critique_pass) if v is not None]
    medical_pass = all(known_pass) if known_pass else False
    truth_score = int(state.get("truth_score", 0) or 0)
    truth_pass = bool(state.get("truth_pass", False))

    if grade in ["A+", "A"]:
        if (dialect is not None and dialect < 8) or (warmth is not None and warmth < 8):
            grade = "B"
        if not medical_pass or fidelity < 9 or not truth_pass or truth_score < 9:
            grade = "C"
    passed = grade in ["A", "A+"]

    # The script the judges just scored (truth verifier's output). When this round
    # passes, that verified script is what moves on — not a fresh, unchecked rewrite.
    verified = state.get("refined_script", "")
    revised = (pick(data, "revised_script", default="", kind=str) or "").strip()
    round_no = state.get("quality_revision_count", 0) + 1
    scores = {"dialect_score": dialect or 0, "warmth_score": warmth or 0, "fidelity_score": fidelity,
              "medical_accuracy_pass": medical_pass, "truth_score": truth_score, "truth_pass": truth_pass}
    rank = [int(passed), truth_score + fidelity + 10 * int(medical_pass and truth_pass),
            (dialect or 0) + (warmth or 0)]

    result = {
        "quality_grade": "PASS" if passed else grade,
        "self_critique_output": pick(data, "critique_report", "report", default="", kind=str) or raw,
        "refined_script": verified if passed else (revised or verified),
        "quality_revision_count": round_no,
        **scores,
    }
    best = state.get("best_script_rank")
    best_script, best_scores, best_round, best_rank = (
        state.get("best_script"), state.get("best_script_scores") or {}, state.get("best_script_round"), best)
    if verified and (not best or rank > list(best)):
        best_script, best_scores, best_round, best_rank = verified, scores, round_no, rank
        result.update(best_script=verified, best_script_rank=rank, best_script_scores=scores,
                      best_script_round=round_no)
    if not passed and round_no >= state.get("max_quality_revision_count", 2) and best_script:
        # Last round and it didn't pass: go on with the best VERIFIED script, never the
        # critique's own unchecked rewrite.
        best_passed = bool(best_rank and best_rank[0])
        if best_round != round_no:
            print(f"  ↩️ self_critique: round {round_no} did not pass; keeping round {best_round}'s script "
                  f"({'it passed' if best_passed else 'it scored higher'})")
        result.update(best_scores)
        result["refined_script"] = best_script
        result["quality_grade"] = "PASS" if best_passed else result["quality_grade"]
    return result
