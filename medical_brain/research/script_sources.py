"""The Script Sources Guide (Layer 3): links the FINAL script's sentences to the
verified facts it was written from, AFTER the script is written (no tags inside the
script — the final-package cue checks need the exact words).

Flow (all built by code over the finished script):
  1. split_script(final_package)      → the spoken sentences, numbered, with their section
  2. map_sources(sentences, research) → one LLM call per batch: sentence → fact ids,
                                        plus the medical claims no fact supports
  3. build_sources_guide(...)         → numbered sources (by first appearance), grouped
                                        by section, with what each supports + stats
  4. render_markdown(guide)           → the appendix for the document
  5. description_block(guide)         → the short Arabic list for the YouTube description

The full research reference list stays in the research folder; only the sources the
finished script actually uses reach the video document.
"""
import re

from ..exports import workbook as ew
from ..llm import call_llm_json
from ..prompts import load_prompt
from ..utils.llm_json import UnreadableAnswer
from . import dossier as rd
from .nodes import _parallel
from .sources import evidence

# Act headers in the production script ("=== الفصل الأول ==="), with or without bold.
_ACT_HEADER = re.compile(r"^\s*(?:\*\*)?\s*={2,}\s*(.*?)\s*={2,}\s*(?:\*\*)?\s*$")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
# Split after sentence-ending punctuation, but only when whitespace or the end follows,
# so decimals ("94.2%") and abbreviations stay inside their sentence.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟…])(?=\s|$)")

DEFAULT_SECTION = "Script"

GUIDE_HEADING = "### 📚 مصادر هذا الفيديو — Sources for this video"
CLAIMS_HEADING = "### ⚠️ ادعاءات تحتاج مراجعة قبل التصوير — Claims to check before filming"

MAX_SNIPPET = 120


def _header_title(line: str) -> str:
    """The section name a header line carries, or '' when the line is not a header."""
    m = _ACT_HEADER.match(line)
    if m:
        return m.group(1).strip(" *#")
    m = _MD_HEADER.match(line)
    if m:
        return m.group(1).strip(" *#")
    return ""


def _snippet(text: str) -> str:
    """A short Arabic snippet of a sentence, at most MAX_SNIPPET characters."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if len(s) <= MAX_SNIPPET:
        return s
    return s[:MAX_SNIPPET - 1].rstrip() + "…"


def split_script(script_text: str) -> list:
    """The spoken script as numbered sentences: [{n, section, text}].

    The current section follows the act headers ("=== title ===" lines) or markdown
    headings; the default is "Script". [cues] and (stage directions) are removed with
    workbook.spoken_text before splitting on . ! ? ؟ … and newlines. Sentences shorter
    than four words are dropped. Numbering starts at 1.
    """
    out, section, n = [], DEFAULT_SECTION, 0
    for line in (script_text or "").splitlines():
        title = _header_title(line)
        if title:
            section = title
            continue
        spoken = ew.spoken_text(line)
        for piece in _SENTENCE_SPLIT.split(spoken):
            text = re.sub(r"\s+", " ", piece).strip()
            if len(text.split()) < 4:
                continue
            n += 1
            out.append({"n": n, "section": section, "text": text})
    return out


def _as_int(value):
    m = re.search(r"\d+", str(value if value is not None else ""))
    return int(m.group()) if m else None


def _evidence_lines(facts: list) -> list:
    return [f"{f['id']} [{f.get('strength', 'n/a') or 'n/a'}] {f.get('claim', '')}" for f in facts]


def _batch_user_prompt(batch: list, evidence_lines: list) -> str:
    sentences = "\n".join(f"[{s['n']}] ({s['section']}) {s['text']}" for s in batch)
    return f"""SCRIPT SENTENCES:
{sentences}

EVIDENCE:
{chr(10).join(evidence_lines)}

Output ONLY the JSON object."""


def map_sources(sentences: list, research_state: dict, batch_size: int = 60, workers=None) -> dict:
    """Map every sentence to the verified facts that support it, one call per batch.

    The system prompt is prompts/source_mapper.md; the user prompt carries the batch's
    SCRIPT SENTENCES and ALL verified facts as EVIDENCE lines. Answers are validated in
    code: sentence numbers must exist in that batch and fact ids must be verified facts
    (accepting "[F12]"); anything else is dropped. An unreadable answer is retried once,
    then that batch contributes nothing. Returns {"links": {n: [fact ids]},
    "unsupported": [{n, text, section, why}]}. `workers=None` uses the research settings'
    parallel_calls (the provider concurrency limit is enforced by the router itself).
    """
    sentences = list(sentences or [])
    if not sentences:
        return {"links": {}, "unsupported": []}
    if workers is None:
        try:
            workers = int((research_state.get("settings") or {}).get("parallel_calls", 4))
        except (TypeError, ValueError):
            workers = 4
    facts = rd.verified_facts(research_state)
    by_id = {f["id"]: f for f in facts}
    by_n = {s["n"]: s for s in sentences}
    evidence_lines = _evidence_lines(facts)
    system_prompt = load_prompt("source_mapper")
    batches = [sentences[i:i + batch_size] for i in range(0, len(sentences), batch_size)]

    def map_batch(batch: list) -> dict:
        batch_ns = {s["n"] for s in batch}
        data = None
        for attempt in range(2):
            try:
                data, _ = call_llm_json(system_prompt, _batch_user_prompt(batch, evidence_lines),
                                        temperature=0, max_tokens=6000, required=["links"])
                break
            except UnreadableAnswer as e:
                data = None
                if attempt == 0:
                    print(f"  📚 sources: answer unreadable ({e}); asking once more")
                else:
                    print(f"  📚 sources: still unreadable ({e}); skipping this batch")
        links, unsupported = {}, []
        if isinstance(data, dict):
            for item in data.get("links") or []:
                if not isinstance(item, dict):
                    continue
                n = _as_int(item.get("sentence"))
                if n not in batch_ns:
                    continue
                fids = item.get("facts") or []
                if isinstance(fids, str):
                    fids = [fids]
                clean = []
                for fid in fids:
                    fid = str(fid).strip().strip("[]").upper()
                    if fid in by_id and fid not in clean:
                        clean.append(fid)
                if clean:
                    links.setdefault(n, [])
                    links[n] += [f for f in clean if f not in links[n]]
            for item in data.get("unsupported") or []:
                if not isinstance(item, dict):
                    continue
                n = _as_int(item.get("sentence"))
                if n not in batch_ns:
                    continue
                why = str(item.get("why", "") or "").strip()
                unsupported.append({"n": n, "text": by_n[n]["text"], "section": by_n[n]["section"], "why": why})
        return {"links": links, "unsupported": unsupported}

    results = _parallel(map_batch, batches, workers)
    links, unsupported, seen_n = {}, [], set()
    for res in results:
        for n, fids in res["links"].items():
            links.setdefault(n, [])
            links[n] += [f for f in fids if f not in links[n]]
        for u in res["unsupported"]:
            if u["n"] in seen_n:
                continue
            seen_n.add(u["n"])
            unsupported.append(u)
    # Time-to-effect / certainty wording is flagged by code too: the model mapping passed
    # "تفك الانقباض في ثواني" as supported because stretching itself had evidence.
    from ..utils.overclaims import overclaim_in
    for sent in sentences:
        hit = overclaim_in(sent.get("text", ""))
        if hit and sent["n"] not in seen_n:
            seen_n.add(sent["n"])
            unsupported.append({"n": sent["n"], "text": sent["text"], "section": sent.get("section", ""),
                                "why": f"time-to-effect / certainty wording «{hit}» — check that the "
                                       "evidence states it, or soften it"})
    unsupported.sort(key=lambda u: u["n"])
    return {"links": links, "unsupported": unsupported}


def _ref_line(source: dict) -> str:
    """title — journal/site, year — link (like dossier.reference_lines, without the id)."""
    meta = ", ".join(str(x) for x in (source.get("journal") or source.get("origin"), source.get("year") or "")
                     if x)
    parts = [str(source.get("title", "") or "").strip(),
             meta,
             str(source.get("url", "") or "").strip()]
    return " — ".join(p for p in parts if p)


def _fact_source_ids(fact: dict) -> list:
    ids = list(fact.get("source_ids", []))
    for m in fact.get("also_supported_by", []) or []:
        ids += list(m.get("source_ids", []))
    return ids


def build_sources_guide(sentences: list, mapping: dict, research_state: dict) -> dict:
    """The numbered sources the finished script uses, grouped by script section.

    Sources are the union of the linked facts' source_ids (including also_supported_by),
    numbered in order of first appearance in the script. Each entry: {n, ref, level,
    sections, supports}. `sections` records the guide's script-section order (an extra
    key so the renderer can group without the mapping). Stats cover linked medical
    sentences, unsupported claims, sources used and total sentences.
    """
    sentences = list(sentences or [])
    links = (mapping or {}).get("links") or {}
    unsupported = list((mapping or {}).get("unsupported") or [])
    facts = rd.verified_facts(research_state)
    by_id = {f["id"]: f for f in facts}
    sources = research_state.get("sources", {}) or {}
    entries, section_order = {}, []

    for s in sentences:
        sec = s.get("section") or DEFAULT_SECTION
        if sec not in section_order:
            section_order.append(sec)
        seen_here = []
        for fid in links.get(s["n"], []) or []:
            fact = by_id.get(fid)
            if not fact:
                continue
            for sid in _fact_source_ids(fact):
                if sid in sources and sid not in seen_here:
                    seen_here.append(sid)
        for sid in seen_here:
            if sid not in entries:
                src = sources[sid]
                entries[sid] = {"n": len(entries) + 1, "ref": _ref_line(src),
                                "level": evidence.LEVELS.get(src.get("level", 6), ""),
                                "level_num": src.get("level", 6), "sections": [],
                                "supports": [], "sentence_count": 0}
            entry = entries[sid]
            if sec not in entry["sections"]:
                entry["sections"].append(sec)
            entry["sentence_count"] += 1
            if len(entry["supports"]) < 2:
                snippet = _snippet(s["text"])
                if snippet and snippet not in entry["supports"]:
                    entry["supports"].append(snippet)

    ordered = [entries[sid] for sid in sorted(entries, key=lambda x: entries[x]["n"])]
    linked = sum(1 for s in sentences
                 if any(fid in by_id for fid in (links.get(s["n"]) or [])))
    return {"entries": ordered, "sections": section_order, "unsupported": unsupported,
            "stats": {"sentences": len(sentences), "linked": linked,
                      "unsupported": len(unsupported), "sources": len(ordered)}}


def render_markdown(guide: dict) -> str:
    """The document appendix: sources grouped by script section, then (only when needed)
    the claims-to-check block for the doctor — never put that block in the video."""
    guide = guide or {}
    entries = guide.get("entries") or []
    lines = [GUIDE_HEADING, ""]
    section_order = list(guide.get("sections") or [])
    if not section_order and entries:
        section_order = [DEFAULT_SECTION]
    for sec in section_order:
        in_section = [e for e in entries if sec in e.get("sections", [])]
        if not in_section:
            continue
        lines += [f"#### {sec}", ""]
        for e in in_section:
            level = f" · {e['level']}" if e.get("level") else ""
            lines.append(f"**[{e['n']}]** {e.get('ref', '')}{level}")
            for snippet in (e.get("supports") or [])[:2]:
                lines.append(f"يدعم: «{snippet}»")
            lines.append("")
    unsupported = guide.get("unsupported") or []
    if unsupported:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", CLAIMS_HEADING, ""]
        for u in unsupported:
            lines.append(f"- _{u.get('section', '')}_ «{u.get('text', '')}» — {u.get('why', '')}")
    return "\n".join(lines).rstrip() + "\n"


def description_block(guide: dict, n: int = 6) -> str:
    """The short Arabic source list for the YouTube description: the top `n` sources by
    number of linked sentences, then best evidence level. No [n] numbering noise."""
    entries = (guide or {}).get("entries") or []
    if not entries:
        return ""
    ranked = sorted(entries, key=lambda e: (-e.get("sentence_count", 0), e.get("level_num", 6), e.get("n", 0)))
    lines = ["📚 المصادر:"]
    lines += [f"- {e.get('ref', '')}" for e in ranked[:n] if e.get("ref")]
    return "\n".join(lines)
