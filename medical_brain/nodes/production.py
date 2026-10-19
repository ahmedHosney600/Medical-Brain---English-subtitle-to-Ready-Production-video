"""Post-production layers: transitions, text/overlay animations, B-roll prompts, production critique.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data.

Long videos (a blueprint with >=2 chapters) run the three design steps chapter by
chapter, join the results under "=== chapter title ===" separators and renumber the
tables globally; a one-chapter blueprint keeps the original single-call behaviour."""

import math
import re

from ..exports import workbook as ew
from ..llm import call_llm, call_llm_json
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import UnreadableAnswer, pick
from . import chapters


# --------------------------------------------------------------------------
# chapter-mode helpers for the three design steps
# --------------------------------------------------------------------------
def _span_key(span: dict, i: int) -> str:
    return span["id"] or span["title"] or f"ch{i}"


def _span_label(span: dict, i: int, n: int) -> str:
    return (f"id: {span['id']} | title: {span['title']} | chapter {i + 1} of {n} | "
            f"from {chapters.fmt_time(span['start_s'])} to {chapters.fmt_time(span['end_s'])}")


def _context_lines(spans: list, i: int) -> tuple:
    previous = spans[i - 1]["text"] if i > 0 else ""
    following = spans[i + 1]["text"] if i + 1 < len(spans) else ""
    return (chapters.last_lines(previous, 60) or "(none — this is the first chapter)",
            chapters.first_lines(following, 60) or "(none — this is the last chapter)")


def _style_block(output: str) -> str:
    """The first ~40 lines of chapter 1's answer for this step (the consistency anchor)."""
    lines = [line for line in (output or "").splitlines() if line.strip()]
    return "\n".join(lines[:40])


def _revision_note(state: PipelineState, previous: str, what: str) -> str:
    if int(state.get("production_revision_count", 0) or 0) <= 0:
        return ""
    return f"""

---

## MODE: REVISION PASS (Pass #{state.get("production_revision_count", 0)})

Apply ONLY the fixes the Production Critique Report flags for THIS CHAPTER's {what}. Keep every other entry exactly as it was.

PREVIOUS OUTPUT FOR THIS CHAPTER:
{previous or "(none)"}

PRODUCTION CRITIQUE REPORT:
{state.get("production_critique_output", "")}"""


def _flagged_indices(state: PipelineState, spans: list) -> list:
    """Chapter indices named by id or title in the production critique (revision pass)."""
    report = state.get("production_critique_output", "") or ""
    flagged = []
    for i, span in enumerate(spans):
        cid, title = span["id"], span["title"]
        if (cid and re.search(rf"(?<![A-Za-z0-9]){re.escape(cid)}(?![A-Za-z0-9])", report)) \
                or (title and title in report):
            flagged.append(i)
    return flagged


def _run_per_chapter(state: PipelineState, step_key: str, spans: list, workers: int,
                     generate) -> tuple:
    """generate(i, style, previous) -> chapter text. Chapter 1 runs first (its answer is
    the PRODUCTION STYLE anchor for the rest); later chapters run in parallel. A revision
    pass re-runs only the chapters the critique flags, keeping the others' previous text."""
    revision = int(state.get("production_revision_count", 0) or 0) > 0
    stored = dict((state.get("production_chapter_outputs") or {}).get(step_key) or {})
    keys = [_span_key(span, i) for i, span in enumerate(spans)]

    rerun = set(range(len(spans)))
    if revision and stored:
        flagged = _flagged_indices(state, spans)
        if flagged:
            rerun = set(flagged)
    for i, key in enumerate(keys):
        if key not in stored:                # nothing to reuse for this chapter
            rerun.add(i)

    outputs = dict(stored)
    if 0 in rerun or not outputs.get(keys[0]):
        outputs[keys[0]] = generate(0, "", stored.get(keys[0], ""))

    rest = sorted(i for i in rerun if i != 0)
    if rest:
        style = _style_block(outputs.get(keys[0], ""))
        results = chapters.parallel_map(lambda i: (i, generate(i, style, stored.get(keys[i], ""))),
                                        rest, workers)
        for i, text in results:
            outputs[keys[i]] = text

    titles = [span["title"] or span["id"] for span in spans]
    texts = [outputs.get(keys[i], "") for i in range(len(spans))]
    return outputs, titles, texts, keys


def _join_chapter_outputs(titles: list, texts: list) -> str:
    """Join per-chapter answers under "=== title ===" separators, one table header row,
    "#" renumbered globally."""
    blocks = []
    for title, text in zip(titles, texts):
        body = (text or "").strip()
        if body:
            blocks.append(f"=== {title} ===\n{body}")
    return chapters.renumber_tables("\n\n".join(blocks))


def _find_prompt_table(lines: list):
    """(start, end, data_start) of the first table whose header names a prompt column."""
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < n and lines[j].strip().startswith("|"):
            j += 1
        header_cells = [c.strip() for c in chapters.split_pipes(lines[i])]
        if any("PROMPT" in c.upper() for c in header_cells):
            separator = j > i + 1 and chapters.is_separator_row(lines[i + 1])
            return i, j, i + (2 if separator else 1)
        i = j
    return None


def _join_broll(titles: list, texts: list, keys: list) -> tuple:
    """Joins the per-chapter B-roll answers. The table itself is consolidated into ONE
    contiguous block (one header row, rows in chapter order, "#" renumbered globally) so
    the existing export/engagement parsers keep reading every row. Chapter separators and
    the non-table text are kept. Also returns {chapter_key: [global B-roll #]}."""
    pieces, all_rows = [], []
    mapping, counter, first = {}, 0, None
    for title, text, key in zip(titles, texts, keys):
        lines = (text or "").splitlines()
        found = _find_prompt_table(lines)
        if not found:
            pieces.append([title, lines, [], None, "|---|"])
            continue
        start, end, data_start = found
        header = lines[start]
        separator = lines[start + 1] if data_start == start + 2 else "|---|"
        number_col = next((k for k, c in enumerate(chapters.split_pipes(header))
                           if c.strip() == "#"), None)
        rows = []
        for row in lines[data_start:end]:
            if chapters.is_separator_row(row):
                continue
            if number_col is not None:
                counter += 1
                cells = chapters.split_pipes(row)
                if number_col < len(cells):
                    cells[number_col] = f" {counter} "
                    row = "|".join(cells)
                mapping.setdefault(key, []).append(counter)
            rows.append(row)
        all_rows.extend(rows)
        pieces.append([title, lines[:start], lines[end:], header, separator])
        if first is None:
            first = len(pieces) - 1

    # The chapter answers often repeat their own "=== title ===" (it was printed twice),
    # and every chapter but the first kept an empty "B-Roll Prompt Table" heading: a
    # critique read that as "25 of 37 B-rolls have no generation prompt".
    table_head = re.compile(r"(?i)^\s*#{2,6}\s*.*\bprompt table\b")
    out_lines = []
    for index, (title, prefix, suffix, header, separator) in enumerate(pieces):
        out_lines.append(f"=== {title} ===")
        prefix = [line for line in prefix if line.strip() != f"=== {title} ===".strip()]
        if header is not None:
            if index == first:
                prefix = [re.sub(r"(?i)\bprompt table\b.*$", "Prompt Table (whole video, every chapter, in time order)",
                                 line) if table_head.match(line) else line for line in prefix]
            else:
                prefix = [("_This chapter's B-roll prompts are rows of the whole-video B-Roll Prompt Table in the "
                           "first chapter (one table, in time order)._") if table_head.match(line) else line
                          for line in prefix]
        out_lines.extend(prefix)
        if index == first and header is not None:
            out_lines.append(header)
            out_lines.append(separator)
            out_lines.extend(all_rows)
        out_lines.extend(suffix)
        out_lines.append("")
    return "\n".join(out_lines).rstrip(), mapping


def _store_step(state: PipelineState, step_key: str, outputs: dict) -> dict:
    all_outputs = dict(state.get("production_chapter_outputs") or {})
    all_outputs[step_key] = outputs
    return all_outputs


def _rewrite_chapter_times(outputs: dict, spans: list, keys: list, state: PipelineState) -> None:
    """Puts every chapter's transition/overlay table on the script's clock, using the
    chapter's start word position so a repeated cue resolves to the right occurrence."""
    script = state.get("refined_script", "")
    wpm = ew.blueprint_wpm(state.get("blueprint"))
    if not script:
        return
    for i, key in enumerate(keys):
        after = int(round(spans[i]["start_s"] * wpm / 60.0)) if wpm else 0
        outputs[key] = ew.rewrite_cue_timestamps(outputs.get(key, ""), script, wpm, after=after)


# --------------------------------------------------------------------------
# step 1 — transitions
# --------------------------------------------------------------------------
def _transition_chapter_prompt(state: PipelineState, spans: list, i: int,
                               style: str, previous: str) -> str:
    span = spans[i]
    last, first = _context_lines(spans, i)
    prompt = f"""Design the transition map for ONE CHAPTER of this finalized script.

THIS CHAPTER: {_span_label(span, i, len(spans))}

THIS CHAPTER'S SCRIPT:
{span['text']}

LAST LINES OF THE PREVIOUS CHAPTER:
{last}

FIRST LINES OF THE NEXT CHAPTER:
{first}

RESTRUCTURE STRATEGY PLAN (act breakdown, energy curve, pacing plan):
{state.get("strategy_plan", "")}

B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
TARGET PLATFORM: {state.get("target_platform", "")}

Read through this chapter's script section by section, noting every scene change inside it, and design a transition for each, following the principles above.

Every Transition Table row must include the ▶️ START CUE (Arabic) column: 4–8 consecutive spoken words copied exactly from this chapter's script where the transition happens. Code re-times every row from that cue, so it is the shared clock for all layers."""
    prompt += visual_events_block(state, spans[i].get("start_s"), spans[i].get("end_s"))
    if style:
        prompt += f"""

PRODUCTION STYLE (chapter 1's map — keep the same transition palette, easing vocabulary and energy language so the chapters look like one video):
{style}"""
    prompt += _revision_note(state, previous, "transitions")
    return prompt


def _transition_chapter_mode(state: PipelineState, system_prompt: str, blueprint: dict) -> dict:
    spans = chapters.chapter_spans(state.get("refined_script", ""), blueprint)
    if len(spans) < 2:
        return _transition_single_call(state, system_prompt)
    system_prompt = system_prompt + "\n\n" + load_prompt("production_chapter_mode")
    workers = int(state.get("chapter_workers", 4) or 4)

    def generate(i, style, previous):
        span = spans[i]
        return call_llm(system_prompt,
                        _transition_chapter_prompt(state, spans, i, style, previous),
                        temperature=0.4,
                        max_tokens=chapters.budget_for_words(len(span["text"].split())))

    outputs, titles, texts, keys = _run_per_chapter(state, "transition_design", spans, workers, generate)
    _rewrite_chapter_times(outputs, spans, keys, state)
    joined = _join_chapter_outputs(titles, [outputs.get(keys[i], "") for i in range(len(spans))])
    joined = ew.rewrite_cue_timestamps(joined, state.get("refined_script", ""),
                                       ew.blueprint_wpm(state.get("blueprint")))
    return {"transition_design": joined,
            "production_chapter_outputs": _store_step(state, "transition_design", outputs)}


def _transition_single_call(state: PipelineState, system_prompt: str) -> dict:
    user_prompt = f"""Design the complete transition map for this finalized script.

FINALIZED SCRIPT:
{state.get("refined_script", "")}

RESTRUCTURE STRATEGY PLAN (act breakdown, energy curve, pacing plan):
{state.get("strategy_plan", "")}

B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
TARGET PLATFORM: {state.get("target_platform", "")}

Read through the script section by section, noting every scene change (talking head → B-roll, section → section, energy shift). Design a transition for each, following the principles above."""
    user_prompt += visual_events_block(state)

    # Only include revision mode block when we're actually revising
    if state.get("production_revision_count", 0) > 0:
        user_prompt += f"""

---

## MODE: REVISION PASS (Pass #{state.get("production_revision_count", 0)})

CRITICAL INSTRUCTION: the B-roll table and overlays were revised before you, so the VISUAL EVENTS list above is the truth now — your previous map may point at B-rolls or moments that changed.
- Rebuild the map so every cut matches the VISUAL EVENTS list; keep your previous map's style, palette and every transition that still matches a visual event.
- Apply every fix the Production Critique Report flags for transitions.

PREVIOUS TRANSITION DESIGN MAP:
{state.get("transition_design", "")}

PRODUCTION CRITIQUE REPORT:
{state.get("production_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.4, max_tokens=5000)
    response = ew.rewrite_cue_timestamps(response, state.get("refined_script", ""),
                                         ew.blueprint_wpm(state.get("blueprint")))
    return {"transition_design": response}


def visual_events_block(state: PipelineState, low: float = None, high: float = None) -> str:
    """The visuals already on the timeline when transitions are designed (they come last):
    every B-roll start/end, whiteboard drawing and warning card, with its script time and
    cue, so the transition map cuts exactly to them instead of guessing."""
    script = state.get("refined_script", "") or ""
    if not script:
        return ""
    wpm = ew.blueprint_wpm(state.get("blueprint"))
    events = []
    rows = ew.broll_rows(ew.realign_broll_table(state.get("broll_prompts", "") or "")[0])
    starts, ends = _broll_row_times(rows, script, wpm)
    for row, start, end in zip(rows, starts, ends):
        if start is not None:
            events.append((start, f"B-roll #{row.get('num', '')} starts — cue «{row.get('start', '')}»"))
        if end is not None:
            events.append((end, f"B-roll #{row.get('num', '')} ends after «{row.get('end', '')}» is spoken "
                                f"(cut back to the presenter here, on the next words)"))
    guide = state.get("text_animation_overlay", "") or ""
    for n, (a, b) in enumerate(_drawing_windows(guide, script, wpm), 1):
        events.append((a, f"Whiteboard Drawing {n} on screen until {_fmt_seconds(b)} (full screen — no other cut inside)"))
    for a, b in _overlay_warning_holds(guide, script, wpm):
        events.append((a, f"Warning-card OVERLAY on top of the presenter until {_fmt_seconds(b)} — not a separate "
                          "shot: no transition into or out of it, and no cut away while it is up"))
    events = sorted((t, e) for t, e in events if (low is None or t >= low - 1) and (high is None or t < high + 1))
    if not events:
        return ""
    return ("\n\nVISUAL EVENTS ALREADY ON THE TIMELINE (designed before you — cut INTO and OUT OF these "
            "exactly, at their cues; add your own transitions only between them, never a second visual on top "
            "of a drawing or B-roll):\n" + "\n".join(f"- {_fmt_seconds(t)} {e}" for t, e in events))


def transition_designer(state: PipelineState) -> dict:
    system_prompt = load_prompt("transition_designer")
    blueprint = state.get("blueprint") or {}
    if chapters.chapter_mode_on(blueprint):
        result = _transition_chapter_mode(state, system_prompt, blueprint)
    else:
        result = _transition_single_call(state, system_prompt)
    # The B-roll table and overlays exist already: sync the new map to them and re-derive
    # every cross-layer link now (the B-roll step used to do this before a map existed).
    merged = dict(state)
    merged.update(result)
    return _sync_after_broll(merged, merged.get("broll_prompts", ""), result,
                             broll_map=merged.get("_broll_timeline_map"))


# --------------------------------------------------------------------------
# step 2 — text animation / overlay
# --------------------------------------------------------------------------
def _overlay_chapter_prompt(state: PipelineState, spans: list, i: int,
                            style: str, previous: str) -> str:
    span = spans[i]
    last, first = _context_lines(spans, i)
    transitions = chapters.chapter_section(state.get("transition_design", ""), span["title"])
    prompt = f"""Design all on-screen text animations, overlays, and conditional drawing animations for ONE CHAPTER of this finalized script.

THIS CHAPTER: {_span_label(span, i, len(spans))}

THIS CHAPTER'S SCRIPT:
{span['text']}

THIS CHAPTER'S TRANSITION MAP (respect timing — don't animate over transitions):
{transitions or "(none provided)"}

LAST LINES OF THE PREVIOUS CHAPTER:
{last}

FIRST LINES OF THE NEXT CHAPTER:
{first}

RESTRUCTURE STRATEGY PLAN (terminology retention, act breakdown):
{state.get("strategy_plan", "")}

B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
TARGET PLATFORM: {state.get("target_platform", "")}

Read this chapter's script and transition map together. Classify every element as one of the 5 primary types: TOP-RIGHT POPUP, TEXT OVERLAY TITLE, WARNING/ALERT BOX, QUOTE BOX, or KINETIC TEXT. For every English technical term in the script, generate a TOP-RIGHT POPUP with its Arabic translation. For every section/chapter transition, generate a TEXT OVERLAY TITLE. For medical disclaimers and warnings, use WARNING/ALERT BOX. For the most impactful sentences, use QUOTE BOX. For statistics and key terms, use KINETIC TEXT. Include START CUE and END CUE (exact first/last Arabic words from the script that trigger each element). For complex medical mechanisms that are hard to follow verbally, consider whether a drawing animation would genuinely help comprehension.

Other chapters are designed at the same time as this one: draw only a mechanism or metaphor this chapter's script INTRODUCES. When the script calls back an analogy from an earlier chapter (it names the same pump, switch or picture again), add no drawing — the earlier chapter already drew it, and a second full-screen drawing of the same metaphor reads as a rerun."""
    if style:
        prompt += f"""

PRODUCTION STYLE (chapter 1's guide — keep the same palette, fonts, corner radius and animation family so the chapters look like one video):
{style}"""
    prompt += _revision_note(state, previous, "elements")
    return prompt


def _overlay_chapter_mode(state: PipelineState, system_prompt: str, blueprint: dict) -> dict:
    spans = chapters.chapter_spans(state.get("refined_script", ""), blueprint)
    if len(spans) < 2:
        return _overlay_single_call(state, system_prompt)
    system_prompt = system_prompt + "\n\n" + load_prompt("production_chapter_mode")
    workers = int(state.get("chapter_workers", 4) or 4)

    def generate(i, style, previous):
        span = spans[i]
        return call_llm(system_prompt,
                        _overlay_chapter_prompt(state, spans, i, style, previous),
                        temperature=0.5,
                        max_tokens=chapters.budget_for_words(len(span["text"].split())))

    outputs, titles, texts, keys = _run_per_chapter(state, "text_animation_overlay", spans, workers, generate)
    _rewrite_chapter_times(outputs, spans, keys, state)
    joined = _join_chapter_outputs(titles, [outputs.get(keys[i], "") for i in range(len(spans))])
    joined = relocate_unfound_overlay_cues(restore_missing_headers(joined), state.get("refined_script", ""),
                                           ew.blueprint_wpm(state.get("blueprint")))
    joined = retime_drawing_specs(joined, state.get("refined_script", ""), ew.blueprint_wpm(state.get("blueprint")))
    joined = ew.rewrite_cue_timestamps(joined, state.get("refined_script", ""),
                                       ew.blueprint_wpm(state.get("blueprint")))
    joined = move_overlays_out_of_drawings(fix_overlay_read_times(joined), state.get("refined_script", ""),
                                           ew.blueprint_wpm(state.get("blueprint")))
    joined = order_overlay_cards(joined, state.get("refined_script", ""), ew.blueprint_wpm(state.get("blueprint")))
    return {"text_animation_overlay": joined,
            "production_chapter_outputs": _store_step(state, "text_animation_overlay", outputs)}


def _overlay_single_call(state: PipelineState, system_prompt: str) -> dict:
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
    response = relocate_unfound_overlay_cues(response, state.get("refined_script", ""),
                                             ew.blueprint_wpm(state.get("blueprint")))
    response = retime_drawing_specs(response, state.get("refined_script", ""), ew.blueprint_wpm(state.get("blueprint")))
    response = ew.rewrite_cue_timestamps(response, state.get("refined_script", ""),
                                         ew.blueprint_wpm(state.get("blueprint")))
    wpm = ew.blueprint_wpm(state.get("blueprint"))
    response = move_overlays_out_of_drawings(fix_overlay_read_times(response), state.get("refined_script", ""), wpm)
    return {"text_animation_overlay": order_overlay_cards(response, state.get("refined_script", ""), wpm)}


def text_animation_overlay_designer(state: PipelineState) -> dict:
    system_prompt = load_prompt("text_animation_overlay_designer")
    blueprint = state.get("blueprint") or {}
    if chapters.chapter_mode_on(blueprint):
        return _overlay_chapter_mode(state, system_prompt, blueprint)
    return _overlay_single_call(state, system_prompt)


# --------------------------------------------------------------------------
# step 3 — B-roll prompts
# --------------------------------------------------------------------------
def _broll_chapter_prompt(state: PipelineState, spans: list, i: int,
                          style: str, previous: str) -> str:
    span = spans[i]
    last, first = _context_lines(spans, i)
    transitions = chapters.chapter_section(state.get("transition_design", ""), span["title"])
    overlays = chapters.chapter_section(state.get("text_animation_overlay", ""), span["title"])
    prompt = f"""Generate AI-ready B-roll prompts for ONE CHAPTER of this finalized script.

{_script_length_line(span.get("text", ""), state.get("blueprint"))}

THIS CHAPTER: {_span_label(span, i, len(spans))}

THIS CHAPTER'S SCRIPT:
{span['text']}

THIS CHAPTER'S TRANSITION DESIGN MAP (match B-roll framing to transition energy and type):
{transitions or "(none provided)"}

THIS CHAPTER'S TEXT ANIMATION & OVERLAY GUIDE (leave compositional space for overlays):
{overlays or "(none provided)"}

LAST LINES OF THE PREVIOUS CHAPTER:
{last}

FIRST LINES OF THE NEXT CHAPTER:
{first}

RESTRUCTURE STRATEGY PLAN (analogy bank — only use local visual context where the script explicitly references local analogies):
{state.get("strategy_plan", "")}

SOURCE SCRIPT ANALYSIS + MEDICAL FACT LEDGER (medical accuracy check for B-roll imagery):
{state.get("source_analysis", "")}

B-ROLL AVAILABILITY: {state.get("broll_availability", "")}
TARGET PLATFORM: {state.get("target_platform", "")}

For each [VISUAL NOTE] in this chapter and each B-roll transition in its transition map, produce a detailed AI generation prompt — plus extra B-roll wherever an explanation runs past ~40 seconds with no visual change (engagement principle G). Mark each as 🖼️ Image or 🎬 Video. Coordinate with text overlay positions. Remember: prefer educational/medical contexts for B-roll, and use male subjects when people appear. Number the rows from 1; code renumbers them across the whole video."""
    if style:
        prompt += f"""

PRODUCTION STYLE (chapter 1's B-roll prompts — keep the same colour grade, lens family and channel look so the chapters look like one video):
{style}"""
    prompt += _revision_note(state, previous, "B-roll rows")
    return prompt


def _broll_chapter_mode(state: PipelineState, system_prompt: str, blueprint: dict) -> dict:
    spans = chapters.chapter_spans(state.get("refined_script", ""), blueprint)
    if len(spans) < 2:
        return _broll_single_call(state, system_prompt)
    system_prompt = system_prompt + "\n\n" + load_prompt("production_chapter_mode")
    workers = int(state.get("chapter_workers", 4) or 4)

    def generate(i, style, previous):
        span = spans[i]
        return call_llm(system_prompt,
                        _broll_chapter_prompt(state, spans, i, style, previous),
                        temperature=0.6,
                        max_tokens=chapters.budget_for_words(len(span["text"].split())))

    outputs, titles, texts, keys = _run_per_chapter(state, "broll_prompts", spans, workers, generate)
    # Repair the model's own tables before storing: camera moves for video rows, then
    # real M:SS timestamps from the cues (revision passes and the critique then see them).
    wpm = ew.blueprint_wpm(state.get("blueprint"))
    for i, key in enumerate(keys):
        after = int(round(spans[i]["start_s"] * wpm / 60.0)) if wpm else 0
        # Only the mechanical per-table fixes here; timeline repair (moving clustered
        # rows and filling gaps) runs once on the stitched table below, where the whole
        # table and script are known.
        outputs[key] = finalize_broll_table(state, outputs.get(key, ""), after=after, repair=False)
    texts = [outputs.get(keys[i], "") for i in range(len(spans))]
    joined, mapping = _join_broll(titles, texts, keys)
    state.pop("_broll_timeline_map", None)
    joined = finalize_broll_table(state, joined)
    broll_map = state.pop("_broll_timeline_map", {})
    return _sync_after_broll(state, joined, {
        "broll_by_chapter": mapping,
        "production_chapter_outputs": _store_step(state, "broll_prompts", outputs)},
        broll_map=broll_map)


def _broll_single_call(state: PipelineState, system_prompt: str) -> dict:
    user_prompt = f"""Generate AI-ready B-roll prompts for every visual moment in this script.

{_script_length_line(state.get("refined_script", ""), state.get("blueprint"))}

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

For each [VISUAL NOTE] in the script and each B-roll transition in the transition map, produce a detailed AI generation prompt — plus extra B-roll wherever an explanation runs past ~40 seconds with no visual change (engagement principle G). Mark each as 🖼️ Image or 🎬 Video. Coordinate with text overlay positions. Remember: prefer educational/medical contexts for B-roll, and use male subjects when people appear."""

    if state.get("production_revision_count", 0) > 0:
        user_prompt += f"""

---

## MODE: REVISION PASS (Pass #{state.get("production_revision_count", 0)})

Revise YOUR PREVIOUS B-ROLL PROMPTS below — don't start over: keep every row the critique didn't flag (same wording), fix each flagged row, and add rows to fill every gap the critique or the code checks name. Output the complete table.

PREVIOUS B-ROLL PROMPTS:
{state.get("broll_prompts", "")}

PRODUCTION CRITIQUE REPORT:
{state.get("production_critique_output", "")}"""

    response = call_llm(system_prompt, user_prompt, temperature=0.6, max_tokens=9000)
    state.pop("_broll_timeline_map", None)
    table = finalize_broll_table(state, response)
    broll_map = state.pop("_broll_timeline_map", {})
    return _sync_after_broll(state, table, broll_map=broll_map)


def _sync_after_broll(state: PipelineState, table: str, result: dict = None,
                      broll_map=None) -> dict:
    """Stores the final B-roll table and syncs the transition map to it (the map's
    designer ran before repair_broll_timeline added its gap-fill cuts). The two
    renumbering maps are then applied to the other layers, so their links keep naming
    the rows that still exist."""
    out = dict(result or {})
    transition = state.get("transition_design", "")
    overlay = state.get("text_animation_overlay", "")
    script = state.get("refined_script", "")
    broll_map = broll_map or {}

    # B-roll references in the already-written layers must be fixed before the sync
    # adds its code-generated cuts, otherwise a new B-roll number could be mistaken
    # for an old one and rewritten twice.
    if broll_map:
        transition = relink_references(transition, "broll", broll_map)
        overlay = relink_references(overlay, "broll", broll_map)

    transition_map = {}
    if transition and table and script:
        broll_changed, map_holder = [], []
        transition, _added = sync_transition_map(
            state, transition, table, script, ew.blueprint_wpm(state.get("blueprint")),
            broll_out=broll_changed, map_out=map_holder)
        if broll_changed:
            table = broll_changed[-1]
        if map_holder:
            transition_map = map_holder[0]

    if transition_map:
        overlay = relink_references(overlay, "transition", transition_map)
        table = relink_references(table, "transition", transition_map)

    if script:
        overlay, table, transition = recompute_link_columns(
            overlay, table, transition, script, ew.blueprint_wpm(state.get("blueprint")))

    out["transition_design"] = transition
    out["text_animation_overlay"] = overlay
    out["broll_prompts"] = table
    return out


LINK_TOLERANCE_SECONDS = 3.0


def _table_row_times(text: str, script: str, wpm: float, need: str) -> list:
    """[(number, seconds)] for the rows of every table whose header contains `need`
    (e.g. "ELEMENT" for the overlay guide), timed by START CUE (running position) or
    Timestamp."""
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    out, pos = [], 0
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        upper = [c.upper() for c in _table_cells(lines[i])]
        if any(need in h for h in upper):
            num_col = next((k for k, h in enumerate(upper) if h.strip() == "#"), None)
            cue_col = next((k for k, h in enumerate(upper) if "START" in h or "▶" in h), None)
            time_col = next((k for k, h in enumerate(upper) if "TIMESTAMP" in h or h.strip() == "TIME"), None)
            separator = i + 1 < j and chapters.is_separator_row(lines[i + 1])
            for k in range(i + (2 if separator else 1), j):
                cells = _table_cells(lines[k])
                number = re.sub(r"[*_`\s]", "", cells[num_col]) if num_col is not None and num_col < len(cells) else ""
                if not re.fullmatch(r"\d+", number):        # "**2**" is row 2
                    continue
                seconds = None
                cue = cells[cue_col] if cue_col is not None and cue_col < len(cells) else ""
                if cue:
                    seconds = ew.cue_seconds(cue, script, wpm, after=pos)
                    later = [p for p in ew.cue_positions(cue, norm_script) if p >= pos]
                    if later:
                        pos = later[0]
                if seconds is None and time_col is not None and time_col < len(cells):
                    stamp = _seconds(cells[time_col])
                    seconds = float(stamp) if stamp is not None else None
                if seconds is not None:
                    out.append((number, seconds))
        i = j
    return out


_OVERLAY_REF = re.compile(r"(?i)\b(Overlay\s*#\s*)(\d+)\b")


def _relink_inline_overlays(broll: str, overlay_rows: list, script: str, wpm: float) -> tuple:
    """Inline "Overlay #N" mentions in a B-roll row (composition notes reserving space for
    a card) must name a card on screen during that clip: re-point to the one that is, or
    "—" (a critique found nine notes keyed to cards at other times or not existing)."""
    layout = _broll_layout(broll or "")
    if not layout or not overlay_rows:
        return broll, 0
    times = {n: t for n, t in overlay_rows}
    rows = [{"num": r["cells"][layout["cols"]["num"]] if layout["cols"]["num"] is not None else "",
             "start": r["cells"][layout["cols"]["start"]] if layout["cols"]["start"] is not None else "",
             "end": r["cells"][layout["cols"]["end"]] if layout["cols"]["end"] is not None else ""}
            for r in layout["rows"]]
    starts, ends = _broll_row_times(rows, script, wpm)
    changed = 0
    for row, st, en in zip(layout["rows"], starts, ends):
        if st is None:
            continue
        lo, hi = st - LINK_TOLERANCE_SECONDS, (en if en is not None else st + BROLL_CLIP_SECONDS[1]) + 1.0
        on_screen = sorted((t, n) for n, t in overlay_rows if lo <= t <= hi)

        def fix(m):
            nonlocal changed
            t = times.get(m.group(2))
            if t is not None and lo <= t <= hi:
                return m.group(0)
            changed += 1
            return f"{m.group(1)}{on_screen[0][1]}" if len(on_screen) == 1 else "—"
        row["cells"] = [_OVERLAY_REF.sub(fix, cell) for cell in row["cells"]]
    if not changed:
        return broll, 0
    return _render_broll_layout(layout, layout["rows"]), changed


def recompute_link_columns(overlay: str, broll: str, transition: str, script: str, wpm: float) -> tuple:
    """Rewrites every "Linked to Transition / Overlay / B-roll #" cell from the shared
    script clock: the row of that layer that starts within ±3 s of this row's start, or
    "—". Model-written links used per-chapter numbers that never matched the final tables
    (a run linked overlays to T#77–T#85 in a 75-row map). Inline references to rows that
    do not exist ("DO NOT OVERLAP T#36") become "—". Returns (overlay, broll, transition)."""
    wpm = ew.blueprint_wpm({"words_per_minute": wpm})
    layers = {
        "TRANSITION": ("T#", [(n, t) for n, t in zip(
            [re.sub(r"\D", "", _row_cell(r, b["cols"], "num")) for b in _transition_blocks(transition) for r in b["rows"]],
            _transition_row_times(_transition_blocks(transition), script, wpm)) if n and t is not None]),
        "OVERLAY": ("#", _table_row_times(overlay, script, wpm, "ELEMENT")),
        "B-ROLL": ("B-roll #", _table_row_times(broll, script, wpm, "PROMPT")),
    }
    known_transitions = {n for n, _ in layers["TRANSITION"][1]}
    known_brolls = {n for n, _ in layers["B-ROLL"][1]}

    def nearest(kind, when):
        prefix, rows = layers[kind]
        close = [(abs(t - when), n) for n, t in rows if abs(t - when) <= LINK_TOLERANCE_SECONDS]
        return f"{prefix}{min(close)[1]}" if close else "—"

    def rewrite(text, need):
        times = dict(_table_row_times(text, script, wpm, need))
        lines = (text or "").splitlines()
        i, changed = 0, 0
        while i < len(lines):
            if not lines[i].strip().startswith("|"):
                i += 1
                continue
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            upper = [c.upper() for c in _table_cells(lines[i])]
            links = {k: kind for k, h in enumerate(upper) if "LINKED" in h
                     for kind in ("TRANSITION", "OVERLAY", "B-ROLL") if kind in h.replace("BROLL", "B-ROLL")}
            num_col = next((k for k, h in enumerate(upper) if h.strip() == "#"), None)
            if links and num_col is not None and any(need in h for h in upper):
                separator = i + 1 < j and chapters.is_separator_row(lines[i + 1])
                for k in range(i + (2 if separator else 1), j):
                    cells = _table_cells(lines[k])
                    when = times.get(re.sub(r"[*_`\s]", "", cells[num_col])) if num_col < len(cells) else None
                    if when is None:
                        continue
                    for col, kind in links.items():
                        if col < len(cells):
                            value = nearest(kind, when)
                            if cells[col].strip() != value:
                                cells[col] = value
                                changed += 1
                    lines[k] = "| " + " | ".join(cells) + " |"
            i = j
        text = "\n".join(lines) + ("\n" if (text or "").endswith("\n") else "") if changed else text
        # Inline mentions of rows that do not exist.
        text = _TRANSITION_REF.sub(lambda m: m.group(0) if m.group(2) in known_transitions else "—", text or "")
        text = _BROLL_REF.sub(lambda m: m.group(0) if m.group(2) in known_brolls else "—", text)
        return text, changed

    overlay, a = rewrite(overlay, "ELEMENT")
    broll, b = rewrite(broll, "PROMPT")
    broll, c = _relink_inline_overlays(broll, layers["OVERLAY"][1], script, wpm)
    b += c
    if transition:
        transition = _BROLL_REF.sub(lambda m: m.group(0) if m.group(2) in known_brolls else "—", transition)
    if a + b:
        print(f"  🔧 cross-layer links: re-derived {a + b} link cells from the script clock")
    return overlay, broll, transition


def broll_prompt_generator(state: PipelineState) -> dict:
    system_prompt = load_prompt("broll_prompt_generator")
    blueprint = state.get("blueprint") or {}
    if chapters.chapter_mode_on(blueprint):
        return _broll_chapter_mode(state, system_prompt, blueprint)
    return _broll_single_call(state, system_prompt)


# Words that name a camera move — a video B-roll without one tends to look like dead stock.
_CAMERA_MOVE = re.compile(
    # Verb forms count too ("pushes slowly forward", "tracks from right to left"): unseen,
    # they got a second "slow push-in" appended and the doubled directions averaged out.
    r"\b(?:push[- ]?in|push(es|ing)?\b|pull[- ]?(back|out)|pull(s|ing)?\s+(back|out|away)|dolly|parallax|orbit|"
    r"tracks?\b|tracking|drift(s|ing)?\b|glid(e|es|ing)\b|arc(s|ing)?\s+(around|left|right)|rack[- ]focus|pan(s|ning)?\b|tilt|"
    r"crane|zoom|slide|time[- ]?lapse|slow[- ]?motion|handheld|gimbal|steadicam|fly[- ]?(through|over)|"
    r"camera (moves|drifts|glides|rises|descends|follows|circles))", re.IGNORECASE)
# On-screen words or numbers requested inside the generated image (AI garbles them).
_TEXT_IN_IMAGE = re.compile(r"[\"“«][^\"”»]{1,60}[\"”»]|\bnumbers? like\b|\breadout\b|\bthe words?\b", re.IGNORECASE)


def auto_repair_camera_moves(table: str) -> tuple:
    """Code-repairs a trivial omission: every 🎬/video B-roll row whose prompt has no
    camera move gets ", slow push-in camera move" inserted before its first Avoid/
    Negative note (or appended at the end). Returns (table, fixed_count)."""
    lines = (table or "").splitlines(keepends=True)
    c_type = c_prompt = None
    fixed = 0
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        if not cells:
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            head = [c.strip().upper() for c in re.split(r"(?<!\\)\|", lines[i - 1])[1:-1]] if i else []
            c_type = next((k for k, h in enumerate(head) if "TYPE" in h or "LAYER" in h), None)
            c_prompt = next((k for k, h in enumerate(head) if "PROMPT" in h), None)
            continue
        if c_type is None or c_prompt is None or max(c_type, c_prompt) >= len(cells):
            continue
        kind = cells[c_type]
        if "VIDEO" not in kind.upper() and "🎬" not in kind:
            continue
        prompt = cells[c_prompt]
        if _CAMERA_MOVE.search(prompt):
            continue
        move = ", slow push-in camera move"
        avoid = re.search(r"\b(avoid|negative)\b", prompt, re.IGNORECASE)
        if avoid:
            new_prompt = prompt[:avoid.start()].rstrip(" ,") + move + " " + prompt[avoid.start():]
        else:
            new_prompt = prompt.rstrip(" ,") + move
        parts = re.split(r"(?<!\\)\|", line)
        parts[c_prompt + 1] = f" {new_prompt.strip()} "
        lines[i] = "|".join(parts)
        fixed += 1
    return "".join(lines), fixed


GAP_FILL_SECONDS = 45
MAX_GAP_FILLS = 8
_MIN_BROLL_SPACING = 8
_LEADING_GAP_SECONDS = 20
_SEPARATOR_CELL = re.compile(r":?-{2,}:?")

# Cross-layer reference patterns. A renumbered table keeps its own "#" column; only
# text that names another row (and the "Linked to …" cells) is rewritten.
_BROLL_REF = re.compile(r"(?i)\b(B[- ]?rolls?\s*#?\s*)(\d+)\b")
_TRANSITION_REF = re.compile(r"(?i)(\b(?:T\s*#\s*|Transition\s*#\s*))(\d+)\b")
_ANY_REF = re.compile(
    r"(?i)\bB[- ]?rolls?\s*#?\s*\d+|\bT\s*#\s*\d+|\bTransition\s*#\s*\d+")
# A bare number in a "Linked to …" cell; not a scale/duration ("settled at 107%", "400ms").
_BARE_REF = re.compile(r"(?<![\w#.%:])(#?)\s*(\d+)\b(?!\s*(?:%|ms\b|s\b|px\b|x\b|°)|[.:]\d)")

_BROLL_MAP_KEYS = ("mapping", "map", "broll_map", "broll_number_map",
                   "number_map", "old_to_new", "renumbered")


class _BrollReport(dict):
    """Timeline-repair counters plus the old→new B-roll number map.

    The map is exposed as `report.mapping` and through the historical aliases above.
    It is deliberately not a normal dict key so older callers that compare the report
    to the four counter keys keep working unchanged."""

    def __init__(self, data=None, mapping=None):
        super().__init__(data or {})
        self.mapping = dict(mapping or {})

    def __getitem__(self, key):
        if key in _BROLL_MAP_KEYS:
            return self.mapping
        if key == "moved_out_of_drawings" and not dict.__contains__(self, key):
            return 0
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key in _BROLL_MAP_KEYS:
            return self.mapping
        if key == "moved_out_of_drawings" and not dict.__contains__(self, key):
            return 0
        return super().get(key, default)

    def __contains__(self, key):
        if key in _BROLL_MAP_KEYS or key == "moved_out_of_drawings":
            return True
        return super().__contains__(key)

    def __eq__(self, other):
        if not isinstance(other, dict):
            return super().__eq__(other)

        def value(source, key):
            if key in _BROLL_MAP_KEYS:
                return source.mapping if isinstance(source, _BrollReport) else source.get(key)
            if key == "moved_out_of_drawings":
                return source.get(key, 0)
            return source.get(key)

        for key in set(self) | set(other):
            if value(self, key) != value(other, key):
                return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)


def _number_value(value):
    """An integer from a table number/mapping value; None for a non-number."""
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else None


def _normalized_number_map(mapping) -> dict:
    out = {}
    for old, new in (mapping or {}).items():
        old_number = _number_value(old)
        if old_number is None:
            continue
        out[old_number] = _number_value(new) if new is not None else None
    return out


def _replace_reference(match, mapping: dict) -> str:
    number = int(match.group(2))
    if number not in mapping:
        return match.group(0)
    new = mapping[number]
    return "—" if new is None else f"{match.group(1)}{new}"


def _link_column_kind(header: str) -> str:
    upper = (header or "").upper()
    if "LINKED" not in upper:
        return ""
    if "TRANSITION" in upper:
        return "transition"
    if "B-ROLL" in upper or "BROLL" in upper:
        return "broll"
    if "OVERLAY" in upper:
        return "overlay"
    return ""


def _rewrite_link_cell(cell: str, kind: str, mapping: dict) -> str:
    """A "Linked to …" cell holds a reference, not free text: also rewrite its bare
    forms (#3 / 3 / After #3). Explicit B-roll/T# references elsewhere in the cell are
    handled first, so their new numbers are never mapped a second time."""
    pattern = _BROLL_REF if kind == "broll" else _TRANSITION_REF
    cell = pattern.sub(lambda m: _replace_reference(m, mapping), cell)
    if _ANY_REF.search(cell):
        return cell

    def bare(match):
        number = int(match.group(2))
        if number not in mapping:
            return match.group(0)
        new = mapping[number]
        if new is None:
            return "—"
        return f"{match.group(1)}{new}"

    return _BARE_REF.sub(bare, cell)


def relink_references(text: str, kind: str, mapping) -> str:
    """Rewrites references to old row numbers after a table has been renumbered.

    `kind` is "broll" or "transition". B-roll references (`B-roll #3`, `B-Roll #3`,
    `B-rolls #3`, `B-roll 3`) and T references (`T#3`, `T #3`, `Transition #3`) are
    rewritten anywhere in the document; a link column named for `kind` also accepts
    bare cells (`#3`, `3`, `After #3`). A reference to a dropped row becomes "—".
    The renumbered table's own "#" column, timestamps and unrelated numbers are left
    alone."""
    kind = (kind or "").lower()
    if not text or kind not in ("broll", "transition"):
        return text
    numbers = _normalized_number_map(mapping)
    if not numbers:
        return text
    pattern = _BROLL_REF if kind == "broll" else _TRANSITION_REF
    lines, header = [], None

    def ending(raw):
        for end in ("\r\n", "\n", "\r"):
            if raw.endswith(end):
                return raw[:-len(end)], end
        return raw, ""

    for raw in text.splitlines(keepends=True):
        line, end = ending(raw)
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = None
            lines.append(pattern.sub(lambda m: _replace_reference(m, numbers), line) + end)
            continue
        parts = re.split(r"(?<!\\)\|", line)
        cells = [c.strip() for c in parts[1:-1]]
        if cells and all(_SEPARATOR_CELL.fullmatch(c) for c in cells if c):
            lines.append(raw)
            continue
        if header is None:
            header = cells
            lines.append(raw)
            continue
        number_col = next((i for i, c in enumerate(header) if c.strip() == "#"), None)
        linked = {i for i, c in enumerate(header) if _link_column_kind(c) == kind}
        for i in range(len(cells)):
            if i == number_col or i + 1 >= len(parts) - 1:
                continue
            original = parts[i + 1]
            content = original.strip()
            if i in linked:
                content = _rewrite_link_cell(content, kind, numbers)
            else:
                content = pattern.sub(lambda m: _replace_reference(m, numbers), content)
            pad = re.match(r"^(\s*)(.*?)(\s*)$", original, re.S)
            parts[i + 1] = pad.group(1) + content + pad.group(3)
        lines.append("|".join(parts) + end)
    return "".join(lines)


def _table_cells(line: str) -> list:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def _broll_layout(table: str):
    """The first markdown table with both START CUE and PROMPT columns: its header,
    separator, column positions and data rows (as mutable cell lists). None when the
    answer has no such table."""
    lines = (table or "").splitlines()
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        header = _table_cells(line)
        upper = [c.upper() for c in header]
        if not any("START" in c for c in upper) or not any("PROMPT" in c for c in upper):
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        separator = lines[i + 1] if i + 1 < j and chapters.is_separator_row(lines[i + 1]) else None
        data_start = i + (2 if separator is not None else 1)

        def col(*names):
            return next((k for k, c in enumerate(upper) if any(n in c for n in names)), None)

        cols = {"num": next((k for k, c in enumerate(header) if c.strip() == "#"), None),
                "time": col("TIMESTAMP", "TIME", "TIMECODE", "⏱"),
                "start": col("START", "▶"), "end": col("END", "⏹"),
                "type": col("TYPE", "LAYER"), "prompt": col("PROMPT")}
        rows = []
        for k in range(data_start, j):
            cells = _table_cells(lines[k])
            if not cells or all(_SEPARATOR_CELL.fullmatch(c) for c in cells if c):
                continue
            rows.append({"cells": (cells + [""] * len(header))[:len(header)]})
        return {"lines": lines, "start": i, "end": j, "header": line, "separator": separator,
                "cols": cols, "rows": rows}
    return None


def _render_broll_layout(layout: dict, rows: list) -> str:
    lines = list(layout["lines"])
    block = [layout["header"]]
    if layout["separator"] is not None:
        block.append(layout["separator"])
    block.extend("| " + " | ".join(row["cells"]) + " |" for row in rows)
    lines[layout["start"]:layout["end"]] = block
    return "\n".join(lines)


def _row_seconds(row: dict, layout: dict, script: str, wpm: float, after: int = 0):
    """A row's start time from its START CUE (falling back to its Timestamp cell)."""
    cols, cells = layout["cols"], row["cells"]
    cue = cells[cols["start"]] if cols["start"] is not None and cols["start"] < len(cells) else ""
    seconds = ew.cue_seconds(cue, script, wpm, after=after) if cue else None
    if seconds is not None:
        return float(seconds)
    if cols["time"] is not None and cols["time"] < len(cells):
        stamp = _seconds(cells[cols["time"]])
        return float(stamp) if stamp is not None else None
    return None


def _running_row_times(rows: list, layout: dict, script: str, wpm: float, after: int = 0) -> list:
    """Row start times in table order, resolving a repeated cue to its occurrence at/after
    the previous row (the same rule the critique uses)."""
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    pos, out = after, []
    for row in rows:
        cols, cells = layout["cols"], row["cells"]
        cue = cells[cols["start"]] if cols["start"] is not None and cols["start"] < len(cells) else ""
        seconds = ew.cue_seconds(cue, script, wpm, after=pos) if cue else None
        if seconds is not None:
            out.append(float(seconds))
            later = [p for p in ew.cue_positions(cue, norm_script) if p >= pos]
            if later:
                pos = later[0]
            continue
        out.append(_row_seconds(row, layout, script, wpm, after=pos))
    return out


def _sorted_rows(rows: list, layout: dict, script: str, wpm: float, after: int = 0) -> list:
    times = [_row_seconds(row, layout, script, wpm, after=after) for row in rows]
    order = sorted(range(len(rows)), key=lambda i: (times[i] is None, times[i] or 0.0))
    return [rows[i] for i in order]


def _script_sentences(script: str, wpm: float) -> list:
    """Spoken sentences with their absolute word start and time. Sentence starts use the
    same normalized word positions as cue_seconds, so they line up with the cues."""
    out, word_pos = [], 0
    for chunk in re.split(r"[.!?؟…]+|\n+", ew.spoken_text(script)):
        chunk = chunk.strip()
        if not chunk:
            continue
        words = ew.normalize_ar(chunk).split()
        if not words:
            continue
        out.append({"text": chunk, "words": words, "start": word_pos,
                    "start_s": word_pos / wpm * 60.0})
        word_pos += len(words)
    return out


def _sentence_cue(sentence: dict, norm_script: str) -> str:
    """The sentence's first words as a cue, extended to 6–8 words when the first five
    are not unique in the script."""
    words = sentence["words"]
    last = min(8, len(words))
    for k in range(min(5, last), last + 1):
        cue = " ".join(words[:k])
        if ew.find_cue(cue, norm_script) <= 1:
            return cue
    return " ".join(words[:last])


# A B-roll anchored on a one- or two-word sentence ("ليه؟") illustrates nothing.
_MIN_CUE_SENTENCE_WORDS = 3
# A moved B-roll gets a new prompt for its new sentence (its old picture illustrated
# the old one); at most this many small calls per table.
MAX_MOVE_REWRITES = 10
# Duplicate/family re-imaging has its own calls: moves out of drawings used the shared 6 up,
# so 9 B-rolls were dropped and the variety audit never ran.
MAX_VARIETY_REWRITES = 12


def _first_sentence(sentences: list, low: float, high, after: int = 0):
    for sentence in sentences:
        if sentence["start"] < after or len(sentence["words"]) < _MIN_CUE_SENTENCE_WORDS:
            continue
        if sentence["start_s"] >= low and (high is None or sentence["start_s"] <= high):
            return sentence
    return None


# The closing (CTA + sign-off) may run without B-roll; a longer bare tail is a gap.
_TRAILING_ALLOW_SECONDS = 20


def _gap_list(rows: list, times: list, end: float = None) -> list:
    gaps = []
    if rows and times and times[0] is not None and times[0] > _LEADING_GAP_SECONDS:
        gaps.append({"lo": 0.0, "hi": times[0], "index": 0, "leading": True,
                     "key": ("start", id(rows[0]))})
    for i in range(len(rows) - 1):
        a, b = times[i], times[i + 1]
        if a is None or b is None or b - a <= GAP_FILL_SECONDS:
            continue
        gaps.append({"lo": a, "hi": b, "index": i + 1, "leading": False,
                     "key": (id(rows[i]), id(rows[i + 1]))})
    # After the last B-roll: a real run's table stopped at 12:27 of a 16:23 script.
    if rows and end and times and times[-1] is not None:
        tail_hi = end - _TRAILING_ALLOW_SECONDS
        if tail_hi - times[-1] > GAP_FILL_SECONDS:
            gaps.append({"lo": times[-1], "hi": tail_hi, "index": len(rows), "leading": False,
                         "key": ("end", id(rows[-1]))})
    return gaps


def _first_markdown_row(answer: str, ncols: int):
    """The first real data row of the model's answer, padded/trimmed to the header width."""
    for line in (answer or "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = _table_cells(line)
        if not cells or all(_SEPARATOR_CELL.fullmatch(c) for c in cells if c):
            continue
        upper = [c.upper() for c in cells]
        if any("START" in c for c in upper) and any("PROMPT" in c for c in upper):
            continue                         # the answer repeated the header
        return (cells + [""] * ncols)[:ncols]
    return None


def _rewrite_moved_row(layout: dict, row: dict, rows: list, sentence: dict, sentences: list,
                       norm_script: str, budget: list) -> bool:
    """Gives a moved row a prompt written for its new sentence. False (drop the row)
    when the budget is spent or the call fails: a picture of the old sentence under
    the new one is worse than no B-roll (a critique flagged sinus pressure shown over
    «ليه في ناس عايشة عادي»)."""
    if budget[0] <= 0:
        return False
    budget[0] -= 1
    try:
        index = sentences.index(sentence)
    except ValueError:
        return False
    cells = _ask_gap_row(layout, rows, index, sentences)
    if cells is None:
        return False
    cols = layout["cols"]
    cells[cols["start"]] = _sentence_cue(sentence, norm_script)
    cells[cols["end"]] = " ".join(sentence["words"][-4:])
    if cols["num"] is not None and cols["num"] < len(cells) and cols["num"] < len(row["cells"]):
        cells[cols["num"]] = row["cells"][cols["num"]]
    row["cells"] = cells
    return True


def _clip_words(row: dict, layout: dict, norm_script: str, after: int = 0) -> str:
    """The words spoken while a B-roll row is on screen (START CUE through END CUE),
    normalized, at most 45 words; "" when its cues are not found."""
    cols = layout["cols"]
    cells = row["cells"]
    if cols["start"] is None or cols["end"] is None or max(cols["start"], cols["end"]) >= len(cells):
        return ""
    starts = [p for p in ew.cue_positions(cells[cols["start"]], norm_script) if p >= after]
    if not starts:
        return ""
    end_cue = ew.normalize_ar(ew.spoken_text(cells[cols["end"]])).split()
    ends = [p for p in ew.cue_positions(cells[cols["end"]], norm_script) if p >= starts[0]]
    words = norm_script.split()
    finish = ends[0] + len(end_cue) if ends else starts[0] + 15
    return " ".join(words[starts[0]:min(finish, starts[0] + 45)])


def _ask_gap_row(layout: dict, rows: list, sentence_index: int, sentences: list, avoid: str = "",
                 focus: str = ""):
    """One small call for one B-roll row in a gap. Returns the parsed cells or None.
    `avoid`: a prompt already used elsewhere in the table — the new image must differ.
    `focus`: the exact words spoken while the clip plays; the picture must show them (the
    surrounding passage is context only — re-imaged rows drew a neighbouring sentence:
    a fizzing supplement over «زي زرار نور علّق», a thigh stretch over curled toes)."""
    cols = layout["cols"]
    header = layout["header"]
    examples = "\n".join("| " + " | ".join(row["cells"]) + " |" for row in rows[:2])
    passage = " ".join(s["text"] for s in sentences[max(0, sentence_index - 1):sentence_index + 2])
    if not focus and 0 <= sentence_index < len(sentences):
        focus = sentences[sentence_index]["text"]
    prompt = f"""Add exactly ONE row to this B-roll prompt table for the passage below.

TABLE HEADER:
{header}

FORMAT EXAMPLES (existing rows):
{examples or "(the table has no rows yet)"}

PASSAGE (spoken script around the moment — context only):
{passage}

ON SCREEN WHILE THESE WORDS ARE SPOKEN (the picture must show exactly this — its object, body
part, action or metaphor — not a neighbouring sentence):
«{focus}»

RULES:
- Photoreal medical B-roll that shows the ON-SCREEN words above, same visual style as the examples.
- 🎬 Video needs a visible subject action and a camera move (slow push-in, dolly, orbit, track…).
- 🖼️ Still needs a Ken Burns move in Composition Notes.
- No text, letters or numbers inside the generated image; put words in Composition Notes as a Premiere overlay.
- Use the passage's first words as START CUE and its last four words as END CUE (code will correct them).
{("- ANOTHER ROW ALREADY SHOWS THIS — use a clearly different subject, setting and angle: " + avoid[:400]) if avoid else ""}
Return ONLY the markdown row (one line, same number of columns as the header)."""
    try:
        answer = call_llm("You add one missing B-roll row to an existing production table.",
                          prompt, temperature=0.6, max_tokens=1200)
        cells = _first_markdown_row(answer, len(_table_cells(layout["header"])))
        if cells is None or cols["start"] is None or cols["end"] is None:
            return None
        return cells
    except Exception:
        return None


DRAWING_WINDOW_SECONDS = 15
DRAWING_MIN_SECONDS = 6             # a stated length below this is a step time
DRAWING_MAX_SECONDS = 40
DRAWING_LEAD_SECONDS = 5           # a clip starting this soon before a drawing overlaps it


def _cue_seconds_loose(cue: str, script: str, wpm: float):
    """Seconds for a drawing's spoken cue, trying a shorter prefix when the quoted
    build-step line carries a little extra wording around the real script words."""
    cue = (cue or "").strip(" \"'“”«»`*.")
    if not cue or not script:
        return None
    seconds = ew.cue_seconds(cue, script, wpm)
    if seconds is not None:
        return float(seconds)
    words = cue.split()
    for length in range(min(7, len(words)), 2, -1):
        seconds = ew.cue_seconds(" ".join(words[:length]), script, wpm)
        if seconds is not None:
            return float(seconds)
    return None


def _drawing_duration(text: str):
    """The seconds a drawing spec/row says it lasts (None when it does not say)."""
    # A "Total: 16.0s" beats the first number found: a real spec listed step times
    # (3.5 s) first, and the drawing got a 4-second window.
    patterns = (
        r"(?i)\btotal\b[^:\n\d]{0,20}:?\s*\**\s*(\d+(?:\.\d+)?)\s*(?:s\b|sec)",
        r"(?i)\(\s*(\d+(?:\.\d+)?)\s*s(?:ec(?:onds)?)?\s*total\s*\)",
        r"(?i)\b(?:total\s+)?duration\b[^:\n\d]{0,20}:\s*\**\s*(\d+(?:\.\d+)?)",
        r"(?i)\bhold\b[^:\n\d]{0,20}:\s*\**\s*(\d+(?:\.\d+)?)",
        r"(?i)(\d+(?:\.\d+)?)\s*(?:s\b|sec(?:ond)?s?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _drawing_cues(block: str) -> list:
    """Spoken (Arabic) cues of a prose drawing spec's build steps, in order: "Cue: «…»",
    "*Narration Cue*: *«…»*", "*Exact spoken line*: `«…»`", "*Spoken beat*: «…»", and any
    «…» quote. The caller tries them in order, so an English caption that is not in the
    script no longer hides the real lines (a real spec's only "cue" was an English
    caption, and its drawing got no window)."""
    found = []
    for match in re.finditer(r"(?i)\b(?:cue|spoken line|spoken beat|narration|line|beat)\b\s*\**\s*[:\-–]\s*[*`\s]*"
                             r"[“\"«]?\s*([^”\"»\n)`]+)", block or ""):
        found.append((match.start(1), match.group(1)))
    for match in re.finditer(r"«([^»\n]{3,})»", block or ""):
        found.append((match.start(1), match.group(1)))
    if not found:
        for line in (block or "").splitlines():
            if not re.search(r"(?i)\bstep\b|^\s*(?:\d+[.)]|[-*•])\s+", line):
                continue
            quoted = re.search(r"[“\"]([^”\"]{3,})[”\"]", line)
            if quoted:
                found.append((0, quoted.group(1)))
    cues, seen = [], set()
    for _, raw in sorted(found, key=lambda item: item[0]):
        cue = raw.strip(" \"'“”«»`*.-–—")
        if len(cue.split()) < 2 or cue in seen:      # unfound captions are skipped by the caller
            continue
        seen.add(cue)
        cues.append(cue)
    return cues


def _overlay_table_drawing_windows(guide: str, script: str, wpm: float) -> list:
    """Drawing windows from the overlay table's DRAWING/WHITEBOARD rows."""
    lines = (guide or "").splitlines()
    windows, i = [], 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        header = _table_cells(lines[i])
        upper = [c.upper() for c in header]
        if not any("ELEMENT" in c for c in upper) or not any(
                "START" in c or "▶" in c for c in upper):
            i = j
            continue

        def col(*names):
            return next((k for k, h in enumerate(upper) if any(n in h for n in names)), None)

        element_col = col("ELEMENT")
        start_col = col("START CUE", "START", "▶")
        time_col = col("TIMESTAMP", "TIMECODE", "TIME", "⏱")
        duration_col = col("HOLD", "DURATION")
        separator = i + 1 < j and chapters.is_separator_row(lines[i + 1])
        for k in range(i + (2 if separator else 1), j):
            cells = _table_cells(lines[k])
            if not cells or all(_SEPARATOR_CELL.fullmatch(c) for c in cells if c):
                continue
            element = cells[element_col].upper() if element_col is not None and element_col < len(cells) else ""
            if "DRAWING" not in element and "WHITEBOARD" not in element:
                continue
            start = None
            if start_col is not None and start_col < len(cells) and cells[start_col]:
                start = _cue_seconds_loose(cells[start_col], script, wpm)
            if start is None and time_col is not None and time_col < len(cells):
                stamp = _seconds(cells[time_col])
                start = float(stamp) if stamp is not None else None
            if start is None:
                for cue in _drawing_cues(" | ".join(cells)):
                    start = _cue_seconds_loose(cue, script, wpm)
                    if start is not None:
                        break
            if start is None:
                continue
            duration = None
            if duration_col is not None and duration_col < len(cells):
                # "34.0s" is 34 seconds (digits-only parsing read 340 and one drawing
                # claimed 5½ minutes: 34 cards, 31 cuts and 12 B-rolls were pushed out).
                found = re.search(r"\d+(?:\.\d+)?", cells[duration_col])
                duration = float(found.group(0)) if found else None
                if duration is not None and not DRAWING_MIN_SECONDS <= duration <= DRAWING_MAX_SECONDS:
                    duration = None
            if not duration or duration <= 0:
                duration = _drawing_duration(" | ".join(cells))
            windows.append((start, start + float(duration or DRAWING_WINDOW_SECONDS)))
        i = j
    return windows


def _prose_drawing_windows(guide: str, script: str, wpm: float) -> list:
    """Drawing windows from the prose specs under "Drawing Animations"."""
    anchors = [m.start() for m in re.finditer(
        r"(?im)^[^\n]*whiteboard image prompt[^\n]*$", guide or "")]
    if not anchors:
        anchors = [m.start() for m in re.finditer(
            r"(?im)^[^\n]*draw-on video prompt[^\n]*$", guide or "")]
    if not anchors:
        return []
    # Only a "Drawing Animations" heading BEFORE the first spec counts: a block cut at the
    # next spec's heading can hold the next chapter's section heading, and starting there
    # (after every anchor) found no drawing at all in a real run.
    section = next((m for m in re.finditer(r"(?im)^\s*#{2,6}\s*drawing animations\b", guide or "")
                    if m.start() < anchors[0]), None)
    previous, windows = (section.start() if section else 0), []
    for pos in anchors:
        block = (guide or "")[previous:pos]
        previous = pos
        cues = _drawing_cues(block)
        start, first = None, 0
        for index, cue in enumerate(cues):
            start = _cue_seconds_loose(cue, script, wpm)
            if start is not None:
                first = index
                break
        if start is None:
            continue
        duration = _drawing_duration(block)
        if duration is not None and duration < DRAWING_MIN_SECONDS:
            duration = None                     # a step time, not the drawing's length
        if duration is None:
            # Some specs put Duration after the image prompt (but before the next
            # drawing/heading) instead of before it.
            after = (guide or "")[pos:]
            after = re.split(r"(?m)^\s*(?:-{3,}|#{1,6}\s+\S)", after, maxsplit=1)[0]
            duration = _drawing_duration(after)
        # The window lasts until the later of start + its stated length and the last
        # build-step line (+ time to say it); later cue matches far away (a label that
        # also occurs minutes later) are ignored, and no drawing runs past 40 s.
        last = None
        for cue in cues[first:]:
            seconds = _cue_seconds_loose(cue, script, wpm)
            if seconds is not None and start < seconds <= start + DRAWING_MAX_SECONDS:
                last = max(last or 0.0, seconds + len(cue.split()) / wpm * 60.0)
        candidates = [start + float(duration)] if duration and duration > 0 else []
        if last is not None:
            candidates.append(last)
        end = max(candidates) if candidates else start + DRAWING_WINDOW_SECONDS
        windows.append((start, min(end, start + DRAWING_MAX_SECONDS)))
    return windows


def _drawing_windows(guide: str, script: str, wpm: float) -> list:
    """[(start_s, end_s)] occupied by whiteboard drawings; [] when none/unknown."""
    if not guide or not script:
        return []
    try:
        heads = list(_DRAWING_HEADING_RE.finditer(guide))
        if heads:
            # One window per "Drawing Animation N" spec, read inside its own block (the
            # spec's Duration/"(Ns total)" belongs to it) — the same windows the spec
            # re-timing writes back.
            prose = []
            for n, head in enumerate(heads):
                end = heads[n + 1].start() if n + 1 < len(heads) else len(guide)
                prose += _prose_drawing_windows(guide[head.start():end], script, wpm)
        else:
            prose = _prose_drawing_windows(guide, script, wpm)
        windows = _overlay_table_drawing_windows(guide, script, wpm) + prose
    except Exception:
        return []
    valid = []
    for start, end in windows:
        if start is None:
            continue
        end = float(end) if end is not None and end > start else float(start) + DRAWING_WINDOW_SECONDS
        valid.append((float(start), end))
    return sorted(valid)


def _inside_drawing_window(seconds, windows: list) -> bool:
    return seconds is not None and any(start <= seconds < end for start, end in windows)


def _move_brolls_out_of_drawings(rows: list, layout: dict, windows: list, sentences: list,
                                 script: str, wpm: float, norm_script: str,
                                 after: int = 0, budget: list = None) -> tuple:
    """Moves each B-roll starting inside a drawing window to the first sentence after
    it, dropping it when the next B-roll leaves no room. Returns (rows, moved, dropped)."""
    if not windows or not rows:
        return rows, 0, 0
    times = _running_row_times(rows, layout, script, wpm, after=after)
    cols = layout["cols"]
    def inside(when):
        return when is not None and any(w[0] <= when < w[1] for w in windows)

    kept, moved, dropped = [], 0, 0
    last_time = None                    # start of the last B-roll kept or placed
    for i, row in enumerate(rows):
        t = times[i]
        window = next((w for w in windows if t is not None and w[0] <= t < w[1]), None)
        if window is None:
            kept.append(row)
            if t is not None:
                last_time = t
            continue
        # The next B-roll that stays where it is (rows inside a window move too), and at
        # least 8 s after the last one placed: two B-rolls moved out of one window landed
        # on the same sentence (0 s apart).
        next_time = next((times[k] for k in range(i + 1, len(times)) if times[k] is not None
                          and not inside(times[k])), None)
        high = next_time - _MIN_BROLL_SPACING if next_time is not None else None
        low = max(window[1], (last_time + _MIN_BROLL_SPACING) if last_time is not None else 0.0)
        sentence = None
        while True:
            sentence = _first_sentence(sentences, low, high, after)
            if sentence is None:
                break
            blocking = next((w for w in windows
                             if w[0] <= sentence["start_s"] < w[1]), None)
            if blocking is None:
                break
            low = blocking[1]
        if sentence is None or cols["start"] is None or cols["end"] is None:
            dropped += 1
            continue
        if budget is not None and not _rewrite_moved_row(layout, row, rows, sentence, sentences,
                                                         norm_script, budget):
            dropped += 1
            continue
        row["cells"][cols["start"]] = _sentence_cue(sentence, norm_script)
        row["cells"][cols["end"]] = " ".join(sentence["words"][-4:])
        kept.append(row)
        last_time = sentence["start_s"]
        moved += 1
    return kept, moved, dropped


def _drawing_subjects(guide: str) -> list:
    """One line per whiteboard drawing: its title (and subject description when given)."""
    out = []
    heads = list(_DRAWING_HEADING_RE.finditer(guide or ""))
    for n, head in enumerate(heads):
        end = heads[n + 1].start() if n + 1 < len(heads) else len(guide)
        block = guide[head.start():end]
        subject = re.search(r"(?i)subject description\**\s*:\s*\**\s*([^\n]{10,240})", block)
        title = re.sub(r"[*#\[\]]", "", head.group(0)).strip()
        out.append((title + (" — " + subject.group(1).strip() if subject else ""))[:260])
    return out


_STAND_IN_DOCTOR_RE = re.compile(r"(?i)\b(doctor|physician|cardiologist|neurologist|clinician|GP)\b(?![^.]{0,40}\b(?:hand|hands|'s hand)\b)")


def _replace_repeated_families(rows: list, layout: dict, sentences: list, script: str, wpm: float,
                               after: int, budget: list, drawings: list = None) -> int:
    """ONE small call groups B-rolls that repeat the same subject + action (word overlap
    cannot see it: three "pill out of a blister pack beside a glass" shots scored like
    unrelated rows). The first of each group stays (and a last-row callback to the
    opening); the others get a new prompt for their own sentence that must differ.
    Shares the call budget; any failure changes nothing."""
    prompt_col = layout["cols"].get("prompt")
    if prompt_col is None or len(rows) < 4 or budget[0] <= 0:
        return 0
    header = [c.strip().upper() for c in _table_cells(layout["header"])]
    cue_col = next((k for k, h in enumerate(header) if "MAIN CUE" in h), None)
    times = _running_row_times(rows, layout, script, wpm, after=after)
    starts = [s["start_s"] for s in sentences]
    listing = []
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    for i, row in enumerate(rows, 1):
        cells = row["cells"]
        text = cells[cue_col] if cue_col is not None and cue_col < len(cells) and cells[cue_col].strip() \
            else (cells[prompt_col][:160] if prompt_col < len(cells) else "")
        spoken = ""
        if times[i - 1] is not None:
            spoken = _clip_words(row, layout, norm_script, int(times[i - 1] * wpm / 60.0) - 2)[:200]
        if not spoken and times[i - 1] is not None:
            k = max((n for n, t in enumerate(starts) if t <= times[i - 1] + 0.5), default=None)
            if k is not None:
                spoken = sentences[k]["text"][:140]
        listing.append(f"{i}. SAID: «{spoken}» — SHOWS: {text}")
    drawing_note, drawing_ask = "", ""
    if drawings:
        drawing_note = ("\n\nWHITEBOARD DRAWINGS IN THE SAME VIDEO (they already show these ideas):\n"
                        + "\n".join(f"D{k}. {d}" for k, d in enumerate(drawings, 1)))
        drawing_ask = (" Also list in \"repeats_drawing\" the B-roll numbers that show the same metaphor or "
                       "object a whiteboard drawing already draws (a real switchboard after a drawn one).")
    try:
        data, _ = call_llm_json(
            "You audit visual variety in a video's B-roll list.",
            "B-ROLL SHOTS (in order):\n" + "\n".join(listing) + drawing_note
            + "\n\nGroup shots that repeat the same SUBJECT doing the same ACTION (e.g. several 'man in bed "
              "clutching his temple beside pills'), even if worded differently. Different angles of different "
              "ideas are NOT repeats." + drawing_ask
            + " Also list in \"misfit\" the shots whose picture does NOT show what is SAID at that moment (it "
              "illustrates a neighbouring sentence or another idea)."
            + " Return ONLY JSON: {\"repeats\": [[3, 11, 20], [14, 19]], \"repeats_drawing\": [4], \"misfit\": [2, 3]}"
              " — [] when none.",
            temperature=0.1, max_tokens=800, required=["repeats"])
    except Exception:
        return 0
    groups = [g for g in (pick(data, "repeats", default=[]) or []) if isinstance(g, list)]
    # A B-roll repeating a drawing's metaphor is re-imaged; it has no earlier shot to keep.
    drawing_repeats = {int(n) for n in (pick(data, "repeats_drawing", default=[]) or [])
                       if str(n).isdigit() and 1 <= int(n) <= len(rows)}
    misfits = {int(n) for n in (pick(data, "misfit", default=[]) or [])
               if str(n).isdigit() and 1 <= int(n) <= len(rows)}
    times = _running_row_times(rows, layout, script, wpm, after=after)
    starts = [s["start_s"] for s in sentences]
    jobs = {}                                   # row number -> what its new picture must avoid
    for group in groups:
        members = sorted({int(n) for n in group if str(n).isdigit() and 1 <= int(n) <= len(rows)})
        keep = members[0] if members else None
        for n in members[1:]:
            if n == len(rows) and keep is not None and keep <= 3:
                continue                       # the closing's deliberate callback to the opening
            jobs.setdefault(n, rows[keep - 1]["cells"][prompt_col])
    # A stand-in doctor competes with the presenter (who IS the doctor): re-image it.
    for n, row in enumerate(rows, 1):
        if prompt_col < len(row["cells"]) and _STAND_IN_DOCTOR_RE.search(row["cells"][prompt_col]):
            jobs.setdefault(n, "a doctor or physician figure — the presenter is the doctor; show the patient, "
                               "the object or the mechanism instead, no doctor in the frame")
    for n in sorted(misfits):
        # Its new picture is written for the sentence actually spoken at its start.
        jobs.setdefault(n, "this picture illustrated a different sentence: " + rows[n - 1]["cells"][prompt_col][:200])
    for n in sorted(drawing_repeats):
        jobs.setdefault(n, "the whiteboard drawing already shows this: "
                        + " | ".join(drawings or []))
    planned = []
    for n in sorted(jobs):
        if budget[0] <= 0:
            break
        j = n - 1
        if times[j] is None:
            continue
        index = max((k for k, t in enumerate(starts) if t <= times[j] + 0.5), default=None)
        if index is None:
            continue
        budget[0] -= 1
        planned.append((j, index, jobs[n]))
    # The rows are independent: ask for their new pictures in parallel (a run spent 16
    # minutes in the B-roll step, mostly on these small calls one after another).
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    focus = {j: _clip_words(rows[j], layout, norm_script, int(times[j] * wpm / 60.0) - 2)
             for j, _, _ in planned}
    answers = chapters.parallel_map(
        lambda job: _ask_gap_row(layout, rows, job[1], sentences, avoid=job[2], focus=focus[job[0]]),
        planned, workers=4)
    replaced = 0
    for (j, _, _), cells in zip(planned, answers):
        if cells is None:
            continue
        old = rows[j]["cells"]
        for name in ("num", "start", "end", "time"):
            at = layout["cols"].get(name)
            if at is not None and at < len(cells) and at < len(old):
                cells[at] = old[at]
        rows[j]["cells"] = cells
        replaced += 1
    if replaced:
        print(f"  🔧 B-roll: re-imaged {replaced} shots that repeated another's subject and action")
    return replaced


def _replace_duplicate_images(rows: list, layout: dict, sentences: list, script: str, wpm: float,
                              after: int, norm_script: str, budget: list) -> int:
    """The later of two near-identical B-roll prompts (≥ 50% shared content words, the
    same rule as the duplicate code check) gets a new prompt for its own sentence that
    must differ from the earlier one. A run showed the same father-and-son sofa shot
    3.5 minutes apart. Shares the moved-row call budget; returns rows re-imaged."""
    prompt_col = layout["cols"].get("prompt")
    if prompt_col is None or len(rows) < 2:
        return 0
    words = [_broll_content_words(r["cells"][prompt_col]) if prompt_col < len(r["cells"]) else set() for r in rows]
    times = _running_row_times(rows, layout, script, wpm, after=after)
    starts = [s["start_s"] for s in sentences]
    replaced = 0
    for j in range(1, len(rows)):
        twin = next((i for i in range(j) if _jaccard(words[i], words[j]) >= 0.5), None)
        if twin is None or times[j] is None or budget[0] <= 0:
            continue
        index = max((k for k, t in enumerate(starts) if t <= times[j] + 0.5), default=None)
        if index is None:
            continue
        budget[0] -= 1
        cells = _ask_gap_row(layout, rows, index, sentences, avoid=rows[twin]["cells"][prompt_col],
                             focus=_clip_words(rows[j], layout, norm_script, int(times[j] * wpm / 60.0) - 2))
        if cells is None:
            continue
        cols = layout["cols"]
        old = rows[j]["cells"]
        for name in ("num", "start", "end", "time"):
            at = cols.get(name)
            if at is not None and at < len(cells) and at < len(old):
                cells[at] = old[at]
        new_words = _broll_content_words(cells[prompt_col]) if prompt_col < len(cells) else set()
        if _jaccard(words[twin], new_words) >= 0.5:
            continue
        rows[j]["cells"], words[j] = cells, new_words
        replaced += 1
    if replaced:
        print(f"  🔧 B-roll: re-imaged {replaced} near-duplicate shots")
    return replaced


BROLL_CLIP_SECONDS = (3.0, 8.0)        # a generated clip is 3–8 s long


def _fit_end_cues(rows: list, layout: dict, sentences: list, script: str, wpm: float,
                  after: int = 0) -> int:
    """When a B-roll's END CUE is missing, before its start, past the next B-roll or more
    than 1.5× its clip length (Duration column, clamped to 3–8 s) after the start, puts it
    at the sentence end closest to start + clip length and writes the real interval back
    to Duration. A critique found 4-second clips whose END CUEs
    were 11–20 s later: the clip would freeze or loop. Returns how many rows changed."""
    cols = layout["cols"]
    if cols["end"] is None or not rows or not sentences:
        return 0
    header = [c.strip().upper() for c in _table_cells(layout["header"])]
    dur_col = next((k for k, h in enumerate(header) if "DUR" in h), None)
    times = _running_row_times(rows, layout, script, wpm, after=after)
    ends = [((s["start"] + len(s["words"])) / wpm * 60.0, s) for s in sentences if s["start"] >= after]
    changed = 0
    for i, row in enumerate(rows):
        start = times[i]
        if start is None:
            continue
        cells = row["cells"]
        clip = BROLL_CLIP_SECONDS[1]
        if dur_col is not None and dur_col < len(cells):
            found = re.search(r"\d+(?:\.\d+)?", cells[dur_col])
            if found:
                clip = min(BROLL_CLIP_SECONDS[1], max(BROLL_CLIP_SECONDS[0], float(found.group(0))))
        limit = next((t for t in times[i + 1:] if t is not None), None)
        current = cells[cols["end"]].strip()
        at = ew.cue_seconds(current, script, wpm, after=max(after, int(start * wpm / 60.0) - 1)) if current else None
        if at is not None:
            at += len(ew.normalize_ar(ew.spoken_text(current)).split()) / wpm * 60.0
            # Long enough to fill and short enough not to freeze: leave the model's cue.
            if start + BROLL_CLIP_SECONDS[0] * 0.85 < at <= start + clip * 1.5 and (limit is None or at <= limit + 0.5):
                continue
        options = [(end, s) for end, s in ends
                   if end > start + BROLL_CLIP_SECONDS[0] * 0.85 and (limit is None or end <= limit + 0.5)]
        if not options:
            continue
        end, sentence = min(options, key=lambda item: abs(item[0] - (start + clip)))
        cue = " ".join(sentence["words"][-4:])
        if ew.normalize_ar(cells[cols["end"]]).split() != cue.split():
            cells[cols["end"]] = cue
            changed += 1
        if dur_col is not None and dur_col < len(cells):
            cells[dur_col] = f"{max(1, round(end - start))}s"
    return changed


GRAPHIC_BEFORE_SECONDS, GRAPHIC_AFTER_SECONDS = 3.0, 6.0
_GRAPHIC_TO_RE = re.compile(r"(?i)animation|graphic|chart|diagram|drawing|whiteboard|\b[23]d\b|infographic|split")


def _designed_graphic_windows(transition_design: str, script: str, wpm: float) -> list:
    """Moments the transition designer cuts TO a graphic/animation/chart (not a B-roll):
    the screen belongs to that graphic, so a B-roll may not start there. A run had a cut
    to the 38% bar chart and a code-added cut to a B-roll at the same second."""
    blocks = _transition_blocks(transition_design or "")
    if not blocks:
        return []
    out = []
    rows = [(block, row) for block in blocks for row in block["rows"]]
    for (block, row), t in zip(rows, _transition_row_times(blocks, script, wpm)):
        from_to = _row_cell(row, block["cols"], "from_to")
        target = re.split(r"\s*(?:→|->)\s*", from_to, maxsplit=1)[-1]
        if t is not None and _GRAPHIC_TO_RE.search(target) and not re.search(r"(?i)b-?\s?roll", target):
            out.append((max(0.0, t - GRAPHIC_BEFORE_SECONDS), t + GRAPHIC_AFTER_SECONDS))
    return out


_DIRECT_ADDRESS_RE = re.compile(r"كومنت|تعليق|اكتبلي|اكتبولي|اكتب لي|اشترك|ابعت الفيديو|شاركني|قولي في"
                                r"|أنا د\.|انا د\.|أنا دكتور|انا دكتور|أنا أحمد|انا احمد|معاكم د\.|معاكم دكتور")


def _direct_address_windows(sentences: list) -> list:
    """The presenter speaking straight to the viewer (comment question, subscribe, share)
    stays on screen: a B-roll there broke the moment the script wrote for his face."""
    return [(max(0.0, s["start_s"] - 1.0), s["start_s"] + len(s["words"]) / 2.25 + 1.0)
            for s in sentences if _DIRECT_ADDRESS_RE.search(s["text"])]


def repair_broll_timeline(state: PipelineState, table: str, script: str, wpm: float,
                          after: int = 0, map_out: list = None, fill_tail: bool = False) -> tuple:
    """Code-repairs the B-roll timeline: clustered rows (<8 s apart) move to a later
    sentence (or are dropped when there is no room), and long stretches without B-roll
    get one model-written row each, with its cues forced to the chosen sentence. Runs on
    the parsed table and re-renders the same header, then re-times and renumbers rows.

    In chapter mode this runs once on the stitched table (not per chapter), where the
    whole table and the whole script are known, so gap-fills cannot fall outside a
    chapter and row numbers are global. Returns (table, report); the report also carries
    the old→new B-roll number map (`report.mapping`)."""
    report = _BrollReport({"moved": 0, "dropped": 0, "filled": 0, "gaps_left": 0})
    layout = _broll_layout(table)
    if layout is None or not script:
        if map_out is not None:
            map_out.append({})
        return table, report
    wpm = ew.blueprint_wpm({"words_per_minute": wpm})
    sentences = _script_sentences(script, wpm)
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    rows = layout["rows"]
    if not rows:
        if map_out is not None:
            map_out.append({})
        return table, report

    num_col = layout["cols"]["num"]
    original_map = {}
    if num_col is not None:
        for row in rows:
            cells = row["cells"]
            if num_col < len(cells):
                row["_old_num"] = cells[num_col].strip()
                old = _number_value(row["_old_num"])
                if old is not None:
                    original_map.setdefault(old, None)

    rows = _sorted_rows(rows, layout, script, wpm, after=after)
    budget = [MAX_MOVE_REWRITES]
    kept, prev_time, prev_pos = [], None, after
    for i, row in enumerate(rows):
        cols, cells = layout["cols"], row["cells"]
        cue = cells[cols["start"]] if cols["start"] is not None else ""
        seconds = ew.cue_seconds(cue, script, wpm, after=prev_pos) if cue else None
        t = float(seconds) if seconds is not None else _row_seconds(row, layout, script, wpm, after=prev_pos)
        if prev_time is not None and t is not None and t - prev_time < _MIN_BROLL_SPACING:
            next_time = _row_seconds(rows[i + 1], layout, script, wpm, after=prev_pos) \
                if i + 1 < len(rows) else None
            low = prev_time + _MIN_BROLL_SPACING
            high = next_time - _MIN_BROLL_SPACING if next_time is not None else None
            sentence = _first_sentence(sentences, low, high, after)
            if sentence is None or cols["start"] is None or cols["end"] is None \
                    or not _rewrite_moved_row(layout, row, rows, sentence, sentences, norm_script, budget):
                report["dropped"] += 1
                continue
            cells = row["cells"]
            cells[cols["start"]] = _sentence_cue(sentence, norm_script)
            cells[cols["end"]] = " ".join(sentence["words"][-4:])
            prev_time, prev_pos = sentence["start_s"], max(prev_pos, sentence["start"])
            kept.append(row)
            report["moved"] += 1
            continue
        kept.append(row)
        if t is not None:
            prev_time = t
        if seconds is not None:
            later = [p for p in ew.cue_positions(cue, norm_script) if p >= prev_pos]
            if later:
                prev_pos = later[0]
    rows = kept

    # Whiteboard drawings own their windows: no B-roll starts under a drawing, and a
    # gap-fill is never inserted inside one.
    overlay_guide = state.get("text_animation_overlay", "")
    # A clip that starts a few seconds before a drawing still plays over it, and a
    # warning card owns its hold: B-rolls are moved out here, where the spacing rule
    # applies (a later shift out of a hold left two B-rolls 6 s apart).
    drawing_windows = sorted(
        [(max(0.0, a - DRAWING_LEAD_SECONDS), b) for a, b in _drawing_windows(overlay_guide, script, wpm)]
        + _overlay_warning_holds(overlay_guide, script, wpm)
        + _overlay_title_holds(overlay_guide, script, wpm)
        + _designed_graphic_windows(state.get("transition_design", ""), script, wpm)
        + _direct_address_windows(sentences))
    if drawing_windows:
        rows, moved_out, dropped_out = _move_brolls_out_of_drawings(
            rows, layout, drawing_windows, sentences, script, wpm, norm_script, after, budget)
        report["dropped"] += dropped_out
        if moved_out:
            report["moved_out_of_drawings"] = moved_out
            print(f"🔧 moved {moved_out} B-rolls out of whiteboard windows")

    # Whole-script tables (after == 0) also cover the tail after the last B-roll.
    script_end = (len(ew.spoken_text(script).split()) / wpm * 60.0) if (fill_tail and after == 0 and wpm) else None
    attempts, calls = set(), 0
    while rows and calls < MAX_GAP_FILLS:
        rows = _sorted_rows(rows, layout, script, wpm, after=after)
        times = _running_row_times(rows, layout, script, wpm, after=after)
        gaps = [g for g in _gap_list(rows, times, script_end) if g["key"] not in attempts]
        if not gaps:
            break
        gap = max(gaps, key=lambda g: g["hi"] - g["lo"])
        attempts.add(gap["key"])
        middle = (gap["lo"] + gap["hi"]) / 2.0
        # At least 8 s from both neighbours: a fill aimed at a gap's middle (inside a
        # drawing) fell back to a sentence 6 s after the previous B-roll.
        low_ok = gap["lo"] + (_MIN_BROLL_SPACING if not gap.get("leading") else 0.001)
        candidates = [(i, s) for i, s in enumerate(sentences)
                      if s["start"] >= after and low_ok <= s["start_s"] <= gap["hi"] - _MIN_BROLL_SPACING
                      and len(s["words"]) >= _MIN_CUE_SENTENCE_WORDS
                      and not _inside_drawing_window(s["start_s"], drawing_windows)]
        if not candidates:
            continue
        index, sentence = min(candidates, key=lambda item: abs(item[1]["start_s"] - middle))
        calls += 1
        cells = _ask_gap_row(layout, rows, index, sentences)
        if cells is None:
            continue
        cols = layout["cols"]
        cells[cols["start"]] = _sentence_cue(sentence, norm_script)
        cells[cols["end"]] = " ".join(sentence["words"][-4:])
        rows.insert(gap["index"], {"cells": cells})
        report["filled"] += 1

    rows = _sorted_rows(rows, layout, script, wpm, after=after)
    variety_budget = [MAX_VARIETY_REWRITES]
    re_imaged = _replace_duplicate_images(rows, layout, sentences, script, wpm, after, norm_script, variety_budget)
    drawings = _drawing_subjects(state.get("text_animation_overlay", ""))
    first = _replace_repeated_families(rows, layout, sentences, script, wpm, after, variety_budget, drawings=drawings)
    re_imaged += first
    if first and variety_budget[0] > 0:
        # New pictures (and gap fills) can repeat each other: audit once more.
        re_imaged += _replace_repeated_families(rows, layout, sentences, script, wpm, after, variety_budget,
                                                drawings=drawings)
    if re_imaged:
        report["re_imaged"] = re_imaged
    _fit_end_cues(rows, layout, sentences, script, wpm, after)
    report["gaps_left"] = len(_gap_list(rows, _running_row_times(rows, layout, script, wpm, after=after),
                                        script_end))

    # Old model number → final number (None for a row this repair dropped).
    mapping = dict(original_map)
    for i, row in enumerate(rows, 1):
        old = _number_value(row.get("_old_num"))
        if old is not None:
            mapping[old] = i
    report.mapping = mapping
    if map_out is not None:
        map_out.append(mapping)

    out = _render_broll_layout(layout, rows)
    out = ew.rewrite_broll_timestamps(out, script, wpm, after=after)
    layout = _broll_layout(out)
    if layout is not None and layout["cols"]["num"] is not None:
        for i, row in enumerate(layout["rows"], 1):
            row["cells"][layout["cols"]["num"]] = str(i)
        out = _render_broll_layout(layout, layout["rows"])
    if report["moved"] or report["dropped"] or report["filled"]:
        print(f"  🔧 B-roll timeline: moved {report['moved']}, dropped {report['dropped']}, "
              f"filled {report['filled']} gaps")
    return out, report


def finalize_broll_table(state: PipelineState, table: str, after: int = 0,
                         repair: bool = True) -> str:
    """Code repairs the B-roll step's own table before it is stored: a camera move for
    every video row that lacks one, then real M:SS timestamps from the START CUES (see
    workbook.rewrite_broll_timestamps) so revision passes and the critique see the real
    timeline instead of the model's invented timestamps. `after` is the word position the
    table's first row starts at (0 for a whole-script table, the chapter's start word
    position for a single chapter's table).

    `repair=False` skips the timeline repair for a single chapter's table; chapter mode
    runs it once on the stitched table (see repair_broll_timeline)."""
    table, realigned = ew.realign_broll_table(table)
    if realigned:
        print(f"  🔧 realigned {realigned} B-roll rows with a missing column")
    table, fixed = auto_repair_camera_moves(table)
    if fixed:
        print(f"  🔧 added a camera move to {fixed} video B-rolls")
    script = state.get("refined_script", "")
    wpm = ew.blueprint_wpm(state.get("blueprint"))
    if table and script:
        table = ew.rewrite_broll_timestamps(table, script, wpm, after=after)
        if repair:
            table, report = repair_broll_timeline(state, table, script, wpm, after=after, fill_tail=True)
            if isinstance(state, dict):
                state["_broll_timeline_map"] = dict(report.mapping)
    return table


def _seconds(stamp: str):
    m = re.search(r"(\d{1,2}):(\d{2})", stamp or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


# --------------------------------------------------------------------------
# transition map ↔ final B-roll table (one shared script clock)
# --------------------------------------------------------------------------
TRANSITION_TOLERANCE_SECONDS = 3.0


def _fmt_seconds(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"


def _transition_cols(header: list) -> dict:
    upper = [c.strip().upper() for c in header]

    def col(*names):
        return next((k for k, h in enumerate(upper) if any(n in h for n in names)), None)

    return {"num": next((k for k, h in enumerate(upper) if h == "#"), None),
            "time": col("TIMESTAMP", "TIMECODE", "TIME", "⏱"),
            "cue": col("START CUE", "▶", "START"),
            "from_to": col("FROM"),
            "type": col("TRANSITION TYPE", "TYPE"),
            "direction": col("DIRECTION"),
            "easing": col("EASING"),
            "duration": col("DURATION"),
            "range": col("RANGE"),
            "rationale": col("RATIONALE")}


def _transition_blocks(text: str) -> list:
    """Every markdown table in `text` whose header is a transition map (chapter mode
    repeats the header once per "=== title ===" section). Rows keep their cell list."""
    lines = (text or "").splitlines()
    blocks, i = [], 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        header = _table_cells(lines[i])
        upper = [c.upper() for c in header]
        if any("FROM" in c and "TO" in c for c in upper) or any("TRANSITION TYPE" in c for c in upper):
            separator = lines[i + 1] if i + 1 < j and chapters.is_separator_row(lines[i + 1]) else None
            data_start = i + (2 if separator is not None else 1)
            rows = []
            for k in range(data_start, j):
                cells = _table_cells(lines[k])
                if not cells or all(_SEPARATOR_CELL.fullmatch(c) for c in cells if c):
                    continue
                rows.append({"cells": (cells + [""] * len(header))[:len(header)], "order": k})
            blocks.append({"start": i, "end": j, "header": lines[i], "separator": separator,
                           "cols": _transition_cols(header), "rows": rows, "ncols": len(header)})
        i = j
    return blocks


def _transition_row_time(row: dict, cols: dict, script: str, wpm: float,
                         after: int = 0) -> float:
    cells = row["cells"]
    cue_col = cols["cue"]
    cue = cells[cue_col] if cue_col is not None and cue_col < len(cells) else ""
    if cue:
        seconds = ew.cue_seconds(cue, script, wpm, after=after)
        if seconds is not None:
            return float(seconds)
    time_col = cols["time"]
    if time_col is not None and time_col < len(cells):
        stamp = _seconds(cells[time_col])
        if stamp is not None:
            return float(stamp)
    return None


def _transition_row_times(blocks: list, script: str, wpm: float) -> list:
    """Times in table order, resolving a repeated cue to its occurrence at/after the
    previous row (the same running-position rule as the rest of the production layer)."""
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    pos, out = 0, []
    for block in blocks:
        cols = block["cols"]
        for row in block["rows"]:
            cells = row["cells"]
            cue = cells[cols["cue"]] if cols["cue"] is not None and cols["cue"] < len(cells) else ""
            seconds = ew.cue_seconds(cue, script, wpm, after=pos) if cue else None
            if seconds is None:
                seconds = _transition_row_time(row, cols, script, wpm, after=pos)
            out.append(seconds)
            if cue:
                later = [p for p in ew.cue_positions(cue, norm_script) if p >= pos]
                if later:
                    pos = later[0]
    return out


def _row_cell(row: dict, cols: dict, name: str) -> str:
    at = cols.get(name)
    return row["cells"][at] if at is not None and at < len(row["cells"]) else ""


def _sort_transition_blocks(blocks: list, script: str, wpm: float,
                            map_out: dict = None) -> None:
    """Sort rows by their script position, renumber 1..n and put each row back in the
    chapter block whose time range contains it (single-table runs keep one block).
    `map_out` accumulates the old→new number map across repeated sorts."""
    def exit_first(row, cols):
        """On one moment a cut OUT of a B-roll precedes the next cut in ("B-roll #2 →
        Talking Head" before "Talking Head → B-roll #3"), so the chain reads #2 → #3."""
        source = re.split(r"\s*(?:→|->)\s*", _row_cell(row, cols, "from_to"), maxsplit=1)[0]
        return 0 if _BROLL_REF.search(source) else 1

    flat, order = [], 0
    for bi, block in enumerate(blocks):
        for row in block["rows"]:
            t = _transition_row_time(row, block["cols"], script, wpm)
            old = _number_value(_row_cell(row, block["cols"], "num"))
            flat.append((t is None, t or 0.0, order, bi, row, old, exit_first(row, block["cols"])))
            order += 1
    flat.sort(key=lambda item: (item[0], item[1], item[6], item[2]))
    step = {}
    for number, item in enumerate(flat, 1):
        num_col = blocks[item[3]]["cols"]["num"]
        old = item[5]
        if old is not None and old > 0:
            step[old] = number
        if num_col is not None:
            item[4]["cells"][num_col] = str(number)

    if map_out is not None:
        if not map_out:
            map_out.update(step)
        else:
            for old, current in list(map_out.items()):
                map_out[old] = step.get(current, current)

    if len(blocks) == 1:
        blocks[0]["rows"] = [item[4] for item in flat]
        return

    # Anchor each chapter block on the rows it already had, so a code-added B-roll cut
    # joins the chapter it belongs to instead of stretching the first block's range.
    ranges = []
    for block in blocks:
        times = []
        for only_original in (True, False):
            times = []
            for row in block["rows"]:
                rationale = _row_cell(row, block["cols"], "rationale")
                if only_original and "code-added" in rationale.lower():
                    continue
                t = _transition_row_time(row, block["cols"], script, wpm)
                if t is not None:
                    times.append(t)
            if times:
                break
        ranges.append((min(times), max(times)) if times else None)

    assigned = [[] for _ in blocks]
    for item in flat:
        _missing, t, order_no, original, row = item[:5]
        time = None if _missing else t
        block_index = original
        if time is not None:
            containing = [k for k, rng in enumerate(ranges) if rng and rng[0] <= time <= rng[1]]
            if containing:
                block_index = original if original in containing else containing[0]
            else:
                distances = []
                for k, rng in enumerate(ranges):
                    if rng is None:
                        distances.append((float("inf"), k))
                    elif time < rng[0]:
                        distances.append((rng[0] - time, k))
                    elif time > rng[1]:
                        distances.append((time - rng[1], k))
                    else:
                        distances.append((0.0, k))
                block_index = min(distances)[1]
        assigned[block_index].append((float("inf") if time is None else time, item[6], order_no, row))
    for block, rows in zip(blocks, assigned):
        rows.sort(key=lambda item: (item[0], item[1], item[2]))
        block["rows"] = [row for _t, _e, _o, row in rows]


def _render_transition_blocks(text: str, blocks: list) -> str:
    lines = (text or "").splitlines()
    out, pos = [], 0
    for block in blocks:
        out.extend(lines[pos:block["start"]])
        out.append(block["header"])
        if block["separator"] is not None:
            out.append(block["separator"])
        out.extend("| " + " | ".join(row["cells"]) + " |" for row in block["rows"])
        pos = block["end"]
    out.extend(lines[pos:])
    joined = "\n".join(out)
    return joined + ("\n" if (text or "").endswith("\n") else "")


def _new_transition_row(block: dict, cue: str, from_to: str, rationale: str,
                        seconds: float) -> dict:
    """A transition row matching `block`'s header (unknown columns left empty)."""
    cols, cells = block["cols"], [""] * block["ncols"]
    values = {"num": "0", "time": _fmt_seconds(seconds), "cue": cue, "from_to": from_to,
              "type": "Cut", "direction": "—", "easing": "—", "duration": "0",
              "range": "—", "rationale": rationale}
    for name, value in values.items():
        at = cols.get(name)
        if at is not None:
            cells[at] = value
    return {"cells": cells, "order": 0}


def _end_cue_finish(cue: str, norm_script: str, wpm: float, start_pos: int):
    """(seconds, next_cue) for a B-roll's END CUE: the moment its last word is spoken (the
    cut back belongs there, not where the cue begins) and the first words spoken after it,
    usable as the exit row's cue. The occurrence at/after the clip's start is the one meant:
    the first occurrence put an outro clip's exit at 2:47 when its END CUE was also said
    there (a critique: "T#22/T#23 point to B-roll #43 at 2:41, but #43 is the outro").
    (None, None) when the cue is not found."""
    positions = [p for p in ew.cue_positions(cue, norm_script) if p >= start_pos]
    if not positions:
        return None, None
    words = norm_script.split()
    finish = positions[0] + len(ew.normalize_ar(ew.spoken_text(cue)).split())
    after = words[finish:finish + 4]
    return finish / wpm * 60.0, (" ".join(after) if len(after) >= 3 else None)


def _broll_row_times(rows: list, script: str, wpm: float, exit_cues: list = None) -> tuple:
    """(start, end) script seconds for every B-roll row, using running cue positions. The
    end is when the END CUE finishes. `exit_cues`, when given, receives per row the words
    spoken right after the END CUE (None when unknown)."""
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    wpm = float(wpm or 135)
    pos, starts, ends = 0, [], []
    for row in rows:
        cue = row.get("start") or ""
        seconds = ew.cue_seconds(cue, script, wpm, after=pos) if cue else None
        if seconds is None:
            stamp = _seconds(row.get("time", ""))
            seconds = float(stamp) if stamp is not None else None
        starts.append(seconds)
        if cue:
            later = [p for p in ew.cue_positions(cue, norm_script) if p >= pos]
            if later:
                pos = later[0]
        start_pos = pos if cue and later else (int(seconds * wpm / 60.0) if seconds is not None else 0)
        end = row.get("end") or ""
        end_seconds, next_cue = _end_cue_finish(end, norm_script, wpm, start_pos) if end else (None, None)
        ends.append(end_seconds)
        if exit_cues is not None:
            exit_cues.append(next_cue)
    return starts, ends


OVERLAY_READ_WORDS_PER_SECOND = 2.5   # comfortable on-screen Arabic reading rate
OVERLAY_MAX_HOLD_SECONDS = 20.0


_RELOCATE_MIN_SHARED = 3
_RELOCATE_MIN_WEIGHT = 1.5        # sum of 1/(sentences containing the word)


def relocate_unfound_overlay_cues(text: str, script: str, wpm: float) -> str:
    """An overlay row whose START CUE is not in the script keeps the model's invented
    timestamp (a red-flag warning card fired at 8:50 over the salt section; its cue
    «لو فيه أعراض» was never spoken). Anchor such a row on the script sentence that
    shares the most meaningful words with its cue + on-screen text (at least 3), so the
    cue re-timing puts it where the line is really said. Rows with no such sentence are
    left alone."""
    if not text or not script:
        return text
    wpm = ew.blueprint_wpm({"words_per_minute": wpm})
    sentences = _script_sentences(script, wpm)
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    if not sentences:
        return text

    def stem(word):
        for prefix in ("وبال", "وال", "بال", "فال", "كال", "لل", "ال", "و", "ب", "ف"):
            if word.startswith(prefix) and len(word) - len(prefix) >= 3:
                return word[len(prefix):]
        return word

    def words(value):
        return {stem(w) for w in ew.normalize_ar(ew.spoken_text(value or "")).split() if len(w) >= 3}

    sentence_words = [words(s["text"]) for s in sentences]
    # Rare words identify a line; common ones ("فيه", "علامات") do not.
    frequency = {}
    for sw in sentence_words:
        for w in sw:
            frequency[w] = frequency.get(w, 0) + 1
    lines = text.splitlines()
    moved, i = 0, 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        upper = [c.upper() for c in _table_cells(lines[i])]
        start_col = next((k for k, h in enumerate(upper) if "START" in h or "▶" in h), None)
        end_col = next((k for k, h in enumerate(upper) if "END" in h or "⏹" in h), None)
        content_col = next((k for k, h in enumerate(upper) if "CONTENT" in h), None)
        if start_col is None or content_col is None or not any("ELEMENT" in h for h in upper):
            i = j
            continue
        separator = i + 1 < j and chapters.is_separator_row(lines[i + 1])
        for k in range(i + (2 if separator else 1), j):
            cells = _table_cells(lines[k])
            if len(cells) <= max(start_col, content_col):
                continue
            cue = cells[start_col]
            if not cue.strip() or ew.cue_positions(cue, norm_script):
                continue
            wanted = words(cue) | words(cells[content_col])
            if not wanted:
                continue
            scored = [(sum(1.0 / frequency[w] for w in wanted & sw), len(wanted & sw), n)
                      for n, sw in enumerate(sentence_words)]
            weight, shared, index = max(scored, key=lambda item: (item[0], item[1], -item[2]))
            if shared < _RELOCATE_MIN_SHARED or weight < _RELOCATE_MIN_WEIGHT:
                continue
            sentence = sentences[index]
            cells[start_col] = _sentence_cue(sentence, norm_script)
            if end_col is not None and end_col < len(cells) and not ew.cue_positions(cells[end_col], norm_script):
                cells[end_col] = " ".join(sentence["words"][-4:])
            lines[k] = "| " + " | ".join(cells) + " |"
            moved += 1
        i = j
    if not moved:
        return text
    print(f"  🔧 overlays: re-anchored {moved} rows whose cue was not in the script")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


_DRAWING_HEADING_RE = re.compile(r"(?im)^[^\n]*\bdrawing animation\s*#?\s*\d+[^\n]*$")
_TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*[–\-—]\s*(\d{1,2}:\d{2})(`?\s*\(\s*\d+\s*s(?:ec(?:onds)?)?\s*(?:total)?\s*\))?")


_NOT_CLOCK = r"(?!\s*(?:[AaPp]\.?[Mm]\b|ص\b|م\b|صباح|مساء|بالليل|الفجر))"


def retime_drawing_specs(guide: str, script: str, wpm: float) -> str:
    """Rewrites each drawing spec's first "M:SS – M:SS" range to the window measured from
    its spoken cues. The model's own estimate went stale ("6:10 – 6:30" for a drawing
    whose lines are spoken 6:24–6:36), and the editor and the critic both read it."""
    if not guide or not script:
        return guide
    heads = list(_DRAWING_HEADING_RE.finditer(guide))
    if not heads:
        return guide
    out, last, changed = [], 0, 0
    for n, head in enumerate(heads):
        end = heads[n + 1].start() if n + 1 < len(heads) else len(guide)
        block = guide[head.start():end]
        windows = _prose_drawing_windows(block, script, wpm)
        if len(windows) == 1 and windows[0][0] is not None:
            start, stop = windows[0]
            stop = stop if stop is not None and stop > start else start + DRAWING_WINDOW_SECONDS
            label = f"{_fmt_seconds(start)} – {_fmt_seconds(stop)} ({int(stop - start)}s total)"  # floor: read back as the length
            # Every other time in the spec (step times, "the editor cuts at 01:36 and back
            # at 01:56") moves by the same offset and stays inside the window: a critique
            # found three different windows in one spec.
            original = block
            # The block runs on to the chapter's summary tables: only a range written as the
            # drawing's own ("On screen", "Timing", "Duration", "Window", a step) is its window
            # (a density row «Hook & Setup (12:12 – 13:07)» was overwritten once).
            first = next((m for m in _TIME_RANGE_RE.finditer(block)
                          if re.search(r"(?i)on screen|timing|duration|window|placement|step|total",
                                       block[max(0, block.rfind("\n", 0, m.start())):m.start()])
                          and not block[max(0, block.rfind("\n", 0, m.start())):m.start()].lstrip().startswith("|")),
                         None)
            # Specs without a range ("starts at 2:16, holds ... from 2:24 to 2:28") shift
            # from their first time stamp (a run re-timed only 1 of 4 such specs; one sat
            # 45 s away from its spoken lines).
            # The spec's own "starts at M:SS" is its stated start (a code-written range from
            # an earlier pass must not hide a stale one); clock times ("3:00 AM" in a
            # thought bubble) are never the drawing's time.
            stamp = (re.search(r"(?i)\bstarts?\s*(?:at|:)?\s*(\d{1,2}:\d{2})" + _NOT_CLOCK, block) or first
                     or re.search(r"(?<![\d:])(\d{1,2}:\d{2})(?![\d:])" + _NOT_CLOCK, block))
            old_start = _seconds(stamp.group(1)) if stamp else None
            if old_start is not None:
                delta = start - old_start

                def shift(m):
                    # Only the drawing's own times: the block runs on to the chapter's
                    # density map and B-roll flags, and clamping those produced "5:22–5:22".
                    value = int(m.group(1)) * 60 + int(m.group(2))
                    if not old_start - 3 <= value <= old_start + DRAWING_MAX_SECONDS + 5:
                        return m.group(0)
                    return _fmt_seconds(value + delta)
                block = re.sub(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])" + _NOT_CLOCK, shift, block)

            # A label that quotes its own cue appears when that cue is spoken.
            def label_time(m):
                at = _cue_seconds_loose(m.group(4), script, wpm)
                if at is None or not start - 2 <= at <= stop + 5:
                    return m.group(0)
                return f"{m.group(1)}{_fmt_seconds(at)}{m.group(3)}"
            block = re.sub(r"(?i)(appears at\s*)(\d{1,2}:\d{2})(\s*\|\s*cue:\s*[«\"“]([^»\"”\n]+))", label_time, block)
            if first:
                at = block.find(first.group(0)) if first.group(0) in block else -1
                if at < 0:          # the shift rewrote it: find the drawing's range again
                    again = next((m for m in _TIME_RANGE_RE.finditer(block) if m.start() >= first.start() - 5), None)
                    at = again.start() if again else -1
                if at >= 0:
                    m = _TIME_RANGE_RE.match(block, at)
                    block = block[:at] + label + ("`" if m.group(3) and m.group(3).startswith("`") else "") + block[m.end():]
            else:
                head_end = block.find("\n")
                head_end = len(block) if head_end < 0 else head_end
                block = (block[:head_end] + f"\n- **On screen (from the spoken cues)**: {label}" + block[head_end:])
            if block != original:
                changed += 1
        out.append(guide[last:head.start()])
        out.append(block)
        last = end
    out.append(guide[last:])
    if changed:
        print(f"  🔧 drawings: re-timed {changed} drawing specs from their spoken cues")
    return "".join(out)


def restore_missing_headers(text: str) -> str:
    """Puts the header (+ separator) back on a table whose rows start with a number and
    have the same column count as an earlier table header in the document. A chapter-mode
    answer (or an older join) left chapters 2–4 of the transition map and overlay guide
    without a header, so no code repair could see their rows."""
    lines = (text or "").splitlines()
    headers, out, i, changed = {}, [], 0, False
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        block = lines[i:j]
        cells = _table_cells(block[0])
        if len(block) > 1 and chapters.is_separator_row(block[1]):
            headers.setdefault(len(cells), (block[0], block[1]))
        elif cells and re.fullmatch(r"\d+", cells[0].strip()) and len(cells) in headers:
            out.extend(headers[len(cells)])
            changed = True
        out.extend(block)
        i = j
    if not changed:
        return text
    return "\n".join(out) + ("\n" if (text or "").endswith("\n") else "")


def fix_overlay_read_times(text: str) -> str:
    """Lengthens every overlay's Hold (s) to fit its words at ~2.5 words/second (at most
    20 s). A critique measured a 60-word red-flag card held 9 s, a 30-word card 5.5 s."""
    text = restore_missing_headers(text)
    lines = (text or "").splitlines()
    changed, fixed, i = False, 0, 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        upper = [c.upper() for c in _table_cells(lines[i])]
        hold_col = next((k for k, h in enumerate(upper) if "HOLD" in h), None)
        content_col = next((k for k, h in enumerate(upper) if "CONTENT" in h or h.startswith("TEXT")), None)
        if hold_col is None or content_col is None or not any("ELEMENT" in h for h in upper):
            i = j
            continue
        separator = i + 1 < j and chapters.is_separator_row(lines[i + 1])
        for k in range(i + (2 if separator else 1), j):
            cells = _table_cells(lines[k])
            if len(cells) <= max(hold_col, content_col):
                continue
            hold = re.search(r"\d+(?:\.\d+)?", cells[hold_col])
            words = len(re.findall(r"[\w\u0600-\u06FF]+", cells[content_col]))
            if not hold or words == 0:
                continue
            need = min(OVERLAY_MAX_HOLD_SECONDS, math.ceil(words / OVERLAY_READ_WORDS_PER_SECOND * 2) / 2)
            if float(hold.group(0)) >= need:
                continue
            cells[hold_col] = cells[hold_col][:hold.start()] + f"{need:g}" + cells[hold_col][hold.end():]
            lines[k] = "| " + " | ".join(cells) + " |"
            changed, fixed = True, fixed + 1
        i = j
    if fixed:
        print(f"  🔧 overlays: lengthened {fixed} holds to their reading time")
    return "\n".join(lines) + ("\n" if (text or "").endswith("\n") else "") if changed else text


def move_overlays_out_of_drawings(text: str, script: str, wpm: float) -> str:
    """A whiteboard drawing fills the screen, so no text card may sit on it (a critique:
    "Drawing 4 is flagged clear, but Warning #28 starts at 12:58 and DVT Popup #29 at
    13:04 — three focal elements stack on the most safety-critical visual"). A card that
    starts inside a drawing's window moves to the first sentence after it; one that starts
    before and runs more than 1 s into it is cut to end at the drawing when it can still
    be read, otherwise moved after it as well. Drawing rows themselves stay."""
    windows = _drawing_windows(text, script, wpm)
    if not windows or not script:
        return text
    sentences = _script_sentences(script, wpm)
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    lines = text.splitlines()
    moved = cut = 0
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        upper = [c.upper() for c in _table_cells(lines[i])]

        def col(*names):
            return next((k for k, h in enumerate(upper) if any(n in h for n in names)), None)

        element_col, start_col, end_col = col("ELEMENT"), col("START CUE", "▶"), col("END CUE", "⏹")
        hold_col, content_col = col("HOLD"), col("CONTENT")
        if element_col is None or start_col is None or hold_col is None:
            i = j
            continue
        separator = i + 1 < j and chapters.is_separator_row(lines[i + 1])
        pos, free_at = 0, 0.0          # cards moved after one drawing queue, never stack
        for k in range(i + (2 if separator else 1), j):
            cells = _table_cells(lines[k])
            if len(cells) <= max(element_col, start_col, hold_col):
                continue
            element = cells[element_col].upper()
            if "DRAWING" in element or "WHITEBOARD" in element:
                continue
            start = ew.cue_seconds(cells[start_col], script, wpm, after=pos)
            hold = re.search(r"\d+(?:\.\d+)?", cells[hold_col])
            if start is None or not hold:
                continue
            later = [p for p in ew.cue_positions(cells[start_col], norm_script) if p >= pos]
            if later:
                pos = later[0]
            length = float(hold.group(0))
            for low, high in windows:
                inside = low - 0.25 <= start < high
                runs_in = start < low and start + length > low + 1.0
                if not inside and not runs_in:
                    continue
                words = len(re.findall(r"[\w\u0600-\u06FF]+", cells[content_col])) if content_col is not None \
                    and content_col < len(cells) else 0
                readable = max(1.5, words / OVERLAY_READ_WORDS_PER_SECOND)
                if runs_in and low - start >= readable:
                    cells[hold_col] = cells[hold_col][:hold.start()] + f"{math.floor((low - start) * 2) / 2:g}" \
                        + cells[hold_col][hold.end():]
                    cut += 1
                else:
                    sentence = _first_sentence(sentences, max(high, free_at), None)
                    if sentence is None:
                        break
                    cells[start_col] = _sentence_cue(sentence, norm_script)
                    if end_col is not None and end_col < len(cells):
                        cells[end_col] = " ".join(sentence["words"][-4:])
                    free_at = sentence["start_s"] + length
                    moved += 1
                lines[k] = "| " + " | ".join(cells) + " |"
                break
        i = j
    if not moved and not cut:
        return text
    print(f"  🔧 overlays: moved {moved} cards off whiteboard drawings, shortened {cut} to end at one")
    text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return ew.rewrite_cue_timestamps(text, script, wpm)


def _overlay_slot(position: str) -> str:
    """The screen zone a card occupies: full-width/centre cards share one zone."""
    p = (position or "").lower()
    if "full" in p or "center" in p or "centre" in p or "middle" in p:
        return "center"
    if "top" in p or "upper" in p:
        return "top-left" if "left" in p else "top-right"
    if "bottom" in p or "lower" in p:
        return "bottom"
    return "center"


def order_overlay_cards(text: str, script: str, wpm: float) -> str:
    """Sorts each overlay table by spoken time and renumbers 1..n across the guide (a
    critique found rows 38 (9:39) above 39 (9:30)); a card overlapping an earlier card in
    the same screen zone waits for it (two full-width titles fired on one frame at 5:22);
    a second section TITLE within 10 s of another is dropped. Runs before the B-roll and
    transition steps read the overlay numbers."""
    if not text or not script:
        return text
    sentences = _script_sentences(script, wpm)
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    lines = text.splitlines()
    number, queued, dropped, reordered = 0, 0, 0, False
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        upper = [c.upper() for c in _table_cells(lines[i])]

        def col(*names):
            return next((k for k, h in enumerate(upper) if any(n in h for n in names)), None)

        element_col, start_col, end_col = col("ELEMENT"), col("START CUE", "▶"), col("END CUE", "⏹")
        hold_col, pos_col = col("HOLD"), col("POSITION")
        time_col = col("TIMESTAMP", "TIMECODE", "⏱")
        num_col = next((k for k, h in enumerate(upper) if h.strip() == "#"), None)
        if element_col is None or start_col is None:
            i = j
            continue
        separator = i + 1 < j and chapters.is_separator_row(lines[i + 1])
        first = i + (2 if separator else 1)
        rows = []
        for k in range(first, j):
            cells = _table_cells(lines[k])
            if len(cells) <= max(element_col, start_col):
                continue
            t = ew.cue_seconds(cells[start_col], script, wpm)
            if t is None and time_col is not None and time_col < len(cells):
                # A paraphrased cue is not in the script; its Timestamp is what the editor
                # (and the critic) will follow — a quote box collided at 11:56 unchecked.
                stamp = _seconds(cells[time_col])
                t = float(stamp) if stamp is not None else None
            rows.append([t if t is not None else float("inf"), len(rows), cells])
        if [r[1] for r in sorted(rows, key=lambda r: (r[0], r[1]))] != [r[1] for r in rows]:
            reordered = True
        rows.sort(key=lambda r: (r[0], r[1]))
        kept, busy, last_title = [], {}, None
        for t, _, cells in rows:
            element = cells[element_col].upper()
            hold = re.search(r"\d+(?:\.\d+)?", cells[hold_col]) if hold_col is not None and hold_col < len(cells) else None
            length = float(hold.group(0)) if hold else 4.0
            if "DRAWING" in element or "WHITEBOARD" in element or t == float("inf"):
                kept.append(cells)
                continue
            if "TITLE" in element:
                if last_title is not None and t - last_title < 10.0:
                    dropped += 1
                    continue
                last_title = t
            slot = _overlay_slot(cells[pos_col] if pos_col is not None and pos_col < len(cells) else "")
            if busy.get(slot, -1.0) > t + 0.25:
                sentence = _first_sentence(sentences, busy[slot], None)
                if sentence is not None:
                    cells[start_col] = _sentence_cue(sentence, norm_script)
                    if end_col is not None and end_col < len(cells):
                        cells[end_col] = " ".join(sentence["words"][-4:])
                    t = sentence["start_s"]
                    queued += 1
            busy[slot] = t + length
            kept.append(cells)
        out = []
        for cells in kept:
            number += 1
            if num_col is not None and num_col < len(cells):
                cells[num_col] = str(number)
            out.append("| " + " | ".join(cells) + " |")
        lines[first:j] = out
        i = first + len(out)
    if not (queued or dropped or reordered):
        return text
    print(f"  🔧 overlays: time-ordered the cards, queued {queued} that overlapped in one screen zone, "
          f"dropped {dropped} duplicate section titles")
    text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return ew.rewrite_cue_timestamps(text, script, wpm)


def _overlay_warning_holds(text: str, script: str, wpm: float) -> list:
    """[(start_s, end_s)] for overlay rows whose Element Type is a WARNING/ALERT box."""
    return _overlay_holds(text, script, wpm, lambda element, position: "WARNING" in element or "ALERT" in element)


def _overlay_title_holds(text: str, script: str, wpm: float) -> list:
    """[(start_s, end_s)] for full-width/centre TITLE cards: a B-roll starting under one
    hides the shot behind the card (a critique: a full-width title fired on the exact
    entry of the licorice-vendor B-roll)."""
    return _overlay_holds(text, script, wpm,
                          lambda element, position: "TITLE" in element and _overlay_slot(position) == "center")


def _overlay_holds(text: str, script: str, wpm: float, wanted) -> list:
    """[(start_s, end_s)] for overlay rows where wanted(ELEMENT TYPE upper, position)."""
    lines = (text or "").splitlines()
    holds, i = [], 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        header = _table_cells(lines[i])
        upper = [c.upper() for c in header]
        if not (any("ELEMENT" in c for c in upper) and any("HOLD" in c for c in upper)
                and any("START" in c for c in upper)):
            i = j
            continue

        def col(*names):
            return next((k for k, h in enumerate(upper) if any(n in h for n in names)), None)

        element_col = col("ELEMENT")
        start_col = col("START CUE", "▶", "START")
        hold_col = col("HOLD")
        time_col = col("TIMESTAMP", "TIMECODE", "TIME", "⏱")
        pos_col = col("POSITION")
        separator = i + 1 < j and chapters.is_separator_row(lines[i + 1])
        pos = 0
        norm_script = ew.normalize_ar(ew.spoken_text(script))
        for k in range(i + (2 if separator else 1), j):
            cells = _table_cells(lines[k])
            if not cells or all(_SEPARATOR_CELL.fullmatch(c) for c in cells if c):
                continue
            element = cells[element_col].upper() if element_col is not None and element_col < len(cells) else ""
            position = cells[pos_col] if pos_col is not None and pos_col < len(cells) else ""
            if start_col is not None and start_col < len(cells) and cells[start_col]:
                later = [p for p in ew.cue_positions(cells[start_col], norm_script) if p >= pos]
                if later:
                    pos = later[0]          # rows run in script order: a repeated cue means the next one
            if not wanted(element, position):
                continue
            hold_match = re.search(r"\d+(?:\.\d+)?",
                                   cells[hold_col] if hold_col is not None and hold_col < len(cells) else "")
            if not hold_match or float(hold_match.group(0)) <= 0:
                continue
            start = None
            if start_col is not None and start_col < len(cells) and cells[start_col]:
                start = ew.cue_seconds(cells[start_col], script, wpm, after=pos)
            if start is None and time_col is not None and time_col < len(cells):
                stamp = _seconds(cells[time_col])
                start = float(stamp) if stamp is not None else None
            if start is not None:
                holds.append((float(start), float(start) + float(hold_match.group(0))))
        i = j
    return holds


def _move_out_of_warning_holds(state: PipelineState, text: str, broll_table: str,
                               script: str, wpm: float) -> tuple:
    """Moves transition rows that would cut away during a warning card's hold to the
    first sentence at/after the hold's end. A code-added cut into B-roll moves that
    B-roll row's own START CUE instead, so the two layers stay on the same clock."""
    drawing_windows = _drawing_windows(state.get("text_animation_overlay", ""), script, wpm)
    warnings = sorted(_overlay_warning_holds(state.get("text_animation_overlay", ""), script, wpm)
                      + drawing_windows)
    blocks = _transition_blocks(text)
    if not warnings or not blocks:
        return text, broll_table, 0

    sentences = _script_sentences(script, wpm)
    norm_script = ew.normalize_ar(ew.spoken_text(script))
    realigned, _ = ew.realign_broll_table(broll_table or "")
    broll_layout = _broll_layout(realigned) if realigned else None
    broll_by_num = {}
    if broll_layout and broll_layout["cols"]["num"] is not None:
        for row in broll_layout["rows"]:
            cells, at = row["cells"], broll_layout["cols"]["num"]
            if at < len(cells):
                broll_by_num[cells[at].strip()] = row

    moved, broll_changed = 0, False
    for warning_start, warning_end in warnings:
        is_drawing = (warning_start, warning_end) in drawing_windows
        for block in blocks:
            cols = block["cols"]
            for row in list(block["rows"]):
                when = _transition_row_time(row, cols, script, wpm)
                if when is None or not (warning_start < when < warning_end):
                    continue
                from_to = _row_cell(row, cols, "from_to")
                if is_drawing and re.search(r"(?i)drawing|whiteboard", from_to):
                    continue                    # the cut into/out of this drawing itself
                if is_drawing and _presenter_only(from_to):
                    block["rows"].remove(row)    # the presenter is off screen: no move to see
                    moved += 1
                    continue
                sentence = _first_sentence(sentences, warning_end, None)
                if sentence is None:
                    continue
                cue = _sentence_cue(sentence, norm_script)
                cells = row["cells"]
                rationale = (cells[cols["rationale"]]
                             if cols["rationale"] is not None and cols["rationale"] < len(cells) else "")
                cut = re.search(r"Code-added: cut to B-roll #(\d+)", rationale)
                if cut and broll_layout is not None and cut.group(1) in broll_by_num:
                    broll_row = broll_by_num[cut.group(1)]
                    start_col = broll_layout["cols"]["start"]
                    if start_col is not None and start_col < len(broll_row["cells"]):
                        broll_row["cells"][start_col] = cue
                        broll_changed = True
                if cols["cue"] is not None and cols["cue"] < len(cells):
                    cells[cols["cue"]] = cue
                elif cols["time"] is not None and cols["time"] < len(cells):
                    cells[cols["time"]] = _fmt_seconds(sentence["start_s"])
                moved += 1

    if moved:
        text = _render_transition_blocks(text, blocks)
        text = ew.rewrite_cue_timestamps(text, script, wpm)
    if broll_changed:
        broll_table = _render_broll_layout(broll_layout, broll_layout["rows"])
        broll_table = ew.rewrite_broll_timestamps(broll_table, script, wpm)
    return text, broll_table, moved


def sync_transition_map(state: PipelineState, transition_design: str, broll_table: str,
                        script: str, wpm: float, broll_out: list = None,
                        map_out: list = None) -> tuple:
    """Runs the sync pass until it changes nothing (at most 3 passes): moving a transition
    out of a warning hold can uncover a B-roll cut, which the next pass adds. See
    _sync_transition_once. Returns (transition_text, total_added). `map_out`, when given,
    receives the old→new transition number map (code-added rows have no old number)."""
    total = 0
    text = restore_missing_headers(transition_design)
    mapping = {} if map_out is not None else None
    for _ in range(3):
        holder = []
        new_text, added, moved = _sync_transition_once(
            state, text, broll_table, script, wpm, holder, map_out=mapping)
        if holder:
            broll_table = holder[-1]
        total += added
        unchanged = new_text == text
        text = new_text
        if (not added and not moved) or unchanged:
            break
    text = drop_zero_length_flashes(text, script, wpm)
    text = drop_moves_on_cuts(text, script, wpm,
                              _card_entries((state or {}).get("text_animation_overlay", ""), script, wpm))
    text = chain_transition_states(text)
    text = rebuild_density_summary(text, script, wpm)
    if broll_out is not None:
        broll_out.append(broll_table)
    if map_out is not None:
        map_out.append(mapping or {})
    return text, total


def _same_visual(a: str, b: str) -> bool:
    """Two labels for one visual: "Warning Card" vs "Warning Card (Beta-Blockers)",
    "Talking Head CU" vs "Talking Head" — the core words before any bracket match."""
    def core(label):
        words = re.sub(r"\(.*?\)|[^\w\s#]", " ", label or "").lower().split()
        return " ".join(words[:2])
    ref_a, ref_b = _BROLL_REF.search(a or ""), _BROLL_REF.search(b or "")
    if ref_a or ref_b:                       # "B-roll #2" and "B-roll #3" are two clips
        return bool(ref_a and ref_b) and ref_a.group(2) == ref_b.group(2)
    return bool(core(a)) and core(a) == core(b)


PRESENTER_FLASH_SECONDS = 2.0


def drop_zero_length_flashes(text: str, script: str, wpm: float) -> str:
    """Drops an "X → Y" row immediately followed by "Y → X" on the same moment: Y would be
    on screen for 0 frames (a critique found Talking Head → Drawing 2 → Talking Head all
    at 8:21). The chain is rebuilt afterwards."""
    blocks = _transition_blocks(text or "")
    if not blocks:
        return text
    rows = [(block, row) for block in blocks for row in block["rows"]]
    times = _transition_row_times(blocks, script, wpm)
    drop = set()
    for k in range(len(rows) - 1):
        (b1, r1), (b2, r2) = rows[k], rows[k + 1]
        t1, t2 = times[k], times[k + 1]
        if t1 is None or t2 is None or k in drop:
            continue
        p1 = re.split(r"\s*(?:→|->)\s*", _row_cell(r1, b1["cols"], "from_to"), maxsplit=1)
        p2 = re.split(r"\s*(?:→|->)\s*", _row_cell(r2, b2["cols"], "from_to"), maxsplit=1)
        # The presenter for 2 s between two B-rolls is a stutter (a critique: "B-roll #9
        # ends 4:10 → presenter for ~1 s → B-roll #10 at 4:12"): cut clip to clip.
        between_clips = (len(p1) == 2 and len(p2) == 2 and _BROLL_REF.search(p1[0]) and _BROLL_REF.search(p2[1])
                         and re.search(r"(?i)talking head|presenter|a-?roll", p1[1]))
        limit = PRESENTER_FLASH_SECONDS if between_clips else COLLISION_SECONDS
        if abs(t2 - t1) > limit:
            continue
        if len(p1) == 2 and len(p2) == 2 and p1[1].strip() and _same_visual(p1[1], p2[0]):
            # "X → Y" + "Y → X" flashes Y: drop both. "B-roll #2 → Talking Head" +
            # "Talking Head → B-roll #3" is one cut from #2 to #3: keep the second row (the
            # chain then names #2 as its From); dropping both orphaned B-roll #3.
            drop.update({k, k + 1} if _same_visual(p1[0], p2[1]) else {k})
    if not drop:
        return text
    for k in sorted(drop, reverse=True):
        block, row = rows[k]
        block["rows"].remove(row)
    print(f"  🔧 transition map: removed {len(drop)} rows of zero-length flashes")
    return _render_transition_blocks(text, blocks)


def _card_entries(guide: str, script: str, wpm: float) -> list:
    """Start times of centre TITLE / KINETIC cards (type animating in on screen)."""
    return [a for a, _ in _overlay_holds(guide, script, wpm, lambda element, position: (
        "TITLE" in element or "KINETIC" in element) and _overlay_slot(position) == "center")]


def drop_moves_on_cuts(text: str, script: str, wpm: float, card_times: list = None) -> str:
    """Drops a presenter-only move (punch-in, reframe, scale) on the same moment as a cut:
    a camera move starting on the frame of a cut reads as a stutter (a critique listed
    seven such pairs, e.g. a cut back from B-roll and a 107% push-in both at 8:46). The
    cut stays; the presenter is simply on camera."""
    blocks = _transition_blocks(text or "")
    if not blocks:
        return text
    rows = [(block, row) for block in blocks for row in block["rows"]]
    times = _transition_row_times(blocks, script, wpm)
    cuts = [t for (block, row), t in zip(rows, times)
            if t is not None and not _presenter_only(_row_cell(row, block["cols"], "from_to"))]
    dropped = 0
    for (block, row), t in zip(rows, times):
        if t is None or not _presenter_only(_row_cell(row, block["cols"], "from_to")):
            continue
        if any(abs(t - c) <= COLLISION_SECONDS + 0.5 for c in cuts) or any(
                abs(t - c) <= 1.0 for c in (card_times or [])):
            block["rows"].remove(row)       # type and camera never animate together
            dropped += 1
    if not dropped:
        return text
    print(f"  🔧 transition map: removed {dropped} presenter moves on the frame of a cut or title card")
    return _render_transition_blocks(text, blocks)


def chain_transition_states(text: str) -> str:
    """Makes the transition table one continuous state chain: in table order (already
    sorted by time), each row's "From" is the previous row's "To". A critique failed a
    map whose rows said "Talking Head → B-roll #9" right after a cut TO a split-screen —
    the editor cannot follow a table whose states jump."""
    blocks = _transition_blocks(text or "")
    if not blocks:
        return text
    previous_to, changed = None, 0
    for block in blocks:
        at = block["cols"].get("from_to")
        if at is None:
            continue
        for row in block["rows"]:
            if at >= len(row["cells"]):
                continue
            parts = re.split(r"\s*(?:→|->)\s*", row["cells"][at], maxsplit=1)
            if len(parts) != 2:
                previous_to = None
                continue
            source, target = parts[0].strip(), parts[1].strip()
            if previous_to and source != previous_to:
                row["cells"][at] = f"{previous_to} → {target}"
                changed += 1
            same_clip = bool(previous_to and _BROLL_REF.search(previous_to) and _BROLL_REF.search(target)
                             and _same_visual(previous_to, target))
            if previous_to and (previous_to == target or same_clip):
                # "Talking Head → Talking Head" is a stale return; "B-roll #36 (Callback) →
                # B-roll #36 (Kitchen Table)" cuts a clip to itself (a 0-second row).
                row["_drop"] = True
                changed += 1
                continue
            previous_to = target
        block["rows"] = [row for row in block["rows"] if not row.get("_drop")]
    if not changed:
        return text
    return _render_transition_blocks(text, blocks)


_DENSITY_HEAD_RE = re.compile(r"^#{2,4}\s*Transition Density Summary.*$", re.IGNORECASE | re.MULTILINE)


def rebuild_density_summary(text: str, script: str, wpm: float) -> str:
    """Replaces the model's "Transition Density Summary" table with counts from the final
    table, per script section: the model's summary predates the code-added cuts and its
    own act times (a real one said 46 transitions over 0:00–12:35 for an 84-row table on a
    17-minute script)."""
    head = _DENSITY_HEAD_RE.search(text or "")
    blocks = _transition_blocks(text or "")
    if not head or not blocks or not script:
        return text
    wpm = ew.blueprint_wpm({"words_per_minute": wpm})
    rows = [(block, row) for block in blocks for row in block["rows"]]
    times = _transition_row_times(blocks, script, wpm)
    marks, words = [], 0
    for title, part in chapters.split_by_chapters(chapters.normalize_section_headers(script)):
        marks.append((words / wpm * 60.0, title or "Opening"))
        words += len(ew.spoken_text(part).split())
    end = words / wpm * 60.0
    counts = [[0, 0] for _ in marks]
    for t, (block, row) in zip(times, rows):
        if t is None:
            continue
        k = max(i for i, (start, _) in enumerate(marks) if start <= t or i == 0)
        kind = _row_cell(row, block["cols"], "type").lower()
        counts[k][0 if ("cut" in kind and "dissolve" not in kind) else 1] += 1
    lines = ["| Section | Time | Total Transitions | Hard Cuts | Soft Transitions |", "|---|---|---|---|---|"]
    for i, ((start, title), (hard, soft)) in enumerate(zip(marks, counts)):
        if hard + soft == 0:
            continue
        stop = marks[i + 1][0] if i + 1 < len(marks) else end
        lines.append(f"| {title[:60]} | {_fmt_seconds(start)}–{_fmt_seconds(stop)} | {hard + soft} | {hard} | {soft} |")
    hard_all, soft_all = sum(c[0] for c in counts), sum(c[1] for c in counts)
    total = hard_all + soft_all
    if not total:
        return text
    lines.append(f"| **Entire Video Total** | 0:00–{_fmt_seconds(end)} | **{total}** | "
                 f"**{hard_all} ({round(100 * hard_all / total)}%)** | **{soft_all} ({round(100 * soft_all / total)}%)** |")
    # Chapter mode has one summary per chapter: keep one whole-video summary (the first).
    later = list(_DENSITY_HEAD_RE.finditer(text))[1:]
    for extra in reversed(later):
        tail = text[extra.end():]
        table = re.search(r"\n((?:[ \t]*\|.*\n?)+)", tail)
        stop = extra.end() + (table.end() if table and not tail[:table.start()].strip() else 0)
        text = text[:extra.start()] + text[stop:]
    head = _DENSITY_HEAD_RE.search(text)
    # Replace the first table after the heading (or insert one right under it).
    after = text[head.end():]
    table = re.search(r"\n((?:[ \t]*\|.*\n?)+)", after)
    if table and not after[:table.start()].strip():
        return text[:head.end()] + "\n\n" + "\n".join(lines) + "\n" + after[table.end():]
    return text[:head.end()] + "\n\n" + "\n".join(lines) + "\n" + after


_VISUAL_WORDS = re.compile(r"(?i)b-?\s?roll|animation|graphic|drawing|whiteboard|motion|\b[23]d\b|split|"
                           r"card|overlay|screen|black|title|map|diagram|insert|cutaway|footage|stock|end ?card")
_PRESENTER_WORDS = re.compile(r"(?i)talking head|a-?roll|presenter|dr\.?\s|د\.|medium|close-?up|wide|punch|reframe")


def _presenter_only(from_to: str) -> bool:
    """True when a transition only changes the presenter shot (both sides are the
    talking head / A-roll framing, no other visual)."""
    text = from_to or ""
    return bool(_PRESENTER_WORDS.search(text)) and not _VISUAL_WORDS.search(text)


def _graphics_become_drawings(blocks: list, overlay_guide: str, script: str, wpm: float) -> int:
    """A transition-map graphic (animation/diagram/split-screen) on screen across a
    whiteboard drawing is the same explanation drawn twice: the drawing owns it. The
    graphic's name in every From → To cell is replaced by the drawing, so the cuts keep
    their timing and lead to one visual. A critique asked for exactly this in three runs."""
    drawings = _drawing_windows(overlay_guide or "", script, wpm)
    if not drawings or not blocks:
        return 0
    rows = [(block, row) for block in blocks for row in block["rows"]]
    timed = sorted([(t, block, row) for (block, row), t in zip(rows, _transition_row_times(blocks, script, wpm))
                    if t is not None], key=lambda item: item[0])
    renamed = 0
    for k, (t, block, row) in enumerate(timed):
        at = block["cols"].get("from_to")
        if at is None or at >= len(row["cells"]):
            continue
        parts = re.split(r"\s*(?:→|->)\s*", row["cells"][at], maxsplit=1)
        target = parts[-1].strip() if len(parts) == 2 else ""
        if not target or not _GRAPHIC_TO_RE.search(target) or re.search(r"(?i)b-?\s?roll|drawing|whiteboard|sketch", target):
            continue
        until = next((u for u, _, _ in timed[k + 1:] if u > t + 0.5), t + GRAPHIC_AFTER_SECONDS)
        hit = next((n for n, (a, b) in enumerate(drawings, 1) if t < b - 1.0 and until > a + 1.0), None)
        if hit is None:
            continue
        name = f"Whiteboard Drawing {hit}"
        for other_t, other_block, other in timed:
            oat = other_block["cols"].get("from_to")
            if oat is not None and oat < len(other["cells"]) and t - 0.5 <= other_t <= until + 1.0 \
                    and target in other["cells"][oat]:
                other["cells"][oat] = other["cells"][oat].replace(target, name)
        renamed += 1
    return renamed


PHANTOM_SLACK_SECONDS = 8.0


def _drop_phantom_broll_rows(blocks: list, brolls: list, starts: list, ends: list,
                             script: str, wpm: float) -> int:
    """Removes rows naming a B-roll that is not on screen then: a number missing from the
    shot list, or a moment more than 8 s outside that clip's window (a critique: "T#22/T#23
    point to B-roll #43 at 2:41, but B-roll #43 is the outro shot at 15:40"). The cuts the
    shot list needs are added afterwards. Returns how many rows were removed."""
    window = {}
    for row, st, en in zip(brolls, starts, ends):
        if st is not None:
            window[str(row.get("num", "")).strip()] = (st, en if en is not None else st + BROLL_CLIP_SECONDS[1])
    if not window:
        return 0
    flat = [(block, row) for block in blocks for row in block["rows"]]
    removed = 0
    for (block, row), t in zip(flat, _transition_row_times(blocks, script, wpm)):
        refs = _BROLL_REF.findall(_row_cell(row, block["cols"], "from_to"))
        if t is None or not refs:
            continue
        for _, number in refs:
            span = window.get(number)
            if span is None or not span[0] - PHANTOM_SLACK_SECONDS <= t <= span[1] + PHANTOM_SLACK_SECONDS:
                block["rows"].remove(row)
                removed += 1
                break
    return removed


def _sync_transition_once(state: PipelineState, transition_design: str, broll_table: str,
                          script: str, wpm: float, broll_out: list = None,
                          map_out: dict = None) -> tuple:
    """Adds the final B-roll table's cuts to the transition map and keeps both layers on
    the script's clock. Run after repair_broll_timeline, because gap-fills add B-rolls the
    map's designer never saw.

    For every B-roll row whose start (and, when it has an END CUE, end) has no transition
    row within ±3 s, one Cut row is added, then all rows are sorted by cue position,
    renumbered and re-timed. Warning/alert overlays are life-safety cards: a transition
    strictly inside their hold is moved to the first sentence after the hold. Idempotent.

    Returns (transition_text, added_count). `broll_out`, when given, receives the (possibly
    re-timed) B-roll table so the node can store it as well."""
    if not transition_design or not broll_table or not script:
        return transition_design, 0, 0
    wpm = ew.blueprint_wpm({"words_per_minute": wpm})
    realigned, _ = ew.realign_broll_table(broll_table)
    brolls = ew.broll_rows(realigned)
    blocks = _transition_blocks(transition_design)
    if not brolls or not blocks:
        return transition_design, 0, 0

    target = next((block for block in blocks if block["cols"]["cue"] is not None), blocks[0])
    exit_cues = []
    starts, ends = _broll_row_times(brolls, script, wpm, exit_cues=exit_cues)
    phantoms = _drop_phantom_broll_rows(blocks, brolls, starts, ends, script, wpm)
    if phantoms:
        print(f"  🔧 transition map: removed {phantoms} rows naming a B-roll that is not on screen then")
    replaced = _graphics_become_drawings(blocks, (state or {}).get("text_animation_overlay", ""), script, wpm)
    if replaced:
        print(f"  🔧 transition map: {replaced} graphic(s) overlapping a whiteboard drawing now cut to the drawing")
    row_refs = [(block, row) for block in blocks for row in block["rows"]]
    # A presenter-only move (punch-in, reframe) is not a cut to anything: it neither
    # covers a B-roll's entry nor may be relabelled as one (a critique found "Scale +
    # Position shift → B-roll #14" whose rationale reframed the presenter).
    timed = [(t, block, row) for t, (block, row) in zip(_transition_row_times(blocks, script, wpm), row_refs)
             if t is not None and not _presenter_only(_row_cell(row, block["cols"], "from_to"))]
    existing = [t for t, _, _ in timed]
    # A cut needed inside a warning card's hold is placed at the hold's end instead:
    # adding it inside and then moving it out uncovered the B-roll edge again, so the
    # passes ping-ponged (a real run: added 1 / moved 1 three times).
    holds = _overlay_warning_holds((state or {}).get("text_animation_overlay", ""), script, wpm)
    # A whiteboard drawing owns its window too: a cut inside it loses the drawing's payoff.
    holds = sorted(holds + _drawing_windows((state or {}).get("text_animation_overlay", ""), script, wpm))
    sentences = _script_sentences(script, wpm) if holds else []
    norm_script = ew.normalize_ar(ew.spoken_text(script)) if holds else ""

    def out_of_hold(when, cue):
        for low, high in holds:
            if when is not None and low < when < high:
                sentence = _first_sentence(sentences, high, None)
                if sentence is not None:
                    return sentence["start_s"], _sentence_cue(sentence, norm_script)
        return when, cue

    def covered(when, low=None, skip=None):
        """A row within ±3 s of `when` (or, for an exit, anywhere from `low` — where the END
        CUE begins — to 3 s after it finishes; the early-exit pass below moves it to the
        finish). `skip` is the clip's start: the entry row never doubles as its exit."""
        low = when - TRANSITION_TOLERANCE_SECONDS if low is None else low - TRANSITION_TOLERANCE_SECONDS
        return any(low <= t <= when + TRANSITION_TOLERANCE_SECONDS
                   and (skip is None or abs(t - skip) > TRANSITION_TOLERANCE_SECONDS) for t in existing) or any(
            lo < when < hi for lo, hi in holds)

    relabelled = [0]

    def relabel(when, number, entering, low=None, skip=None):
        """A model row already at this B-roll edge that names no B-roll (e.g. "A-roll →
        2D Animation" where B-roll #5 starts) contradicts the shot list: point it at the
        B-roll, keeping its transition type. Rows naming some B-roll are left alone."""
        low = when - TRANSITION_TOLERANCE_SECONDS if low is None else low - TRANSITION_TOLERANCE_SECONDS
        near = [(abs(t - when), block, row) for t, block, row in timed
                if low <= t <= when + TRANSITION_TOLERANCE_SECONDS
                and (skip is None or abs(t - skip) > TRANSITION_TOLERANCE_SECONDS)]
        if not near or not number:
            return
        _, block, row = min(near, key=lambda item: item[0])
        at = block["cols"].get("from_to")
        if at is None or at >= len(row["cells"]):
            return
        current = row["cells"][at]
        if re.search(r"b-?\s?roll", current, re.IGNORECASE):
            if re.search(rf"b-?\s?roll\s*#?\s*{re.escape(number)}\b", current, re.IGNORECASE):
                return
            # Names another B-roll number at this B-roll's edge: a stale or wrong ID.
            parts = re.split(r"\s*(?:→|->)\s*", current, maxsplit=1)
            if len(parts) == 2:
                k = 1 if entering else 0
                fixed = re.sub(r"(?i)(b-?\s?roll\s*#?\s*)\d+", rf"\g<1>{number}", parts[k], count=1)
                if fixed != parts[k]:
                    parts[k] = fixed
                    row["cells"][at] = f"{parts[0]} → {parts[1]}"
                    relabelled[0] += 1
            return
        parts = re.split(r"\s*(?:→|->)\s*", current, maxsplit=1)
        if entering and len(parts) > 1 and _GRAPHIC_TO_RE.search(parts[1]):
            return                              # a designed graphic owns this moment
        side_from = parts[0].strip() or "Talking Head"
        side_to = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "Talking Head"
        row["cells"][at] = (f"{side_from} → B-roll #{number}" if entering
                            else f"B-roll #{number} → {side_to}")
        relabelled[0] += 1

    added = 0
    shifted = {}                    # B-roll number -> new START CUE (moved out of a hold)
    for index, (row, start, end, exit_cue) in enumerate(zip(brolls, starts, ends, exit_cues)):
        number = str(row.get("num", "")).strip()
        if start is not None:
            start_at, start_cue = out_of_hold(start, row.get("start", ""))
            if start_at != start and number:
                shifted[number] = start_cue      # one visual owner: the B-roll waits too
            if not covered(start_at):
                target["rows"].append(_new_transition_row(
                    target, start_cue, f"Talking Head → B-roll #{number}",
                    f"Code-added: cut to B-roll #{number}", start_at))
                existing.append(start_at)
                added += 1
            else:
                relabel(start_at, number, entering=True)
        if row.get("end") and end is not None:
            end_at, end_cue = out_of_hold(end, exit_cue or row.get("end", ""))
            cue_begins = end - len(ew.normalize_ar(ew.spoken_text(row["end"])).split()) / wpm * 60.0
            low = cue_begins if end_at == end else None
            if not covered(end_at, low, start):
                # The next clip starting on this moment: one cut from clip to clip (its
                # entry is then covered by this row).
                following = next(((str(r.get("num", "")).strip(), t) for r, t in
                                  zip(brolls[index + 1:], starts[index + 1:]) if t is not None), None)
                if following and abs(following[1] - end_at) <= PRESENTER_FLASH_SECONDS:
                    move, why = f"B-roll #{number} → B-roll #{following[0]}", f"Code-added: straight on to B-roll #{following[0]}"
                else:
                    move, why = f"B-roll #{number} → Talking Head", "Code-added: back to the presenter"
                target["rows"].append(_new_transition_row(target, end_cue, move, why, end_at))
                existing.append(end_at)
                added += 1
            else:
                relabel(end_at, number, entering=False, low=low, skip=start)

    # Each whiteboard drawing is one full-screen shot: the map must cut to it and back (a
    # critique: "the drawing guide says the editor cuts at 01:36 and back at 01:56 — those
    # two cuts do not exist anywhere in the Transition Table").
    drawings = _drawing_windows((state or {}).get("text_animation_overlay", ""), script, wpm)
    if drawings:
        all_sentences = sentences or _script_sentences(script, wpm)
        norm = norm_script or ew.normalize_ar(ew.spoken_text(script))
        flat_now = [(block, row) for block in blocks for row in block["rows"]]
        now_times = _transition_row_times(blocks, script, wpm)
        labelled = [(t, _row_cell(row, block["cols"], "from_to")) for (block, row), t in zip(flat_now, now_times)
                    if t is not None]
        for n, (low, high) in enumerate(drawings, 1):
            name = f"Whiteboard Drawing {n}"
            into = any(abs(t - low) <= TRANSITION_TOLERANCE_SECONDS and re.search(
                r"(?i)drawing|whiteboard", ft.split("→")[-1]) for t, ft in labelled)
            if not into:
                sentence = _first_sentence(all_sentences, low - 1.0, low + TRANSITION_TOLERANCE_SECONDS)
                if sentence is not None:
                    target["rows"].append(_new_transition_row(
                        target, _sentence_cue(sentence, norm), f"Talking Head → {name}",
                        f"Code-added: cut to {name} (full screen)", sentence["start_s"]))
                    added += 1
            # The window's end is an estimate: a designed exit anywhere after the drawing
            # starts is its exit (an exit 4 s early got a second one added every pass).
            out = any(low < t <= high + TRANSITION_TOLERANCE_SECONDS and re.search(
                r"(?i)drawing|whiteboard", ft.split("→")[0]) for t, ft in labelled)
            if not out:
                sentence = _first_sentence(all_sentences, high - 1.0, high + 2 * TRANSITION_TOLERANCE_SECONDS)
                if sentence is not None:
                    target["rows"].append(_new_transition_row(
                        target, _sentence_cue(sentence, norm), f"{name} → Talking Head",
                        f"Code-added: back to the presenter after {name}", sentence["start_s"]))
                    added += 1

    # A presenter-only move (punch-in, reframe) while a B-roll fills the screen cannot be
    # seen and contradicts the cut at the same second (a critique listed 24 such pairs).
    on_screen = [(st - 1.5, en if en is not None else st + BROLL_CLIP_SECONDS[1])
                 for st, en in zip(starts, ends) if st is not None]
    all_times = _transition_row_times(blocks, script, wpm)
    dropped_moves = 0
    flat = [(block, row) for block in blocks for row in block["rows"]]
    for (block, row), t in zip(flat, all_times):
        if t is None or not _presenter_only(_row_cell(row, block["cols"], "from_to")):
            continue
        if any(lo <= t < hi for lo, hi in on_screen):
            block["rows"].remove(row)
            dropped_moves += 1
    if dropped_moves:
        print(f"  🔧 transition map: removed {dropped_moves} presenter moves hidden under a B-roll")
    # A cut OUT of B-roll #N a second after cutting in flashes the clip: the exit belongs
    # at the B-roll's END CUE (a critique found four 1–2 s flashes of 5–7 s clips).
    flat = [(block, row) for block in blocks for row in block["rows"]]
    flat_times = _transition_row_times(blocks, script, wpm)
    by_num = {str(r.get("num", "")).strip(): (r, st, en, xc)
              for r, st, en, xc in zip(brolls, starts, ends, exit_cues)}
    stretched = 0
    for (block, row), t in zip(flat, flat_times):
        parts = re.split(r"\s*(?:→|->)\s*", _row_cell(row, block["cols"], "from_to"), maxsplit=1)
        m = _BROLL_REF.search(parts[0])
        cue_col = block["cols"].get("cue")
        if not m or t is None or cue_col is None or cue_col >= len(row["cells"]):
            continue
        entry = by_num.get(m.group(2))
        if not entry:
            continue
        brow, st, en, exit_cue = entry
        if st is None or en is None or not brow.get("end"):
            continue
        # Back to the presenter before the END CUE finishes cuts the clip short (a critique
        # listed seven 1–2 s clips); a cut on to a designed graphic keeps its early moment.
        to_presenter = len(parts) < 2 or not _GRAPHIC_TO_RE.search(parts[1]) and not _BROLL_REF.search(parts[1])
        later = [x for x in starts if x is not None and x > st + 0.5]
        next_start = min(later) if later else None
        late = to_presenter and en + 1.5 < t and (next_start is None or t < next_start - 0.5)
        early = st - 0.5 <= t < min(st + 2.5, en - 1.0) or to_presenter and st - 0.5 <= t < en - 0.75 or late
        if early:
            row["cells"][cue_col] = exit_cue or brow["end"]
            stretched += 1
    if stretched:
        print(f"  🔧 transition map: moved {stretched} early B-roll exits to the clip's end")
    # A code-added cut is redundant when another row already names that B-roll edge at
    # the same moment (a relabelled designed row + a code-added row both "→ B-roll #5").
    flat = [(block, row) for block in blocks for row in block["rows"]]
    flat_times = _transition_row_times(blocks, script, wpm)
    redundant = []
    for k, ((block, row), t) in enumerate(zip(flat, flat_times)):
        if t is None or "code-added" not in _row_cell(row, block["cols"], "rationale").lower():
            continue
        refs = set(_BROLL_REF.findall(_row_cell(row, block["cols"], "from_to")))
        for m, ((other_block, other), u) in enumerate(zip(flat, flat_times)):
            if m != k and u is not None and abs(u - t) <= COLLISION_SECONDS and other not in [r for _, r in redundant] \
                    and refs & set(_BROLL_REF.findall(_row_cell(other, other_block["cols"], "from_to"))):
                redundant.append((block, row))
                break
    for block, row in redundant:
        if row in block["rows"]:
            block["rows"].remove(row)
    if redundant:
        print(f"  🔧 transition map: removed {len(redundant)} code-added cuts another row already makes")
    if added:
        print(f"  🔧 transition map: added {added} B-roll cuts")
    if relabelled[0]:
        print(f"  🔧 transition map: pointed {relabelled[0]} rows at the B-roll that plays there")
    if shifted:
        layout = _broll_layout(realigned)
        num_col, start_col = layout["cols"]["num"], layout["cols"]["start"]
        if num_col is not None and start_col is not None:
            for brow in layout["rows"]:
                cells = brow["cells"]
                key = cells[num_col].strip() if num_col < len(cells) else ""
                if key in shifted and start_col < len(cells):
                    cells[start_col] = shifted[key]
            broll_table = ew.rewrite_broll_timestamps(_render_broll_layout(layout, layout["rows"]), script, wpm)
            print(f"  🔧 moved {len(shifted)} B-rolls out of warning-card holds and drawing windows")

    _sort_transition_blocks(blocks, script, wpm, map_out=map_out)
    text = _render_transition_blocks(transition_design, blocks)
    text = ew.rewrite_cue_timestamps(text, script, wpm)

    text, broll_table, moved = _move_out_of_warning_holds(state, text, broll_table, script, wpm)
    if moved:
        print(f"  🔧 moved {moved} transitions out of warning-card holds and drawing windows")
        blocks = _transition_blocks(text)
        if blocks:
            _sort_transition_blocks(blocks, script, wpm, map_out=map_out)
            text = _render_transition_blocks(text, blocks)
            text = ew.rewrite_cue_timestamps(text, script, wpm)

    if broll_out is not None:
        broll_out.append(broll_table)
    if isinstance(state, dict) and broll_table != realigned:
        state["broll_prompts"] = broll_table
    return text, added, moved


def _script_length_line(script: str, blueprint) -> str:
    """How long the (chapter's) spoken script is, and how many B-roll moments to plan."""
    wpm = ew.blueprint_wpm(blueprint)
    words = len(ew.spoken_text(script or "").split())
    minutes = words / wpm if wpm else 0
    return (f"SCRIPT LENGTH: about {words} spoken words ≈ {minutes:.1f} minutes at {wpm:g} wpm "
            f"→ plan about {round(minutes * 1.5)}–{round(minutes * 2)} B-roll moments "
            "(1.5–2 per minute).")


# Words that carry no visual meaning in a B-roll prompt (camera/style boilerplate).
_BROLL_STOPWORDS = frozenset("""
photorealistic cinematic shot lens lighting camera natural film grain middle eastern realistic
with that this from into over under very then than they them their there here when what which
while your have has been being will would could should about above after before between both
each more most some such only also just like make made shows show view scene image video
close wide angle light color colour tone mood style high low full frame background foreground
detail texture quality slow push dolly orbit track tracking zoom pan tilt move moving
""".split())


def _broll_visible(prompt: str) -> str:
    """The visible part of a B-roll prompt: everything before an 'Avoid'/'negative' note."""
    return re.split(r"\b(avoid|negative)\b", prompt or "", maxsplit=1, flags=re.IGNORECASE)[0]


def _broll_content_words(prompt: str) -> set:
    """Lowercase 4+ letter content words of a prompt, minus camera/style stopwords."""
    return {w for w in re.findall(r"[a-z]{4,}", _broll_visible(prompt).lower())
            if w not in _BROLL_STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def broll_engagement_problems(broll_prompts: str, overlay_guide: str = "", minutes: float = 0,
                              script: str = "", wpm: float = 135) -> list:
    """Code checks that keep B-roll eye-catching (they hold whatever model wrote it):
    motion in every video clip, no text inside generated media, enough visual events, no long
    gaps, and no stacked or near-duplicate moments.

    When `script` is given the rows are timed by where their START CUE sits in the spoken
    script (falling back to the Timestamp column), the video's length is the real spoken
    length, and the extra stacking/repetition checks run. Without it, behaviour is unchanged."""
    lines = (broll_prompts or "").splitlines()
    head = next((i for i, l in enumerate(lines) if l.strip().startswith("|") and "PROMPT" in l.upper()
                 and ("TYPE" in l.upper() or "TIMESTAMP" in l.upper())), None)
    if head is None:
        return ["The B-Roll Prompt Table is missing or has no 'AI Generation Prompt' column."]
    cols = [c.strip().upper() for c in re.split(r"(?<!\\)\|", lines[head])[1:-1]]

    def col(*names):
        return next((i for i, c in enumerate(cols) if any(n in c for n in names)), None)

    c_num, c_time, c_type = col("#"), col("TIMESTAMP", "TIME"), col("TYPE")
    c_prompt, c_notes = col("GENERATION PROMPT", "PROMPT"), col("COMPOSITION", "NOTES")
    c_cue = col("START CUE", "START", "▶")
    c_end = col("END CUE", "END", "⏹")
    wpm = ew.blueprint_wpm({"words_per_minute": wpm})
    if script:
        spoken = len(ew.spoken_text(script).split())
        minutes = spoken / wpm if spoken else 0
    norm_script = ew.normalize_ar(ew.spoken_text(script)) if script else ""
    problems, timed, prompts, rows = [], [], [], 0
    pos = 0
    for line in lines[head + 1:]:
        if not line.strip().startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) or len(cells) <= (c_prompt or 0):
            continue
        rows += 1
        num = cells[c_num] if c_num is not None else str(rows)
        prompt = cells[c_prompt] if c_prompt is not None else ""
        kind = cells[c_type] if c_type is not None else ""
        notes = cells[c_notes] if c_notes is not None and c_notes < len(cells) else ""
        start_cue = cells[c_cue] if c_cue is not None and c_cue < len(cells) else ""
        end_cue = cells[c_end] if c_end is not None and c_end < len(cells) else ""
        t = None
        if script and c_cue is not None and c_cue < len(cells):
            # Rows are in script order: a repeated cue resolves to the occurrence at/after
            # the previous row's position, so the times are on the real timeline.
            seconds = ew.cue_seconds(start_cue, script, wpm, after=pos)
            if seconds is not None:
                t = int(round(seconds))
                positions = [p for p in ew.cue_positions(start_cue, norm_script) if p >= pos]
                if positions:
                    pos = positions[0]
        if t is None and c_time is not None:
            t = _seconds(cells[c_time])
        if t is not None:
            timed.append((t, num, start_cue, end_cue))
        prompts.append((num, _broll_content_words(prompt)))
        visible = _broll_visible(prompt)
        if "VIDEO" in kind.upper() or "🎬" in kind:
            if not _CAMERA_MOVE.search(prompt):
                problems.append(f"B-roll #{num} (video) has no camera move: add one (slow push-in, dolly, orbit, "
                                "rack focus, tracking…) and a visible subject action.")
        elif not re.search(r"ken burns|push|zoom|slide|pan|move", notes + " " + prompt, re.IGNORECASE):
            problems.append(f"B-roll #{num} (still) has no Ken Burns move in Composition Notes.")
        asks_text = _TEXT_IN_IMAGE.search(visible)
        if asks_text:
            problems.append(f"B-roll #{num} asks for on-screen text/numbers inside the generated image "
                            f"({asks_text.group(0)[:40]}): AI garbles them — show shapes only and put the words "
                            "in Composition Notes as a Premiere overlay.")
    drawings = len(re.findall(r"draw-on (video )?prompt", overlay_guide or "", re.IGNORECASE))
    if minutes and rows + drawings < round(1.2 * minutes):
        problems.append(f"Only {rows} B-rolls + {drawings} whiteboard drawings for a ~{minutes:.0f}-minute video: "
                        f"plan at least {round(1.5 * minutes)} visual moments (about 1.5–2 per minute).")
    timed.sort(key=lambda row: row[0])
    times = [t for t, _, _, _ in timed]
    if times and times[0] > 20:
        problems.append(f"The first B-roll starts at {times[0] // 60}:{times[0] % 60:02d}: add one in the first 20 seconds.")
    gap = 60 if script else 90
    for (a, _, _, a_end), (b, _, b_start, _) in zip(timed, timed[1:]):
        if b - a > gap:
            problems.append(f"No B-roll between «{a_end}» and «{b_start}» "
                            f"({a // 60}:{a % 60:02d}–{b // 60}:{b % 60:02d}, ~{b - a}s): "
                            "add one in that stretch (or a whiteboard drawing).")
    if script:
        for (a, na, na_start, _), (b, nb, nb_start, _) in zip(timed, timed[1:]):
            if b - a < 8:
                problems.append(f"B-rolls #{na} and #{nb} («{na_start}» and «{nb_start}») start {b - a}s apart: "
                                "too heavy — spread them or drop one.")
        for i in range(len(prompts)):
            for j in range(i + 1, len(prompts)):
                if _jaccard(prompts[i][1], prompts[j][1]) >= 0.5:
                    problems.append(f"B-rolls #{prompts[i][0]} and #{prompts[j][0]} show nearly the same image: "
                                    "replace one with a different angle of the idea")
    return problems


def _known_transition_numbers(text: str) -> set:
    known = set()
    for block in _transition_blocks(text or ""):
        for row in block["rows"]:
            number = re.sub(r"\D", "", _row_cell(row, block["cols"], "num"))
            if number:
                known.add(number)
    return known


def _link_cells(text: str):
    """(kind, cell) for every "Linked to …" cell in markdown tables."""
    lines = (text or "").splitlines()
    header, i = None, 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("|"):
            header = None
            i += 1
            continue
        cells = _table_cells(line)
        if cells and all(_SEPARATOR_CELL.fullmatch(c) for c in cells if c):
            i += 1
            continue
        if header is None:
            header = cells
            i += 1
            continue
        for k, name in enumerate(header):
            kind = _link_column_kind(name)
            if kind and k < len(cells):
                yield kind, cells[k]
        i += 1


def _bare_link_numbers(cell: str, kind: str) -> list:
    """Bare numbers in a link cell, after explicit refs of that kind are removed."""
    pattern = _BROLL_REF if kind == "broll" else _TRANSITION_REF
    rest = pattern.sub("", cell or "")
    if _ANY_REF.search(rest):
        return []
    return [m.group(2) for m in _BARE_REF.finditer(rest)]


def broll_reference_problems(broll_prompts: str, transition_design: str = "",
                             overlay_guide: str = "") -> list:
    """Remaining cross-layer links that name a row which no longer exists: B-roll
    references in the transition/overlay/B-roll documents, transition references in
    them, and bare numbers in the matching "Linked to …" columns."""
    known_broll = {str(row.get("num", "")).strip() for row in ew.broll_rows(broll_prompts or "")}
    known_broll.discard("")
    known_transition = _known_transition_numbers(transition_design)
    problems, seen = [], set()
    documents = (transition_design, overlay_guide, broll_prompts)

    for text in documents:
        for line in (text or "").splitlines():
            for match in _BROLL_REF.finditer(line):
                number = match.group(2)
                if number in known_broll or ("broll", number) in seen:
                    continue
                seen.add(("broll", number))
                problems.append(f"Transition/overlay row references B-roll #{number}, "
                                "which does not exist in the B-roll table")
            for match in _TRANSITION_REF.finditer(line):
                number = match.group(2)
                if number in known_transition or ("transition", number) in seen:
                    continue
                seen.add(("transition", number))
                problems.append(f"Transition/overlay row references transition #{number}, "
                                "which does not exist in the transition map")

    for text in documents:
        for kind, cell in _link_cells(text):
            if kind == "broll":
                known = known_broll
            elif kind == "transition":
                known = known_transition
            else:
                continue
            for number in _bare_link_numbers(cell, kind):
                if number in known or (kind, number) in seen:
                    continue
                seen.add((kind, number))
                if kind == "broll":
                    problems.append(f"Transition/overlay row references B-roll #{number}, "
                                    "which does not exist in the B-roll table")
                else:
                    problems.append(f"Transition/overlay row references transition #{number}, "
                                    "which does not exist in the transition map")
    return problems


def _video_minutes(state: PipelineState) -> float:
    m = re.search(r"\d+(?:\.\d+)?", str(state.get("target_duration") or ""))
    if m:
        return float(m.group())
    words = len(ew.spoken_text(ew.filming_script(state.get("refined_script", ""))).split())
    return words / 140 if words else 0


def script_clock(script: str, wpm: float) -> str:
    """Code-measured clock of the script for the production judge: where every
    "=== … ===" section starts and where the spoken script ends (spoken words / wpm).
    A real judge guessed the end at 11:35 for a 16:23 script and failed every layer
    for a "drift" that did not exist."""
    if not script or not wpm:
        return ""
    marks, words = [], 0
    for title, text in chapters.split_by_chapters(chapters.normalize_section_headers(script)):
        if title:
            marks.append(f"{_fmt_seconds(words / wpm * 60.0)} «{title[:50]}»")
        words += len(ew.spoken_text(text).split())
    return (f"spoken words = {words}, {wpm:g} wpm → the script ENDS at {_fmt_seconds(words / wpm * 60.0)}"
            + (f"; sections start at: {'; '.join(marks)}" if marks else ""))


COLLISION_SECONDS = 0.75


def transition_collisions(transition_design: str, script: str, wpm: float) -> list:
    """Problem lines for 2+ transition rows on the same second where a designed (not
    code-added) row is involved: the editor can cut only once (a run had three on «طيب
    إزاي نقيس صح» at 5:10)."""
    blocks = _transition_blocks(transition_design or "")
    if not blocks:
        return []
    rows = [(block, row) for block in blocks for row in block["rows"]]
    timed = sorted(((t, block, row) for (block, row), t in zip(rows, _transition_row_times(blocks, script, wpm))
                    if t is not None), key=lambda item: item[0])
    problems, i = [], 0
    while i < len(timed):
        j = i
        while j + 1 < len(timed) and timed[j + 1][0] - timed[i][0] <= COLLISION_SECONDS:
            j += 1
        group = timed[i:j + 1]
        designed = [g for g in group if "code-added" not in _row_cell(g[2], g[1]["cols"], "rationale").lower()]
        if len(group) > 1 and designed:
            names = "; ".join(f"T#{re.sub(r'[^0-9]', '', _row_cell(r, b['cols'], 'num'))} "
                              f"{_row_cell(r, b['cols'], 'from_to')[:50]}" for _, b, r in group)
            problems.append(f"{len(group)} transitions on the same moment ({_fmt_seconds(group[0][0])}): {names} — "
                            "keep one, chain the rest over real time.")
        i = j + 1
    return problems


def graphic_drawing_overlaps(transition_design: str, overlay_guide: str, script: str, wpm: float) -> list:
    """Problem lines for a transition-map graphic/animation that plays inside a whiteboard
    drawing's window: two full-screen visuals booked for the same seconds (a run held a 3D
    artery animation 4:27–5:10 across a whiteboard drawing at 4:36–4:47)."""
    drawings = _drawing_windows(overlay_guide or "", script, wpm)
    if not drawings:
        return []
    blocks = _transition_blocks(transition_design or "")
    rows = [(block, row) for block in blocks for row in block["rows"]]
    timed = sorted(((t, _row_cell(row, block["cols"], "from_to")) for (block, row), t in
                    zip(rows, _transition_row_times(blocks, script, wpm)) if t is not None), key=lambda x: x[0])
    problems = []
    for k, (t, from_to) in enumerate(timed):
        target = re.split(r"\s*(?:→|->)\s*", from_to, maxsplit=1)[-1]
        # The cut INTO the drawing itself is right; a non-drawing graphic on screen is not.
        if not _GRAPHIC_TO_RE.search(target) or re.search(r"(?i)b-?\s?roll|drawing|whiteboard|sketch", target):
            continue
        until = next((u for u, _ in timed[k + 1:] if u > t + 0.5), t + GRAPHIC_AFTER_SECONDS)
        for start, end in drawings:
            if abs(t - start) <= 2.0:
                continue                        # the designer's own name for this drawing
            if t < end - 1.0 and until > start + 1.0:
                problems.append(f"«{target[:40]}» is on screen {_fmt_seconds(t)}–{_fmt_seconds(until)}, across the "
                                f"whiteboard drawing {_fmt_seconds(start)}–{_fmt_seconds(end)}: two full-screen "
                                "visuals at once — keep the drawing and end or drop the graphic before it.")
    return problems


def production_quality_critique(state: PipelineState) -> dict:
    system_prompt = load_prompt("production_quality_critique")
    code_problems = broll_engagement_problems(state.get("broll_prompts", ""), state.get("text_animation_overlay", ""),
                                              _video_minutes(state), script=state.get("refined_script", ""),
                                              wpm=ew.blueprint_wpm(state.get("blueprint")))
    code_problems += transition_collisions(state.get("transition_design", ""), state.get("refined_script", ""),
                                           ew.blueprint_wpm(state.get("blueprint")))
    code_problems += graphic_drawing_overlaps(state.get("transition_design", ""),
                                              state.get("text_animation_overlay", ""),
                                              state.get("refined_script", ""), ew.blueprint_wpm(state.get("blueprint")))
    code_problems += broll_reference_problems(state.get("broll_prompts", ""),
                                              state.get("transition_design", ""),
                                              state.get("text_animation_overlay", ""))
    code_note = ("\n\n### Code checks — B-roll engagement (must fix)\n" + "\n".join(f"- {p}" for p in code_problems)
                 if code_problems else "")

    clock = script_clock(state.get("refined_script", ""), ew.blueprint_wpm(state.get("blueprint")))

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
{("SCRIPT CLOCK (measured by code — exact; use these times, do not estimate the script's timing): " + clock) if clock else ""}
{("CODE CHECKS (measured by code — each one is a CRITICAL issue to list in your report):" + code_note) if code_problems else ""}

Evaluate all three layers individually AND as an integrated system. Score each criterion. Output ONLY the JSON object."""

    try:
        data, response = call_llm_json(system_prompt, user_prompt, temperature=0.5, max_tokens=6000,
                                       required=[("production_grade", "grade")])
    except UnreadableAnswer as e:
        data, response = None, e.raw
    text = response.strip()
    try:
        if data is None:
            raise ValueError("unreadable JSON answer")

        grade = str(pick(data, "production_grade", "grade", default="")).strip().upper()
        
        # A score the critic left out is unknown, not 0: it doesn't fail a gate on its own.
        names = {
            "transition_appropriateness": ("transition_appropriateness_score",),
            "transition_restraint": ("transition_restraint_score",),
            "text_animation_clarity": ("text_animation_clarity_score", "icon_clarity_score"),
            "animation_timing": ("animation_timing_score",),
            "broll_relevance": ("broll_relevance_score",),
            "broll_medical_accuracy": ("broll_medical_accuracy_score",),
            "visual_integration": ("visual_integration_score",),
            "density_balance": ("density_balance_score",),
            "platform_fit": ("platform_fit_score",),
            "organic_feel": ("organic_feel_score",),
            "visual_engagement": ("visual_engagement_score",),
        }
        scores = {k: pick(data, *v, kind=int) for k, v in names.items()}
        known = {k: v for k, v in scores.items() if v is not None}

        # Apply hard gates
        gates_ok = (all(v >= 7 for v in known.values())
                    and all(known.get(k, 10) >= 8 for k in
                            ("visual_integration", "density_balance", "organic_feel", "visual_engagement")))
        if not gates_ok or code_problems:
            grade = "NEEDS_REVISION"
        if grade not in ["PASS"]:
            grade = "NEEDS_REVISION"

        report = pick(data, "production_critique_report", "critique_report", "report", default="", kind=str) or text
        return {
            "production_grade": grade,
            "production_critique_output": report + code_note,
            "production_revision_count": state.get("production_revision_count", 0) + 1,
        }
    except Exception:
        return {
            "production_grade": "NEEDS_REVISION",
            "production_critique_output": response + code_note,
            "production_revision_count": state.get("production_revision_count", 0) + 1,
        }
