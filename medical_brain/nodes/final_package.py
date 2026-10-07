"""Final production document: assembles the package in two LLM calls, with code audits.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import re

from ..llm import call_llm
from ..prompts import load_prompt
from ..state import PipelineState
from ..exports import workbook as ew


MAX_FINAL_PACKAGE_RETRIES = 2


def _audit_part1(text: str) -> list:
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

    # Check spoken script length against target duration
    try:
        script_match = re.search(r"###\s*🎬?\s*PRODUCTION SCRIPT.*?\n(.*?)(\n###\s*WHAT CHANGED|\Z)", text, re.DOTALL)
        if script_match:
            script_body = script_match.group(1)
            cleaned = re.sub(r"\[.*?\]", "", script_body)
            cleaned = re.sub(r"\*\*\(.*?\)\*\*", "", cleaned)
            cleaned = re.sub(r"===.*?===", "", cleaned)
            cleaned = re.sub(r"\*\*\[.*?\]\*\*", "", cleaned)
            spoken_words = len(cleaned.split())
            
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


def _audit_part2(text: str, script: str = "") -> list:
    """Audit Part 2 (Post-Production) for quality issues. `script` is the
    production script the cues must come from."""
    issues = []

    # Storyboard must be present and have enough rows
    if "INTEGRATED PRODUCTION STORYBOARD" not in text:
        issues.append("MISSING: The INTEGRATED PRODUCTION STORYBOARD section is absent entirely.")
    else:
        start_idx = text.find("INTEGRATED PRODUCTION STORYBOARD")
        end_idx = text.find("TRANSITION MAP", start_idx)
        if end_idx == -1:
            end_idx = start_idx + 8000
        storyboard_block = text[start_idx:end_idx]
        data_rows = [
            line for line in storyboard_block.split("\n")
            if line.startswith("|")
            and not line.startswith("|---|")
            and "Timecode" not in line
            and "START CUE" not in line
        ]
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

    return issues


def final_script_package(state: PipelineState) -> dict:

    system_prompt_1 = load_prompt("final_script_package_part1")

    base_user_prompt_1 = f"""Compile Part 1 of the YouTube Production Deliverable.

FINAL SCRIPT (definitive version):
{state.get("refined_script", "")}

HOOK VARIATIONS:
{state.get("hook", "")}

CTA VERSIONS:
{state.get("cta_output", "")}

APPROVED TITLE OPTIONS (from the packaging loop — copy through, do not rewrite):
{state.get("title_options", "")}

APPROVED THUMBNAIL CONCEPTS (from the packaging loop — copy through, do not rewrite):
{state.get("thumbnail_concepts", "")}

RECOMMENDED TITLE (use as the primary Video Title): {state.get("recommended_title", "") or "(see packaging audit)"}

A/B TEST SET (YouTube "Test & compare" — copy this list under the title options as "#### A/B Test Set"):
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
        issues_1 = _audit_part1(part1)
        if best_1 is None or len(issues_1) < best_1[0]:
            best_1 = (len(issues_1), part1)
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

TRANSITION DESIGN MAP (reproduce in full, add SB# cross-references):
{state.get("transition_design", "")}

TEXT ANIMATION & OVERLAY GUIDE (reproduce in full, add SB# cross-references):
{state.get("text_animation_overlay", "")}

AI B-ROLL GENERATION PROMPTS (reproduce with FULL prompts in storyboard rows AND detail table):
{state.get("broll_prompts", "")}

PRODUCTION QUALITY CRITIQUE: {state.get("production_critique_output", "")}
PRODUCTION GRADE: {state.get("production_grade", "")}
MEDICAL TOPIC: {state.get("medical_topic", "")}
PLATFORM: {state.get("target_platform", "")}

RULES:
1. Storyboard: 25-40 rows, full video 0:00 to outro.
2. START CUE / END CUE: 4-8 consecutive spoken words copied exactly from the Production Script above, unique in the script, never stage directions or placeholders.
3. B-ROLL storyboard rows: write FULL AI prompt inline - NEVER "[Full Prompt in SB]" or any shortcut.
4. B-Roll detail table: ALSO write full AI prompt in each row - self-contained, duplicates are fine."""

    part2 = ""
    best_2 = None
    correction_note_2 = ""
    for attempt in range(MAX_FINAL_PACKAGE_RETRIES + 1):
        user_prompt_2 = base_user_prompt_2
        if correction_note_2:
            user_prompt_2 += f"\n\n⚠️ CORRECTION REQUIRED (attempt {attempt + 1}):\n{correction_note_2}"
        part2 = call_llm(system_prompt_2, user_prompt_2, temperature=0.3, max_tokens=12000)
        issues_2 = _audit_part2(part2, filmed_script)
        if best_2 is None or len(issues_2) < best_2[0]:
            best_2 = (len(issues_2), part2)
        if not issues_2:
            print(f"  [final_package Part2] OK on attempt {attempt + 1}")
            break
        print(f"  [final_package Part2] Retry {attempt + 1}: {issues_2}")
        correction_note_2 = "The previous output had these problems - fix ALL of them:\n"
        for i, issue in enumerate(issues_2, 1):
            correction_note_2 += f"  {i}. {issue}\n"
        correction_note_2 += "Regenerate the COMPLETE output fixing every issue above."
    part2 = best_2[1]

    final_package = part1 + "\n\n---\n\n" + part2
    return {"final_package": final_package}
