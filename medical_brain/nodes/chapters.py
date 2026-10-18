"""Chapter-mode helpers: the script blueprint, the shared script memory, and the
small text utilities that let the writers work chapter by chapter.

Everything here is pure (no model calls, no state mutation) so it can be tested on
its own; the nodes in script_writing.py/analysis.py call into it.
"""
import contextvars
import json
import re
from concurrent.futures import ThreadPoolExecutor

from ..exports.workbook import _META_LINE, _SUMMARY_HEAD, normalize_ar, spoken_text
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


def missing_chapters(blueprint, script: str) -> list:
    """Titles of blueprint chapters whose "=== <title> ===" header is not in `script`
    (the format split_by_chapters/join_chapters use). [] when not chapter mode."""
    if not chapter_mode_on(blueprint):
        return []
    present = {title for title, _ in split_by_chapters(script or "") if title}
    missing = []
    for chapter in (blueprint.get("chapters") or []):
        if not isinstance(chapter, dict):
            continue
        title = str(chapter.get("title", "") or "").strip()
        if title and title not in present:
            missing.append(title)
    return missing


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
    out, next_num, last_end = [], {}, {}
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
        # A header must stay when other text (headings, notes) separates this table from
        # the previous one of the same shape: without it the table cannot be parsed (a
        # chapter-mode run lost every chapter-2–4 transition and overlay table this way).
        has_header = len(block) > 1 and is_separator_row(block[1])
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
        elif has_header and any(l.strip() and not _HEADER_RE.match(l) for l in lines[last_end[sig]:i]):
            out.extend(block[:data_start])
        last_end[sig] = j
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


# A repeated passage is the same sentence said twice: identical normalized words, or
# word sets that overlap this much (order ignored, so a lightly reworded repeat counts).
REPEAT_JACCARD = 0.8
_SENTENCE_SPLIT_RE = re.compile(r"[.!?؟…]+")


def _segment_sentences(text: str) -> list:
    """(normalized_words, original_sentence) for each sentence of a segment, with [cues]
    and stage directions dropped (spoken_text) and normalized like the cue checks."""
    out = []
    for chunk in _SENTENCE_SPLIT_RE.split(spoken_text(text)):
        chunk = chunk.strip()
        if not chunk:
            continue
        words = normalize_ar(chunk).split()
        if words:
            out.append((words, chunk))
    return out


def repeated_sentences(script: str, min_words: int = 7) -> list:
    """[(sentence, first_location, second_location)] for sentences the script says more
    than once (identical normalized word sequences, or word sets with Jaccard >= 0.8):
    the first occurrence is kept, the later one is the one to remove or rephrase. Only
    sentences of at least `min_words` words are compared. A location is the
    "=== chapter ===" title, or "OPENING"/"CLOSING" for the parts before the first and
    after the last header."""
    located = []
    for title, text in split_by_chapters(script or ""):
        label = title.strip() if title and title.strip() else ("OPENING" if not located else "CLOSING")
        for words, raw in _segment_sentences(text):
            if len(words) >= min_words:
                located.append((words, raw, label))
    out = []
    for i in range(len(located)):
        wi, rawi, loci = located[i]
        seti = set(wi)
        for j in range(i + 1, len(located)):
            wj, _, locj = located[j]
            if wi == wj:
                match = True
            else:
                union = seti | set(wj)
                match = bool(union) and len(seti & set(wj)) / len(union) >= REPEAT_JACCARD
            if match:
                out.append((rawi, loci, locj))
                break
    return out


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
    """A chapter/segment's spoken word count: chapter headers and markdown label lines
    are not spoken, and neither are the non-spoken parts workbook.spoken_text drops
    ([cues], line-opening/long stage directions, "=== headers ===", "**speaker labels:**"
    and markdown markers). Short inline parentheses that are spoken terms stay."""
    lines = [line for line in (text or "").split("\n")
             if not _HEADER_RE.match(line) and not _MD_HEADING_RE.match(line)]
    return len(spoken_text("\n".join(lines)).split())


# --------------------------------------------------------------------------
# narrative/word-quality checks used by self_critique
# --------------------------------------------------------------------------
# A trailing CTA/outro section is not "content": it must not make the ending look heavy.
_CTA_TITLE_RE = re.compile(r"CTA|OUTRO|CLOSING", re.IGNORECASE)
HEAVY_ENDING_SHARE = 0.30      # the last content section may not hold > 30% of the words
HEAVY_ENDING_RATIO = 1.6       # nor be > 1.6 × the average section


# "Timestamp: 11:35" or a bare "| 11:00 |" / "| 0:00–3:00 |" field inside a header.
_HEADER_TIME_RE = re.compile(r"((?:Timestamp\s*:|\|)\s*)~?\d{1,2}:\d{2}(?:\s*[–-]\s*\d{1,2}:\d{2})?(?=\s*(?:\||===|$))",
                             re.IGNORECASE)


def retime_header_labels(script: str, wpm: float = 135) -> str:
    """Rewrite the planned "Timestamp: M:SS" inside "=== … ===" headers to where the
    section really starts (spoken words before it / wpm). A script grown from 11 to 16
    minutes kept "OUTRO | Timestamp: 11:35" and the production judge took it as the end."""
    if not script or not wpm:
        return script
    out, words = [], 0
    for title, text in split_by_chapters(script):
        if title and _HEADER_TIME_RE.search(text.split("\n", 1)[0]):
            head, sep, rest = text.partition("\n")
            head = _HEADER_TIME_RE.sub(lambda m: m.group(1) + fmt_time(words / wpm * 60.0), head, count=1)
            text = head + sep + rest
        out.append(text)
        words += len(spoken_text(text).split())
    return "".join(out)


def content_sections(script: str) -> list:
    """[(title, text)] for the "=== … ===" content sections, without the preamble and
    without a trailing CTA/OUTRO/CLOSING section (its title contains one of those)."""
    # Production labels ("=== DISCLAIMER | Timestamp … ===") are not content chapters.
    titled = [(title, text) for title, text in split_by_chapters(script or "")
              if title and "|" not in title and not _PRODUCTION_LABEL_RE.search(title)]
    while titled and _CTA_TITLE_RE.search(titled[-1][0]):
        titled.pop()
    return titled


def heavy_ending(script: str):
    """None when the ending is balanced; a dict when the last content section is too
    heavy. The final chapter should be the lightest (≤ 20% of the words), so a last
    section over 30% of the spoken words (or 1.6× the average) means the ending was
    crammed. A script with fewer than two content sections has no "ending section" to
    compare."""
    sections = content_sections(script)
    if len(sections) < 2:
        return None
    words = [spoken_words(text) for _, text in sections]
    total = sum(words)
    if total <= 0:
        return None
    # Tiny bridge/intro sections (< 80 words) are not chapters: counting them dragged the
    # average down until a normal chapter looked 2× too big.
    real = [w for w in words if w >= 80] or words
    average = sum(real) / len(real)
    # The crammed part is often the second-to-last section, with a short prevention or
    # summary section after it (a real run: red flags + medicines + diseases = 32% in
    # act 4, then a light act 5), so the last two content sections are both checked.
    # The last section fails on either rule; the one before it only on the ratio rule
    # (with three sections a ~33% share is simply normal).
    checks = [(len(words) - 1, True)]
    if len(words) >= 3:
        checks.append((len(words) - 2, False))
    for index, share_rule in checks:
        size = words[index]
        if (share_rule and size > total * HEAVY_ENDING_SHARE) or size > average * HEAVY_ENDING_RATIO:
            return {"title": sections[index][0], "words": size, "total": total,
                    "average": average, "share": size / total, "ratio": size / average}
    return None


# A Latin word of 3+ letters outside parentheses is "bare" (the viewer cannot parse it).
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_PAREN_GROUP_RE = re.compile(r"\(([^()]*)\)")


def english_terms(script: str) -> tuple:
    """(bare_words, paren_groups) in the spoken text only.

    bare_words: Latin words of 3+ letters outside parentheses (a bare English term in
    an Arabic sentence). paren_groups: the contents of parenthesised groups that contain
    Latin letters (the allowed "Arabic explanation (English term)" form). [cues],
    === headers === and stage directions are dropped by spoken_text first."""
    text = spoken_text(script or "")
    groups = [m.group(1).strip() for m in _PAREN_GROUP_RE.finditer(text)
              if re.search(r"[A-Za-z]", m.group(1))]
    outside = _PAREN_GROUP_RE.sub(" ", text)
    return _LATIN_WORD_RE.findall(outside), groups


_COMMENT_MARKERS_RE = re.compile(r"كومنت|تعليق|اكتبلي|اكتبولي")
COMMENT_QUESTION_FRACTION = 0.4


def has_early_comment_question(script: str, fraction: float = COMMENT_QUESTION_FRACTION) -> bool:
    """True when a comment/engagement question sits in a sentence before `fraction` of
    the spoken words (the signature early-comment device)."""
    text = spoken_text(script or "")
    total = len(text.split())
    if total <= 0:
        return False
    position = 0
    for sentence in re.split(r"(?<=[.!?؟…])\s+|\n+", text):
        if _COMMENT_MARKERS_RE.search(sentence) and position < total * fraction:
            return True
        position += len(sentence.split())
    return False


# Assembly sometimes writes section headers as "[ACT 1 — title]" instead of
# "=== title ===": the chapter-aware checks (heavy ending, missing chapters) then saw
# no chapters at all in a real run.
_BRACKET_HEADER_RE = re.compile(r"^\s*\[\s*((?:ACT|CHAPTER|PART|الفصل)\s*\d+\s*[—:\-–|].*?)\s*\]\s*$", re.IGNORECASE)
# "[الفخ الأول: مش كل صداع جيوب | 1:15–3:15 | Energy: 3]": a titled section with its
# planned time range. The planned times go stale when the script grows, so they are dropped.
_TIMED_BRACKET_HEADER_RE = re.compile(
    r"^\s*\[\s*([^\[\]|]{2,90}?)\s*\|\s*~?\d{1,2}:\d{2}(?:\s*[–\-—]\s*~?\d{1,2}:\d{2})?\s*(?:\|[^\[\]]*)?\]\s*$")
_CUE_LABEL_RE = re.compile(r"^[A-Z][A-Z0-9 \-/&]*:")
# "=== [CHAPTER 1 | العنوان] ===": brackets inside the header and a "|" after the number
# (a "|" title reads as a production label "DISCLAIMER | Timestamp …" and is skipped).
_WRAPPED_HEADER_RE = re.compile(r"^\s*===\s*\[\s*(.*?)\s*\](?:\s*\|[^=\[\]]*)?\s*===\s*$")
# "**الفصل الأول: الرقم العالي | 1:10–3:30**": a whole bold line used as a section title
# (single-script mode). Speaker labels ("**د. أحمد حسني:**") end with a colon and stay.
_SECTION_WORD_RE = re.compile(r"^(?:الفصل|المقدمة|الخاتمة|المقدّمة|ACT\b|CHAPTER\b|PART\b|INTRO|OUTRO)", re.IGNORECASE)
# A bold cue line ("**[VISUAL NOTE: …]**", "**[نبرة واثقة]**", "**SFX: …**") is not a title.
BOLD_TITLE_MAX = 12
_BOLD_CUE_RE = re.compile(r"^(?:\[|\(|[A-Z][A-Z0-9 \-/&+]*:|(?:نبرة|وقفة|طاقة|نظرة|ميل|ابتسامة|صوت|حركة)\b)")
_BOLD_TITLE_RE = re.compile(r"^\s*\*\*([^*\n]{3,110}?)\*\*\s*$")
_TIME_OR_ENERGY_RE = re.compile(r"^(?:timestamp\s*:\s*)?~?\d{1,2}:\d{2}|^energy\b|^\d+(?:\.\d+)?\s*/\s*5$", re.IGNORECASE)
_NUMBERED_PIPE_RE = re.compile(r"^((?:ACT|CHAPTER|PART|الفصل)\s*\d+)\s*\|\s*(.+)$", re.IGNORECASE)


def normalize_section_headers(text: str) -> str:
    """Rewrite bracketed act/chapter header lines ("[ACT 2 — اللغز]") to "=== ACT 2 — اللغز ===",
    and timed section lines ("[العنوان | 1:15–3:15 | Energy: 3]") to "=== العنوان ===". Cue lines ([VISUAL NOTE: …], [SFX: …], [HOOK]) are left alone."""
    if not text:
        return text
    lines = text.split("\n")
    # A model wrapped a header in a markdown code fence: the ``` lines would print as-is.
    fenced = [k for k, line in enumerate(lines) if re.fullmatch(r"\s*```[\w-]*\s*", line)]
    changed = bool(fenced)
    for k in reversed(fenced):
        del lines[k]
    bold_converted = []
    for i, line in enumerate(lines):
        m = _WRAPPED_HEADER_RE.match(line) or (_HEADER_RE.match(line) if "|" in line else None)
        if m:
            title = m.group(1).strip()
            numbered = _NUMBERED_PIPE_RE.match(title)
            if numbered:
                title = f"{numbered.group(1)} — {numbered.group(2).strip()}"
            elif "|" in title:
                # "title | 0:20 | Energy: 3": planned time/energy fields go stale; keep the title.
                head, *fields = [f.strip() for f in title.split("|")]
                if head and fields and all(_TIME_OR_ENERGY_RE.search(f) for f in fields) \
                        and not _PRODUCTION_LABEL_RE.search(head):
                    title = head
            if f"=== {title} ===" != line.strip():
                lines[i] = f"=== {title} ==="
                changed = True
            continue
        m = _BOLD_TITLE_RE.match(line)
        if m and not _BOLD_CUE_RE.match(m.group(1).strip()):
            head, *fields = [f.strip() for f in m.group(1).split("|")]
            words = head.split()
            titled = bool(fields) or bool(_SECTION_WORD_RE.search(head))
            if (head and not head.endswith((":", "：")) and len(words) <= 14
                    and all(_TIME_OR_ENERGY_RE.search(f) for f in fields)
                    and (titled or (len(words) >= 2 and not re.search(r"[.!?؟…]$", head)))):
                bold_converted.append((i, lines[i]))
                lines[i] = f"=== {head} ==="
                changed = True
                continue
        m = _BRACKET_HEADER_RE.match(line)
        if m:
            title = m.group(1).strip()
            numbered = _NUMBERED_PIPE_RE.match(title)
            lines[i] = f"=== {numbered.group(1)} — {numbered.group(2).strip() if numbered else ''} ===" \
                if numbered else f"=== {title} ==="
            changed = True
            continue
        m = _TIMED_BRACKET_HEADER_RE.match(line)
        if m and not _CUE_LABEL_RE.match(m.group(1).strip()):
            lines[i] = f"=== {m.group(1).strip()} ==="
            changed = True
    # A script has a handful of chapters: dozens of bold lines are emphasis or cues the
    # rule misread (a run turned every bold cue into a section) — undo those.
    if len(bold_converted) > BOLD_TITLE_MAX:
        for k, original in bold_converted:
            lines[k] = original
        changed = bool(fenced) or any(l != o for l, o in zip(lines, text.split("\n")))
    return "\n".join(lines) if changed else text


_SCRIPT_KEYS = ("script", "verified_script", "revised_script", "refined_script", "text", "content", "output")


def unwrap_script_text(text: str) -> str:
    """The script inside a double-encoded answer. A verifier once returned
    '{"type":"json_object","script":"[HOOK]\\n\\n…"}' as its corrected script: the escaped
    "\\n" read as thousands of English words and the script lost every line break and
    header. JSON objects give back their script field; text full of literal "\\n" with
    almost no real line breaks is decoded. Anything else comes back unchanged."""
    raw = text or ""
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[\w-]*\s*|\s*```$", "", stripped).strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in _SCRIPT_KEYS:
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return unwrap_script_text(value)
                if isinstance(value, dict):
                    return unwrap_script_text(json.dumps(value, ensure_ascii=False))
    literal = raw.count("\\n")
    if literal >= 20 and raw.count("\n") * 4 < literal:
        return raw.replace("\\n", "\n").replace('\\"', '"').replace("\\t", "\t")
    return raw


def strip_script_extras(text: str) -> str:
    """Drop the non-spoken wrapper a polishing pass may add: a leading metadata block
    ("SCRIPT: …", "Word Count: …" up to the first ---) and a trailing
    "POLISH SUMMARY:"-style block. "=== chapter ===" headers, [cues] and the spoken
    script are kept. Text with neither wrapper comes back unchanged."""
    original = text or ""
    if not original.strip():
        return original
    unwrapped = unwrap_script_text(original)
    if unwrapped != original:
        return strip_script_extras(unwrapped)
    raw = original.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = raw.split("\n")
    changed = False
    rules = [i for i, line in enumerate(lines) if line.strip() == "---"]
    if rules:
        first = rules[0]
        head = [line for line in lines[:first] if line.strip()]
        if head and all(_META_LINE.match(line) or line.lstrip().startswith("#") for line in head):
            lines = lines[first + 1:]
            changed = True
    for i, line in enumerate(lines):
        if _SUMMARY_HEAD.match(line):
            lines = lines[:i]
            changed = True
            break
    if not changed:
        return original
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# The chosen frame disclaimer belongs in the opening. A later pass sometimes carries it
# to the closing; code moves a drifted paragraph back (and self_critique flags one that
# cannot be found at all).
DISCLAIMER_JACCARD = 0.4
DISCLAIMER_EARLY_FRACTION = 0.15   # hook + intro fit; a disclaimer after chapter 1 (22%) is late


def _paragraph_spans(text: str) -> list:
    """[(start_line, end_line)] for each non-blank paragraph (a run of non-blank lines)."""
    lines = (text or "").split("\n")
    spans, i = [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip():
            j += 1
        spans.append((i, j))
        i = j
    return spans


def _best_disclaimer(script: str, disclaimer: str):
    """(spans, lines, index, start_word) for the paragraph that best matches the frame
    disclaimer (word-set Jaccard >= DISCLAIMER_JACCARD; cue and '---' lines ignored).
    index is None when no paragraph matches; start_word is the spoken words before it."""
    lines = (script or "").split("\n")
    spans = _paragraph_spans(script)
    target = _word_set(disclaimer)
    best, best_score, best_start, words = None, 0.0, 0, 0
    for index, (start, end) in enumerate(spans):
        score = _jaccard(target, _word_set("\n".join(lines[start:end])))
        if score > best_score:
            best, best_score, best_start = index, score, words
        words += spoken_words("\n".join(lines[start:end]))
    if best is None or best_score < DISCLAIMER_JACCARD:
        return spans, lines, None, 0
    return spans, lines, best, best_start


# A reworded disclaimer still says "this is not a substitute for your doctor": the
# polishing passes paraphrase the frame's wording, and a word-overlap check alone called
# a present disclaimer "missing" and forced a revision round in a real run.
_DISCLAIMER_GIST_RE = re.compile(r"(مش بديل|ليس بديل|لا يغني|مايغنيش|ما يغنيش|بديل عن)")
_DISCLAIMER_WHO_RE = re.compile(r"(دكتور|طبيب|الكشف|استشار)")


def long_disclaimer(script: str, limit: int = 50):
    """(words, paragraph) for the first disclaimer paragraph longer than `limit` spoken
    words, else None. A one-line warm disclaimer was polished into an 80-word formal
    block ("خريطة طريق علمية… تحت إشراف طبي متخصص") in a real run."""
    for paragraph in re.split(r"\n\s*\n", script or ""):
        text = spoken_text(paragraph)
        if _DISCLAIMER_GIST_RE.search(text) and _DISCLAIMER_WHO_RE.search(text):
            n = len(text.split())
            if n > limit:
                return n, " ".join(text.split()[:25])
    return None


def has_disclaimer(script: str, disclaimer: str) -> bool:
    """True when some paragraph matches the frame disclaimer (word-set Jaccard >= 0.4),
    or a spoken sentence in the first half says it is not a substitute for a doctor."""
    if _best_disclaimer(script, disclaimer)[2] is not None:
        return True
    text = spoken_text(script or "")
    half = len(text.split()) / 2
    position = 0
    for sentence in re.split(r"(?<=[.!?؟…])\s+|\n+", text):
        if position > half:
            break
        if _DISCLAIMER_GIST_RE.search(sentence) and _DISCLAIMER_WHO_RE.search(sentence):
            return True
        position += len(sentence.split())
    return False


def _is_spoken_line(line: str) -> bool:
    """A line the presenter says: Arabic text that is not a [cue], `cue`, *(stage
    direction)*, header or "Key: value" metadata line."""
    s = (line or "").strip().strip('"“”')
    if not s or s.startswith(("[", "`", "*(", "(", "#", "===")) or s == "---":
        return False
    if re.match(r"^[A-Za-z][A-Za-z /&()\-]{1,40}:", s):
        return False
    return bool(re.search(r"[؀-ۿ]", s))


def move_disclaimer_early(script: str, disclaimer: str) -> tuple:
    """Move a drifted frame disclaimer back to the opening.

    The paragraph whose word set best matches the disclaimer (Jaccard >= 0.4) is moved
    to just before the hook's "---" line when that line lies in the first 30% of the
    script's spoken words, otherwise to just after the first paragraph. Returns
    (script, moved): unchanged when the disclaimer is empty, still early, or not found."""
    spans, lines, index, start_word = _best_disclaimer(script, disclaimer)
    if index is None:
        return script, False
    total = max(1, spoken_words(script))
    if start_word <= total * DISCLAIMER_EARLY_FRACTION:
        return script, False
    start, end = spans[index]
    paragraph = "\n".join(lines[start:end])
    del lines[start:end]
    # The hook ends at the first "---" AFTER the first spoken line: a script can open with
    # a metadata block ("SCRIPT: …", "Word Count: …") closed by its own "---".
    first_spoken = next((k for k, line in enumerate(lines) if _is_spoken_line(line)), 0)
    separator = next((k for k, line in enumerate(lines)
                      if k > first_spoken and line.strip() == "---"), None)
    early = separator is not None and \
        spoken_words("\n".join(lines[:separator])) <= total * DISCLAIMER_EARLY_FRACTION
    if early:
        insert = ["", paragraph, ""] if separator > 0 and lines[separator - 1].strip() else [paragraph, ""]
        lines[separator:separator] = insert
    else:
        spans_after = _paragraph_spans("\n".join(lines))
        first_end = spans_after[0][1] if spans_after else len(lines)
        lines[first_end:first_end] = ["", paragraph]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n"), True


def too_short_rewrite(new_text: str, reference_words: int, floor: float = 0.4) -> bool:
    """True when an LLM answer that REPLACES a chapter/segment came back near-empty:
    the reference is long enough to judge (>= 60 words) and the new text is below
    `floor` × the reference. A canned proxy error ("I encountered an error…") trips
    this, so the caller can retry or keep the old text instead of shipping the stub."""
    try:
        reference_words = int(float(reference_words or 0))
    except (TypeError, ValueError):
        return False
    if reference_words < 60:
        return False
    return spoken_words(new_text) < reference_words * floor


# --------------------------------------------------------------------------
# missing-chapter repair + one sign-off only
# --------------------------------------------------------------------------
# A restored chapter must be a real chapter, not a stub or a canned error.
RESTORE_MIN_WORDS = 60
# A sign-off is only looked for in the tail: the opening and body may mention
# subscribing/commenting without being the closing.
SIGNOFF_TAIL = 0.65
# The closing tail after the last '---' must be short; a long one is real content.
CLOSING_MAX_WORDS = 250
SIGNOFF_JACCARD = 0.5

# Sign-off markers, normalised Arabic + English; each tuple is ONE marker, any variant
# counts once. normalize_ar lowercases nothing, so the English variants are lower-case.
_SIGNOFF_MARKERS = (
    ("اشترك", "الاشتراك"),
    ("الجرس",),
    ("لايك", "الاعجاب"),
    ("التعليقات",),
    ("الحلقه الجايه", "الحلقه القادمه"),
    ("دمتم",),
    ("اشوفكم",),
    ("مع السلامه", "سلام"),
    ("subscribe",),
    ("comment",),
    ("see you",),
)
# A farewell marker ends the video: on its own it makes a paragraph a sign-off.
_FAREWELL_MARKERS = (
    ("دمتم",),
    ("اشوفكم",),
    ("الحلقه الجايه", "الحلقه القادمه"),
    ("مع السلامه",),
    ("see you",),
)


def _detection_text(paragraph: str) -> str:
    """A paragraph's text for sign-off detection: [cue] and '---' lines are not spoken."""
    kept = []
    for line in (paragraph or "").splitlines():
        stripped = line.strip()
        if re.fullmatch(r"\[.*\]", stripped) or re.fullmatch(r"-{3,}", stripped):
            continue
        kept.append(line)
    return "\n".join(kept)


def _marker_hits(normalized: str, groups) -> int:
    """How many marker GROUPS appear (any variant) in the normalized text."""
    return sum(1 for group in groups if any(variant in normalized for variant in group))


def _is_signoff_paragraph(paragraph: str) -> tuple:
    """(is_signoff, has_farewell) for one paragraph: >= 2 sign-off markers, or a
    farewell marker. Cue and '---' lines never count."""
    normalized = normalize_ar(_detection_text(paragraph)).lower()
    if not normalized:
        return False, False
    farewell = bool(_marker_hits(normalized, _FAREWELL_MARKERS))
    return (farewell or _marker_hits(normalized, _SIGNOFF_MARKERS) >= 2), farewell


def _word_set(text: str) -> set:
    return set(normalize_ar(_detection_text(text)).split())


def _jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def farewell_paragraphs(script: str) -> list:
    """The paragraphs that carry a farewell marker (cue and '---' lines ignored). Used by
    self_critique to name a script that still ends with more than one good-bye."""
    out = []
    for paragraph in re.split(r"\n\s*\n", script or ""):
        if _is_signoff_paragraph(paragraph)[1]:
            text = " ".join(_detection_text(paragraph).split()).strip()
            if text:
                out.append(text)
    return out


SHARE_LINE = "ابعت الفيديو ده لحد في البيت بيعاني من ده."
_SHARE_RE = re.compile(r"(ابعت|ابعتوا|شارك|شاركوا|ابعتي)\s+(ال)?(فيديو|الفيديو|ده)")


def ensure_share_line(script: str) -> tuple:
    """(script, added). The closing must carry the family-share line («ابعت الفيديو ده
    لحد في البيت بيعاني من ده»); judges failed the callback-closing criterion when a
    revision dropped it. When the last third of the spoken script has no share request,
    the line is inserted as its own paragraph right before the final sign-off paragraph.
    Short texts (< 300 spoken words) and scripts without a sign-off are left alone."""
    text = script or ""
    if not text.strip():
        return script, False
    spoken = spoken_text(text)
    if len(spoken.split()) < 300:                    # not a full video script
        return script, False
    if _SHARE_RE.search(normalize_ar(spoken[int(len(spoken) * 0.66):])) or _SHARE_RE.search(spoken[int(len(spoken) * 0.66):]):
        return script, False
    paragraphs = re.split(r"(\n\s*\n)", text)
    for k in range(len(paragraphs) - 1, -1, -1):
        if paragraphs[k].strip() and _is_signoff_paragraph(paragraphs[k])[1]:
            # Right before the good-bye sentence itself: a closing paragraph often holds
            # the recap and the last question too, and the share line comes after those.
            parts = re.split(r"(?<=[.!?؟…])(\s+)", paragraphs[k])
            for j in range(len(parts)):
                if parts[j].strip() and _is_signoff_paragraph(parts[j])[1]:
                    if j == 0:
                        break
                    parts.insert(j, SHARE_LINE + "\n")
                    paragraphs[k] = "".join(parts)
                    return "".join(paragraphs), True
            paragraphs.insert(k, SHARE_LINE + "\n\n")
            return "".join(paragraphs), True
    return script, False                             # no closing to put it before


def _collapse_double_dashes(text: str) -> str:
    """A deletion can leave two '---' lines (with only blanks between) where one was:
    collapse the duplicate."""
    out = []
    for line in (text or "").split("\n"):
        if re.fullmatch(r"\s*-{3,}\s*", line):
            k = len(out) - 1
            while k >= 0 and out[k].strip() == "":
                k -= 1
            if k >= 0 and re.fullmatch(r"\s*-{3,}\s*", out[k]):
                del out[k + 1:]
                continue
        out.append(line)
    return "\n".join(out)


CLOSING_SECTION_TITLE = "CLOSING — الخاتمة"


def ensure_closing_section(script: str, closing_hint: str = "") -> tuple:
    """(script, added). Puts the closing (recap, call to action, share line, sign-off)
    under its own "=== CLOSING — الخاتمة ===" header when it sits inside the last
    chapter: there it counted as chapter content and made every last chapter look ~150+
    words over plan. Scripts that already have a closing/CTA section are left alone."""
    parts = split_by_chapters(script or "")
    titled = [(k, t) for k, (t, _) in enumerate(parts) if t]
    if not titled or any(_CTA_TITLE_RE.search(t) for _, t in titled):
        return script, False
    k, title = titled[-1]
    body, closing = split_closing(parts[k][1], closing_hint)
    if not closing.strip() or not body.strip():
        return script, False
    parts[k] = (title, body.rstrip() + f"\n\n=== {CLOSING_SECTION_TITLE} ===\n" + closing.strip() + "\n")
    return "".join(text for _, text in parts), True


def split_closing(text: str, closing_hint: str = "") -> tuple:
    """(body, closing): separate a script's closing/sign-off tail from its body.

    With a '---' separator the closing is the tail after the LAST separator when the tail
    is short (<= 250 spoken words) and carries a sign-off marker. Without one, the trailing
    paragraphs that carry sign-off markers and overlap `closing_hint` (word-set Jaccard
    >= 0.5) are the closing. Returns (text, "") when nothing looks like a closing, so an
    ordinary chapter is never cut."""
    raw = text or ""
    if not raw.strip():
        return text, ""
    lines = raw.splitlines()
    last_sep = None
    for i, line in enumerate(lines):
        if re.fullmatch(r"\s*-{3,}\s*", line):
            last_sep = i
    if last_sep is not None:
        tail = "\n".join(lines[last_sep + 1:]).strip()
        body = "\n".join(lines[:last_sep]).rstrip()
        if body and tail and spoken_words(tail) <= CLOSING_MAX_WORDS:
            if _marker_hits(normalize_ar(_detection_text(tail)).lower(), _SIGNOFF_MARKERS):
                return body, tail
    hint_words = _word_set(closing_hint)
    if not hint_words:
        return raw, ""
    paragraphs = re.split(r"\n\s*\n", raw)
    idx = len(paragraphs) - 1
    closing = []
    while idx >= 0:
        is_signoff, _ = _is_signoff_paragraph(paragraphs[idx])
        if not is_signoff or _jaccard(_word_set(paragraphs[idx]), hint_words) < SIGNOFF_JACCARD:
            break
        closing.insert(0, paragraphs[idx])
        idx -= 1
    if not closing or idx < 0:
        return raw, ""
    body = "\n\n".join(paragraphs[:idx + 1]).strip()
    if not body:
        return raw, ""
    return body, "\n\n".join(closing).strip()


def dedupe_signoffs(script: str) -> tuple:
    """(script, removed_count): keep ONE closing in the tail.

    A sign-off paragraph is a blank-line-separated paragraph (cue and '---' lines ignored)
    with >= 2 sign-off markers or a farewell marker, and only paragraphs in the last 35% of
    the spoken words are considered. With two or more, the last contiguous run is the real
    closing; every sign-off before the FINAL one is deleted when it carries a farewell or
    repeats the closing's words (Jaccard >= 0.5). A bare comment question that resembles
    neither stays. The byte-identical script comes back when nothing is removed, and the
    first 65% is never touched."""
    text = script or ""
    if not text.strip():
        return script, 0
    paragraphs = re.split(r"\n\s*\n", text)
    spoken = [spoken_words(p) for p in paragraphs]
    total = sum(spoken)
    if total <= 0:
        return script, 0
    threshold = total * SIGNOFF_TAIL
    signoffs = []                       # [(paragraph index, has_farewell)]
    cumulative = 0
    for i, paragraph in enumerate(paragraphs):
        is_signoff, farewell = _is_signoff_paragraph(paragraph)
        if cumulative >= threshold and is_signoff:
            signoffs.append((i, farewell))
        cumulative += spoken[i]
    if len(signoffs) < 2:
        return script, 0

    block_end = signoffs[-1][0]
    block_start = block_end
    j = len(signoffs) - 1
    while j - 1 >= 0 and signoffs[j - 1][0] == signoffs[j][0] - 1:
        block_start = signoffs[j - 1][0]
        j -= 1
    kept_words = _word_set("\n\n".join(paragraphs[block_start:block_end + 1]))

    remove = set()
    for i, farewell in signoffs:
        if i >= block_end:
            continue                    # the final closing is always kept
        if i >= block_start:
            # Inside the final closing block a paragraph is part of that closing (a
            # comment question before the good-bye); only an extra good-bye goes.
            if farewell:
                remove.add(i)
            continue
        # A sign-off before the closing block is a duplicate of it when it says a
        # good-bye or repeats the closing's words; a bare comment question that does
        # neither is left alone.
        if farewell or _jaccard(_word_set(paragraphs[i]), kept_words) >= SIGNOFF_JACCARD:
            remove.add(i)
    if not remove:
        return script, 0
    result = "\n\n".join(p for i, p in enumerate(paragraphs) if i not in remove)
    return _collapse_double_dashes(result), len(remove)


def restore_missing_chapters(blueprint, script: str, *sources: str) -> tuple:
    """(script, restored_titles): put a dropped blueprint chapter back.

    Only in chapter mode. For each blueprint chapter whose "=== title ===" header is absent
    from `script`, copy that chapter (header included) from the FIRST source that has it with
    >= 60 spoken words (a proxy-error stub is skipped), then insert it at its blueprint
    position: before the first present chapter that follows it, or after the last present
    chapter that precedes it — but before any trailing closing (split_closing). A script with
    no chapter headers gets the chapters before its closing. The script is rebuilt with
    join_chapters; restored_titles is [] when nothing changed.
    """
    if not chapter_mode_on(blueprint):
        return script, []
    missing = missing_chapters(blueprint, script)
    if not missing:
        return script, []

    source_maps = []
    for source in sources:
        if isinstance(source, str) and source.strip():
            source_maps.append({t: x for t, x in split_by_chapters(source) if t})

    chapter_texts = {}
    for title in missing:
        for mapping in source_maps:
            text = mapping.get(title)
            if text is not None and spoken_words(text) >= RESTORE_MIN_WORDS:
                chapter_texts[title] = text
                break
    if not chapter_texts:
        return script, []

    parts = split_by_chapters(script or "")
    preamble = parts[0][1] if parts and not parts[0][0] else ""
    present = [[t, x] for t, x in parts if t]
    titles = [(str(c.get("title", "") or "").strip(), c)
              for c in (blueprint.get("chapters") or []) if isinstance(c, dict)]
    order = {title: i for i, (title, _) in enumerate(titles) if title}

    # Keep a trailing closing out of the way so an appended chapter lands before it.
    closing_tail = ""
    if present:
        body, closing = split_closing(present[-1][1])
        if closing:
            present[-1][1] = body
            closing_tail = closing
    elif preamble:
        body, closing = split_closing(preamble)
        if closing:
            preamble = body
            closing_tail = closing

    restored_titles = []
    for title in missing:
        text = chapter_texts.get(title)
        if text is None:
            continue
        position = order.get(title)
        if position is None:
            continue
        insert_at = len(present)
        for idx, (present_title, _) in enumerate(present):
            present_position = order.get(present_title)
            if present_position is None or present_position > position:
                insert_at = idx
                break
        present.insert(insert_at, [title, str(text).strip()])
        restored_titles.append(title)
    if not restored_titles:
        return script, []

    out = []
    if preamble.strip():
        out.append(preamble)
    out.extend(text for _, text in present)
    if closing_tail.strip():
        out.append(closing_tail)
    return join_chapters(out), restored_titles


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
