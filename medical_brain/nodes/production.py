"""Post-production layers: transitions, text/overlay animations, B-roll prompts, production critique.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data.

Long videos (a blueprint with >=2 chapters) run the three design steps chapter by
chapter, join the results under "=== chapter title ===" separators and renumber the
tables globally; a one-chapter blueprint keeps the original single-call behaviour."""

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

    out_lines = []
    for index, (title, prefix, suffix, header, separator) in enumerate(pieces):
        out_lines.append(f"=== {title} ===")
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

Read through this chapter's script section by section, noting every scene change inside it, and design a transition for each, following the principles above."""
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

    outputs, titles, texts, _ = _run_per_chapter(state, "transition_design", spans, workers, generate)
    joined = _join_chapter_outputs(titles, texts)
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


def transition_designer(state: PipelineState) -> dict:
    system_prompt = load_prompt("transition_designer")
    blueprint = state.get("blueprint") or {}
    if chapters.chapter_mode_on(blueprint):
        return _transition_chapter_mode(state, system_prompt, blueprint)
    return _transition_single_call(state, system_prompt)


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

Read this chapter's script and transition map together. Classify every element as one of the 5 primary types: TOP-RIGHT POPUP, TEXT OVERLAY TITLE, WARNING/ALERT BOX, QUOTE BOX, or KINETIC TEXT. For every English technical term in the script, generate a TOP-RIGHT POPUP with its Arabic translation. For every section/chapter transition, generate a TEXT OVERLAY TITLE. For medical disclaimers and warnings, use WARNING/ALERT BOX. For the most impactful sentences, use QUOTE BOX. For statistics and key terms, use KINETIC TEXT. Include START CUE and END CUE (exact first/last Arabic words from the script that trigger each element). For complex medical mechanisms that are hard to follow verbally, consider whether a drawing animation would genuinely help comprehension."""
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

    outputs, titles, texts, _ = _run_per_chapter(state, "text_animation_overlay", spans, workers, generate)
    joined = _join_chapter_outputs(titles, texts)
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
    return {"text_animation_overlay": response}


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
        outputs[key] = finalize_broll_table(state, outputs.get(key, ""), after=after)
    texts = [outputs.get(keys[i], "") for i in range(len(spans))]
    joined, mapping = _join_broll(titles, texts, keys)
    joined = finalize_broll_table(state, joined)
    return {"broll_prompts": joined,
            "broll_by_chapter": mapping,
            "production_chapter_outputs": _store_step(state, "broll_prompts", outputs)}


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
    return {"broll_prompts": finalize_broll_table(state, response)}


def broll_prompt_generator(state: PipelineState) -> dict:
    system_prompt = load_prompt("broll_prompt_generator")
    blueprint = state.get("blueprint") or {}
    if chapters.chapter_mode_on(blueprint):
        return _broll_chapter_mode(state, system_prompt, blueprint)
    return _broll_single_call(state, system_prompt)


# Words that name a camera move — a video B-roll without one tends to look like dead stock.
_CAMERA_MOVE = re.compile(
    r"\b(?:push[- ]?in|pull[- ]?(back|out)|dolly|parallax|orbit|track(ing)?\b|rack[- ]focus|pan(s|ning)?\b|tilt|"
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


def finalize_broll_table(state: PipelineState, table: str, after: int = 0) -> str:
    """Code repairs the B-roll step's own table before it is stored: a camera move for
    every video row that lacks one, then real M:SS timestamps from the START CUES (see
    workbook.rewrite_broll_timestamps) so revision passes and the critique see the real
    timeline instead of the model's invented timestamps. `after` is the word position the
    table's first row starts at (0 for a whole-script table, the chapter's start word
    position for a single chapter's table)."""
    table, fixed = auto_repair_camera_moves(table)
    if fixed:
        print(f"  🔧 added a camera move to {fixed} video B-rolls")
    script = state.get("refined_script", "")
    if table and script:
        table = ew.rewrite_broll_timestamps(table, script, ew.blueprint_wpm(state.get("blueprint")), after=after)
    return table


def _seconds(stamp: str):
    m = re.search(r"(\d{1,2}):(\d{2})", stamp or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


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


def _video_minutes(state: PipelineState) -> float:
    m = re.search(r"\d+(?:\.\d+)?", str(state.get("target_duration") or ""))
    if m:
        return float(m.group())
    words = len(ew.spoken_text(ew.filming_script(state.get("refined_script", ""))).split())
    return words / 140 if words else 0


def production_quality_critique(state: PipelineState) -> dict:
    system_prompt = load_prompt("production_quality_critique")
    code_problems = broll_engagement_problems(state.get("broll_prompts", ""), state.get("text_animation_overlay", ""),
                                              _video_minutes(state), script=state.get("refined_script", ""),
                                              wpm=ew.blueprint_wpm(state.get("blueprint")))
    code_note = ("\n\n### Code checks — B-roll engagement (must fix)\n" + "\n".join(f"- {p}" for p in code_problems)
                 if code_problems else "")

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
