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
    s = re.sub(r"[\"`“”«»]", "", s)        # quotes glue to words: و"كاميرا → وكاميرا
    s = re.sub(r"[أإآٱ]", "ا", s)
    s = s.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    s = _PUNCT.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def spoken_text(script: str) -> str:
    """The words actually spoken in the production script: drops [cues],
    (stage directions), **=== act headers ===** and markdown markers."""
    s = re.sub(r"\[[^\]]*\]", " ", script or "")
    # Stage directions are parentheses that open a line, e.g. "(الكاميرا قريبة، نبرة هادئة)",
    # or long ones. Short inline parentheses are spoken terms, e.g. "الـ (AF)", and stay.
    s = re.sub(r"(^|\n)[ \t>*\"«]*\([^)\n]*\)", r"\1 ", s)
    s = re.sub(r"\((?:[^)\s]+\s+){4,}[^)]*\)", " ", s)
    s = re.sub(r"=+[^=\n]*=+", " ", s)
    s = re.sub(r"\*\*[^*\n]{1,40}:\*\*", " ", s)      # speaker labels like **د. أحمد حسني:**
    s = re.sub(r"[*#>]+", " ", s)                       # leftover markdown markers
    return s


_PLACEHOLDER_HINTS = ("بداية الفيديو", "نهاية الفيديو", "start of video", "end of video", "n/a", "—", "...")


def find_cue(cue: str, norm_script: str) -> int:
    """How many times the cue occurs (as whole words) in the normalized script."""
    c = normalize_ar(spoken_text(cue))
    if not c:
        return 0
    return len(re.findall(r"(?:^|\s)" + re.escape(c) + r"(?=\s|$)", norm_script))


def cue_problem(cue: str, norm_script: str, min_words: int = 3) -> Optional[str]:
    """None when the cue is usable; otherwise a short reason."""
    raw = (cue or "").strip()
    if not raw or not normalize_ar(spoken_text(raw)) or raw in ("—", "-") \
            or any(h in raw.lower() for h in _PLACEHOLDER_HINTS if h != "—"):
        return "placeholder, not a spoken sentence"
    words = normalize_ar(spoken_text(raw)).split()
    if len(words) < min_words:
        return f"too short ({len(words)} words) to find reliably"
    n = find_cue(raw, norm_script)
    if n == 0:
        return "not found word-for-word in the production script"
    if n > 1:
        return f"appears {n} times in the script (add words until it is unique)"
    return None


def _bare(word: str) -> str:
    """A normalized word without the "و" (and) it is often glued to: والسكته → السكته."""
    return word[1:] if len(word) >= 4 and word.startswith("و") else word


# Non-spoken notes in the script: [VISUAL NOTE: …] cues, and stage directions in
# parentheses that open a line, e.g. **(د. أحمد بيعدل وضعية جلوسه، نبرة فيها ذكاء)**
_NOTE = re.compile(r"(\[[^\]]*\]|(?:^|\n)[ \t>*\"«]*\([^)\n]*\)[*\"»]*)")


class _ScriptIndex:
    """The spoken script as tokens: original spelling (for the editor) + normalized
    (for matching; a leading "و" is ignored, see _bare)."""

    def __init__(self, script: str):
        self.orig, self.norm = [], []
        self.brackets = []   # (normalized text of a non-spoken note, index of the next spoken word)
        for i, piece in enumerate(_NOTE.split(script or "")):
            if i % 2:
                self.brackets.append((normalize_ar(piece), len(self.norm)))
                continue
            for tok in spoken_text(piece).split():
                n = normalize_ar(tok)
                if n:
                    self.orig.append(tok)
                    self.norm.append(_bare(n))

    def occurrences(self, words: list) -> list:
        k = len(words)
        return [i for i in range(len(self.norm) - k + 1) if self.norm[i:i + k] == words] if k else []

    def unique_window(self, start: int, length: int, max_length: int) -> tuple:
        """Grow a window from `start` until it occurs exactly once (shifted back
        if it would run past the end of the script)."""
        start = max(0, min(start, len(self.norm) - length))
        length = min(length, len(self.norm) - start)
        while length < max_length and start + length < len(self.norm) \
                and len(self.occurrences(self.norm[start:start + length])) > 1:
            length += 1
        return start, length

    def grow_back(self, start: int, length: int, max_length: int) -> tuple:
        """Like unique_window, but grows to the left (for END cues)."""
        while length < max_length and start > 0 \
                and len(self.occurrences(self.norm[start:start + length])) > 1:
            start, length = start - 1, length + 1
        return start, length

    def text(self, start: int, length: int, from_end: bool = False) -> str:
        """Original words of the window, kept inside one sentence when the cue is long enough."""
        words = self.orig[start:start + length]
        ends = [i for i, w in enumerate(words) if re.search(r"[.!?؟:]+[\"»)]*$", w)]
        if from_end:
            cut = [i for i in ends if i < len(words) - 1 and len(words) - 1 - i >= 3]
            if cut:
                words = words[cut[-1] + 1:]
        else:
            cut = [i for i in ends if i >= 2]
            if cut:
                words = words[:cut[0] + 1]
        text = re.sub(r"[\"“”«»]", "", " ".join(words))
        if text.count("(") != text.count(")"):        # window cut a "(AF)" in half
            text = text.replace("(", "").replace(")", "")
        return text.strip(" ,،.؛:")


# Common short words (normalized) that say nothing about where a cue is.
_FILLER = set(normalize_ar("""في من علي على عن ما زي ده دي دا اللي الي و يا ان إن لو مش هو هي انت إنت انا أنا
احنا إحنا كده كدة بس او أو ولا لا مع بقى بقي كمان عشان علشان يعني كل حاجة ايه إيه هل اي أي ال ب ل""").split())


def _locate(cue_words: list, idx: _ScriptIndex, after: int, length: int, max_length: int,
            end_anchor: bool = False):
    """Best place for a cue: exact match (first at/after `after`), else the window
    with the most shared words. END cues that are too short grow to the left, so the
    cue still ends where the spoken sentence ends. Returns (start, length, grew_left) or None."""
    cue_words = [_bare(w) for w in cue_words]
    hits = idx.occurrences(cue_words)
    if hits:
        start = next((h for h in hits if h >= after), hits[0])
        if end_anchor and (len(cue_words) < length or len(hits) > 1):
            grow = length - len(cue_words)
            new_start = max(0, start - grow)
            return idx.grow_back(new_start, len(cue_words) + (start - new_start), max_length) + (True,)
        return idx.unique_window(start, max(length, len(cue_words)), max_length) + (False,)
    # Cue copied from a [VISUAL NOTE: …] / [KINETIC TEXT: …]: the event sits where
    # that note is, so take the spoken words right after it (or right before, for END).
    cue_text = " ".join(cue_words)
    spots = [at for text, at in idx.brackets if cue_text and cue_text in text]
    if spots:
        at = next((a for a in spots if a >= after), spots[0])
        if end_anchor and at <= after < len(idx.norm):
            # The note sits before this row's START: end with the sentence START opens.
            ends = [k for k in range(after + 2, min(len(idx.orig), after + 25))
                    if re.search(r"[.!?؟]+[\"»)]*$", idx.orig[k])]
            stop = ends[0] + 1 if ends else min(len(idx.orig), after + max_length)
            start = max(after, stop - length)
            return idx.grow_back(start, stop - start, max_length) + (True,)
        if end_anchor and at > 0:
            start = max(0, at - length)
            return idx.grow_back(start, at - start, max_length) + (True,)
        if at < len(idx.norm):
            return idx.unique_window(at, length, max_length) + (False,)
    # Fuzzy match on content words only: filler like "زي ما في" matches anywhere.
    wanted = set(cue_words) - _FILLER
    if len(wanted) < 2:
        return None
    need = max(2, (len(wanted) + 1) // 2)
    best, best_score = None, 0
    span = max(len(cue_words), length) + 2
    for order in (range(after, len(idx.norm)), range(0, after)):
        for s in order:
            window = idx.norm[s:s + span]
            if not window or window[0] not in set(cue_words):
                continue
            score = len(wanted & set(window))
            if score > best_score:
                best, best_score = s, score
        if best_score >= need:
            break
    if best is None or best_score < need:
        return None
    return idx.unique_window(best, max(length, min(len(cue_words), max_length)), max_length) + (False,)


def repair_cue_table(text: str, section_key: str, start_col: int, end_col, script: str,
                     length: int = 5, max_length: int = 9) -> tuple:
    """Replaces START/END cues that aren't exact, unique script words with the matching
    words from the script (closest match, in timeline order). Returns
    (text, fixed, examples). Cues that can't be placed are left as they are."""
    idx = _ScriptIndex(script)
    norm_script = normalize_ar(spoken_text(script))
    lines = text.splitlines(keepends=True)
    in_section, header_seen, pos = False, False, 0
    fixed, examples = 0, []
    for i, line in enumerate(lines):
        if _is_part_heading(line.rstrip("\n")):
            in_section = section_key.upper() in line.upper()
            header_seen = False
            continue
        if not in_section or not line.lstrip().startswith("|"):
            continue
        parts = re.split(r"(?<!\\)\|", line)
        cells = [c.strip() for c in parts[1:-1]]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            # Only tables whose header names a cue column (a section can hold other
            # tables, e.g. the B-roll density summary, whose numbers aren't cues).
            head = [c.strip().upper() for c in re.split(r"(?<!\\)\|", lines[i - 1])[1:-1]] if i else []
            header_seen = len(head) > start_col and any(
                k in head[start_col] for k in ("START", "CUE", "LINE", "SENTENCE", "▶"))
            continue
        cols = [c for c in (start_col, end_col) if c is not None]
        if not header_seen or len(cells) <= max(cols):
            continue
        for col in cols:
            cue = cells[col]
            end = col == end_col
            if cue_problem(cue, norm_script, min_words=3) is None:
                found = _locate(normalize_ar(spoken_text(cue)).split(), idx, pos, length, max_length)
            else:
                cue_words = normalize_ar(spoken_text(cue)).split()
                found = _locate(cue_words, idx, pos, length, max_length, end_anchor=end) if cue_words else None
                if found:
                    new = idx.text(*found)
                    if new and cue_problem(new, norm_script, min_words=3) is None:
                        parts[col + 1] = f" {new} "
                        fixed += 1
                        if len(examples) < 5:
                            examples.append(f"{cue} → {new}")
            if found and col == start_col:
                pos = found[0]
        lines[i] = "|".join(parts)
    return "".join(lines), fixed, examples


def repair_cues(package_text: str, script: str) -> tuple:
    """Repairs the storyboard, VIDEO SECTIONS and retention-table cues. Returns (text, fixed, examples)."""
    text, n1, ex1 = repair_cue_table(package_text, "INTEGRATED PRODUCTION STORYBOARD", 3, 4, script)
    text, n2, ex2 = repair_cue_table(text, "VIDEO SECTIONS", 2, 3, script, length=8, max_length=25)
    # The B-roll detail table: | # | Timestamp | ▶️ START CUE | ⏹️ END CUE | …
    text, n4, ex4 = repair_cue_table(text, "AI B-ROLL GENERATION PROMPTS", 2, 3, script)
    n2, ex2 = n2 + n4, ex2 + ex4
    # RETENTION ARCHITECTURE: | Element | Timestamp | Line (first Arabic words) |
    text, n3, ex3 = repair_cue_table(text, "RETENTION ARCHITECTURE", 2, None, script)
    return text, n1 + n2 + n3, ex1 + ex2 + ex3


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
    "VERIFIED SOURCES",
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


def replace_section(text: str, key: str, body: str, before: str = "WHAT CHANGED") -> str:
    """Swaps the body of the part whose heading names `key` (heading kept). If there is
    no such part, adds '### key' before the `before` part, or at the end."""
    parts = split_sections(text)
    for i, (heading, _) in enumerate(parts):
        if heading and key.upper() in heading.upper():
            parts[i] = (heading, "\n" + body.strip() + "\n\n---\n")
            break
    else:
        new = (f"### {key}", "\n" + body.strip() + "\n\n---\n")
        at = next((i for i, (h, _) in enumerate(parts) if h and before.upper() in h.upper()), len(parts))
        parts.insert(at, new)
    out = []
    for heading, part_body in parts:
        if heading:
            out.append(heading)
        out.append(part_body)
    return "\n".join(out)


def embedded_part(text: str) -> str:
    """A step's own output placed inside the document: its top heading dropped (the
    document has one) and its other headings demoted to ####, so none of them can
    start a new top-level part."""
    lines = (text or "").strip().splitlines()
    while lines and (not lines[0].strip() or lines[0].strip() == "---" or re.match(r"^#{1,3}\s", lines[0])):
        lines.pop(0)
    lines = [re.sub(r"^#{1,3}\s+", "#### ", l) for l in lines]
    return "\n".join(lines).strip()


def approved_broll_table(broll_prompts: str) -> str:
    """broll_prompt_generator's output, ready to sit under the document's B-roll heading."""
    return embedded_part(broll_prompts)


def _cells(line: str) -> list:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def broll_rows(text: str) -> list:
    """Rows of the B-roll prompt table (the first table with a PROMPT column), as
    [{num, time, start, end, kind ('image'/'video'/'drawing'/''), prompt}]."""
    lines = (text or "").splitlines()
    out, cols = [], None
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("|"):
            if cols and out:
                break
            continue
        cells = _cells(s)
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            if cols is None and i:
                head = [c.upper() for c in _cells(lines[i - 1])]
                if any("PROMPT" in h for h in head):
                    def col(*names, head=head):
                        return next((k for k, h in enumerate(head) if any(n in h for n in names)), None)
                    cols = {"num": col("#"), "time": col("TIMESTAMP", "TIME"), "start": col("START"),
                            "end": col("END"), "type": col("TYPE", "LAYER"), "prompt": col("PROMPT")}
            continue
        if not cols or i + 1 < len(lines) and re.fullmatch(r"\|?[\s:|-]+\|?", lines[i + 1].strip() or "x"):
            continue                         # a header row
        get = lambda k: cells[cols[k]] if cols.get(k) is not None and cols[k] < len(cells) else ""
        kind_cell = get("type")
        kind = ("drawing" if "DRAW" in kind_cell.upper() else "image" if ("🖼" in kind_cell or "IMAGE" in kind_cell.upper())
                else "video" if ("🎬" in kind_cell or "VIDEO" in kind_cell.upper()) else "")
        prompt = get("prompt") or max(cells, key=len, default="")
        out.append({"num": re.sub(r"\D", "", get("num")), "time": get("time"), "start": get("start"),
                    "end": get("end"), "kind": kind, "prompt": prompt})
    return out


def plain_prompt(prompt: str) -> str:
    """A generator-ready prompt: no markdown marks, <br> tags or escaped pipes."""
    p = prompt.replace("\\|", "/").replace('\\"', '"').strip().strip('"\'`')
    p = re.sub(r"^(?:🎬|🖼️)?\s*(?:Video|Image)\s*\|?\s*", "", p, flags=re.IGNORECASE)
    p = re.sub(r"\*\*|__|<br\s*/?>", " ", p)
    return re.sub(r"\s{2,}", " ", p).replace(" :", ":").strip()


def fill_broll_rows(package_text: str, broll_table: str) -> str:
    """Storyboard B-ROLL rows that name "B-roll #N" get the approved prompt N copied in."""
    prompts = {r["num"]: plain_prompt(r["prompt"]).replace("|", "/") for r in broll_rows(broll_table) if r["num"]}
    if not prompts:
        return package_text
    out, in_storyboard = [], False
    for line in package_text.splitlines(keepends=True):
        if _is_part_heading(line.rstrip("\n")):
            in_storyboard = "INTEGRATED PRODUCTION STORYBOARD" in line.upper()
        elif in_storyboard and line.lstrip().startswith("|") and "B-ROLL" in line.upper():
            line = re.sub(r"B-?roll\s*#\s*(\d+)",
                          lambda m: prompts.get(m.group(1), m.group(0)), line, flags=re.IGNORECASE)
        out.append(line)
    return "".join(out)


_META_LINE = re.compile(r"^\s*[*_]*[A-Za-z][A-Za-z0-9 /&()\-]{1,40}[*_]*\s*:")
_SUMMARY_HEAD = re.compile(r"^\s*[#*_ ]*[A-Z][A-Z /&\-]{2,40}(SUMMARY|NOTES|LOG|CHANGES)[*_ ]*:?\s*[*_]*\s*$")


def filming_script(script: str) -> str:
    """The script the presenter reads: script_refinement's output without its
    metadata block (title, word count, self-given scores) and its closing summary."""
    lines = (script or "").strip().splitlines()
    # Leading "Key: value" block, up to the first '---'.
    if "---" in [l.strip() for l in lines]:
        first_rule = [l.strip() for l in lines].index("---")
        head = [l for l in lines[:first_rule] if l.strip()]
        if head and all(_META_LINE.match(l) or l.lstrip().startswith("#") for l in head):
            lines = lines[first_rule + 1:]
    # Closing "POLISH SUMMARY:" (or similar) block.
    for i, line in enumerate(lines):
        if _SUMMARY_HEAD.match(line):
            lines = lines[:i]
            break
    # A part heading inside the script would split the document; keep it as bold text.
    lines = [f"**{re.sub(r'^#+\s*', '', l).strip()}**" if _is_part_heading(l) else l for l in lines]
    return _strip_rules("\n".join(lines))


def table_rows(block: str) -> list:
    """Data rows of the first markdown table in block, as lists of cells. A table the
    model split with blank lines, sub-headings (#### ACT 2) or a repeated header row
    is read as one; a different table after it is not."""
    rows, header, last_pipe = [], None, None
    for line in (block or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        # Split on unescaped pipes only; models often escape pipes inside a cell as \|
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s.strip().strip("|"))]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            this_header = [c.lower() for c in (last_pipe or [])]
            if header is None:
                header = this_header
            else:
                if rows and rows[-1] is last_pipe:
                    rows.pop()              # that line was the next table's header, not data
                if this_header != header:
                    break                   # a different table starts here
            last_pipe = None
            continue
        if header is not None and [c.lower() for c in cells] != header:
            rows.append(cells)
        last_pipe = cells
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


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _parse_duration(s: str) -> int:
    """'1:30', '1m 40s', '1.5 min', '90 sec', '45 ثانية', 'دقيقة ونص' → seconds."""
    s = (s or "").lower().translate(_AR_DIGITS)
    m = re.search(r"(\d+):(\d{1,2})", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    mins = re.search(r"(\d+(?:\.\d+)?)\s*(?:m\b|min|mins|minute|minutes|دقيقة|دقائق|دقايق|د\b)", s)
    secs = re.search(r"(\d+(?:\.\d+)?)\s*(?:s\b|sec|secs|second|seconds|ثانية|ثواني|ثوان|ث\b)", s)
    total = (float(mins.group(1)) * 60 if mins else 0) + (float(secs.group(1)) if secs else 0)
    if not mins and re.search(r"دقيقة\s*و\s*نص", s):
        total += 90
    elif not mins and re.search(r"(?<!\d\s)دقيقة", s) and not secs:
        total += 60
    return int(round(total))


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


# An old chapter line in any form: "0:00 …", "- 0:00 …", "**0:00** …", "[00:00]", Arabic digits.
_CHAPTER_LINE = re.compile(r"^\s*(?:[-*•]\s*)?(?:\*\*)?\[?[\d٠-٩]{1,2}:[\d٠-٩]{2}")


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
            while j < len(lines) and (not lines[j].strip() or _CHAPTER_LINE.match(lines[j]) or lines[j].strip().startswith("[")):
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


def quality_warning(state: Optional[dict]) -> str:
    """One line when the script's quality loop ended without passing its medical gates."""
    state = state or {}
    if not state.get("quality_revision_count"):
        return ""
    if (state.get("quality_grade") == "PASS" and state.get("medical_accuracy_pass")
            and state.get("truth_pass")):
        return ""
    return (f"⚠️ The medical quality gate did not pass after {state.get('quality_revision_count')} rounds "
            f"(fidelity {state.get('fidelity_score', '?')}/10, medical accuracy "
            f"{'pass' if state.get('medical_accuracy_pass') else 'FAIL'}, truth {state.get('truth_score', '?')}/10"
            f"{'' if state.get('truth_pass') else ' FAIL'}): review the script against the fact-check "
            "reports in checkpoint.json before filming.")


def production_warning(state: Optional[dict]) -> str:
    """One line when the post-production review ended without passing, with what is left to fix."""
    state = state or {}
    if not state.get("production_revision_count") or state.get("production_grade") == "PASS":
        return ""
    from ..nodes.production import _video_minutes, broll_engagement_problems   # (avoids an import cycle)
    left = broll_engagement_problems(state.get("broll_prompts", ""), state.get("text_animation_overlay", ""),
                                     _video_minutes(state))
    return (f"⚠️ The post-production review did not pass after {state.get('production_revision_count')} rounds; "
            "check the critique in checkpoint.json (production_critique_output)"
            + (". Still to fix by hand: " + " · ".join(left) if left else "."))


def add_research_sources(package: str, references: list, key_refs: list) -> str:
    """For a video built from a research dossier: the full reference list goes into the
    document, and the key sources into the YouTube description (before its closing fence)."""
    if not references:
        return package
    text = package.rstrip() + "\n\n---\n\n### 📚 VERIFIED SOURCES\n\n" + "\n".join(f"- {r}" for r in references) + "\n"
    if key_refs:
        block = "\n📚 المصادر:\n" + "\n".join("- " + re.sub(r"^\[S\d+\]\s*", "", r) for r in key_refs) + "\n"
        m = re.search(r"(Description[^\n]*\n(?:(?!```)[^\n]*\n)*?```[^\n]*\n)(.*?)(\n```)", text, re.IGNORECASE | re.DOTALL)
        if m:
            text = text[:m.end(2)] + "\n" + block.rstrip("\n") + text[m.end(2):]
    return text


def package_warning(state: Optional[dict]) -> str:
    """One line when the assembled document still failed its own checks after the retries."""
    left = (state or {}).get("final_package_issues") or []
    if not left:
        return ""
    return ("⚠️ The final document still has problems after its retries (fix by hand or re-run): "
            + " · ".join(str(i)[:220] for i in left[:6]))


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
    # Anything not placed above stays in the appendix — including text the model
    # put before the first part heading.
    leftovers = [(h or "### OTHER NOTES", _strip_rules(b)) for i, (h, b) in enumerate(sections)
                 if i not in used and len(_strip_rules(b)) > 20]

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
    for warning in (quality_warning(state), production_warning(state), package_warning(state)):
        if warning:
            md += ["", f"> {warning}"]
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
    ab_set = state.get("packaging_ab_test_set") or ""
    if isinstance(ab_set, (list, tuple)):
        ab_set = "\n".join(f"{i}. {x}" for i, x in enumerate(ab_set, 1))
    ab_set = str(ab_set).strip()
    if (title or ab_set) and "A/B TEST SET" not in final_package.upper():
        md += ["", "### 🧪 A/B TEST SET (YouTube Test & compare)", ""]
        if title:
            md += [f"**Recommended title:** {title}", ""]
        if ab_set:
            md += [ab_set]
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
