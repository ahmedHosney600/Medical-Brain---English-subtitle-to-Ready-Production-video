"""Final production document: assembles the package in two LLM calls, with code audits.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import re

from ..llm import call_llm
from ..prompts import load_prompt
from ..state import PipelineState
from ..exports import workbook as ew
from . import chapters


MAX_FINAL_PACKAGE_RETRIES = 2


def _repair_cues(text: str, script: str, part: str) -> str:
    """Models often paraphrase cues; swap each bad cue for the script's own words
    before the audit, so a retry is only spent on cues code can't place."""
    if not script:
        return text
    text, fixed, examples = ew.repair_cues(text, script)
    if fixed:
        print(f"  [final_package {part}] 🔧 fixed {fixed} cues to the script's exact words, e.g. "
              + "; ".join(examples[:3]))
    return text


# Checks on the script's own wording. When code inserted the verified script, a
# retry can't change it (and the quality loop already gated it): warn only.
_SCRIPT_WORDING_ISSUES = ("SENSATIONALIST", "VULGAR", "PRODUCTION SCRIPT TOO SHORT",
                          "PRODUCTION SCRIPT TOO LONG", "MISSING PRODUCTION SCRIPT")


def _audit_part1(text: str, script_is_verified: bool = False, blueprint=None) -> list:
    issues = _audit_part1_all(text, blueprint)
    if not script_is_verified:
        return issues
    for issue in issues:
        if issue.startswith(_SCRIPT_WORDING_ISSUES):
            print(f"  [final_package Part1] ⚠️ note on the verified script (not retried): {issue[:200]}")
    return [i for i in issues if not i.startswith(_SCRIPT_WORDING_ISSUES)]


def _audit_part1_all(text: str, blueprint=None) -> list:
    """Audit Part 1 (Script + Packaging) for quality issues.

    NOTE: title/thumbnail quality (count, completeness, anti-cliché, honesty)
    is no longer checked here — that's the job of packaging_honesty_ctr_auditor,
    which gates the packaging BEFORE it ever reaches this assembly step. This
    audit now only needs to confirm those approved sections were copied through
    and that the production script itself is clean.
    """
    issues = []

    # Approved packaging sections must actually be present (copy-through check,
    # not a quality check — quality was already gated upstream).
    if "\U0001f916 AI Image Generation Prompt" not in text:
        issues.append(
            "MISSING APPROVED THUMBNAIL CONCEPTS: The approved thumbnail concepts (with AI Image Generation Prompts) "
            "were not copied through from the packaging loop output. Copy them through in full, unmodified."
        )

    # Production script must be present
    if "\u0646\u0628\u0631\u0629" not in text and "HOOK" not in text:
        issues.append("MISSING PRODUCTION SCRIPT: The full teleprompter-ready script is absent.")

    # Extract only the production script content to check forbidden terms.
    # Title/thumbnail wording is no longer generated here (it's copied through
    # already-approved packaging output), so this is now purely a safety net
    # on the SPOKEN script — the same content self_critique already checked,
    # re-verified here in case anything drifted during final assembly.
    content_lines = []
    in_script = False
    for line in text.splitlines():
        line_strip = line.strip()
        if "### 🎬 PRODUCTION SCRIPT" in line or "### PRODUCTION SCRIPT" in line:
            in_script = True
            continue
        elif in_script and line_strip.startswith("###"):
            in_script = False
        
        if in_script:
            content_lines.append(line)

    # Filter out compliance explanations, disclaimers, or echoed rules
    clean_lines = [
        l for l in content_lines 
        if not any(k in l.lower() for k in ["free of", "strictly forbidden", "forbidden", "title rules", "rules:", "قواعد", "خالي من", "تنبيه"])
    ]
    target_dialogue = "\n".join(clean_lines) if clean_lines else text

    # Check for forbidden body-antagonism / melodrama clichés in the script
    forbidden_terms = ["بيخونك", "يخونك", "غدر", "خيانة", "طعنة", "يخذله", "بيطعنك"]
    found_forbidden = [t for t in forbidden_terms if t in target_dialogue]
    if found_forbidden:
        issues.append(
            f"SENSATIONALIST BODY-ANTAGONISM DETECTED: Found forbidden melodrama/betrayal term(s): {found_forbidden}. "
            "Never frame the human body or organs as backstabbers or traitors. "
            "Replace with grounded scientific paradoxes, surprising clinical facts, or myth-busting."
        )

    # Check for forbidden mechanic-shop jargon or low-brow street slang
    slang_forbidden = [
        "كلبشت", "بتكلبش", "تكلبش", "قفشت", "موضوع ناشف", "طبي ناشف", "كلام ناشف",
        "بتغلس", "بيتجنن", "تتجنن", "مابتخرفش", "بتخرف", "حتة لحمة", "وجع رخم", "قرصت عليها",
        "فيوزات", "فيوز", "تجنزر", "يجنزر", "يصدي", "تصدي", "تفرك"
    ]
    found_slang = [s for s in slang_forbidden if s in target_dialogue]
    if found_slang:
        issues.append(
            f"VULGAR / STREET SLANG DETECTED: Found inappropriate street slang or mechanic term(s): {found_slang}. "
            "The presenter is Dr. Ahmed Hosney — use Educated, Moderate Egyptian Arabic (العامية المصرية المثقفة البيضاء المعتدلة). "
            "Do NOT use low-brow street slang like 'كلبشت' or 'موضوع ناشف' or mechanic jargon like 'فيوزات'. "
            "Replace with clean, natural phrasing (e.g., 'شَدّت فجأة', 'قفلت', 'انقبضت ومبتفكش', 'موضوع معقد وممل', 'زرار النور علّق', 'جرس الإنذار شغال')."
        )

    # Check retention architecture table desync with normalized Arabic matching
    try:
        script_match = re.search(r"###\s*🎬?\s*PRODUCTION SCRIPT.*?\n(.*?)(\n###\s*WHAT CHANGED|\Z)", text, re.DOTALL)
        if script_match and "### RETENTION ARCHITECTURE" in text:
            script_body = script_match.group(1)
            
            def _normalize_ar(s: str) -> str:
                s = re.sub(r"[أإآ]", "ا", s)
                s = re.sub(r"ة", "ه", s)
                s = re.sub(r"ى", "ي", s)
                return re.sub(r"\s+", " ", s).strip()

            clean_script = re.sub(r"\[.*?\]", " ", script_body)
            clean_script = re.sub(r"[\.…\"'“”«»\(\)\[\]،؛؟!:ـ\-\n]", " ", clean_script)
            normalized_script = _normalize_ar(clean_script)

            ret_block = text.split("### RETENTION ARCHITECTURE")[1].split("###")[0]
            missing_cues = []
            for line in ret_block.split("\n"):
                if line.startswith("|") and not line.startswith("|---|") and "Timestamp" not in line and "Element" not in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4:
                        elem, cue = parts[1], parts[3]
                        clean_cue = re.sub(r"[\.…\"'“”«»\(\)\[\]،؛؟!:ـ\-]", " ", cue)
                        norm_cue = _normalize_ar(clean_cue)
                        words = norm_cue.split()
                        
                        found = False
                        if len(words) >= 2:
                            two_word = " ".join(words[:2])
                            three_word = " ".join(words[:3]) if len(words) >= 3 else two_word
                            if two_word in normalized_script or three_word in normalized_script:
                                found = True
                            else:
                                for i in range(len(words) - 1):
                                    if " ".join(words[i:i+2]) in normalized_script:
                                        found = True
                                        break
                        elif len(words) == 1:
                            if words[0] in normalized_script:
                                found = True
                                
                        if not found and words:
                            missing_cues.append(f"{elem}: '{cue}'")
            if missing_cues:
                issues.append(
                    f"RETENTION ARCHITECTURE DESYNC: The following lines in the Retention table do not appear in the Production Script text: {missing_cues}. "
                    "Every hook, open loop reference, disclaimer, and loop resolution in the Retention table MUST be present as actual spoken dialogue in the script."
                )
    except Exception:
        pass

    # Check spoken script length against target duration (blueprint-aware in chapter mode)
    try:
        script_match = re.search(r"###\s*🎬?\s*PRODUCTION SCRIPT.*?\n(.*?)(\n###\s*WHAT CHANGED|\Z)", text, re.DOTALL)
        if script_match:
            script_body = script_match.group(1)
            cleaned = re.sub(r"\[.*?\]", "", script_body)
            cleaned = re.sub(r"\*\*\(.*?\)\*\*", "", cleaned)
            cleaned = re.sub(r"===.*?===", "", cleaned)
            cleaned = re.sub(r"\*\*\[.*?\]\*\*", "", cleaned)
            spoken_words = len(cleaned.split())

            bp = blueprint if isinstance(blueprint, dict) else {}
            if chapters.chapter_mode_on(bp):
                try:
                    minutes = float(bp.get("target_minutes", 0) or 0)
                except (TypeError, ValueError):
                    minutes = 0
                try:
                    wpm = float(bp.get("words_per_minute", 135) or 135)
                except (TypeError, ValueError):
                    wpm = 135
                target_words = int(minutes * wpm) if minutes > 0 else 0
                if not target_words:
                    try:
                        target_words = sum(int(float(c.get("words", 0) or 0))
                                           for c in (bp.get("chapters") or []) if isinstance(c, dict))
                    except (TypeError, ValueError):
                        target_words = 0
                if target_words:
                    low, high = int(target_words * 0.85), int(target_words * 1.15)
                    if spoken_words < low:
                        issues.append(
                            f"PRODUCTION SCRIPT TOO SHORT: Spoken dialogue has only {spoken_words} words, but the "
                            f"blueprint targets ~{target_words} words (±15% = {low}–{high}). Expand the biological "
                            "explanations, physiological mechanisms, and clinical case study details."
                        )
                    elif spoken_words > high:
                        issues.append(
                            f"PRODUCTION SCRIPT TOO LONG: Spoken dialogue has {spoken_words} words, but the "
                            f"blueprint targets ~{target_words} words (±15% = {low}–{high}). Tighten the "
                            "explanations so each chapter lands near its word budget."
                        )
            else:
                duration_match = re.search(r"Target Duration\s*\|\s*(\d+)", text, re.IGNORECASE)
                target_min = int(duration_match.group(1)) if duration_match else 0
                if target_min >= 8 and spoken_words < 1000:
                    issues.append(
                        f"PRODUCTION SCRIPT TOO SHORT: Spoken dialogue has only {spoken_words} words (~{spoken_words // 140} minutes), but the target duration is {target_min} minutes (needs ~1,150–1,350 words). "
                        "You must expand the biological explanations, physiological mechanisms, and clinical case study details."
                    )
    except Exception:
        pass

    # VIDEO SECTIONS = YouTube chapters: titles must be usable as chapters, and
    # the start/end sentences must really be in the script (the editor searches for them).
    sections = ew.parse_video_sections(text)
    issues.extend(f"CHAPTER TITLE: {p}" for p in ew.check_chapter_titles(sections))
    cue_problems = ew.check_cue_rows(
        [(f"VIDEO SECTIONS row {s['num']}", s["start"], s["end"]) for s in sections],
        ew.production_script(text), min_words=1,
    )
    if cue_problems:
        issues.append(
            "VIDEO SECTIONS SENTENCES NOT EXACT: " + "; ".join(cue_problems[:12]) +
            ". Copy every Start/End Sentence character-for-character from the spoken words of the PRODUCTION SCRIPT."
        )

    return issues


def whiteboard_problems(detail: str) -> list:
    """A DRAWING ANIM row must describe a white whiteboard, and its Premiere labels
    must be placed by cue words (times shift after filming)."""
    problems = []
    d = detail.lower()
    if re.search(r"\b(black|dark)\s+(back\s?ground|board|canvas)|\bblackboard\b", d):
        problems.append("says black/dark background; the drawing must be black marker on a plain WHITE whiteboard")
    elif "white" not in d:
        problems.append("the IMAGE PROMPT must ask for a plain white whiteboard and the DRAW-ON PROMPT for a white background")
    labels = re.split(r"labels in premiere\s*:", d, maxsplit=1)
    if len(labels) == 2:
        labels = labels[1].split("|")[0]
        if re.search(r"\b\d{1,2}:\d{2}\b", labels):
            problems.append("LABELS IN PREMIERE give times (M:SS); give the cue words where each label appears instead")
    return problems


def _audit_part2(text: str, script: str = "") -> list:
    """Audit Part 2 (Post-Production) for quality issues. `script` is the
    production script the cues must come from."""
    issues = []

    # Storyboard must be present and have enough rows
    if "INTEGRATED PRODUCTION STORYBOARD" not in text:
        issues.append("MISSING: The INTEGRATED PRODUCTION STORYBOARD section is absent entirely.")
    else:
        data_rows = ew.parse_storyboard(text)
        if len(data_rows) < 15:
            issues.append(
                f"THIN STORYBOARD: Only {len(data_rows)} rows found. "
                "The storyboard must cover the FULL video with 25-40 rows, "
                "one per atomic post-production event."
            )

    # B-roll shortcuts — the #1 known issue
    broll_shortcuts = [
        "[full prompt in sb]",
        "[see sb#",
        "[see prompt #",
        "[same as above]",
        "[full prompt as above]",
        "see storyboard",
        "as in storyboard",
        "[full prompt]",
        "... [full",
    ]
    found_shortcuts = []
    text_lower = text.lower()
    for s in broll_shortcuts:
        if s in text_lower:
            found_shortcuts.append(s)
    if found_shortcuts:
        issues.append(
            f"SHORTCUT CROSS-REFERENCES DETECTED in B-roll prompts: {found_shortcuts}. "
            "EVERY B-ROLL row in the storyboard AND EVERY row in the B-Roll detail table "
            "MUST contain the full AI generation prompt written out in full — "
            "no shortcuts, no cross-references, no ellipses."
        )

    # B-Roll detail section must exist
    if "AI B-ROLL GENERATION PROMPTS" not in text:
        issues.append("MISSING: The AI B-ROLL GENERATION PROMPTS detail section is absent.")

    rows = ew.parse_storyboard(text)

    # Cues: the editor finds every event by its words, so they must be exact.
    if script and rows:
        cue_problems = ew.check_cue_rows(
            [(f"SB#{r['num']} ({r['layer']})", r["start"], r["end"]) for r in rows], script
        )
        if cue_problems:
            issues.append(
                f"STORYBOARD CUES NOT EXACT ({len(cue_problems)}): " + "; ".join(cue_problems[:20]) +
                ". Every START/END CUE must be 4-8 consecutive spoken words copied exactly from the PRODUCTION SCRIPT and unique in it."
            )

    # Whiteboard drawings must be producible in Google Flow.
    for r in rows:
        if "DRAWING" in r["layer"]:
            d = r["detail"].upper()
            if "IMAGE PROMPT" not in d or "DRAW-ON PROMPT" not in d:
                issues.append(
                    f"DRAWING ANIM SB#{r['num']} is missing its IMAGE PROMPT and/or DRAW-ON PROMPT "
                    "(see the DRAWING ANIM detail format)."
                )
            issues.extend(f"DRAWING ANIM SB#{r['num']}: {p}" for p in whiteboard_problems(r["detail"]))

    return issues


# --------------------------------------------------------------------------
# Part 2 in chapter mode: one storyboard per chapter, stitched by code
# --------------------------------------------------------------------------
_STORYBOARD_HEADER = ("| # | Act | ⏱️ Timecode | ▶️ START CUE (exact first Arabic words from script) | "
                      "⏹️ END CUE (exact last Arabic words) | Layer | Detail |")
_STORYBOARD_SEPARATOR = "|---|---|---|---|---|---|---|"


def _storyboard_rows(answer: str) -> list:
    """Storyboard rows of one chapter's answer, tolerant of a missing heading."""
    rows = ew.parse_storyboard(answer or "")
    if rows:
        return rows
    out = []
    for cells in ew.table_rows(answer or ""):
        if len(cells) < 7:
            continue
        out.append({"num": cells[0], "act": cells[1], "time": cells[2], "start": cells[3],
                    "end": cells[4], "layer": cells[5].upper().strip("* "),
                    "detail": " | ".join(cells[6:]).strip()})
    return out


def _stitch_storyboard(spans: list, rows_by_index: dict) -> tuple:
    """One heading's worth of rows, in chapter order, "#" renumbered 1..N globally.
    Returns (table_markdown, {global_number: chapter_index})."""
    lines = [_STORYBOARD_HEADER, _STORYBOARD_SEPARATOR]
    number_to_chapter, number = {}, 0
    for i, _ in enumerate(spans):
        for row in rows_by_index.get(i) or []:
            number += 1
            number_to_chapter[number] = i
            lines.append(f"| {number} | {row['act']} | {row['time']} | {row['start']} | "
                         f"{row['end']} | {row['layer']} | {row['detail']} |")
    return "\n".join(lines), number_to_chapter


def _attributed_chapters(issues: list, number_to_chapter: dict, rows_by_index: dict, n: int) -> list:
    """Chapters a retry can fix: those named by SB# in an issue, plus any that came
    back with no rows at all. Empty ⇒ the caller re-runs every chapter."""
    flagged = set()
    for issue in issues or []:
        for match in re.finditer(r"SB#(\d+)", str(issue)):
            chapter = number_to_chapter.get(int(match.group(1)))
            if chapter is not None:
                flagged.add(chapter)
    for i in range(n):
        if not rows_by_index.get(i):
            flagged.add(i)
    return sorted(flagged)


def _expected_storyboard_rows(span: dict) -> int:
    minutes = max(0.5, (span["end_s"] - span["start_s"]) / 60.0)
    return max(6, int(round(minutes * 4)))


def _broll_items_block(rows: list, numbers: set) -> str:
    selected = [r for r in rows if r.get("num", "").isdigit() and int(r["num"]) in numbers]
    if not selected:
        return "(no B-roll items assigned to this chapter)"
    lines = ["| # | Timestamp | Type | AI Generation Prompt |", "|---|---|---|---|"]
    for row in selected:
        prompt = ew.plain_prompt(row.get("prompt", "")).replace("|", "/")
        lines.append(f"| {row['num']} | {row.get('time', '')} | {row.get('kind', '')} | {prompt} |")
    return "\n".join(lines)


def _part2_skeleton(storyboard_table: str) -> str:
    return f"""### 🎬 INTEGRATED PRODUCTION STORYBOARD

{storyboard_table}

---

### 📐 TRANSITION MAP (Detail Reference)
[[APPROVED TRANSITION MAP — inserted by code]]

---

### 🎨 TEXT ANIMATION & OVERLAY GUIDE (Detail Reference)
[[APPROVED TEXT ANIMATION & OVERLAY GUIDE — inserted by code]]

---

### 🖼️ AI B-ROLL GENERATION PROMPTS (Detail Reference)
[[APPROVED B-ROLL TABLE — inserted by code]]

---

### INTEGRATION DATA
```
Section covered by chapter mode: the storyboard above.
```
"""


def _part2_chapter_mode(state: PipelineState, system_prompt_2: str, filmed_script: str,
                        approved_parts: list, approved_broll: str):
    """One storyboard call per chapter, stitched into a single Part 2 document."""
    blueprint = state.get("blueprint") or {}
    spans = chapters.chapter_spans(filmed_script, blueprint)
    if len(spans) < 2:
        return None

    system_prompt_2 = system_prompt_2 + "\n\n" + load_prompt("production_chapter_mode")
    workers = int(state.get("chapter_workers", 4) or 4)
    broll_by_chapter = state.get("broll_by_chapter") or {}
    all_broll_rows = ew.broll_rows(state.get("broll_prompts", ""))

    def build_prompt(i: int, correction: str) -> tuple:
        span = spans[i]
        transitions = chapters.chapter_section(state.get("transition_design", ""), span["title"])
        overlays = chapters.chapter_section(state.get("text_animation_overlay", ""), span["title"])
        numbers = set(broll_by_chapter.get(span["id"], []) or [])
        broll_block = _broll_items_block(all_broll_rows, numbers)
        expected = _expected_storyboard_rows(span)
        start, end = chapters.fmt_time(span["start_s"]), chapters.fmt_time(span["end_s"])
        prompt = f"""Compile the storyboard for ONE CHAPTER of the Part 2 YouTube Production Deliverable (Post-Production Guide).

THIS CHAPTER: id: {span['id']} | title: {span['title']} | chapter {i + 1} of {len(spans)} | from {start} to {end}
CHAPTER TIME SPAN: {start}–{end}

PRODUCTION SCRIPT (this chapter — copy START CUE / END CUE words exactly from here; spoken words only):
{span['text']}

TRANSITION DESIGN MAP for this chapter (put every transition in the storyboard; the map itself is inserted by code):
{transitions or "(none provided)"}

TEXT ANIMATION & OVERLAY GUIDE for this chapter (put every element and drawing in the storyboard; the guide itself is inserted by code):
{overlays or "(none provided)"}

AI B-ROLL GENERATION PROMPTS for this chapter (each storyboard B-ROLL row names its B-roll #; code copies the approved prompt in):
{broll_block}

PRODUCTION QUALITY CRITIQUE: {state.get("production_critique_output", "")}
PRODUCTION GRADE: {state.get("production_grade", "")}
MEDICAL TOPIC: {state.get("medical_topic", "")}
PLATFORM: {state.get("target_platform", "")}

RULES:
1. Storyboard: about {expected} rows covering ONLY this chapter's span ({start}–{end}), at the usual density per minute.
2. START CUE / END CUE: 4-8 consecutive spoken words copied exactly from this chapter's Production Script, unique in the whole script (add neighbouring words if they repeat), never stage directions or placeholders.
3. B-ROLL storyboard rows: write "B-roll #N" using the GLOBAL numbers given above — code copies the full approved prompt into the row.
4. Return ONLY this chapter's "### 🎬 INTEGRATED PRODUCTION STORYBOARD" heading and its rows; do NOT write the Transition Map, Text Animation & Overlay Guide, B-roll table or Integration Data sections (code assembles them)."""
        if correction:
            prompt += f"\n\n⚠️ CORRECTION REQUIRED:\n{correction}"
        return prompt, expected

    rows_by_index, issues_2 = {}, []
    best = None
    correction = ""
    for attempt in range(MAX_FINAL_PACKAGE_RETRIES + 1):
        if attempt == 0:
            todo = list(range(len(spans)))
        else:
            todo = _attributed_chapters(issues_2, number_to_chapter, rows_by_index, len(spans))
            if not todo:
                todo = list(range(len(spans)))

        def run_chapter(i, note=correction):
            prompt, expected = build_prompt(i, note)
            answer = call_llm(system_prompt_2, prompt, temperature=0.3,
                              max_tokens=4000 + 150 * expected)
            return i, _storyboard_rows(answer)

        for i, rows in chapters.parallel_map(run_chapter, todo, workers):
            rows_by_index[i] = rows

        table, number_to_chapter = _stitch_storyboard(spans, rows_by_index)
        part2 = _part2_skeleton(table)
        for key, body in approved_parts:
            if body:
                part2 = ew.replace_section(part2, key, body, before="INTEGRATION DATA")
        part2 = ew.fill_broll_rows(part2, approved_broll)
        part2 = _repair_cues(part2, filmed_script, "Part2")
        issues_2 = _audit_part2(part2, filmed_script)
        if best is None or len(issues_2) < best[0]:
            best = (len(issues_2), part2, issues_2)
        if not issues_2:
            print(f"  [final_package Part2] OK on attempt {attempt + 1}")
            break
        print(f"  [final_package Part2] Retry {attempt + 1}: {issues_2}")
        correction = "The previous output had these problems - fix ALL of them:\n"
        for i, issue in enumerate(issues_2, 1):
            correction += f"  {i}. {issue}\n"
        correction += "Regenerate this chapter's storyboard rows fixing every issue above (keep cues exact)."
    return best[1], best


def _part2_single_call(system_prompt_2: str, base_user_prompt_2: str, approved_parts: list,
                       approved_broll: str, filmed_script: str) -> tuple:
    part2 = ""
    best_2 = None
    correction_note_2 = ""
    for attempt in range(MAX_FINAL_PACKAGE_RETRIES + 1):
        user_prompt_2 = base_user_prompt_2
        if correction_note_2:
            user_prompt_2 += f"\n\n⚠️ CORRECTION REQUIRED (attempt {attempt + 1}):\n{correction_note_2}"
        part2 = call_llm(system_prompt_2, user_prompt_2, temperature=0.3, max_tokens=12000)
        for key, body in approved_parts:
            if body:
                part2 = ew.replace_section(part2, key, body, before="INTEGRATION DATA")
        part2 = ew.fill_broll_rows(part2, approved_broll)
        part2 = _repair_cues(part2, filmed_script, "Part2")
        issues_2 = _audit_part2(part2, filmed_script)
        if best_2 is None or len(issues_2) < best_2[0]:
            best_2 = (len(issues_2), part2, issues_2)
        if not issues_2:
            print(f"  [final_package Part2] OK on attempt {attempt + 1}")
            break
        print(f"  [final_package Part2] Retry {attempt + 1}: {issues_2}")
        correction_note_2 = "The previous output had these problems - fix ALL of them:\n"
        for i, issue in enumerate(issues_2, 1):
            correction_note_2 += f"  {i}. {issue}\n"
        correction_note_2 += "Regenerate the COMPLETE output fixing every issue above."
    return part2, best_2


def final_script_package(state: PipelineState) -> dict:

    system_prompt_1 = load_prompt("final_script_package_part1")
    # The script that passed the medical checks is what gets filmed: code puts it
    # into the document word for word, so the assembly model can't rewrite it.
    verified_script = ew.filming_script(state.get("refined_script", ""))

    base_user_prompt_1 = f"""Compile Part 1 of the YouTube Production Deliverable.

FINAL SCRIPT (definitive, fact-checked version — inserted into the PRODUCTION SCRIPT section by code; quote it exactly):
{verified_script}

HOOK VARIATIONS:
{state.get("hook", "")}

CTA VERSIONS:
{state.get("cta_output", "")}

APPROVED TITLE OPTIONS (from the packaging loop — copy through, do not rewrite):
{state.get("title_options", "")}

APPROVED THUMBNAIL CONCEPTS (from the packaging loop — copy through, do not rewrite):
{state.get("thumbnail_concepts", "")}

RECOMMENDED TITLE (use as the primary Video Title): {state.get("recommended_title", "") or "(see packaging audit)"}

A/B TEST SET (YouTube "Test & compare" — copy this list under the title options, under a level-4 heading named A/B Test Set):
{state.get("packaging_ab_test_set", "") or "(see packaging audit)"}

PACKAGING AUDIT:
{state.get("packaging_critique_output", "")}

SEO PRIMARY KEYWORD: {state.get("seo_primary_keyword", "")}
SEO SECONDARY KEYWORDS: {state.get("seo_secondary_keywords", "")}

SOURCE ANALYSIS + MEDICAL FACT LEDGER (for adaptation log):
{state.get("source_analysis", "")}

RESTRUCTURE STRATEGY PLAN:
{state.get("strategy_plan", "")}

SCORES:
Critique Grade: {state.get("quality_grade", "")}
Dialect Score: {state.get("dialect_score", "")}
Warmth Score: {state.get("warmth_score", "")}
Fidelity Score: {state.get("fidelity_score", "")}
Medical Accuracy Pass: {state.get("medical_accuracy_pass", "")}
Scientific Truth Score: {state.get("truth_score", "")}/10
Scientific Truth Pass: {state.get("truth_pass", "")}
Naturalness Score: {state.get("naturalness_score", "")}
Contextual Alignment Score: {state.get("contextual_alignment_score", "")}

MEDICAL TOPIC: {state.get("medical_topic", "")}
PLATFORM: {state.get("target_platform", "")}
PRESENTER PROFILE: {state.get("presenter_profile", "")}
AVOID LIST: {state.get("avoid_list", "")}

CRITICAL: Copy the APPROVED TITLE OPTIONS and APPROVED THUMBNAIL CONCEPTS through in full, including every AI Image Generation Prompt paragraph in full. Do NOT abbreviate, reword, or say "same as above" — these were already written and quality-gated upstream."""

    part1 = ""
    best_1 = None  # (issue_count, text): a retry can come back worse, so keep the best
    correction_note_1 = ""
    for attempt in range(MAX_FINAL_PACKAGE_RETRIES + 1):
        user_prompt_1 = base_user_prompt_1
        if correction_note_1:
            user_prompt_1 += f"\n\n\u26a0\ufe0f CORRECTION REQUIRED (attempt {attempt + 1}):\n{correction_note_1}"
        part1 = call_llm(system_prompt_1, user_prompt_1, temperature=0.3, max_tokens=10000)
        if verified_script:
            part1 = ew.replace_section(part1, "PRODUCTION SCRIPT", verified_script)
        part1 = _repair_cues(part1, ew.production_script(part1), "Part1")
        issues_1 = _audit_part1(part1, script_is_verified=bool(verified_script),
                                blueprint=state.get("blueprint"))
        if best_1 is None or len(issues_1) < best_1[0]:
            best_1 = (len(issues_1), part1, issues_1)
        if not issues_1:
            print(f"  [final_package Part1] OK on attempt {attempt + 1}")
            break
        print(f"  [final_package Part1] Retry {attempt + 1}: {issues_1}")
        correction_note_1 = "The previous output had these problems - fix ALL of them:\n"
        for i, issue in enumerate(issues_1, 1):
            correction_note_1 += f"  {i}. {issue}\n"
        correction_note_1 += "Regenerate the COMPLETE output fixing every issue above."
    part1 = best_1[1]
    # The description's chapter list is always rebuilt from VIDEO SECTIONS, so
    # the chapter titles can never drift from the section titles.
    part1 = ew.rebuild_description_chapters(part1)
    # Part 2's cues must come from the script the presenter actually reads.
    filmed_script = ew.production_script(part1) or state.get("refined_script", "")

    # -----------------------------------------------------------------
    # CALL 2 - Post-Production Storyboard & Detail Sections (with retry)
    # -----------------------------------------------------------------
    system_prompt_2 = load_prompt("final_script_package_part2")

    base_user_prompt_2 = f"""Compile Part 2 of the YouTube Production Deliverable (Post-Production Guide).

PRODUCTION SCRIPT (copy START CUE / END CUE words exactly from here — spoken words only):
{filmed_script}

TRANSITION DESIGN MAP (put every transition in the storyboard; the map itself is inserted by code):
{state.get("transition_design", "")}

TEXT ANIMATION & OVERLAY GUIDE (put every element and drawing in the storyboard; the guide itself is inserted by code):
{state.get("text_animation_overlay", "")}

AI B-ROLL GENERATION PROMPTS (each storyboard B-ROLL row names its B-roll #; code copies the approved prompt in):
{state.get("broll_prompts", "")}

PRODUCTION QUALITY CRITIQUE: {state.get("production_critique_output", "")}
PRODUCTION GRADE: {state.get("production_grade", "")}
MEDICAL TOPIC: {state.get("medical_topic", "")}
PLATFORM: {state.get("target_platform", "")}

RULES:
1. Storyboard: 25-40 rows, full video 0:00 to outro.
2. START CUE / END CUE: 4-8 consecutive spoken words copied exactly from the Production Script above, unique in the script, never stage directions or placeholders.
3. B-ROLL storyboard rows: write "B-roll #N" (the number in the B-roll table) — code copies the full approved prompt into the row.
4. TRANSITION MAP, TEXT ANIMATION & OVERLAY GUIDE and AI B-ROLL GENERATION PROMPTS: write only each heading and its placeholder line — the approved versions are inserted by code."""

    # The B-roll table the production loop approved goes in whole: a retyped copy
    # lost half its rows in one run (and the export files are built from it).
    approved_broll = ew.approved_broll_table(state.get("broll_prompts", ""))
    approved_parts = [("TRANSITION MAP", ew.embedded_part(state.get("transition_design", ""))),
                      ("TEXT ANIMATION & OVERLAY GUIDE", ew.embedded_part(state.get("text_animation_overlay", ""))),
                      ("AI B-ROLL GENERATION PROMPTS", approved_broll)]

    if chapters.chapter_mode_on(state.get("blueprint")):
        result = _part2_chapter_mode(state, system_prompt_2, filmed_script, approved_parts, approved_broll)
    else:
        result = None
    if result is None:
        result = _part2_single_call(system_prompt_2, base_user_prompt_2, approved_parts,
                                    approved_broll, filmed_script)
    part2, best_2 = result

    final_package = part1 + "\n\n---\n\n" + part2
    # Models sometimes double a heading's marks ("#### #### A/B Test Set").
    final_package = re.sub(r"^(#{1,6})\s+#{1,6}\s+", r"\1 ", final_package, flags=re.MULTILINE)
    # What the best attempts still got wrong, for the warning at the top of the workbook.
    left = [f"Part 1: {i}" for i in best_1[2]] + [f"Part 2: {i}" for i in best_2[2]]
    return {"final_package": final_package, "final_package_issues": left}
