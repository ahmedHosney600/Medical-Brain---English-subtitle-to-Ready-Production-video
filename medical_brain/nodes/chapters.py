"""Chapter-mode helpers: the script blueprint, the shared script memory, and the
small text utilities that let the writers work chapter by chapter.

Everything here is pure (no model calls, no state mutation) so it can be tested on
its own; the nodes in script_writing.py/analysis.py call into it.
"""
import contextvars
import json
import re
from concurrent.futures import ThreadPoolExecutor

from ..llm.progress import quiet
from ..utils.llm_json import parse_llm_json

# A chapter header line: "=== <title> ===" (the writer's chapter format).
_HEADER_RE = re.compile(r"^\s*===\s*(.*?)\s*===\s*$")
_MEMORY_RE = re.compile(r"```memory\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
_NOTES_RE = re.compile(r"^\s*={0,3}\s*SEGMENT NOTES\s*={0,3}\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
_SEGMENT_NOTES_HEADING_RE = re.compile(r"^\s*#{1,6}\s*SEGMENT NOTES\b", re.IGNORECASE | re.MULTILINE)

# The whole-script cleaner: markdown headings, production labels and model notes.
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+\S")
_NOTE_LINE_RE = re.compile(r"^\s*#{0,6}\s*(?:Register Applied|Score|Dialect|Warmth|Flags)\b\s*:",
                           re.IGNORECASE)
_NOTES_SECTION_RE = re.compile(r"^\s*[=#]*\s*SEGMENT NOTES\b", re.IGNORECASE | re.MULTILINE)
_PRODUCTION_LABEL_RE = re.compile(
    r"\b(DISCLAIMER|CTA|OUTRO|HOOK|OPENING|CLOSING|INTRO|RETENTION|SEGMENT|NOTES|"
    r"TIMESTAMP|SCORE|DIALECT|WARMTH|FLAGS|REGISTER)\b", re.IGNORECASE)


# --------------------------------------------------------------------------
# token budget
# --------------------------------------------------------------------------
def budget_for_words(words) -> int:
    """max_tokens for a call that must produce `words` Arabic words: Arabic tokenises
    heavily, and the answer also carries headers, cues and the memory block."""
    try:
        words = int(float(words or 0))
    except (TypeError, ValueError):
        words = 0
    return max(3000, int(words * 3.2) + 2500)


# --------------------------------------------------------------------------
# script blueprint
# --------------------------------------------------------------------------
def parse_blueprint(text: str) -> dict:
    """The blueprint JSON from a strategy plan: the last ```json block under
    "Script Blueprint" (or, failing that, the last fenced block). {} when unreadable."""
    raw = text or ""
    marker = raw.find("Script Blueprint")
    scope = raw[marker:] if marker >= 0 else raw

    # Prefer the last fenced json block in scope.
    blocks = re.findall(r"```(?:json|JSON)?\s*\n?(.*?)```", scope, re.DOTALL)
    candidates = [b for b in blocks if "{" in b]
    if candidates:
        try:
            data = parse_llm_json(candidates[-1])
            if isinstance(data, dict):
                return data
        except ValueError:
            pass

    # An unclosed fence (a cut-off answer still starts with ```json): take from the
    # last opening fence to the end and let the tolerant parser repair it.
    idx = scope.rfind("```")
    if idx >= 0:
        tail = re.sub(r"^```(?:json|JSON)?\s*", "", scope[idx:])
        tail = re.sub(r"\s*```.*$", "", tail, flags=re.DOTALL)
        try:
            data = parse_llm_json(tail)
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {}


def valid_blueprint(blueprint) -> bool:
    """A usable blueprint: a non-empty chapters list, each with an id, a title and
    words > 0."""
    if not isinstance(blueprint, dict):
        return False
    chapters = blueprint.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        return False
    seen = set()
    for ch in chapters:
        if not isinstance(ch, dict):
            return False
        cid = str(ch.get("id", "") or "").strip()
        title = str(ch.get("title", "") or "").strip()
        try:
            words = int(float(ch.get("words", 0) or 0))
        except (TypeError, ValueError):
            return False
        if not cid or not title or words <= 0 or cid in seen:
            return False
        seen.add(cid)
    return True


CHAPTER_MODE_MIN_WORDS = 1600   # ~11-12 min of Egyptian Arabic; shorter bodies fit one call


def chapter_mode_on(blueprint) -> bool:
    """Chapter mode only when the blueprint has at least two chapters AND the body is long
    enough to need them. The planner sometimes splits a 10-minute video into two chapters;
    such a short body keeps the original single-call behaviour."""
    if not valid_blueprint(blueprint):
        return False
    chapters = blueprint.get("chapters") or []
    words = sum(int(float(c.get("words", 0) or 0)) for c in chapters)
    return len(chapters) >= 2 and words >= CHAPTER_MODE_MIN_WORDS


def blueprint_signature(blueprint) -> str:
    """A stable key for a blueprint (so a checkpoint is only reused for the same plan)."""
    try:
        chapters = blueprint.get("chapters") or []
        shape = [(str(c.get("id", "")), str(c.get("title", "")), c.get("words", 0))
                 for c in chapters if isinstance(c, dict)]
        return json.dumps(shape, ensure_ascii=False)
    except Exception:
        return ""


# --------------------------------------------------------------------------
# script memory
# --------------------------------------------------------------------------
def _as_list(value) -> list:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dedupe(items) -> list:
    out, seen = [], set()
    for x in items:
        key = str(x).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(str(x).strip())
    return out


def split_memory(text: str):
    """(chapter_text, memory_dict). Removes the writer's ```memory block from the text."""
    raw = text or ""
    match = _MEMORY_RE.search(raw)
    if not match:
        return raw.strip(), {}
    memory = {}
    try:
        parsed = parse_llm_json(match.group(1))
        if isinstance(parsed, dict):
            memory = parsed
    except ValueError:
        memory = {}
    cleaned = (raw[:match.start()] + raw[match.end():]).strip()
    return cleaned, memory


def merge_memory(memory, new=None, chapter_id: str = "") -> dict:
    """The running memory after one more chapter: terms_defined unique by term,
    facts_used unioned, open loops = opened − closed, plus per-chapter summaries."""
    memory = memory or {}
    new = new or {}

    seen_terms, terms = set(), []
    for source in (memory.get("terms_defined"), new.get("terms_defined")):
        for term in (source or []):
            if isinstance(term, dict):
                name = str(term.get("term", "") or "").strip()
                phrasing = str(term.get("phrasing", "") or "").strip()
            else:
                name, phrasing = str(term or "").strip(), ""
            if not name or name.lower() in seen_terms:
                continue
            seen_terms.add(name.lower())
            terms.append({"term": name, "phrasing": phrasing})

    def union(field):
        return _dedupe(_as_list(memory.get(field)) + _as_list(new.get(field)))

    all_closed = union("loops_closed")
    closed_keys = {x.lower() for x in all_closed}
    all_open = _dedupe(_as_list(memory.get("loops_open")) + _as_list(new.get("loops_opened")))
    open_loops = [x for x in all_open if x.lower() not in closed_keys]

    summaries = []
    for source in (memory.get("summaries") or []):
        if isinstance(source, dict):
            summaries.append(dict(source))
        elif str(source).strip():
            summaries.append({"chapter": "", "summary": str(source).strip()})
    summary = str(new.get("summary", "") or "").strip()
    if summary:
        summaries.append({"chapter": chapter_id, "summary": summary})

    return {"terms_defined": terms, "facts_used": union("facts_used"),
            "loops_open": open_loops, "loops_closed": all_closed, "summaries": summaries}


def render_memory(memory) -> str:
    """The memory block handed to the next chapter writer."""
    memory = memory or {}
    lines = []
    terms = memory.get("terms_defined") or []
    if terms:
        lines.append("TERMS ALREADY DEFINED (use the same wording; never re-define):")
        for term in terms:
            if isinstance(term, dict):
                name, phrasing = term.get("term", ""), term.get("phrasing", "")
            else:
                name, phrasing = term, ""
            lines.append(f"- {name}" + (f" — said as: {phrasing}" if phrasing else ""))
    facts = memory.get("facts_used") or []
    if facts:
        lines.append("FACTS ALREADY USED (don't repeat; a short callback only):")
        lines.append("- " + "; ".join(str(f) for f in facts))
    if memory.get("loops_open"):
        lines.append("OPEN LOOPS STILL TO PAY OFF:")
        lines.append("- " + "; ".join(str(l) for l in memory["loops_open"]))
    if memory.get("loops_closed"):
        lines.append("LOOPS ALREADY CLOSED:")
        lines.append("- " + "; ".join(str(l) for l in memory["loops_closed"]))
    summaries = memory.get("summaries") or []
    if summaries:
        lines.append("WHAT EARLIER CHAPTERS ALREADY SAID:")
        for s in summaries:
            if isinstance(s, dict):
                cid, summary = str(s.get("chapter", "") or ""), str(s.get("summary", "") or "")
                lines.append(f"- {cid}: {summary}" if cid else f"- {summary}")
            else:
                lines.append(f"- {s}")
    return "\n".join(lines)


def last_lines(text: str, n_words: int = 150) -> str:
    """The last `n_words` words of a text (the handover sentence for the next writer)."""
    words = (text or "").split()
    return " ".join(words[-n_words:]) if n_words and n_words > 0 else ""


def first_lines(text: str, n_words: int = 120) -> str:
    """The first `n_words` words of a text (for flow into a segment)."""
    words = (text or "").split()
    return " ".join(words[:n_words])


def ensure_header(text: str, title: str) -> str:
    """Guarantees a "=== title ===" header on a chapter text the model returned."""
    text = (text or "").strip()
    if re.match(r"^\s*===", text):
        return text
    return f"=== {title} ===\n{text}" if title else text


# --------------------------------------------------------------------------
# chapter timing (used by the whole-script production steps and the package)
# --------------------------------------------------------------------------
def fmt_time(seconds) -> str:
    """Seconds -> "M:SS" for prompts."""
    try:
        total = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    return f"{total // 60}:{total % 60:02d}"


def chapter_spans(script: str, blueprint) -> list:
    """[{id, title, text, start_s, end_s}] — one span per "=== title ===" chapter in
    `script`. The preamble (hook) joins the first chapter's span and trailing text
    (CTA/closing) joins the last, so the spans cover the whole spoken script. Start/end
    times come from cumulative word counts at the blueprint's words_per_minute."""
    parts = split_by_chapters(script or "")
    chapters_list = [c for c in ((blueprint or {}).get("chapters") or []) if isinstance(c, dict)]

    preamble, segments = "", []
    for title, text in parts:
        if title:
            segments.append([title, text])
        else:
            preamble = (preamble + "\n\n" + text).strip() if preamble else text
    if not segments:
        if (script or "").strip():
            segments = [["", script or ""]]
        else:
            return []
    if preamble and segments:
        segments[0][1] = (preamble.strip() + "\n\n" + segments[0][1]).strip()

    try:
        wpm = float((blueprint or {}).get("words_per_minute", 135) or 135)
    except (TypeError, ValueError):
        wpm = 135
    if wpm <= 0:
        wpm = 135

    spans, cum = [], 0
    for i, (title, text) in enumerate(segments):
        chapter = next((c for c in chapters_list
                        if str(c.get("title", "") or "").strip() == title), None)
        if chapter is None and i < len(chapters_list):
            chapter = chapters_list[i]
        title = title or str((chapter or {}).get("title", "") or "").strip()
        cid = str((chapter or {}).get("id", "") or "").strip() or f"C{i + 1}"
        words = len(text.split())
        start, cum = cum, cum + words
        spans.append({"id": cid, "title": title or cid, "text": text,
                      "start_s": start / wpm * 60.0, "end_s": cum / wpm * 60.0})
    return spans


def chapter_section(joined: str, title: str) -> str:
    """The "=== title ===" section of a joined per-chapter step output ("" if absent)."""
    if not title:
        return ""
    for name, text in split_by_chapters(joined or ""):
        if name == title:
            return text.strip()
    return ""


# --------------------------------------------------------------------------
# markdown table joining: one header row, "#" renumbered globally
# --------------------------------------------------------------------------
def split_pipes(line: str) -> list:
    return re.split(r"(?<!\\)\|", line)


def is_separator_row(line: str) -> bool:
    cells = split_pipes(line.strip())
    cells = [c for c in cells if c.strip()]
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells)


def renumber_tables(text: str, row_callback=None) -> str:
    """Dedupes repeated markdown table header/separator rows (the first one wins) and
    renumbers the "#" column 1..N globally per distinct table header. `row_callback`
    (header_signature, new_number, original_line_index) is called for every numbered
    data row, so callers can map global numbers back to their chapter."""
    lines = (text or "").splitlines()
    out, next_num = [], {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip().startswith("|"):
            out.append(line)
            i += 1
            continue
        j = i
        while j < n and lines[j].strip().startswith("|"):
            j += 1
        block = lines[i:j]
        separator = len(block) > 1 and is_separator_row(block[1])
        data_start = 2 if separator else 1
        raw_cells = split_pipes(block[0])
        cells = raw_cells[1:-1] if (len(raw_cells) > 1 and not raw_cells[0].strip()
                                    and not raw_cells[-1].strip()) else raw_cells
        sig = tuple(c.strip() for c in cells)
        has_num = any(c == "#" for c in sig)
        num_col = next((k for k, c in enumerate(sig) if c == "#"), None)
        first_seen = sig not in next_num
        if first_seen:
            next_num[sig] = 1
            out.extend(block[:data_start])
        for k in range(data_start, len(block)):
            row = block[k]
            if has_num and not is_separator_row(row):
                split = split_pipes(row)
                offset = 1 if (split and not split[0].strip()) else 0
                pos = offset + num_col
                if pos < len(split):
                    number = next_num[sig]
                    split[pos] = f" {number} "
                    row = "|".join(split)
                    next_num[sig] = number + 1
                    if row_callback:
                        row_callback(sig, number, i + k)
            out.append(row)
        i = j
    return "\n".join(out)


# --------------------------------------------------------------------------
# chapter / segment text
# --------------------------------------------------------------------------
def split_by_chapters(script: str) -> list:
    """[(title, text)] split on "=== title ===" headers. Text before the first
    header is returned as ("", preamble); each segment's text includes its header
    line, so concatenating the texts reproduces the script."""
    text = script or ""
    headers, offset = [], 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        match = _HEADER_RE.match(stripped)
        if match:
            headers.append((offset, match.group(1).strip()))
        offset += len(line)
    if not headers:
        return [("", text)]
    parts = []
    if headers[0][0] > 0:
        parts.append(("", text[:headers[0][0]]))
    for i, (start, title) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        parts.append((title, text[start:end]))
    return parts


def join_chapters(parts) -> str:
    """Join chapter/segment texts (strings or (title, text)) into one script."""
    texts = []
    for part in parts:
        if isinstance(part, (tuple, list)):
            part = part[1] if len(part) > 1 else part[0]
        if part and str(part).strip():
            texts.append(str(part).strip())
    return "\n\n".join(texts)


def split_segment_answer(text: str):
    """(rewritten_segment, notes) — notes is whatever the model wrote after the
    "SEGMENT NOTES" marker; the segment is everything before it."""
    raw = text or ""
    match = _NOTES_RE.search(raw)
    if not match:
        return raw.strip(), ""
    return raw[:match.start()].strip(), raw[match.end():].strip()


def strip_segment_notes(text: str) -> str:
    """The script part of a dialogue output, without the trailing SEGMENT NOTES section."""
    raw = text or ""
    match = _SEGMENT_NOTES_HEADING_RE.search(raw)
    return raw[:match.start()].rstrip() if match else raw


def spoken_words(text: str) -> int:
    """A chapter/segment's spoken word count: its chapter header and any markdown
    label lines are not spoken, so they are not counted."""
    lines = [line for line in (text or "").split("\n")
             if not _HEADER_RE.match(line) and not _MD_HEADING_RE.match(line)]
    return len(" ".join(lines).split())


def clean_spoken_script(text: str) -> str:
    """The spoken script only.

    Keeps the writer's "=== chapter ===" headers but drops everything that is not
    spoken and would bloat the script: markdown headings ("#…"), production labels
    ("=== DISCLAIMER | Timestamp … ==="), the SEGMENT NOTES section and the model's
    score/register note lines ("Register Applied:", "Score:", "Dialect:", …). Runs of
    three or more blank lines collapse to one blank line."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    notes = _NOTES_SECTION_RE.search(raw)
    if notes:
        raw = raw[:notes.start()]
    kept = []
    for line in raw.split("\n"):
        header = _HEADER_RE.match(line)
        if header:
            inner = header.group(1)
            if "|" in inner or _PRODUCTION_LABEL_RE.search(inner):
                continue                      # a production label, not a chapter header
            kept.append(line)
            continue
        if _MD_HEADING_RE.match(line):
            continue
        if _NOTE_LINE_RE.match(line):
            continue
        kept.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return cleaned.strip()


# --------------------------------------------------------------------------
# integrity edits
# --------------------------------------------------------------------------
def _chapter_key(chapter: dict) -> str:
    return str(chapter.get("id", "") or chapter.get("title", "") or "").strip()


# An edit's "find" may differ from the script only in formatting: tatweel, Arabic
# diacritics, punctuation or spacing. These helpers let apply_integrity_edits retry
# with a normalised match instead of skipping the edit.
_TATWEEL = "\u0640"
_DIACRITICS_RE = re.compile(r"[\u064b-\u0652]")
_IGNORED_PUNCT = set("،,.؟?!«»\"'")


def _normalize_with_map(text: str):
    """(normalised, mapping): the text lower-case-free of tatweel, diacritics and
    ignored punctuation, with whitespace collapsed. mapping[i] is the index in the
    original text of the character that produced normalised[i]."""
    out, mapping = [], []
    for i, ch in enumerate(text or ""):
        if ch == _TATWEEL or _DIACRITICS_RE.match(ch):
            continue
        if ch in _IGNORED_PUNCT:
            continue
        if ch.isspace():
            if out and out[-1] == " ":
                continue
            out.append(" ")
            mapping.append(i)
            continue
        out.append(ch)
        mapping.append(i)
    return "".join(out), mapping


def _normalized_span(text: str, find: str):
    """(start, end) in `text` of the one normalised match of `find`, or None when the
    normalised find is empty or occurs zero or more than once."""
    nf, _ = _normalize_with_map(find)
    if not nf.strip():
        return None
    nt, mapping = _normalize_with_map(text)
    if nt.count(nf) != 1:
        return None
    start = nt.index(nf)
    return mapping[start], mapping[start + len(nf) - 1] + 1


def apply_integrity_edits(script: str, blueprint, edits) -> tuple:
    """Apply the integrity editor's targeted edits in code.

    An edit is applied when its "find" occurs exactly once inside the named chapter's
    text. If it occurs zero times, one retry is made after normalising whitespace,
    tatweel, Arabic diacritics and punctuation — and the edit is applied only when
    that leaves exactly one match. Returns (new_script, applied, skipped, log_lines)."""
    chapters = [c for c in ((blueprint or {}).get("chapters") or []) if isinstance(c, dict)]
    segments = split_by_chapters(script)
    chapter_title_text = [(t, x) for (t, x) in segments if t]
    texts = [x for _, x in chapter_title_text]
    applied, skipped, log = 0, 0, []

    def locate(where: str):
        for i, chapter in enumerate(chapters):
            cid = str(chapter.get("id", "") or "").strip()
            title = str(chapter.get("title", "") or "").strip()
            if where and (where == cid or where == title or (title and where in title)):
                return i if i < len(texts) else None
        for i, (title, _) in enumerate(chapter_title_text):
            if where and (where == title or (title and where in title)):
                return i
        return None

    for edit in (edits or []):
        if not isinstance(edit, dict):
            continue
        where = str(edit.get("chapter", "") or "").strip()
        find = str(edit.get("find", "") or "")
        replace = str(edit.get("replace", "") or "")
        if not find:
            skipped += 1
            log.append(f"skipped an edit for '{where}': empty find")
            continue
        idx = locate(where)
        if idx is None:
            skipped += 1
            log.append(f"skipped an edit: no chapter matches '{where}'")
            continue
        count = texts[idx].count(find)
        if count == 1:
            texts[idx] = texts[idx].replace(find, replace)
            applied += 1
            continue
        if count == 0:
            span = _normalized_span(texts[idx], find)
            if span is not None:
                start, end = span
                texts[idx] = texts[idx][:start] + replace + texts[idx][end:]
                applied += 1
                log.append(f"applied an edit in '{where}' after normalising spacing, "
                           "diacritics or punctuation")
                continue
        skipped += 1
        log.append(f"skipped an edit in '{where}': find occurs {count} times (needs exactly one)")

    out, k = [], 0
    for title, text in segments:
        if title:
            out.append(texts[k])
            k += 1
        else:
            out.append(text)
    return join_chapters(out), applied, skipped, log


# --------------------------------------------------------------------------
# small parallel helper (same contextvars fix as the research workflow)
# --------------------------------------------------------------------------
def parallel_map(fn, items, workers: int = 4) -> list:
    """Run fn(item) for every item, results in the original order. A fresh copy of
    the caller's contextvars goes to each task so call_llm still sees current_node
    (model routing). workers <= 1 or a single item keeps the plain loop."""
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(item) for item in items]
    with quiet():
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in items]
            return [f.result() for f in futures]
