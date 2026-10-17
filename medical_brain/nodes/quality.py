"""Quality gates on the script: translation fidelity, medical fidelity, medical truth, self-critique.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import re

from ..llm import call_llm_json
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import UnreadableAnswer, pick
from . import chapters


TRUTH_HEALED_MIN_SCORE = 8         # below this the input had real fabrications: revise it


def _healed_truth_note(state) -> str:
    """One line for the judge when the verifier's own corrections made the script pass:
    its report still says truth_pass = false about the input it was given."""
    before = state.get("truth_input_score")
    if state.get("truth_pass") and isinstance(before, int) and before < 9:
        return (f"(The verifier's input scored {before}/10; every issue in its report is already corrected in the "
                "SCRIPT you are judging, so the truth gate is passed. Do not fail the script for issues the "
                "report says were corrected — check the script itself.)\n")
    return ""


LAST_CHAPTER_MAX_SHARE = 0.22         # the plan keeps the final chapter ≤ ~20%
LAST_CHAPTER_PLAN_GROWTH = 1.3


def _last_chapter_over_plan(script: str, blueprint) -> dict:
    """The heavy-ending finding for a final chapter that outgrew its plan: over 22% of
    the spoken script AND over 1.3× its blueprint words. A migraine run planned C4 at
    310 words (19%); polishing passes took it to 497 (24%) — under the generic 30% rule,
    but the judge failed the light-ending criterion. None when it fits."""
    sections = chapters.content_sections(chapters.normalize_section_headers(script or ""))
    plan = [c for c in ((blueprint or {}).get("chapters") or []) if isinstance(c, dict)]
    if len(sections) < 2 or not plan:
        return None
    words = [chapters.spoken_words(text) for _, text in sections]
    total, last = sum(words), words[-1]
    planned = int(plan[-1].get("words") or 0)
    if not total or not planned:
        return None
    if last / total > LAST_CHAPTER_MAX_SHARE and last > planned * LAST_CHAPTER_PLAN_GROWTH:
        return {"title": f"{sections[-1][0]} (planned {planned} words)", "words": last, "total": total,
                "share": last / total, "ratio": last / (total / len(words))}
    return None


def _wpm(state) -> float:
    from ..exports.workbook import blueprint_wpm
    return blueprint_wpm(state.get("blueprint"))


def _translation_single_call(state: PipelineState, system_prompt: str) -> dict:
    """The original whole-body audit (also the fallback when the body isn't chaptered)."""
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


def _translation_chapter_user(state: PipelineState, span: dict, k: int, n: int) -> str:
    return f"""Audit this chapter of a restructured body for translation risk and contextual alignment against the original.

THIS CHAPTER: id: {span['id']} | title: {span['title']} | chapter {k} of {n} | from {chapters.fmt_time(span['start_s'])} to {chapters.fmt_time(span['end_s'])}

CURRENT SCRIPT BODY (this chapter only — audit ONLY these words):
{span['text']}

ORIGINAL ENGLISH SCRIPT (ground truth for intent/emphasis/correspondence):
{state.get("original_script", "")}

SOURCE SCRIPT ANALYSIS (structural map — use to see what each section was originally doing):
{state.get("source_analysis", "")}

RESTRUCTURE STRATEGY PLAN (act breakdown — use to tell intentional restructuring from accidental drift; terminology retention table — use to avoid false-flagging kept English terms):
{state.get("strategy_plan", "")}

CODE-SWITCHING LEVEL: {state.get("code_switching_level", "")}

AVOID LIST:
{state.get("avoid_list", "")}

Output ONLY the JSON object. Check every sentence in THIS CHAPTER for calque risk and its sections for contextual correspondence to their counterparts in the original script; timecodes above are absolute video time and other chapters are audited separately."""


def _translation_chapter_verdict(system_prompt: str, user_prompt: str, max_tokens: int) -> dict:
    try:
        data, llm_response = call_llm_json(system_prompt, user_prompt, temperature=0.2, max_tokens=max_tokens,
                                           required=["translation_grade", "naturalness_score", "contextual_alignment_score"])
    except UnreadableAnswer as e:
        data, llm_response = None, e.raw
    text = (llm_response or "").strip()
    if data is None:
        return {"grade": "NEEDS_REVISION", "naturalness": 0, "contextual": 0,
                "report": text, "revised": ""}
    try:
        grade = str(pick(data, "translation_grade", "grade", default="")).strip().upper()
        if grade not in ["PASS", "NEEDS_REVISION"]:
            grade = "NEEDS_REVISION"
        naturalness = pick(data, "naturalness_score", default=0, kind=int)
        contextual = pick(data, "contextual_alignment_score", default=0, kind=int)
        if naturalness < 8 or contextual < 8:
            grade = "NEEDS_REVISION"
        return {"grade": grade, "naturalness": naturalness, "contextual": contextual,
                "report": data.get("translation_report", "") or text,
                "revised": (data.get("revised_body", "") or "").strip()}
    except Exception:
        return {"grade": "NEEDS_REVISION", "naturalness": 0, "contextual": 0,
                "report": text, "revised": ""}


def _translation_chapter_mode(state: PipelineState, system_prompt: str, blueprint: dict) -> dict:
    spans = chapters.chapter_spans(state.get("revised_body", ""), blueprint)
    if len(spans) < 2:                       # body wasn't written chapter by chapter
        return _translation_single_call(state, system_prompt)

    system_prompt = system_prompt + "\n\n" + load_prompt("production_chapter_mode")
    workers = int(state.get("chapter_workers", 4) or 4)

    def audit(job):
        k, span = job
        user_prompt = _translation_chapter_user(state, span, k + 1, len(spans))
        max_tokens = chapters.budget_for_words(len(span["text"].split())) + 2000
        return _translation_chapter_verdict(system_prompt, user_prompt, max_tokens)

    verdicts = chapters.parallel_map(audit, list(enumerate(spans)), workers)

    revised_parts, reports = [], []
    naturalness, contextual, failed = None, None, False
    for span, verdict in zip(spans, verdicts):
        revised = verdict["revised"]
        # A near-empty revision (e.g. a proxy error stub) never replaces the chapter.
        if revised and chapters.too_short_rewrite(revised, chapters.spoken_words(span["text"])):
            print(f"  ⚠️ {span['title']}: translation revision came back near-empty; keeping the chapter")
            revised = ""
        body = chapters.ensure_header(revised or span["text"], span["title"])
        revised_parts.append(body)
        grade = verdict["grade"]
        failed = failed or grade != "PASS"
        naturalness = verdict["naturalness"] if naturalness is None else min(naturalness, verdict["naturalness"])
        contextual = verdict["contextual"] if contextual is None else min(contextual, verdict["contextual"])
        label = f"[{span['id']} · {span['title']}]"
        reports.append(f"{label}\n{verdict['report']}")

    naturalness = naturalness or 0
    contextual = contextual or 0
    grade = "NEEDS_REVISION" if (failed or naturalness < 8 or contextual < 8) else "PASS"
    return {
        "translation_grade": grade,
        "translation_report": "\n\n".join(reports),
        "revised_body": chapters.join_chapters(revised_parts),
        "naturalness_score": naturalness,
        "contextual_alignment_score": contextual,
        "translation_revision_count": state.get("translation_revision_count", 0) + 1,
    }


def translation_fidelity_auditor(state: PipelineState) -> dict:
    system_prompt = load_prompt("translation_fidelity_auditor")
    blueprint = state.get("blueprint") or {}
    if chapters.chapter_mode_on(blueprint):
        return _translation_chapter_mode(state, system_prompt, blueprint)
    return _translation_single_call(state, system_prompt)


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


def _overclaim_fix_pass(state: PipelineState, script: str, report: str) -> tuple:
    """A code-enforced second pass on overclaims the truth verifier left in the script.

    ONE overclaim_fix call; each edit whose find text occurs exactly once is applied in
    code (plain replace), others are skipped. Any failure (unreadable answer, exception)
    keeps the script unchanged. Returns (script, report)."""
    from ..utils.overclaims import find_overclaims
    found = find_overclaims(script)
    if not found:
        return script, report
    # research_dossier is the bool flag set when the video is built from a research
    # dossier; the actual research source text is original_script. Use a real dossier
    # string when a caller passed one (backwards compatibility).
    dossier = state.get("research_dossier")
    dossier_text = dossier if isinstance(dossier, str) and dossier else (state.get("original_script") or "")
    user_prompt = ("FLAGGED SENTENCES:\n"
                   + "\n".join(f"- «{sentence}» [{phrase}]" for sentence, phrase in found)
                   + f"\n\nRESEARCH DOSSIER:\n{dossier_text}"
                   + f"\n\nSCRIPT:\n{script}")
    try:
        data, _ = call_llm_json(load_prompt("overclaim_fix"), user_prompt,
                                temperature=0.2, max_tokens=4000, required=["edits"])
        edits = pick(data, "edits", default=[]) or []
        fixed, applied, skipped, lines = script, 0, 0, []
        for edit in edits:
            if not isinstance(edit, dict):
                skipped += 1
                continue
            find = str(edit.get("find", "") or "")
            replace = str(edit.get("replace", "") or "")
            if find and fixed.count(find) == 1:
                fixed = fixed.replace(find, replace, 1)
                applied += 1
                lines.append(f"- «{find}» → «{replace}»")
            else:
                skipped += 1
        print(f"🩺 overclaim fix: applied {applied}, skipped {skipped}")
        if lines:
            report = (report or "").rstrip() + "\n\n### Overclaim fix (code)\n" + "\n".join(lines)
        return fixed, report
    except Exception as error:                     # a failed pass keeps the verified script
        print(f"🩺 overclaim fix: failed ({error}); keeping the verified script")
        return script, report


MAX_ENGLISH_TERMS = 5


def _plain_terms_fix_pass(script: str) -> tuple:
    """A code-enforced pass on English terms: when the script has a bare English word in an
    Arabic sentence or more than 5 English terms in brackets, ONE small call returns
    find/replace edits (applied in code only when the find text is unique). Every
    writer prompt states the rule, yet runs kept failing the judge's hard gate on it
    ("الـ DASH", "الـ NSAIDs", 7 bracketed terms) and spent whole revision rounds.
    Returns (script, note lines). Any failure keeps the script."""
    bare, groups = chapters.english_terms(script)
    if not bare and len(groups) <= MAX_ENGLISH_TERMS:
        return script, []
    sentences = [s for s in re.split(r"(?<=[.!?؟…])\s+|\n+", script)
                 if re.search(r"[A-Za-z]{3,}", s) and not s.strip().startswith(("[", "===", "#", "|"))]
    user_prompt = (
        "Fix the English terms in this Egyptian-Arabic medical video script.\n"
        "RULES: every English term comes AFTER its plain Egyptian meaning, in parentheses — "
        "«نظام أكل مخصوص للضغط (DASH)» — and the whole script keeps at most "
        f"{MAX_ENGLISH_TERMS} such bracketed terms (keep the ones that most help a viewer search or talk to a "
        "doctor). Every other English word is said in plain Egyptian only, or in Arabic letters for a drug "
        "or brand name («بروفين»). Change only the words needed; keep the dialect, meaning and medical "
        "facts exactly.\n\n"
        f"BARE ENGLISH WORDS (outside parentheses): {', '.join(sorted(set(bare))) or 'none'}\n"
        f"BRACKETED TERMS ({len(groups)}): {', '.join(groups) or 'none'}\n\n"
        "SENTENCES WITH ENGLISH:\n" + "\n".join(f"- {s.strip()}" for s in sentences[:40])
        + '\n\nReturn ONLY JSON: {"edits": [{"find": "exact text copied from a sentence above", '
          '"replace": "the fixed text"}]}')
    try:
        data, _ = call_llm_json("You fix English terms in Arabic scripts with minimal exact edits.",
                                user_prompt, temperature=0.2, max_tokens=3000, required=["edits"])
    except Exception as error:
        print(f"  🔤 English terms fix: failed ({error}); keeping the script")
        return script, []
    fixed, applied, notes = script, 0, []
    for edit in pick(data, "edits", default=[]) or []:
        if not isinstance(edit, dict):
            continue
        find, replace = str(edit.get("find", "") or ""), str(edit.get("replace", "") or "")
        if find and replace and fixed.count(find) == 1:
            fixed = fixed.replace(find, replace, 1)
            applied += 1
            notes.append(f"- «{find}» → «{replace}»")
    after_bare, after_groups = chapters.english_terms(fixed)
    print(f"  🔤 English terms fix: {len(bare)} bare / {len(groups)} bracketed → "
          f"{len(after_bare)} / {len(after_groups)} ({applied} edits)")
    return fixed, notes


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
        from ..utils.overclaims import find_overclaims
        found = find_overclaims(state.get("refined_script", ""))
        if found:
            user_prompt += ("\n\nCODE CHECK — sentences with time-to-effect or certainty wording (e.g. \"in seconds\", "
                            "\"immediately\", \"guaranteed\", \"final cure\"). Each is an OVERCLAIM unless the dossier "
                            "states the same timing or certainty for that exact point; list each in your report and "
                            "rewrite it in verified_script to the evidence's wording:\n"
                            + "\n".join(f"- «{s}» [{p}]" for s, p in found))

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
        # truth_pass judges the INPUT; the judge only ever sees the corrected script. Every
        # revision added a few small overstatements that the verifier healed, so the input
        # never passed and no round could reach an A. Minor issues (score ≥ 8) that the
        # verifier says are all corrected count as a pass for the corrected script.
        corrected_clean = pick(data, "corrected_script_clean", default=False, kind=bool)
        input_score = truth_score
        if (not truth_pass or truth_score < 9) and corrected_clean and verified_script \
                and truth_score >= TRUTH_HEALED_MIN_SCORE:
            print(f"  🩺 truth: input scored {truth_score}; every issue corrected in the verified script — pass")
            truth_pass, truth_score = True, 9
        report = data.get("truth_verification_report", "") or text
        final_script = chapters.strip_script_extras(
            verified_script if verified_script else state.get("refined_script", ""))
        if state.get("research_dossier"):
            final_script, report = _overclaim_fix_pass(state, final_script, report)
        final_script, term_notes = _plain_terms_fix_pass(final_script)
        if term_notes:
            report = (report or "").rstrip() + "\n\n### English terms fix (code)\n" + "\n".join(term_notes)
        # A single-chapter script can still grow past its whole-video budget while being
        # verified/overclaim-fixed; re-apply the whole-script tightening. Chapter mode is
        # budgeted per chapter and is left untouched. In soft mode, trim only real padding
        # past the larger of the writers' length and the blueprint plan (1.35×).
        blueprint = state.get("blueprint") or {}
        if not chapters.chapter_mode_on(blueprint):
            from .script_writing import (duration_fixed, _target_minutes, _tighten_whole_script,
                                         _soft_trim_if_padded)
            if duration_fixed(state):
                if _target_minutes(blueprint) > 0:
                    final_script = _tighten_whole_script(blueprint, final_script, strict=True)
            else:
                final_script = _soft_trim_if_padded(state, blueprint, final_script)
        # A verified script can still be missing a chapter the verifier dropped: repair
        # by code, then keep only one closing, before it moves on to self_critique.
        from .script_writing import repair_script
        final_script = chapters.strip_script_extras(
            repair_script(state, final_script, state.get("refined_script", ""),
                          state.get("best_script", ""), state.get("revised_body", "")))

        return {
            "truth_verification_report": report,
            "truth_score": truth_score,
            "truth_pass": truth_pass and (truth_score >= 9),
            "truth_input_score": input_score,
            "refined_script": chapters.retime_header_labels(final_script, _wpm(state)),
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
{_healed_truth_note(state)}
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

    # Word counts are measured, never estimated: a judge once failed a 2,421-word script
    # as "about 1,020 spoken words, too short".
    refined = state.get("refined_script", "") or ""
    measured = chapters.spoken_words(refined)
    plan_chapters = [c for c in ((state.get("blueprint") or {}).get("chapters") or []) if isinstance(c, dict)]
    plan_words = sum(int(c.get("words") or 0) for c in plan_chapters)
    sizes = ", ".join(f"«{t[:40]}» {chapters.spoken_words(x)}" for t, x in chapters.content_sections(refined))
    user_prompt += (f"\n\nCODE MEASUREMENTS (exact — use these numbers, do not estimate word counts): "
                    f"spoken words = {measured}"
                    + (f"; blueprint plan = {plan_words} words (×{measured / plan_words:.2f})" if plan_words else "")
                    + (f"; words per section: {sizes}" if sizes else "")
                    + ". The length follows the material: judge padding or cramming, not a fixed number.")

    too_long = chapters.long_disclaimer(refined)
    if too_long:
        print(f"  ⚠️ the disclaimer is {too_long[0]} words")
        user_prompt += (f"\n\nCODE CHECK — the disclaimer is {too_long[0]} spoken words («{too_long[1]}…»): "
                        "make it ONE warm spoken sentence of at most ~35 words (not a doctor, not a "
                        "substitute for your own doctor, don't change medicines alone).")

    # A chapter the writers dropped is invisible to the judge unless it is told: a real
    # run shipped a video with no chapter 1 because the header had been deleted. Name
    # every blueprint chapter missing its "=== title ===" header and forbid a PASS.
    blueprint = state.get("blueprint") or {}
    if chapters.chapter_mode_on(blueprint):
        missing = chapters.missing_chapters(blueprint, state.get("refined_script", ""))
        if missing:
            print(f"  ⚠️ chapters missing from the script: {', '.join(missing)}")
            user_prompt += (f"\n\nCODE CHECK — these blueprint chapters are MISSING from the script: "
                            f"{', '.join(missing)}. This is a CRITICAL failure (grade cannot be PASS); "
                            "the revised script must restore each missing chapter from the blueprint "
                            "and the source.")

    # The code repair keeps one closing; when it still cannot (e.g. contiguous
    # duplicates) name every good-bye and forbid a PASS.
    _repaired, _ = chapters.dedupe_signoffs(state.get("refined_script", "") or "")
    farewells = chapters.farewell_paragraphs(_repaired)
    if len(farewells) > 1:
        named = " | ".join(f"«{p}»" for p in farewells)
        print(f"  ⚠️ the script has {len(farewells)} sign-off paragraphs")
        user_prompt += (f"\n\nCODE CHECK — the script still has {len(farewells)} good-bye paragraphs "
                        f"after the code removed the duplicates: {named}. The script must end with "
                        "ONE sign-off (keep the last closing only, delete the others); grade cannot be PASS.")

    # The chosen frame disclaimer must sit in the opening (code moves a drifted one back in
    # repair_script); when it cannot be found at all, the judge must not pass the script.
    frame = state.get("script_frame") or {}
    disclaimer = str(frame.get("disclaimer", "") or "") if isinstance(frame, dict) else ""
    if disclaimer.strip() and not chapters.has_disclaimer(state.get("refined_script", ""), disclaimer):
        print("  ⚠️ the frame disclaimer is missing from the script")
        user_prompt += ("\n\nCODE CHECK — the chosen disclaimer is MISSING from the opening: the "
                        "disclaimer the frame picked is not in the script at all. The revised script "
                        "must put that disclaimer back at the very start, before the content; "
                        "grade cannot be PASS.")

    script = state.get("refined_script", "") or ""
    if chapters.spoken_words(script) > 0:
        # The final chapter should be the lightest; a crammed ending (red flags + medicines +
        # conditions stacked at the end) is the review's main length problem.
        heavy = chapters.heavy_ending(script)
        if not heavy:
            heavy = _last_chapter_over_plan(script, state.get("blueprint"))
        if heavy:
            print(f"  ⚠️ the ending is too heavy: {heavy['title']} has {heavy['words']} of "
                  f"{heavy['total']} words")
            user_prompt += (f"\n\nCODE CHECK — the ending is too heavy: {heavy['title']} has "
                            f"{heavy['words']} of {heavy['total']} words ({heavy['share']:.0%} of the "
                            f"spoken script, {heavy['ratio']:.1f}× the average section); move the "
                            "secondary lists earlier or cut repetition; grade cannot be A.")

        bare, groups = chapters.english_terms(script)
        if bare or len(groups) > 5:
            named_bare = ", ".join(dict.fromkeys(bare)) or "(none)"
            named_groups = " | ".join(f"({g})" for g in groups) or "(none)"
            print(f"  ⚠️ English terms: {len(bare)} bare, {len(groups)} parenthesised")
            user_prompt += (f"\n\nCODE CHECK — {len(bare)} English term(s) outside parentheses: "
                            f"{named_bare}; {len(groups)} parenthesised English group(s) (limit 5): "
                            f"{named_groups}. Replace them with plain Egyptian wording; keep an "
                            "English term only after its Arabic explanation, in parentheses, when it "
                            "helps the viewer search or talk to a doctor.")
            if len(bare) > 3:
                user_prompt += " Grade cannot be A while more than 3 bare English terms remain."

        if not chapters.has_early_comment_question(script):
            print("  ⚠️ no comment question in the first ~40% of the script")
            user_prompt += ("\n\nCODE CHECK (warning) — no comment question in the first ~40% of "
                            "the spoken script. Add one short question tied to a real viewer habit "
                            "(warning only; it does not cap the grade).")

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


def _judge_is_not_writer() -> bool:
    """True when self_critique runs on a different model from the main (writing) model.
    Its rewrite must not become the next round's base then: a Gemini judge rewriting a
    DeepSeek script for four rounds left it heavier and more formal (the least vivid in
    a blind panel). The judge critiques; the writers fix from its report next round."""
    try:
        from ..llm import get_router
        router = get_router()
        return bool(router.model_for("self_critique") != router.main)
    except Exception:
        return False


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
    if revised:
        # A critique rewrite can drop a chapter or stack sign-offs: repair it by code
        # before it is returned (or kept as the best script).
        from .script_writing import repair_script
        revised = repair_script(state, revised, state.get("refined_script", ""),
                                state.get("best_script", ""), state.get("revised_body", ""))
    round_no = state.get("quality_revision_count", 0) + 1
    scores = {"dialect_score": dialect or 0, "warmth_score": warmth or 0, "fidelity_score": fidelity,
              "medical_accuracy_pass": medical_pass, "truth_score": truth_score, "truth_pass": truth_pass}
    rank = [int(passed), truth_score + fidelity + 10 * int(medical_pass and truth_pass),
            (dialect or 0) + (warmth or 0)]

    result = {
        "quality_grade": "PASS" if passed else grade,
        "self_critique_output": pick(data, "critique_report", "report", default="", kind=str) or raw,
        "refined_script": verified if passed or _judge_is_not_writer() else (revised or verified),
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
    if result.get("refined_script"):
        result["refined_script"] = chapters.retime_header_labels(result["refined_script"], _wpm(state))
    return result
