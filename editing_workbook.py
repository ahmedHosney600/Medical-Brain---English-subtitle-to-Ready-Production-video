"""
Turns the final script package into ONE document ordered like the editing
workflow in Premiere (raw cut → speed → audio → L/J cuts → captions → B-roll →
text overlays → zooms & transitions → icons & shapes → animations → color →
whiteboard), followed by publishing material and a reference appendix.

Everything here is plain Python over text the LLM already produced: nothing is
retyped by a model, so nothing can be shortened or cut off on the way.

Also holds the checks used by main.py's final-package audits:
  - normalize_ar / find_cue     : is a sentence cue really in the script, and only once?
  - check_cue_rows              : cue problems for a list of table rows
  - check_chapter_titles        : can VIDEO SECTIONS titles be used as YouTube chapters?
  - rebuild_description_chapters: description chapters = section titles, always
"""

import re
from typing import Optional

# --------------------------------------------------------------------------
# Arabic text matching
# --------------------------------------------------------------------------
_DIACRITICS = re.compile(r"[ً-ْٰـ]")  # harakat, superscript alef, tatweel
_PUNCT = re.compile(r"[\.…,\"'`“”‘’«»\(\)\[\]\{\}،؛؟\?!:;\-–—_*/\\|<>~#]")


def normalize_ar(text: str) -> str:
    """Normalizes Arabic for cue matching: no diacritics or punctuation,
    أإآ→ا, ى→ي, ة→ه, single spaces."""
    s = _DIACRITICS.sub("", text or "")
    s = re.sub(r"[أإآٱ]", "ا", s)
    s = s.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    s = _PUNCT.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def spoken_text(script: str) -> str:
    """The words actually spoken in the production script: drops [cues],
    (stage directions), **=== act headers ===** and markdown markers."""
    s = re.sub(r"\[[^\]]*\]", " ", script or "")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"=+[^=\n]*=+", " ", s)
    return s


_PLACEHOLDER_HINTS = ("بداية الفيديو", "نهاية الفيديو", "start of video", "end of video", "n/a", "—", "...")


def find_cue(cue: str, norm_script: str) -> int:
    """How many times the cue occurs (as whole words) in the normalized script."""
    c = normalize_ar(cue)
    if not c:
        return 0
    return len(re.findall(r"(?:^|\s)" + re.escape(c) + r"(?=\s|$)", norm_script))


def cue_problem(cue: str, norm_script: str, min_words: int = 3) -> Optional[str]:
    """None when the cue is usable; otherwise a short reason."""
    raw = (cue or "").strip()
    if not raw or raw.startswith("(") or any(h in raw.lower() for h in _PLACEHOLDER_HINTS if h != "—") or raw in ("—", "-"):
        return "placeholder, not a spoken sentence"
    words = normalize_ar(raw).split()
    if len(words) < min_words:
        return f"too short ({len(words)} words) to find reliably"
    n = find_cue(raw, norm_script)
    if n == 0:
        return "not found word-for-word in the production script"
    if n > 1:
        return f"appears {n} times in the script (add words until it is unique)"
    return None


def check_cue_rows(rows: list, script: str, min_words: int = 3) -> list:
    """rows: [(label, start_cue, end_cue), ...] → list of problem strings."""
    norm = normalize_ar(spoken_text(script))
    problems = []
    for label, start, end in rows:
        for kind, cue in (("START", start), ("END", end)):
            why = cue_problem(cue, norm, min_words)
            if why:
                problems.append(f"{label} {kind} CUE '{cue}': {why}")
    return problems


# --------------------------------------------------------------------------
# Markdown helpers
# --------------------------------------------------------------------------
# Top-level parts of the package. Models don't always use the same heading
# level (### vs ##), so a part starts at any 1–3 level heading naming one of
# these; every other heading stays inside its part.
SECTION_KEYS = [
    "SCRIPT METADATA", "YOUTUBE PACKAGING", "PRODUCTION SCRIPT", "WHAT CHANGED",
    "RETENTION ARCHITECTURE", "CTA VERSIONS", "QA RESULTS", "VIDEO SECTIONS",
    "INTEGRATED PRODUCTION STORYBOARD", "TRANSITION MAP", "TEXT ANIMATION & OVERLAY GUIDE",
    "AI B-ROLL GENERATION PROMPTS", "INTEGRATION DATA",
    "SCRIPT PACKAGE", "POST-PRODUCTION GUIDE",  # wrapper headings
]


def _is_part_heading(line: str) -> bool:
    m = re.match(r"^(#{1,3})\s+(.*)$", line)
    if not m:
        return False
    title = m.group(2).upper()
    return any(k in title for k in SECTION_KEYS)


def split_sections(text: str) -> list:
    """Splits into top-level parts → [(heading_line, body)]. Text before the
    first part heading is returned with heading ''."""
    out, heading, buf = [], "", []
    for line in (text or "").splitlines():
        if _is_part_heading(line):
            out.append((heading, "\n".join(buf)))
            heading, buf = line, []
        else:
            buf.append(line)
    out.append((heading, "\n".join(buf)))
    return out


def _strip_rules(body: str) -> str:
    """Removes leading/trailing blank lines and '---' separators."""
    lines = body.splitlines()
    while lines and lines[0].strip() in ("", "---"):
        lines.pop(0)
    while lines and lines[-1].strip() in ("", "---"):
        lines.pop()
    return "\n".join(lines)


def find_section(text: str, key: str) -> str:
    for heading, body in split_sections(text):
        if key.upper() in heading.upper():
            return body
    return ""


def table_rows(block: str) -> list:
    """Data rows of the first markdown table in block, as lists of cells."""
    rows, seen_header = [], False
    for line in (block or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if seen_header and rows:
                break
            continue
        # Split on unescaped pipes only; models often escape pipes inside a cell as \|
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s.strip().strip("|"))]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            seen_header = True
            continue
        if not seen_header:
            continue  # header row
        rows.append(cells)
    return rows


def production_script(text: str) -> str:
    return find_section(text, "PRODUCTION SCRIPT")


# --------------------------------------------------------------------------
# VIDEO SECTIONS = YouTube chapters
# --------------------------------------------------------------------------
# Production labels that mean nothing to a viewer scanning chapters.
BANNED_CHAPTER_WORDS = [
    "مقدمه", "المقدمه", "هوك", "الهوك", "خاتمه", "الخاتمه", "للاشتراك", "الدعوه",
    "اشترك", "الاشتراك", "الجزء الاول", "الجزء الثاني", "الفصل الاول", "فاصل",
    "hook", "intro", "outro", "cta", "act ", "section",
]
MAX_CHAPTER_CHARS = 45


def parse_video_sections(text: str) -> list:
    """[{num, title, start, end, duration_s}] from the VIDEO SECTIONS table."""
    out = []
    for cells in table_rows(find_section(text, "VIDEO SECTIONS")):
        if len(cells) < 4:
            continue
        out.append({
            "num": cells[0],
            "title": cells[1].strip("*[] "),
            "start": cells[2],
            "end": cells[3],
            "duration_s": _parse_duration(cells[4]) if len(cells) > 4 else 0,
            "notes": cells[5] if len(cells) > 5 else "",
        })
    return out


def _parse_duration(s: str) -> int:
    s = (s or "").lower()
    m = re.search(r"(\d+):(\d{1,2})", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    mins = re.search(r"(\d+)\s*m", s)
    secs = re.search(r"(\d+)\s*s", s)
    return (int(mins.group(1)) * 60 if mins else 0) + (int(secs.group(1)) if secs else 0)


def check_chapter_titles(sections: list) -> list:
    problems = []
    if len(sections) < 3:
        problems.append(f"Only {len(sections)} VIDEO SECTIONS rows; YouTube chapters need at least 3.")
    for s in sections:
        t = s["title"]
        nt = " " + normalize_ar(t).lower() + " "
        bad = [w for w in BANNED_CHAPTER_WORDS if (" " + w.strip() + " ") in nt or (w.endswith(" ") and w in nt)]
        if bad:
            problems.append(f"Section {s['num']} title '{t}' uses production label(s) {bad}; write what this part answers for the viewer.")
        if len(t) > MAX_CHAPTER_CHARS:
            problems.append(f"Section {s['num']} title '{t}' is {len(t)} characters; keep chapter titles ≤ {MAX_CHAPTER_CHARS}.")
        if 0 < s["duration_s"] < 10:
            problems.append(f"Section {s['num']} is {s['duration_s']}s; YouTube chapters must be at least 10 seconds.")
    return problems


def _fmt_time(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def chapter_lines(sections: list) -> list:
    t, lines = 0, []
    for s in sections:
        lines.append(f"{_fmt_time(t)} {s['title']}")
        t += s["duration_s"] or 0
    return lines


def rebuild_description_chapters(text: str) -> str:
    """Replaces the chapter list inside the SEO description with one built from
    the VIDEO SECTIONS table, so titles always match and the first is 0:00."""
    sections = parse_video_sections(text)
    if len(sections) < 3:
        return text
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "(Chapters)" in line or "الفصول" in line and line.strip().startswith("⏱"):
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or re.match(r"^\s*\[?\d{1,2}:\d{2}", lines[j]) or lines[j].strip().startswith("[")):
                if lines[j].strip() == "" and j > i + 1:
                    break
                j += 1
            rest = lines[j:]
            while rest and not rest[0].strip():
                rest.pop(0)
            return "\n".join(lines[:i + 1] + chapter_lines(sections) + [""] + rest).rstrip("\n") + "\n"
    return text


# --------------------------------------------------------------------------
# Storyboard
# --------------------------------------------------------------------------
def parse_storyboard(text: str) -> list:
    """[{num, act, time, start, end, layer, detail}]. The Detail column itself
    contains '|' separators, so everything after the 6th cell is the detail."""
    rows = []
    for cells in table_rows(find_section(text, "INTEGRATED PRODUCTION STORYBOARD")):
        if len(cells) < 7:
            continue
        rows.append({
            "num": cells[0], "act": cells[1], "time": cells[2],
            "start": cells[3], "end": cells[4], "layer": cells[5].upper().strip("* "),
            "detail": " | ".join(cells[6:]).strip(),
        })
    return rows


TEXT_LAYERS = ["TOP-RIGHT POPUP", "TEXT OVERLAY TITLE", "WARNING/ALERT BOX", "QUOTE BOX", "KINETIC TEXT", "LOWER-THIRD"]
MOTION_LAYERS = ["TRANSITION", "ZOOM/REFRAME", "SFX"]


def _layer_step(layer: str) -> str:
    l = layer.replace(" ", "")
    if "B-ROLL" in l or "BROLL" in l:
        return "broll"
    if "DRAWING" in l or "WHITEBOARD" in l:
        return "whiteboard"
    if "ICON" in l or "SHAPE" in l:
        return "icons"
    if any(t.replace(" ", "") in l for t in TEXT_LAYERS) or "POPUP" in l or "LOWER" in l:
        return "text"
    if any(t.replace(" ", "") in l for t in MOTION_LAYERS) or "ZOOM" in l:
        return "motion"
    if "ANIM" in l:
        return "animations"
    return "other"


# --------------------------------------------------------------------------
# Workbook
# --------------------------------------------------------------------------
def _cue(cue: str, norm_script: str) -> str:
    return cue if cue_problem(cue, norm_script, min_words=1) is None else f"⚠️ {cue}"


def _rows_table(rows: list, norm_script: str) -> str:
    out = ["| SB# | ⏱️ ~Time | ▶️ START CUE | ⏹️ END CUE | Layer | Detail |", "|---|---|---|---|---|---|"]
    for r in rows:
        detail = r["detail"].replace("|", "·")
        out.append(f"| {r['num']} | {r['time']} | {_cue(r['start'], norm_script)} | {_cue(r['end'], norm_script)} | {r['layer']} | {detail} |")
    return "\n".join(out)


def _rows_list(rows: list, norm_script: str) -> str:
    """Long-prompt rows (B-roll, whiteboard) as readable blocks, not table cells."""
    out = []
    for r in rows:
        out.append(f"**SB {r['num']} · ~{r['time']} · {r['layer']}**  ")
        out.append(f"▶️ {_cue(r['start'], norm_script)}  →  ⏹️ {_cue(r['end'], norm_script)}")
        for part in [p.strip() for p in r["detail"].split(" | ") if p.strip()]:
            out.append(f"- {part}")
        out.append("")
    return "\n".join(out).rstrip()


def _term_glossary(rows: list) -> str:
    pairs = []
    for r in rows:
        if "POPUP" not in r["layer"]:
            continue
        m = re.search(r"\"?\[?([^\"\]\|→]+?)\s*(?:→|->)\s*([^\"\]\|]+?)\]?\"?(?:\s*\||$)", r["detail"])
        if m:
            pair = (m.group(1).strip(' "[]'), m.group(2).strip(' "[]'))
            if pair not in pairs:
                pairs.append(pair)
    if not pairs:
        return ""
    out = ["| English term (check spelling in captions) | Arabic |", "|---|---|"]
    out += [f"| {e} | {a} |" for e, a in pairs]
    return "\n".join(out)


def _part(heading: str, body: str) -> list:
    """Re-emits a package part at ### level, with its own sub-headings pushed
    below it so the document outline stays consistent."""
    title = re.sub(r"^#+\s*", "", heading).strip()
    inner = re.sub(r"^#{1,3}(?=\s)", "####", body, flags=re.MULTILINE)
    return ["", f"### {title}", "", inner]


def _empty(note: str = "Nothing from the script package for this step.") -> str:
    return f"_{note}_"


def build_editing_workbook(final_package: str, state: Optional[dict] = None) -> str:
    """Reorders final_package into the editing workflow. Raises on unexpected
    structure; the caller falls back to the original package."""
    state = state or {}
    sections = split_sections(final_package)
    used = set()

    def take(key: str) -> tuple:
        for idx, (h, b) in enumerate(sections):
            if idx not in used and key.upper() in h.upper():
                used.add(idx)
                return h, _strip_rules(b)
        return "", ""

    meta = take("Script Metadata")
    packaging = take("YOUTUBE PACKAGING")
    script = take("PRODUCTION SCRIPT")
    adaptation = take("WHAT CHANGED")
    retention = take("RETENTION ARCHITECTURE")
    ctas = take("CTA VERSIONS")
    qa = take("QA RESULTS")
    video_sections = take("VIDEO SECTIONS")
    storyboard = take("INTEGRATED PRODUCTION STORYBOARD")
    transitions = take("TRANSITION MAP")
    text_guide = take("TEXT ANIMATION")
    broll_detail = take("AI B-ROLL")
    integration = take("INTEGRATION DATA")
    leftovers = [(h, _strip_rules(b)) for i, (h, b) in enumerate(sections)
                 if i not in used and h and _strip_rules(b)]

    if not script[1] or not storyboard[1]:
        raise ValueError("production script or storyboard section missing")

    norm_script = normalize_ar(spoken_text(script[1]))
    secs = parse_video_sections(final_package)
    sb = parse_storyboard(final_package)
    groups = {}
    for r in sb:
        groups.setdefault(_layer_step(r["layer"]), []).append(r)

    def num(r):
        m = re.match(r"\d+", r["num"])
        return int(m.group()) if m else 0
    for g in groups.values():
        g.sort(key=num)

    title = state.get("recommended_title") or ""
    md = []
    md.append("# 🎬 VIDEO PRODUCTION WORKBOOK" + (f" — {title}" if title else ""))
    md.append("Ordered like the editing workflow. Find every item by its **sentence cue** (▶️ START / ⏹️ END); "
              "times marked ~ are estimates from before filming and will shift after silence cutting and the speed-up. "
              "A ⚠️ before a cue means it was not found word-for-word in the script: search for the nearest sentence.")

    # ── Overview
    md += ["", "## 0 · OVERVIEW", "", re.sub(r"^#{1,3}(?=\s)", "####", meta[1], flags=re.MULTILINE) or _empty()]

    # ── Filming
    md += ["", "## 🎥 BEFORE FILMING"] + _part("PRODUCTION SCRIPT (Teleprompter)", script[1])
    if retention[1]:
        md += _part(*retention)
    if ctas[1]:
        md += _part(*ctas)

    md += ["", "## ✂️ EDITING STEPS"]

    # ── Steps 1–2
    md += ["", "### STEP 1–2 · Raw cut & First Listen", ""]
    if secs:
        md += ["**Sections (use these to check nothing was cut by mistake):**", "",
               "| # | Section / Chapter | ▶️ Starts with | ⏹️ Ends with | ~Length |", "|---|---|---|---|---|"]
        for s in secs:
            md.append(f"| {s['num']} | {s['title']} | {_cue(s['start'], norm_script)} | {_cue(s['end'], norm_script)} | {_fmt_time(s['duration_s'])} |")
    ret_rows = table_rows(retention[1])
    if ret_rows:
        md += ["", "**Must survive the cut** (the video's promises depend on these lines):", ""]
        for cells in ret_rows:
            if len(cells) >= 3:
                md.append(f"- [ ] {cells[0]}: «{cells[2]}»")
    md += ["", "> Step 2.4: start B-roll generation now in the Google Flow automator with `broll_images.txt` / `broll_videos.txt` "
           "(and `whiteboard_images.txt` for step 13). Prompts are listed in STEP 7 and STEP 13."]

    # ── Steps 3–4
    md += ["", "### STEP 3 · Adjust speed", "", _empty()]
    md += ["", "### STEP 4 · Enhance audio", "", _empty()]

    # ── Step 5
    md += ["", "### STEP 5 · L/J cuts (optional)", ""]
    if len(secs) > 1:
        md += ["Candidate points: where one section ends and the next starts.", "",
               "| Between | ⏹️ Last line of the section | ▶️ First line of the next |", "|---|---|---|"]
        for a, b in zip(secs, secs[1:]):
            md.append(f"| {a['title']} → {b['title']} | {_cue(a['end'], norm_script)} | {_cue(b['start'], norm_script)} |")
    else:
        md.append(_empty())

    # ── Step 6
    gloss = _term_glossary(sb)
    md += ["", "### STEP 6 · Captions", ""]
    md += ["Auto-captions often misspell these English terms. Check them:", "", gloss] if gloss else [_empty()]

    # ── Step 7
    md += ["", "### STEP 7 · B-rolls", ""]
    md.append(_rows_list(groups.get("broll", []), norm_script) if groups.get("broll") else _empty("No B-roll rows in the storyboard."))

    # ── Step 8
    md += ["", "### STEP 8 · Text overlays", ""]
    if groups.get("text"):
        md += ["Styles, fonts and colors: see the Text Animation & Overlay Guide in the appendix.", "",
               _rows_table(groups["text"], norm_script)]
    else:
        md.append(_empty("No text overlay rows in the storyboard."))

    # ── Step 9
    md += ["", "### STEP 9 · Zooms, transitions & SFX", ""]
    md.append(_rows_table(groups["motion"], norm_script) if groups.get("motion") else _empty())

    # ── Step 10
    md += ["", "### STEP 10 · Icons & shapes transitions (optional)", ""]
    md.append(_rows_table(groups["icons"], norm_script) if groups.get("icons") else _empty())

    # ── Step 11
    md += ["", "### STEP 11 · Animations (optional)", ""]
    md.append(_rows_table(groups["animations"], norm_script) if groups.get("animations") else _empty())

    # ── Step 12
    md += ["", "### STEP 12 · Coloring layer", "", _empty()]

    # ── Step 13
    md += ["", "### STEP 13 · Whiteboard layer", ""]
    if groups.get("whiteboard"):
        md += ["How to make each drawing: (1) generate the **whiteboard image** in Google Flow; "
               "(2) use it as the start frame for the **draw-on video** prompt (image-to-video); "
               "(3) add the **labels** in Premiere as text, since AI can't draw Arabic or anatomy labels reliably.", "",
               _rows_list(groups["whiteboard"], norm_script)]
    else:
        md.append(_empty("No whiteboard drawings in this video."))

    if groups.get("other"):
        md += ["", "### Other storyboard events", "", _rows_table(groups["other"], norm_script)]

    # ── Publishing
    md += ["", "## 🚀 PUBLISHING", ""]
    if packaging[1]:
        md += _part(*packaging)
    if secs:
        md += ["", "### YOUTUBE CHAPTERS — fix the times after editing", "",
               "Put the playhead on each start sentence in the final edit and copy its time into the description.", "",
               "| ~Time | Chapter title | ▶️ Starts with |", "|---|---|---|"]
        t = 0
        for s in secs:
            md.append(f"| {_fmt_time(t)} | {s['title']} | {_cue(s['start'], norm_script)} |")
            t += s["duration_s"] or 0

    # ── Appendix
    md += ["", "## 📚 REFERENCE APPENDIX"]
    for h, b in (storyboard, transitions, text_guide, broll_detail, adaptation, qa, integration, video_sections, *leftovers):
        if h and b:
            md += _part(h, b)

    return "\n".join(md).strip() + "\n"
