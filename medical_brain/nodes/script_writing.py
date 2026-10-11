"""Writing the Arabic script: hook, body, CTA, dialect/warmth layer, refinement.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data.

Long videos (a blueprint with ≥2 chapters) are written chapter by chapter from the
shared plan + script memory, then checked as one whole by an integrity pass. A
one-chapter blueprint keeps the original single-call behaviour exactly."""

import json
import re

from ..llm import call_llm, call_llm_json
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import UnreadableAnswer, pick
from . import chapters


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


# --------------------------------------------------------------------------
# body
# --------------------------------------------------------------------------
def _chapter_key(chapter: dict) -> str:
    return str(chapter.get("id", "") or chapter.get("title", "") or "").strip()


def _chapter_words(chapter: dict) -> int:
    try:
        return int(float(chapter.get("words", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _ensure_header(text: str, title: str) -> str:
    text = (text or "").strip()
    if re.match(r"^\s*===", text):
        return text
    return f"=== {title} ===\n{text}" if title else text


def _previous_chapter_texts(state: PipelineState, chapter_list: list, prev_drafts: dict) -> dict:
    """Previous text of each blueprint chapter, from revised_body (by title, then by
    order) and from saved drafts."""
    titled = [(t, x) for (t, x) in chapters.split_by_chapters(state.get("revised_body", "") or "") if t]
    out = {}
    for i, chapter in enumerate(chapter_list):
        cid = _chapter_key(chapter)
        title = str(chapter.get("title", "") or "").strip()
        text = next((x for (t, x) in titled if title and t == title), None)
        if text is None and i < len(titled):
            text = titled[i][1]
        if text is not None:
            out[cid] = text
    for cid, text in (prev_drafts or {}).items():
        out.setdefault(cid, text)
    return out


def _chapter_body_prompt(state: PipelineState, chapter: dict, memory: dict,
                         previous_chapter_text: str, previous_version: str, report: str) -> str:
    cid = str(chapter.get("id", "") or "").strip()
    title = str(chapter.get("title", "") or "").strip()
    covers = ", ".join(str(c) for c in (chapter.get("covers") or []))
    opens = ", ".join(str(c) for c in (chapter.get("opens_loops") or []))
    closes = ", ".join(str(c) for c in (chapter.get("closes_loops") or []))

    prompt = f"""Rebuild the script body in Egyptian Arabic — ONE CHAPTER of a longer video.

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

SCRIPT BLUEPRINT (JSON):
{json.dumps(state.get("blueprint", {}), ensure_ascii=False, indent=2)}

THIS CHAPTER:
id: {cid}
title: {title}
job: {chapter.get("job", "")}
words: {chapter.get("words", 0)}
covers: {covers or "(none listed)"}
opens_loops: {opens or "(none)"}
closes_loops: {closes or "(none)"}
depends_on_previous: {bool(chapter.get("depends_on_previous", True))}

SCRIPT MEMORY:
{chapters.render_memory(memory) or "(first chapter — no memory yet)"}

LAST LINES OF THE PREVIOUS CHAPTER:
{chapters.last_lines(previous_chapter_text, 150) or "(none — this is the first chapter)"}

Write ONLY this chapter in Egyptian Arabic. Start with its header line exactly: === {title} === and end with a bridge into the next chapter's job."""

    if previous_version:
        prompt += f"""

---

## MODE: REVISION PASS

**Revision Count**: {state.get("translation_revision_count", 0)}
("0" = initial draft. "1"+ = apply fixes to this chapter's previous version below.)

PREVIOUS VERSION OF THIS CHAPTER:
{previous_version}

TRANSLATION RISK / CONTEXTUAL ALIGNMENT REPORT (fix only what it flags in this chapter):
{report}"""
    return prompt


# A fresh chapter longer than this × its blueprint words gets one tightening call. In a
# 20-minute run every chapter came in 20–25% over and the script ran 27 minutes.
TIGHTEN_ABOVE = 1.1


def _tighten_chapter(state: PipelineState, system_prompt: str, chapter: dict,
                     text: str, memory: dict) -> tuple:
    """One tightening call when a fresh chapter overshoots its blueprint budget; the
    tightened text replaces it when it is shorter and keeps ≥ 0.7 × target. Returns
    (text, memory)."""
    target = _chapter_words(chapter)
    before = chapters.spoken_words(text)
    if target <= 0 or before <= target * TIGHTEN_ABOVE:
        return text, memory

    title = str(chapter.get("title", "") or "").strip()
    cid = _chapter_key(chapter)
    print(f"  ✂️ chapter {cid}: {before} words vs target {target} — tightening")
    # Writers overshoot a requested length by ~20%, so the request aims a little under it.
    user_prompt = (f"TIGHTEN this chapter to about {int(target * 0.9)} words (never more than "
                   f"{target}): keep every medical point, "
                   "the opening continuity, the loops and the bridge; cut repetition and padding"
                   "\n\n" + text)
    try:
        response = call_llm(system_prompt, user_prompt, temperature=0.75,
                            max_tokens=chapters.budget_for_words(target))
    except Exception as error:                     # a failed pass keeps the original
        print(f"  ✂️ chapter {cid}: tightening failed ({error}); keeping the original")
        return text, memory

    tightened, tight_memory = chapters.split_memory(response)
    tightened = _ensure_header(tightened, title)
    after = chapters.spoken_words(tightened)
    before = chapters.spoken_words(text)
    # A tightened chapter that is shorter but still a bit long beats the original; only a
    # cut below 70% of the target (content likely lost) is rejected.
    if 0.7 * target <= after < before:
        print(f"  ✂️ chapter {cid}: tightened to {after} words")
        return tightened, (tight_memory or memory)
    print(f"  ✂️ chapter {cid}: tightened to {after} words (out of range); keeping the original")
    return text, memory


def _body_chapter_mode(state: PipelineState, base_system: str, blueprint: dict) -> dict:
    system_prompt = base_system + "\n\n" + load_prompt("chapter_mode")
    chapter_list = [c for c in (blueprint.get("chapters") or []) if isinstance(c, dict)]
    round_no = int(state.get("translation_revision_count", 0) or 0)
    revising = round_no > 0
    signature = chapters.blueprint_signature(blueprint)

    prev_drafts = state.get("chapter_drafts") or {}
    prev_memory = state.get("chapter_memory") or {}
    # Checkpoint reuse: the same blueprint, same revision pass, already-written chapters.
    reuse = (state.get("chapter_drafts_round", -1) == round_no
             and state.get("chapter_drafts_key") == signature)

    # Which chapters the translation report flags (none identifiable ⇒ all).
    flagged = set()
    if revising:
        report = state.get("translation_report", "") or ""
        for chapter in chapter_list:
            cid = str(chapter.get("id", "") or "").strip()
            title = str(chapter.get("title", "") or "").strip()
            if (cid and re.search(rf"(?<![A-Za-z0-9]){re.escape(cid)}(?![A-Za-z0-9])", report)) \
                    or (title and title in report):
                flagged.add(cid or title)
        if not flagged:
            flagged = {_chapter_key(c) for c in chapter_list}

    prev_by_key = _previous_chapter_texts(state, chapter_list, prev_drafts)
    drafts = dict(prev_drafts) if reuse else {}
    mem_by = dict(prev_memory)
    memory = {}
    ordered = []

    for chapter in chapter_list:
        cid = _chapter_key(chapter)
        title = str(chapter.get("title", "") or "").strip()
        previous_chapter_text = ordered[-1] if ordered else ""

        if reuse and cid in drafts and (not revising or cid not in flagged):
            text, mem = drafts[cid], mem_by.get(cid, {})
        elif revising and cid not in flagged and prev_by_key.get(cid):
            # Revision keeps unflagged chapters byte-for-byte.
            text, mem = prev_by_key[cid], mem_by.get(cid, {})
            drafts[cid] = text
        else:
            previous_version = prev_by_key.get(cid) or ("" if reuse else prev_drafts.get(cid, ""))
            user_prompt = _chapter_body_prompt(state, chapter, memory, previous_chapter_text,
                                               previous_version, state.get("translation_report", ""))
            response = call_llm(system_prompt, user_prompt, temperature=0.75,
                                max_tokens=chapters.budget_for_words(_chapter_words(chapter)))
            text, mem = chapters.split_memory(response)
            text = _ensure_header(text, title)
            text, mem = _tighten_chapter(state, system_prompt, chapter, text, mem)
            drafts[cid] = text
        mem_by[cid] = mem
        ordered.append(text)
        memory = chapters.merge_memory(memory, mem, cid)

    return {"revised_body": chapters.join_chapters(ordered),
            "script_memory": memory, "chapter_memory": mem_by,
            "chapter_drafts": drafts, "chapter_drafts_round": round_no,
            "chapter_drafts_key": signature}


def body_restructurer(state: PipelineState) -> dict:
    system_prompt = load_prompt("body_restructurer")
    blueprint = state.get("blueprint") or {}

    if chapters.chapter_mode_on(blueprint):
        return _body_chapter_mode(state, system_prompt, blueprint)

    # The blueprint's word budget, stated outright: without it a 10-minute body came out
    # at 2,362 spoken words for a 1,175-word budget.
    target = _target_words(blueprint)
    length_line = (f"\nLENGTH BUDGET: about {target} spoken words for the body (cues and visual "
                   f"notes not counted), never more than {int(target * TIGHTEN_ABOVE)}. Depth comes "
                   "from choosing the most useful points, not from adding more.\n") if target > 0 else ""

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
{length_line}
---

## MODE: REVISION PASS

**Revision Count**: {state.get("translation_revision_count", 0)}
("0" = initial draft. "1"+ = apply fixes to the prior draft below.)

PREVIOUS FULL BODY:
{state.get("revised_body", "")}

TRANSLATION RISK / CONTEXTUAL ALIGNMENT REPORT:
{state.get("translation_report", "")}"""

    llm_response = call_llm(system_prompt, user_prompt, temperature=0.75, max_tokens=9000)
    if target > 0:
        llm_response, _ = _tighten_chapter(state, system_prompt, {"id": "body", "words": target},
                                           llm_response, {})
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


# --------------------------------------------------------------------------
# dialect & warmth
# --------------------------------------------------------------------------
# A critique that flags the frame means the frame step must run again in a revision.
_FRAME_CRITIQUE_RE = re.compile(r"\b(hook|opening|cta|closing|outro)\b", re.IGNORECASE)


def _fallback_segments(state: PipelineState) -> list:
    """Today's segments (raw hook/preamble, chapters, raw CTA) — used only when the
    frame call fails."""
    segments, preamble = [], ""
    for title, text in chapters.split_by_chapters(state.get("revised_body", "") or ""):
        if title:
            segments.append((title, text))
        else:
            preamble = text
    hook = state.get("hook", "") or ""
    head = "\n\n".join(x for x in (hook, preamble) if x and x.strip())
    if head.strip():
        segments.insert(0, ("HOOK", head))
    cta = state.get("cta_output", "") or ""
    if cta.strip():
        segments.append(("CTA / OUTRO", cta))
    return segments


def _frame_user_prompt(state: PipelineState, blueprint: dict, critique: str = "") -> str:
    titled = [x for t, x in chapters.split_by_chapters(state.get("revised_body", "") or "") if t]
    first_start = chapters.first_lines(titled[0], 120) if titled else ""
    last_end = chapters.last_lines(titled[-1], 120) if titled else ""
    prompt = f"""HOOK OPTIONS:
{state.get("hook", "")}

RETENTION, DISCLAIMER & CTA MATERIAL:
{state.get("cta_output", "")}

SCRIPT BLUEPRINT (JSON):
{json.dumps(blueprint or {}, ensure_ascii=False, indent=2)}

FIRST CHAPTER START:
{first_start or "(none)"}

LAST CHAPTER END:
{last_end or "(none)"}"""
    if critique:
        prompt += f"""

REVISION CRITIQUE (the opening/closing was flagged for revision — address it):
{critique}"""
    return prompt


def _call_script_frame(state: PipelineState, blueprint: dict, critique: str = "") -> dict:
    """ONE JSON call that picks one hook and one CTA/outro and writes the opening,
    disclaimer and closing. None (with a warning) when the call cannot be read."""
    try:
        data, _ = call_llm_json(load_prompt("script_frame"),
                                _frame_user_prompt(state, blueprint, critique),
                                temperature=0.4, max_tokens=3000,
                                required=["opening", "closing"])
    except UnreadableAnswer:
        data = None
    if not isinstance(data, dict):
        data = None
    opening = str(pick(data, "opening", default="") or "").strip() if data else ""
    closing = str(pick(data, "closing", default="") or "").strip() if data else ""
    if not opening or not closing:
        print("  ⚠️ script frame: choosing the opening/closing failed; "
              "falling back to the raw hook and CTA material")
        return None
    return {"opening": opening,
            "disclaimer": str(pick(data, "disclaimer", default="") or "").strip(),
            "closing": closing,
            "chosen_hook": str(pick(data, "chosen_hook", default="") or "").strip()}


def _frame_segments(state: PipelineState, frame: dict, chapter_source: str) -> list:
    """[opening (+ disclaimer), chapter 1 … chapter n, closing] from a frame."""
    segments = []
    head = "\n\n".join(x for x in (frame.get("opening", ""), frame.get("disclaimer", ""))
                       if x and x.strip())
    if head.strip():
        segments.append(("OPENING", head))
    segments.extend((title, text) for title, text in chapters.split_by_chapters(chapter_source or "")
                    if title)
    closing = frame.get("closing", "") or ""
    if closing.strip():
        segments.append(("CLOSING", closing))
    return segments


def _dialect_segments(state: PipelineState, revising: bool):
    """([(label, text)], frame_to_store). In chapter mode the segments are built from
    one chosen opening and one chosen closing (the frame step); in a revision pass the
    stored frame's segments come from refined_script, and the frame is re-chosen only
    when the critique flags the opening/closing."""
    if revising:
        critique = state.get("self_critique_output", "") or ""
        if _FRAME_CRITIQUE_RE.search(critique):
            frame = _call_script_frame(state, state.get("blueprint") or {}, critique)
            if frame:
                return _frame_segments(state, frame, state.get("refined_script", "")), frame
        return [(title or "HOOK", text) for title, text in
                chapters.split_by_chapters(state.get("refined_script", "") or "")], None

    frame = state.get("script_frame") or {}
    if not isinstance(frame, dict) or not str(frame.get("opening", "") or "").strip():
        frame = _call_script_frame(state, state.get("blueprint") or {})
    if frame:
        return _frame_segments(state, frame, state.get("revised_body", "")), frame
    return _fallback_segments(state), None


def _segment_prompt(state: PipelineState, label: str, text: str, before: str,
                    after: str, revising: bool) -> str:
    prompt = f"""Rewrite for dialect authenticity and warmth — ONE SEGMENT only.

SEGMENT LABEL: {label}

DIALECT REGISTER: {state.get("dialect_register", "")}
CODE-SWITCHING LEVEL: {state.get("code_switching_level", "")}

THIS SEGMENT (rewrite ONLY this; keep its header, cues, notes and timestamps):
{text}

LAST LINES BEFORE IT (for flow — do NOT rewrite them):
{chapters.last_lines(before, 150) or "(none)"}

FIRST LINES AFTER IT (for flow — do NOT rewrite them):
{chapters.first_lines(after, 120) or "(none)"}

PRESENTER PROFILE:
{state.get("presenter_profile", "")}

AVOID LIST:
{state.get("avoid_list", "")}

LENGTH: this segment has {chapters.spoken_words(text)} spoken words. Keep the rewrite within ±5% of that — change wording, do not add new sentences, examples or asides (the video's length is already budgeted).

Rewrite ONLY this segment; keep its header exactly as it is. Do not rewrite anything outside it. Score both dialect and warmth dimensions for this segment. Flag anything ambiguous for the fidelity check.

After the rewritten segment, add a line exactly:
=== SEGMENT NOTES ===
then your scores and flags for this segment."""

    if revising:
        prompt += f"""

---

## MODE: REVISION PASS (Pass #{state.get("quality_revision_count", 0)})

Pay special attention to anything the critique flagged under DIALECT AUTHENTICITY or WARMTH/DE-CLINICALIZATION. Fix ONLY what's flagged. Never reintroduce a claim the truth verification or fidelity audit below corrected or flagged.

AUDIT / CRITIQUE REPORT:
{state.get("self_critique_output", "")}

MEDICAL TRUTH VERIFICATION REPORT (score {state.get("truth_score", 0)}/10):
{state.get("truth_verification_report", "")}

MEDICAL FIDELITY AUDIT:
{state.get("fidelity_audit_output", "")}"""
    return prompt


# A dialect rewrite of one segment may grow at most this much before it is redone.
DIALECT_GROWTH_MAX = 1.1


def _dialect_chapter_mode(state: PipelineState, system_prompt: str, revising: bool) -> dict:
    segments, frame = _dialect_segments(state, revising)
    workers = int(state.get("chapter_workers", 4) or 4)

    def rewrite(job):
        i, (label, text) = job
        before = segments[i - 1][1] if i > 0 else ""
        after = segments[i + 1][1] if i + 1 < len(segments) else ""
        prompt = _segment_prompt(state, label, text, before, after, revising)
        budget = chapters.budget_for_words(len(text.split()))
        answer = call_llm(system_prompt, prompt, temperature=0.75, max_tokens=budget)
        script, notes = chapters.split_segment_answer(answer)
        original = chapters.spoken_words(text)
        grown = chapters.spoken_words(script)
        if original >= 50 and grown > original * DIALECT_GROWTH_MAX:
            # Models ignore the ±5% line: in a 20-minute run this pass added 865 words.
            print(f"  🗣️ {label}: dialect rewrite grew {original} → {grown} words — asking again")
            retry = call_llm(system_prompt, prompt + (
                f"\n\nYOUR LAST REWRITE WAS {grown} SPOKEN WORDS; THE SEGMENT HAS {original}. "
                f"Rewrite it again in at most {int(original * 1.05)} words: change wording only, "
                "add nothing new."), temperature=0.5, max_tokens=budget)
            script2, notes2 = chapters.split_segment_answer(retry)
            if chapters.spoken_words(script2) <= original * DIALECT_GROWTH_MAX:
                return label, script2, notes2
            print(f"  🗣️ {label}: still too long; keeping the segment as written")
            return label, text, notes
        return label, script, notes

    results = chapters.parallel_map(rewrite, list(enumerate(segments)), workers)
    joined = chapters.clean_spoken_script(chapters.join_chapters([script for _, script, _ in results]))
    notes = [(label, note) for label, _, note in results if note]
    output = joined
    if notes:
        output += "\n\n## SEGMENT NOTES\n\n" + "\n\n".join(f"### {label}\n{note}" for label, note in notes)
    result = {"dialect_warmth_output": output, "dialect_script": joined}
    if frame:
        result["script_frame"] = frame
    return result


def dialect_warmth_layer(state: PipelineState) -> dict:
    system_prompt = load_prompt("dialect_warmth_layer")

    revising = state.get("quality_revision_count", 0) > 0 and state.get("refined_script", "")
    if chapters.chapter_mode_on(state.get("blueprint")):
        return _dialect_chapter_mode(state, system_prompt, bool(revising))

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


# --------------------------------------------------------------------------
# refinement + whole-script integrity pass
# --------------------------------------------------------------------------
def _target_words(blueprint: dict) -> int:
    try:
        chapter_words = sum(int(float(c.get("words", 0) or 0))
                            for c in (blueprint.get("chapters") or []) if isinstance(c, dict))
    except (TypeError, ValueError):
        chapter_words = 0
    if chapter_words > 0:
        return chapter_words
    try:
        minutes = float(blueprint.get("target_minutes", 0) or 0)
        wpm = float(blueprint.get("words_per_minute", 135) or 135)
        if minutes > 0:
            return int(minutes * wpm)
    except (TypeError, ValueError):
        pass
    return 0


def _compact_view(state: PipelineState, script: str) -> str:
    chapter_list = [c for c in ((state.get("blueprint") or {}).get("chapters") or []) if isinstance(c, dict)]
    titled = [(t, x) for (t, x) in chapters.split_by_chapters(script) if t]
    memory = state.get("chapter_memory") or {}
    lines = []
    for i, chapter in enumerate(chapter_list):
        cid = str(chapter.get("id", "") or "").strip()
        title = str(chapter.get("title", "") or "").strip()
        segment = next((x for (t, x) in titled if title and t == title), None)
        if segment is None and i < len(titled):
            segment = titled[i][1]
        segment = segment or ""
        mem = memory.get(cid) or memory.get(title) or {}
        terms = ", ".join(str(t.get("term")) if isinstance(t, dict) else str(t)
                          for t in (mem.get("terms_defined") or []))
        lines.append("\n".join([
            f"### {cid}: {title} — {len(segment.split())} words (target {chapter.get('words', 0)})",
            f"SUMMARY: {str(mem.get('summary', '') or '').strip() or '(none)'}",
            f"TERMS DEFINED: {terms or '(none)'}",
            f"FACTS USED: {', '.join(str(f) for f in (mem.get('facts_used') or [])) or '(none)'}",
            f"LOOPS OPEN: {', '.join(str(l) for l in (mem.get('loops_open') or [])) or '(none)'}",
            f"FIRST ~120 WORDS: {chapters.first_lines(segment, 120)}",
            f"LAST ~120 WORDS: {chapters.last_lines(segment, 120)}",
        ]))
    return "\n\n".join(lines)


def _integrity_prompt(state: PipelineState, script: str, target_words: int) -> str:
    blueprint = state.get("blueprint") or {}
    words = len((script or "").split())
    parts = [f"SCRIPT BLUEPRINT (JSON):\n{json.dumps(blueprint, ensure_ascii=False, indent=2)}",
             f"TARGET WORDS: {target_words or '(not set)'}"]
    if words <= 2200:
        parts.append(f"FULL SCRIPT (word count {words}):\n{script}")
    else:
        parts.append(f"COMPACT VIEW PER CHAPTER (the full script is {words} words):\n{_compact_view(state, script)}")
    return "\n\n".join(parts)


def _refinement_chapter_mode(state: PipelineState, blueprint: dict) -> dict:
    # (a) Code assembly of the dialect output segments, in order; dropped labels/notes.
    assembled = state.get("dialect_script") or chapters.strip_segment_notes(state.get("dialect_warmth_output", ""))
    assembled = chapters.clean_spoken_script(assembled)
    target_words = _target_words(blueprint)
    try:
        data, _ = call_llm_json(load_prompt("script_integrity"),
                                _integrity_prompt(state, assembled, target_words),
                                temperature=0.3, max_tokens=6000, required=["edits"])
    except UnreadableAnswer:
        data = None
    if data is None:
        print("  🧵 integrity pass: answer unreadable; keeping the assembled script")
        return {"refined_script": assembled,
                "integrity_report": {"score": 0, "summary": "integrity pass failed; assembled script kept",
                                     "issues": [], "applied": 0, "skipped": 0}}
    # (c) Apply edits in code; skipped edits are logged, then clean again.
    edited, applied, skipped, log = chapters.apply_integrity_edits(
        assembled, blueprint, pick(data, "edits", default=[]) or [])
    edited = chapters.clean_spoken_script(edited)
    for line in log:
        print(f"  🧵 integrity: {line}")
    report = {"score": pick(data, "integrity_score", kind=int) or 0,
              "summary": str(pick(data, "summary", default="") or ""),
              "issues": pick(data, "issues", default=[]) or [],
              "applied": applied, "skipped": skipped}
    # (d)
    return {"refined_script": edited, "integrity_report": report}


def script_refinement(state: PipelineState) -> dict:
    system_prompt = load_prompt("script_refinement")
    blueprint = state.get("blueprint") or {}

    if chapters.chapter_mode_on(blueprint):
        return _refinement_chapter_mode(state, blueprint)

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
