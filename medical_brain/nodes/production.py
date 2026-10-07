"""Post-production layers: transitions, text/overlay animations, B-roll prompts, production critique.

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import re

from ..exports import workbook as ew
from ..llm import call_llm, call_llm_json
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import UnreadableAnswer, pick


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

    response = call_llm(system_prompt, user_prompt, temperature=0.6, max_tokens=9000)
    return {"broll_prompts": response}


# Words that name a camera move — a video B-roll without one tends to look like dead stock.
_CAMERA_MOVE = re.compile(
    r"\b(?:push[- ]?in|pull[- ]?(back|out)|dolly|parallax|orbit|track(ing)?\b|rack[- ]focus|pan(s|ning)?\b|tilt|"
    r"crane|zoom|slide|time[- ]?lapse|slow[- ]?motion|handheld|gimbal|steadicam|fly[- ]?(through|over)|"
    r"camera (moves|drifts|glides|rises|descends|follows|circles))", re.IGNORECASE)
# On-screen words or numbers requested inside the generated image (AI garbles them).
_TEXT_IN_IMAGE = re.compile(r"[\"“«][^\"”»]{1,60}[\"”»]|\bnumbers? like\b|\breadout\b|\bthe words?\b", re.IGNORECASE)


def _seconds(stamp: str):
    m = re.search(r"(\d{1,2}):(\d{2})", stamp or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def broll_engagement_problems(broll_prompts: str, overlay_guide: str = "", minutes: float = 0) -> list:
    """Code checks that keep B-roll eye-catching (they hold whatever model wrote it):
    motion in every video clip, no text inside generated media, enough visual events, no long gaps."""
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
    problems, times, rows = [], [], 0
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
        t = _seconds(cells[c_time]) if c_time is not None else None
        if t is not None:
            times.append(t)
        visible = re.split(r"\b(avoid|negative)\b", prompt, maxsplit=1, flags=re.IGNORECASE)[0]
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
    times.sort()
    if times and times[0] > 20:
        problems.append(f"The first B-roll starts at {times[0] // 60}:{times[0] % 60:02d}: add one in the first 20 seconds.")
    for a, b in zip(times, times[1:]):
        if b - a > 90:
            problems.append(f"No B-roll between {a // 60}:{a % 60:02d} and {b // 60}:{b % 60:02d}: "
                            "add one in that stretch (or a whiteboard drawing).")
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
                                              _video_minutes(state))
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
