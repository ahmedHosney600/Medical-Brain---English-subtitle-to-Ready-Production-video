"""The research steps: plan → collect sources → screen them → extract facts → verify
them → check coverage → write the dossier. A review round (review.md) re-runs the
same steps for the sections the user asked to add or change.

System prompts live in prompts/research/<step>.md."""
import contextvars
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from ..llm import call_llm_json
from ..llm.errors import StepFailed
from ..llm.progress import quiet
from ..paths import PROJECT_ROOT
from ..prompts import load_prompt
from ..utils.llm_json import UnreadableAnswer, pick
from . import sources as src
from .sources import evidence

KINDS = ("fact", "myth", "advice", "red_flag", "debated")
STRENGTHS = ("proven", "probably_helps", "weak_or_disputed", "no_benefit", "harmful", "untested", "n/a")
_STRENGTH_ALIASES = {
    "probably_help": "probably_helps", "probably": "probably_helps",
    "weak_disputed": "weak_or_disputed", "disputed": "weak_or_disputed", "weak": "weak_or_disputed",
    "no_effect": "no_benefit", "no_benefits": "no_benefit",
    "not_studied": "untested", "not_tested": "untested", "no_studies": "untested", "unstudied": "untested",
}


def _norm_strength(value) -> str:
    """The verifier's evidence label, normalised to the allowed set (unknown → "n/a")."""
    s = str(value or "").strip().lower()
    if s in ("", "n/a", "na", "none", "null", "not_applicable"):
        return "n/a"
    s = re.sub(r"\s*/\s*", "_", s)
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    s = _STRENGTH_ALIASES.get(s, s)
    return s if s in STRENGTHS else "n/a"


def _source_names(state: dict, ids) -> str:
    """Human names (journal or origin) for a list of source ids."""
    names = []
    for i in ids:
        s = state.get("sources", {}).get(i) or {}
        name = s.get("journal") or s.get("origin") or i
        if name and name not in names:
            names.append(str(name))
    return ", ".join(names)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _section_ids(n: int) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return letters[n] if n < 26 else letters[n // 26 - 1] + letters[n % 26]


def _new_section(state: dict, idx: int, raw: dict) -> dict:
    lit = [q for q in (pick(raw, "literature_queries", default=[]) or []) if str(q).strip()]
    pub = [q for q in (pick(raw, "public_queries", default=[]) or []) if str(q).strip()]
    return {"id": _section_ids(idx), "title": str(pick(raw, "title", default="Section")).strip(),
            "questions": [str(q) for q in (pick(raw, "questions", default=[]) or [])],
            "queries": {"literature": lit, "public": pub},
            "pending": {"literature": list(lit), "public": list(pub)},
            "candidates": [], "source_ids": []}


def _sections(state: dict, ids) -> list:
    wanted = set(ids or [])
    return [s for s in state.get("outline", []) if not wanted or s["id"] in wanted]


def _source_block(state: dict, sid: str, limit: int = 3500, borrowed: bool = False) -> str:
    s = state["sources"][sid]
    head = f"[{sid}] {s['title']} — {s.get('journal') or s.get('origin')}, {s.get('year') or 'n.d.'} — " \
           f"{evidence.LEVELS.get(s.get('level', 6), '')}"
    if borrowed:
        head += " (from another section)"
    if s.get("fulltext") is True and limit == 3500:   # an enriched source carries a much longer text
        limit = 9000
    return head + "\n" + (s.get("text") or "")[:limit]


_WORD = re.compile(r"[a-z]{4,}")


def _word_set(text: str) -> set:
    """The 4+ letter words of a phrase, for matching a brief point to a section."""
    return set(_WORD.findall((text or "").lower()))


# Function words and generic question verbs carry no matching signal, so they are removed
# before comparing a section's questions with a source's title and text.
_STOPWORDS = {
    "what", "when", "where", "which", "while", "with", "without", "would", "could",
    "should", "their", "there", "these", "those", "they", "them", "then", "than",
    "this", "that", "have", "has", "had", "been", "being", "were", "was", "are",
    "from", "into", "about", "after", "before", "because", "between", "during",
    "over", "under", "your", "does", "will", "still", "also", "more", "most",
    "some", "such", "only", "other", "others", "many", "much", "very", "make",
    "makes", "made", "take", "taken", "takes", "using", "used", "uses", "often",
    "the", "and", "for", "not", "can", "cannot", "may", "might", "must", "how",
    "why", "who", "any", "all", "its", "per", "via",
}


def _matching_words(text: str) -> set:
    """The 4+ letter words of a phrase, minus the stopwords above (cross-section match)."""
    return _word_set(text) - _STOPWORDS


def _cross_section_sources(state: dict, sec: dict, limit: int = None) -> list:
    """Sources already collected for other sections that match this section's questions.

    Pure: every source in ``state["sources"]`` that is not already in ``sec["candidates"]``
    is scored by the number of distinct 4+ letter question/title words it shares with the
    source's title plus the first ~1500 characters of its text (stopwords removed). Ties
    go to the higher evidence score. Only sources scoring at least 3 are returned, best
    first, at most ``limit`` (``settings["cross_section_sources"]`` by default)."""
    settings = state.get("settings") or {}
    if limit is None:
        limit = settings.get("cross_section_sources", 4)
    limit = int(limit or 0)
    if limit <= 0:
        return []
    years = settings.get("research_years", 10)
    existing = set(sec.get("candidates", []))
    qwords = _matching_words(str(sec.get("title", "")) + " " + " ".join(sec.get("questions", [])))
    if not qwords:
        return []
    ranked = []
    for sid, source in (state.get("sources") or {}).items():
        if sid in existing:
            continue
        sword = _matching_words(str(source.get("title", "")) + " " + (source.get("text") or "")[:1500])
        score = len(qwords & sword)
        if score >= 3:
            ranked.append((-score, -evidence.score(source, years), sid))
    ranked.sort()
    return [sid for _, _, sid in ranked[:limit]]


_QUESTION_WORD = re.compile(r"[a-z0-9]+")


def _question_form(text: str) -> str:
    """A question lowercased with everything but letters and digits removed (equality key)."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _question_words(text: str) -> set:
    return set(_QUESTION_WORD.findall(str(text or "").lower()))


def add_questions(existing, new) -> list:
    """``existing`` + the questions in ``new`` that are not near-duplicates of a kept one.

    Two questions are near-duplicates when their letters-and-digits-only forms are equal,
    or when their word sets overlap with a Jaccard similarity of 0.6 or more. Each new
    question is checked against the kept ones and against the earlier new ones, so a
    coverage round that re-proposes the same question in slightly different words adds it
    only once."""
    out = [str(q) for q in (existing or [])]
    forms = [_question_form(q) for q in out]
    words = [_question_words(q) for q in out]
    for question in (new or []):
        text = str(question)
        form = _question_form(text)
        if not form:
            continue
        word_set = _question_words(text)
        duplicate = any(form == f for f in forms)
        if not duplicate:
            for other in words:
                union = word_set | other
                if union and len(word_set & other) / len(union) >= 0.6:
                    duplicate = True
                    break
        if duplicate:
            continue
        out.append(text)
        forms.append(form)
        words.append(word_set)
    return out


def _expert_brief_block(state: dict) -> str:
    """The EXPERT BRIEF block for the planner: the checklist to cover, never evidence."""
    brief = state.get("expert_brief") or {}
    lines = [f"- {p.get('id', '')} [{p.get('confidence', '')}] {p.get('point', '')}"
             for p in (brief.get("points") or []) if isinstance(p, dict)]
    guidelines = "; ".join(str(g) for g in (brief.get("key_guidelines") or []) if str(g).strip()) or "(none)"
    misconceptions = "; ".join(str(m) for m in (brief.get("misconceptions") or []) if str(m).strip()) or "(none)"
    return ("EXPERT BRIEF: a specialist's checklist — cover every point, never treat it as evidence:\n"
            + ("\n".join(lines) if lines else "- (none)")
            + f"\nKey guidelines: {guidelines}\nMisconceptions: {misconceptions}")


def _norm(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[‐-―−]", "-", t)
    t = re.sub(r"[^\w%.\-' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _numbers(text: str) -> set:
    # A digit run glued to a preceding letter is part of a name (CoQ10, B12, HbA1c,
    # SGLT2), not a statistic, so it is ignored. The digit in the lookbehind keeps the
    # engine from restarting mid-run (otherwise "CoQ10" would still yield "0").
    # "COVID-19" keeps "19" (preceded by "-").
    return {n.replace(",", "") for n in re.findall(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?", text or "")}


def quote_problem(fact: dict, state: dict) -> str:
    """Why a fact can't be traced to its source ('' if it can). The quote must be in
    a cited source's text, and every number in the claim must be in the quote."""
    ids = [i for i in fact.get("source_ids", []) if i in state.get("sources", {})]
    if not ids:
        return "no valid source id"
    quote = _norm(fact.get("quote", ""))
    if len(quote) < 15:
        return "supporting quote missing or too short"
    pieces = [_norm(p) for p in re.split(r"\.\.\.|…", fact.get("quote", ""))]
    pieces = [p for p in pieces if len(p) >= 12] or [quote]
    found = False
    for sid in ids:
        text = _norm(state["sources"][sid].get("text", ""))
        if all(p in text for p in pieces):
            found = True
            break
    if not found:
        return "quote not found word-for-word in the cited source"
    missing = _numbers(fact.get("claim", "")) - _numbers(fact.get("quote", ""))
    if missing:
        return f"number(s) {sorted(missing)} in the claim are not in the quote"
    return ""


_log_lock = threading.Lock()


def _log(msg: str) -> None:
    with _log_lock:                # concurrent sections must not interleave one line
        print(f"  🔎 {msg}", flush=True)


def _parallel(fn, items, workers: int) -> list:
    """Run fn(item) for every item and return the results in the original order.

    A fresh copy of the caller's context goes to each task: worker threads do not
    inherit contextvars, so without it call_llm_json would see current_node=None
    (wrong model routing in hybrid mode). The live progress line is silenced for the
    block. workers <= 1, or a single item, keeps the plain sequential loop."""
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(item) for item in items]
    with quiet():
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in items]
            return [f.result() for f in futures]     # re-raises whatever the task raised


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------
_EXPERT_CONFIDENCE = ("high", "medium", "low")


def expert_brief(state: dict) -> dict:
    """The model's own specialist knowledge, written BEFORE the search as directions.

    Never evidence: the points are searched for and only what the sources confirm reaches
    the facts and the patient guide. One call; an unreadable answer gives an empty brief
    (logged) so the research always continues."""
    system_prompt = load_prompt("research/expert_brief")
    user_prompt = f"""TOPIC: {state.get("topic", "")}
AUDIENCE: {state.get("audience") or "Egyptian general public watching a medical YouTube channel (Dr. Ahmed Hosney)"}
CREATOR'S NOTES: {state.get("user_notes") or "(none)"}

Write the expert brief. Output ONLY the JSON object."""
    try:
        data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.3, max_tokens=6000,
                                required=["points"])
    except UnreadableAnswer:
        data = {}
        _log("expert brief: answer unreadable — continuing without a brief")
    points, seen = [], set()
    for raw in pick(data, "points", default=[]) or []:
        if not isinstance(raw, dict):
            continue
        point = str(pick(raw, "point", default="") or "").strip()
        if not point:
            continue
        pid = str(pick(raw, "id", default="") or "").strip()
        if not pid or pid in seen:               # missing/duplicate id → next free K-number
            n = len(points) + 1
            pid = f"K{n}"
            while pid in seen:
                n += 1
                pid = f"K{n}"
        seen.add(pid)
        confidence = str(pick(raw, "confidence", default="") or "").strip().lower()
        if confidence not in _EXPERT_CONFIDENCE:
            confidence = "low"
        points.append({
            "id": pid, "point": point, "confidence": confidence,
            "why": str(pick(raw, "why", default="") or "").strip(),
            "literature_queries": [str(q).strip() for q in (pick(raw, "literature_queries", default=[]) or [])
                                   if str(q).strip()],
            "public_queries": [str(q).strip() for q in (pick(raw, "public_queries", default=[]) or [])
                               if str(q).strip()],
        })
    brief = {
        "points": points,
        "key_guidelines": [str(g).strip() for g in (pick(data, "key_guidelines", default=[]) or [])
                           if str(g).strip()],
        "misconceptions": [str(m).strip() for m in (pick(data, "misconceptions", default=[]) or [])
                           if str(m).strip()],
    }
    _log(f"expert brief: {len(points)} points, {len(brief['key_guidelines'])} guidelines, "
         f"{len(brief['misconceptions'])} misconceptions")
    return {"expert_brief": brief}


def _research_folder(state: dict) -> str:
    """The session's research/ folder for side files (raw answers) — from state["session"].

    A fresh run stores the session id (its folder is output/<id>/research); a path that
    already names an existing session folder is used as is."""
    session = str(state.get("session") or "").strip()
    if not session:
        return ""
    if os.path.isdir(session) or os.path.isabs(session) or os.sep in session or "/" in session:
        session_dir = session
    else:
        session_dir = os.path.join(PROJECT_ROOT, "output", session)
    return os.path.join(session_dir, "research")


def _save_planner_raw(state: dict, attempt: int, raw: str) -> str:
    """Writes an unreadable/incomplete planner answer to the research folder.

    Returns the path written ('' when there is no folder or it cannot be written)."""
    folder = _research_folder(state)
    if not folder:
        return ""
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"unreadable_topic_planner_{attempt}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw or "")
        return path
    except OSError as exc:
        _log(f"planner: could not save the raw answer ({type(exc).__name__}: {exc})")
        return ""


def topic_planner(state: dict) -> dict:
    system_prompt = load_prompt("research/topic_planner")
    user_prompt = f"""TOPIC: {state.get("topic", "")}
AUDIENCE: {state.get("audience") or "Egyptian general public watching a medical YouTube channel (Dr. Ahmed Hosney)"}
CREATOR'S NOTES: {state.get("user_notes") or "(none)"}

{_expert_brief_block(state)}

Plan the research. Output ONLY the JSON object."""
    data, raw_sections, saved = None, [], []
    # Two attempts: the first full-temperature call, then one fresh call with the same
    # prompts but a cooler temperature. The raw answer of a failed attempt is saved so a
    # stopped run can be diagnosed. No plan → StepFailed (never a one-section fallback).
    for attempt, temperature in ((1, 0.4), (2, 0.2)):
        try:
            data, raw = call_llm_json(system_prompt, user_prompt, temperature=temperature,
                                      max_tokens=8000, required=["sections"])
        except UnreadableAnswer as e:
            path = _save_planner_raw(state, attempt, e.raw)
            if path:
                saved.append(path)
            _log(f"planner: attempt {attempt} unreadable — raw answer saved to {path or '(nowhere)'}")
            data, raw_sections = None, []
            continue
        raw_sections = [s for s in (pick(data, "sections", default=[]) or []) if isinstance(s, dict)]
        if raw_sections:
            break
        path = _save_planner_raw(state, attempt, raw)
        if path:
            saved.append(path)
        _log(f"planner: attempt {attempt} had no sections — raw answer saved to {path or '(nowhere)'}")
    if not raw_sections:
        where = ", ".join(saved) if saved else "an unwritable session folder"
        raise StepFailed("topic_planner: the research plan could not be read after 2 attempts "
                         f"(raw answers saved to {where}); resume to try again")
    outline = [_new_section(state, i, s) for i, s in enumerate(raw_sections)]
    for sec in outline:                      # the planner may repeat a question in-answer
        sec["questions"] = add_questions([], sec["questions"])
    _log(f"outline: {len(outline)} sections — " + "; ".join(s["title"] for s in outline))
    topic_type = str(pick(data, "topic_type", default="") or "").strip()
    classic = []
    for c in (pick(data, "classic_checks", default=[]) or []):
        if not isinstance(c, dict):
            continue
        item = str(pick(c, "item", default="") or "").strip()
        if item:
            classic.append({"item": item, "section": str(pick(c, "section", default="") or "").strip()})
    special = [str(g).strip() for g in (pick(data, "special_groups", default=[]) or []) if str(g).strip()]
    # Complementary items are classic checks too: every one gets its own evidence row.
    comp_raw = pick(data, "complementary", default={}) or {}
    if not isinstance(comp_raw, dict):
        comp_raw = {}
    comp_items = pick(comp_raw, "items", default=[]) or []
    if not isinstance(comp_items, list):
        comp_items = []
    comp = {"applies": bool(pick(comp_raw, "applies", default=False, kind=bool)),
            "why": str(pick(comp_raw, "why", default="") or "").strip(),
            "items": [str(i).strip() for i in comp_items if str(i).strip()]}
    seen_items = {c["item"].strip().lower() for c in classic}
    # Add a forgotten complementary item to the section that reads "complementary/traditional"
    # (by its title), else to the last section — the same label the model would have used.
    comp_section = next((s["title"] for s in outline
                         if re.search(r"omplementary|raditional", s["title"], re.I)),
                        outline[-1]["title"] if outline else "")
    for item in comp["items"]:
        if item.lower() not in seen_items:
            classic.append({"item": item, "section": comp_section})
            seen_items.add(item.lower())
    _log(f"type: {topic_type or '(none)'}; classic checks: {len(classic)}; "
         f"special groups: {', '.join(special) or '(none)'}")
    _log("complementary: " + ("applies" if comp["applies"] else "not applicable")
         + (f" — {comp['why']}" if comp["why"] else "")
         + (f"; items: {', '.join(comp['items'])}" if comp["items"] else ""))
    ids = [s["id"] for s in outline]
    return {"outline": outline, "angle": str(pick(data, "angle", default="")),
            "topic_type": topic_type, "classic_checks": classic, "complementary": comp,
            "special_groups": special,
            "must_cover": [str(m) for m in (pick(data, "must_cover", default=[]) or [])],
            "focus": ids, "work": ids, "screen_round": 0, "verify_round": 0, "coverage_round": 0}


def _collect_queries(job, per_query: int, years: int, fallback_years: int = None,
                     older_per_query: int = 2) -> list:
    """Search one section's queries. Every literature query also runs a relevance search
    restricted to papers *older* than the normal window (``[now-fallback_years TO
    now-years-1]``): long-established safety evidence (e.g. the 1990s reports against
    sublingual nifedipine) is older than the normal window, so a query's recent hits can
    be only loosely related. The older results are appended to that query's own results,
    deduped by key. Public queries are never widened, and a failing older search is
    ignored (logged like run_query does)."""
    _, queries = job
    found = []
    for kind, q in queries:
        results = src.run_query(kind, q, per_query, years)
        if kind == "literature" and fallback_years and older_per_query:
            try:
                older = src.europepmc.search_older(q, max_results=older_per_query, years=years,
                                                   oldest_years=fallback_years)
            except Exception as e:        # a parser surprise must not stop the research
                src.http.log.append(("Europe PMC", q, f"{type(e).__name__}: {e}"))
                older = []
            seen = {s.get("key") for s in results}
            for s in older:
                if s.get("key") not in seen:
                    seen.add(s.get("key"))
                    results.append(s)
        found.extend(results)
    return found


def _merge_found(sources: dict, by_key: dict, sec: dict, found: list, next_id: int):
    """Add found sources to `sec` (dedupe by key, deterministic S-ids). -> (next_id, added)."""
    added = 0
    for s in found:
        if not s.get("text"):
            continue
        sid = by_key.get(s["key"])
        if not sid:
            sid = f"S{next_id}"
            next_id += 1
            s["text"] = s["text"][:6000]
            s["sections"] = []
            sources[sid] = s
            by_key[s["key"]] = sid
            added += 1
        if sec["id"] not in sources[sid]["sections"]:
            sources[sid]["sections"].append(sec["id"])
        if sid not in sec["candidates"]:
            sec["candidates"].append(sid)
    return next_id, added


def _web_search(google, query: str, seen: set) -> list:
    """google.search(query) minus hrefs already `seen`; never raises."""
    try:
        hits = google.search(query) or []
    except Exception as exc:
        _log(f"web: search failed ({type(exc).__name__}: {exc})")
        return []
    out = []
    for r in hits:
        href = r.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(dict(r))
    return out


def _site_search(sitesearch, query: str, seen: set) -> list:
    """sitesearch.search(query) minus hrefs already `seen`; never raises."""
    try:
        hits = sitesearch.search(query) or []
    except Exception as exc:
        _log(f"web: site search failed ({type(exc).__name__}: {exc})")
        return []
    out = []
    for r in hits:
        href = r.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(dict(r))
    return out


def _web_triage(topic: str, secs: list, results: dict, k: int) -> dict:
    """One web_triage call over every section with results -> {id: verdict}; {} on failure."""
    blocks = []
    for sec in secs:
        rs = results.get(sec["id"])
        if not rs:
            continue
        lines = [f"## SECTION {sec['id']}: {sec['title']}",
                 "Questions: " + " | ".join(sec.get("questions", []))]
        lines += [f"[{r.get('rank')}] {r.get('site', '')} — {r.get('title', '')} — {r.get('snippet', '')}"
                  for r in rs]
        blocks.append("\n".join(lines))
    if not blocks:
        return {}
    user = (f"TOPIC: {topic}\nALLOWED PER SECTION: {k}\n\n" + "\n\n".join(blocks)
            + "\n\nOutput ONLY the JSON object.")
    try:
        data, _ = call_llm_json(load_prompt("research/web_triage"), user,
                                temperature=0.2, max_tokens=3000, required=["sections"])
    except UnreadableAnswer:
        return {}
    return {str(pick(v, "id", default="")): v for v in (pick(data, "sections", default=[]) or [])
            if isinstance(v, dict)}


def _web_picks(results: list, verdict, k: int) -> list:
    """The results the triage numbered in `open`, in ranking order (at most k)."""
    by_rank = {r.get("rank"): r for r in results}
    nums = []
    for n in (pick(verdict, "open", default=[]) or []):
        try:
            nums.append(int(n))
        except (TypeError, ValueError):
            continue
    return [by_rank[n] for n in nums if n in by_rank][:k]


def _web_open(google, href: str, result: dict):
    """google.fetch_page for one picked result; never raises (returns (href, None))."""
    try:
        return href, google.fetch_page(result)
    except Exception as exc:
        _log(f"web: open failed ({type(exc).__name__}: {exc})")
        return href, None


def _web_blocks(google, page: dict) -> list:
    try:
        return google.paragraphs(page.get("html") or "")
    except Exception as exc:
        _log(f"web: paragraphs failed ({type(exc).__name__}: {exc})")
        return []


def _web_research(state: dict, sections_with_queries: list, settings: dict) -> dict:
    """Web research: site-native search (and/or Google through the user's Chrome) → LLM
    triage → open picked pages → LLM passage pick.

    ``web_search`` is "sites+google" (default: site search first, Google appended), "sites"
    (site search only), "google" (Google only) or "off". Site search never counts against
    ``max_google_queries``. Best-effort: any failure logs one line and returns {} so
    research continues without the web. Appends "<engine>:<query>" to state["queries_done"]
    so a later round never repeats a query."""
    mode = settings.get("web_search", "sites+google")
    if mode == "off":
        return {}
    use_sites = mode in ("sites", "sites+google")
    use_google = mode in ("google", "sites+google")
    try:
        from .sources import google, sitesearch      # lazy: websockets may be missing
    except Exception as exc:
        _log(f"web: unavailable ({type(exc).__name__}: {exc})")
        return {}
    have_browser = False
    try:
        from .sources import browser
        have_browser = browser.available()
    except Exception as exc:
        _log(f"web: browser unavailable ({type(exc).__name__}: {exc})")
    if use_google and not have_browser:
        if not use_sites:                            # Google-only still needs Chrome
            return {}
        use_google = False                           # sites+google: keep the site half
    try:
        topic = state.get("topic", "")
        per_section = max(1, int(settings.get("web_queries_per_section", 1) or 1))
        per_page = max(1, int(settings.get("web_pages_per_section", 3) or 3))
        done = state.setdefault("queries_done", [])

        # Reserve Google budget for the later coverage rounds: the first cycle may spend at
        # most 2/3 of the run cap, then stops issuing Google searches for remaining sections.
        # Site search is not budgeted (it is not Google).
        max_queries = max(1, int(settings.get("max_google_queries", 24) or 24))
        first_cycle = state.get("coverage_round", 0) == 0
        first_share = (max_queries * 2) // 3
        budget_floor = max_queries - first_share       # stop once remaining() <= floor
        budget_logged = [False]

        def budget_left() -> bool:
            if not first_cycle or google.remaining() > budget_floor:
                return True
            if not budget_logged[0]:
                budget_logged[0] = True
                _log(f"web: first-round Google budget ({first_share}/{max_queries}) reached; "
                     "deferring the rest to later rounds")
            return False

        # a. Site search first (unbudgeted), then Google for the same public queries,
        #    appended; the combined, deduped list goes to the single triage call.
        results = {}
        for sec in sections_with_queries:
            pubs = (sec.get("pending", {}).get("public") or sec.get("queries", {}).get("public") or [])
            queries = [str(q).strip() for q in pubs[:per_section]]
            queries = [q for q in queries if q]
            found, seen = [], set()
            if use_sites:
                for query in queries:
                    tag = "sites:" + query
                    if tag in done:
                        continue
                    done.append(tag)
                    found.extend(_site_search(sitesearch, query, seen))
            if use_google:
                for query in queries:
                    tag = "google:" + query
                    if tag in done:
                        continue
                    if not budget_left():
                        break
                    done.append(tag)
                    found.extend(_web_search(google, query, seen))
            if found:
                for i, r in enumerate(found, 1):
                    r["rank"] = i
                results[sec["id"]] = found
        if not results:
            return {}
        by_id = {sec["id"]: sec for sec in sections_with_queries}

        # b. One triage call for all sections with results; top-k by rank on failure.
        verdicts = _web_triage(topic, sections_with_queries, results, per_page)
        opened, refine = {}, {}
        for sid, rs in results.items():
            chosen = _web_picks(rs, verdicts.get(sid, {}), per_page) if verdicts else []
            if chosen:
                opened[sid] = chosen
                continue
            better = ([str(q) for q in (pick(verdicts.get(sid, {}), "better_queries", default=[]) or [])
                       if str(q).strip()][:2]
                      if settings.get("web_refine") and verdicts else [])
            if better:
                refine[sid] = better
            else:
                opened[sid] = rs[:per_page]      # no refinement: fall back to top-k by rank

        # c. One refinement round for sections the triage could not fill (web_refine only).
        if refine and (not use_google or budget_left()):
            for sid, queries in refine.items():
                if use_google and not budget_left():
                    break
                seen = {r["href"] for r in results[sid]}
                extra = []
                for query in queries:
                    if use_sites:
                        tag = "sites:" + query
                        if tag not in done:
                            done.append(tag)
                        extra.extend(_site_search(sitesearch, query, seen))
                    if use_google:
                        tag = "google:" + query
                        if tag not in done:
                            done.append(tag)
                        extra.extend(_web_search(google, query, seen))
                if extra:
                    merged = results[sid] + extra
                    for i, r in enumerate(merged, 1):
                        r["rank"] = i
                    results[sid] = merged
            again = _web_triage(topic, [by_id[s] for s in refine],
                                {s: results[s] for s in refine}, per_page)
            for sid in refine:
                opened[sid] = _web_picks(results[sid], again.get(sid, {}), per_page) or results[sid][:per_page]

        # d. Open the picked results; one fetch per href, attached by final URL.
        picks, hrefs = {}, {}
        for sid, rs in opened.items():
            picks[sid] = [r["href"] for r in rs if r.get("href")]
            for r in rs:
                if r.get("href"):
                    hrefs.setdefault(r["href"], r)
        fetched = _parallel(lambda h: _web_open(google, h, hrefs[h]), list(hrefs), 3)
        by_final, href_final = {}, {}
        for href, page in fetched:
            if not page:
                continue
            final = page.get("url") or href
            href_final[href] = final
            by_final.setdefault(final, page)

        # e. One passage-picker call per (page, section); keep only chosen paragraphs.
        jobs = []
        for sec in sections_with_queries:
            sid, seen_final = sec["id"], set()
            for href in picks.get(sid, []):
                final = href_final.get(href)
                if not final or final in seen_final:
                    continue
                seen_final.add(final)
                page = by_final[final]
                blocks = _web_blocks(google, page)
                if len(blocks) < 3:
                    continue
                numbered = "\n".join(f"[P{i}] {b}" for i, b in enumerate(blocks, 1))[:20000]
                user = (f"TOPIC: {topic}\nSECTION {sid}: {sec['title']}\n"
                        f"QUESTIONS: {' | '.join(sec.get('questions', []))}\n\n"
                        f"PAGE: {page.get('title', '')} — {final}\nPARAGRAPHS:\n{numbered}\n\n"
                        "Output ONLY the JSON object.")
                jobs.append((sid, page, blocks, user))

        def keep_one(job):
            sid, page, blocks, user = job
            try:
                data, _ = call_llm_json(load_prompt("research/passage_picker"), user,
                                        temperature=0.1, max_tokens=1500, required=["keep"])
            except UnreadableAnswer:
                return None
            keep = pick(data, "keep", default=[]) or []
            if not keep or str(pick(data, "relevance", default="")).lower() == "low":
                return None
            try:
                return sid, google.to_source(page, keep, blocks)
            except Exception as exc:
                _log(f"web: source failed ({type(exc).__name__}: {exc})")
                return None

        out = {}
        for kept in _parallel(keep_one, jobs, settings.get("parallel_calls", 4)):
            if kept:
                out.setdefault(kept[0], []).append(kept[1])

        # f. One summary line per section.
        for sec in sections_with_queries:
            sid = sec["id"]
            opened_n = len({href_final.get(h) for h in picks.get(sid, []) if href_final.get(h)})
            kept_n = len(out.get(sid, []))
            if results.get(sid) or opened_n or kept_n:
                _log(f"{sid} web: {len(results.get(sid, []))} results, opened {opened_n}, "
                     f"kept {kept_n} pages")
        return out
    except Exception as exc:                      # web trouble must not stop the research
        _log(f"web: failed ({type(exc).__name__}: {exc})")
        return {}


def _expert_seed_queries(state: dict) -> bool:
    """Add the expert brief's queries to their best-matching section's pending queries.

    Each point goes to the section whose title + questions share the most words with it
    (first section on a tie). Done exactly once per run, deduped against ``queries_done``
    and against what the section already has pending. Returns True when it ran."""
    if state.get("expert_queries_added"):
        return False
    points = (state.get("expert_brief") or {}).get("points") or []
    outline = state.get("outline", [])
    if not points or not outline:
        return False
    done = set(state.get("queries_done", []))
    for point in points:
        if not isinstance(point, dict):
            continue
        words = _word_set(str(point.get("point", "")))
        best, best_score = outline[0], -1
        for sec in outline:
            sec_words = _word_set(str(sec.get("title", "")) + " " + " ".join(sec.get("questions", [])))
            score = len(words & sec_words)
            if score > best_score:
                best, best_score = sec, score
        pending = best.setdefault("pending", {"literature": [], "public": []})
        for kind in ("literature", "public"):
            bucket = pending.setdefault(kind, [])
            for raw in point.get(f"{kind}_queries") or []:
                query = str(raw).strip()
                if not query or f"{kind}:{query}" in done or query in bucket:
                    continue
                bucket.append(query)
    state["expert_queries_added"] = True
    return True


def source_collector(state: dict) -> dict:
    settings = state.get("settings", {})
    per_query, years = settings.get("sources_per_query", 6), settings.get("research_years", 10)
    fallback_years = settings.get("research_years_fallback", 40)
    older_per_query = settings.get("older_per_query", 2)
    _expert_seed_queries(state)
    outline = [dict(s) for s in state.get("outline", [])]
    sources = {k: dict(v) for k, v in state.get("sources", {}).items()}
    by_key = {v["key"]: k for k, v in sources.items()}
    next_id = state.get("next_source", 1)
    done = list(state.get("queries_done", []))
    focus = set(state.get("focus") or [s["id"] for s in outline])
    # 1. Pick each section's still-undone queries, marking them done in order.
    jobs = []
    for sec in outline:
        if sec["id"] not in focus:
            continue
        queries = []
        for kind in ("literature", "public"):
            for q in sec.get("pending", {}).get(kind, []):
                tag = f"{kind}:{q}"
                if tag in done:
                    continue
                done.append(tag)
                queries.append((kind, q))
        jobs.append((sec, queries))
    # 2. Search each section's queries concurrently (network-bound), then
    # 3. merge in outline order so source ids stay deterministic.
    results = _parallel(lambda job: _collect_queries(job, per_query, years, fallback_years,
                                                    older_per_query), jobs,
                        settings.get("parallel_calls", 4))
    # 3b. LLM-assisted Google research, while pending still holds this round's public queries.
    web_sections = [sec for sec in outline if sec["id"] in focus
                    and (sec.get("pending", {}).get("public") or sec.get("queries", {}).get("public"))]
    state["queries_done"] = done
    # Web pages are found once per cycle; the screener's gap rounds re-searched the same
    # sites for minutes and kept almost nothing new, so they use the literature only.
    web = _web_research(state, web_sections, settings) if not state.get("screen_round") else {}
    done = state["queries_done"]
    for (sec, _), found in zip(jobs, results):
        next_id, new_here = _merge_found(sources, by_key, sec, found, next_id)
        next_id, web_here = _merge_found(sources, by_key, sec, web.get(sec["id"], []), next_id)
        sec["pending"] = {"literature": [], "public": []}
        sec["candidates"].sort(key=lambda i: -evidence.score(sources[i], years))
        # Sources collected for another section that clearly match this section's questions
        # are added after this round's own finds and before the cap.
        borrowed = _cross_section_sources({"sources": sources, "settings": settings}, sec)
        sec["borrowed"] = list(dict.fromkeys(list(sec.get("borrowed", [])) + borrowed))
        for sid in borrowed:
            if sid not in sec["candidates"]:
                sec["candidates"].append(sid)
        # Pages the LLM picked from Google rank as level 5, below every trial or review,
        # so a plain cut would drop them all; borrowed sources and web pages keep their
        # own slots in the list.
        cap = settings.get("sources_per_section", 8) * 2
        # Borrowed sources accumulate across rounds; reserve at most a quarter of the slots
        # for them so they can't crowd out the section's own finds.
        borrowed_ids = [i for i in sec["candidates"] if i in set(sec["borrowed"])][:max(4, cap // 4)]
        web_ids = [i for i in sec["candidates"]
                   if sources[i].get("origin") == "Web" and i not in borrowed_ids][:cap // 2]
        room = max(0, cap - len(borrowed_ids) - len(web_ids))
        rest = [i for i in sec["candidates"] if i not in borrowed_ids and i not in web_ids][:room]
        sec["candidates"] = [i for i in sec["candidates"]
                             if i in borrowed_ids or i in web_ids or i in rest]
        _log(f"{sec['id']} {sec['title'][:50]}: {new_here + web_here} new sources, "
             f"{len(sec['candidates'])} candidates")
    return {"outline": outline, "sources": sources, "next_source": next_id, "queries_done": done,
            "source_log": list(src.http.log),
            "expert_queries_added": bool(state.get("expert_queries_added"))}


def source_screener(state: dict) -> dict:
    settings = state.get("settings", {})
    outline = [dict(s) for s in state.get("outline", [])]
    sources = {k: dict(v) for k, v in state.get("sources", {}).items()}
    focus = set(state.get("focus") or [s["id"] for s in outline])
    blocks = []
    for sec in outline:
        if sec["id"] not in focus:
            continue
        lines = [f"## SECTION {sec['id']}: {sec['title']}", "Questions: " + " | ".join(sec["questions"])]
        borrowed_ids = set(sec.get("borrowed", []))
        for sid in sec["candidates"]:
            lines.append(_source_block(state, sid, limit=700, borrowed=sid in borrowed_ids))
        blocks.append("\n".join(lines))
    if not blocks:
        return {"need_sources": False, "to_extract": state.get("work", []), "sources": sources}
    system_prompt = load_prompt("research/source_screener")
    user_prompt = f"""TOPIC: {state.get("topic", "")}
KEEP UP TO {settings.get("sources_per_section", 8)} SOURCES PER SECTION.

{chr(10).join(blocks)}

Output ONLY the JSON object."""
    try:
        data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.2, max_tokens=5000,
                                required=["sections"])
    except UnreadableAnswer:
        data = {}
    verdicts = {str(pick(v, "id", default="")): v for v in (pick(data, "sections", default=[]) or [])
                if isinstance(v, dict)}
    gaps = []
    for sec in outline:
        if sec["id"] not in focus:
            continue
        v = verdicts.get(sec["id"], {})
        keep = [i for i in (pick(v, "keep", default=[]) or []) if i in sec["candidates"]]
        if not keep:                       # no verdict: keep the best-ranked sources
            keep = sec["candidates"][:settings.get("sources_per_section", 8)]
        # The model's pick first, then earlier keeps; capped so the section's source
        # list cannot grow without bound each screening round.
        sec["source_ids"] = list(dict.fromkeys(keep + sec.get("source_ids", [])))[
            :settings.get("max_sources_per_section", 12)]
        # A kept borrowed source now serves this section too: record it for provenance.
        borrowed_ids = set(sec.get("borrowed", []))
        for sid in sec["source_ids"]:
            if sid in borrowed_ids and sid in sources:
                own = sources[sid].setdefault("sections", [])
                if sec["id"] not in own:
                    own.append(sec["id"])
        lit = [q for q in (pick(v, "literature_queries", default=[]) or []) if q]
        pub = [q for q in (pick(v, "public_queries", default=[]) or []) if q]
        has_queries = bool(lit or pub)
        # Only search again for a section with a real, unfilled gap (or almost no
        # sources at all): a reported gap when the section is already stocked is not
        # worth a full extra search round.
        need_more = (bool(pick(v, "gaps", default=[]) or [])
                     and len(sec["source_ids"]) < settings.get("sources_per_section", 8))
        if has_queries and (need_more or len(sec["source_ids"]) < 2):
            sec["pending"] = {"literature": lit, "public": pub}
            gaps.append(sec["id"])
    rounds = state.get("screen_round", 0)
    more = bool(gaps) and rounds < settings.get("max_screen_rounds", 2)
    _log(f"screening: kept {sum(len(s['source_ids']) for s in outline if s['id'] in focus)} sources"
         + (f"; searching again for gaps in {', '.join(gaps)}" if more else ""))
    return {"outline": outline, "screen_round": rounds + 1, "need_sources": more,
            "focus": gaps if more else state.get("focus", []), "sources": sources,
            "to_extract": state.get("work", []), "screen_notes": str(pick(data, "notes", default=""))}


def _expert_hint(state: dict) -> str:
    """The expert checklist as things to LOOK FOR in this section's sources. The quote and
    number checks still reject anything the sources don't actually say."""
    points = (state.get("expert_brief") or {}).get("points") or []
    if not points:
        return ""
    lines = "\n".join(f"- {p.get('point', '')}" for p in points if p.get("point"))
    return ("A SPECIALIST EXPECTS THESE POINTS (extract one ONLY if these sources state it — with its "
            "exact quote and numbers; never from your own knowledge):\n" + lines)


FIX_MODE = ("FIX MODE: return ONLY corrected versions of the facts listed under FIX THESE FACTS "
            "(same meaning, corrected claim and/or exact quote). Do not add any other facts. "
            "Drop a fact if the sources do not support it.")


def _classic_canonical(state: dict) -> dict:
    """lower-case item -> canonical item text (the only forms a fact may carry as ``check``)."""
    return {str(c.get("item", "")).strip().lower(): str(c.get("item", "")).strip()
            for c in (state.get("classic_checks") or []) if isinstance(c, dict) and str(c.get("item", "")).strip()}


def _classic_for_section(state: dict, sec: dict) -> list:
    """The classic-check items, the current section's first (matched by id or title)."""
    title = str(sec.get("title", "")).strip().lower()
    sid = str(sec.get("id", "")).strip().lower()

    def mine(c) -> bool:
        own = str(c.get("section", "")).strip().lower()
        return bool(own) and (own == sid or own == title)

    checks = [c for c in (state.get("classic_checks") or []) if isinstance(c, dict)]
    first = [str(c["item"]).strip() for c in checks if mine(c) and str(c.get("item", "")).strip()]
    rest = [str(c["item"]).strip() for c in checks
            if not mine(c) and str(c.get("item", "")).strip()]
    return list(dict.fromkeys(first + rest))


_FULLTEXT_ORIGINS = ("Europe PMC", "PubMed")
_FULLTEXT_SHORT = 3000          # a source at least this long already has real text


def _fulltext_paragraphs(text: str) -> list:
    """Plain full text → its non-empty paragraphs (blank-line separated)."""
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _best_fulltext_excerpts(paragraphs: list, qwords: set, cap: int) -> list:
    """The paragraphs overlapping the section's words best, in document order, total ≤ cap.

    Each paragraph is scored by how many 4+ letter words it shares with the section's
    title and questions; paragraphs with no overlap are never included."""
    ranked = sorted(((i, len(qwords & _word_set(p)), p) for i, p in enumerate(paragraphs)),
                    key=lambda t: (-t[1], t[0]))
    chosen, used = [], 0
    for i, score, paragraph in ranked:
        if score <= 0 or used + len(paragraph) > cap:
            continue
        chosen.append((i, paragraph))
        used += len(paragraph)
    chosen.sort()
    return [p for _, p in chosen]


def _enrich_fulltext(sources: dict, sections: list, settings: dict) -> tuple:
    """Append open-access full-text excerpts to short Europe PMC/PubMed sources.

    Only sources a section kept, still lacking a text long enough to extract from and with
    no "fulltext" marker are considered, at most ``fulltext_max_per_round`` per call. The
    lookups/fetches run through ``_parallel``; any failure just marks the source "none" so
    it is not retried. Returns (enriched, kept): sources actually extended and the number
    of distinct kept sources seen."""
    settings = settings or {}
    cap = int(settings.get("fulltext_max_per_round", 8) or 0)
    char_cap = int(settings.get("fulltext_chars", 6000) or 0)
    seen, candidates, kept = set(), [], 0
    for sec in sections:
        for sid in sec.get("source_ids", []):
            if sid in seen or sid not in sources:
                continue
            seen.add(sid)
            kept += 1
            source = sources[sid]
            if "fulltext" in source:                      # already handled in an earlier round
                continue
            if len(source.get("text") or "") >= _FULLTEXT_SHORT:
                continue
            if source.get("origin") not in _FULLTEXT_ORIGINS:
                continue
            candidates.append((sec, sid))
    candidates = candidates[:cap] if cap > 0 else []

    def enrich(candidate):
        sec, sid = candidate
        source = sources[sid]
        pmcid = str(source.get("pmcid") or "").strip()
        open_access = bool(source.get("open_access"))
        if not pmcid and source.get("pmid"):              # PubMed has no pmcid of its own
            pmcid, open_access = src.europepmc.lookup_pmcid(str(source["pmid"]).strip())
        if not pmcid or not open_access:
            return sid, None
        body = src.europepmc.fulltext(pmcid)
        if not body:
            return sid, None
        qwords = _word_set(str(sec.get("title", "")) + " " + " ".join(sec.get("questions", [])))
        return sid, _best_fulltext_excerpts(_fulltext_paragraphs(body), qwords, char_cap)

    results = _parallel(enrich, candidates, settings.get("parallel_calls", 4))
    enriched = 0
    for sid, excerpts in results:
        if excerpts:
            source = sources[sid]
            source["text"] = ((source.get("text") or "")
                              + "\n\nFULL TEXT EXCERPTS:\n" + "\n\n".join(excerpts))
            source["fulltext"] = True
            enriched += 1
        else:
            sources[sid]["fulltext"] = "none"
    return enriched, kept


def fact_extractor(state: dict) -> dict:
    settings = state.get("settings", {})
    system_prompt = load_prompt("research/fact_extractor")
    # Work on copies of the sources so the full-text excerpts can be returned in the
    # state update (a source may be shared by several sections).
    sources = {k: dict(v) for k, v in state.get("sources", {}).items()}
    state = {**state, "sources": sources}
    facts = [f for f in state.get("facts", []) if f.get("status") != "fix"]
    # Facts sent back for fixing are replaced by this extraction; if they don't come
    # back corrected they stay visible in the dossier's "not included" list.
    rejected = list(state.get("rejected", [])) + [
        dict(f, status="rejected", note=f"{f.get('note', '')} (sent back for correction)")
        for f in state.get("facts", []) if f.get("status") == "fix"]
    redo = {}
    for f in state.get("facts", []):
        if f.get("status") == "fix":
            redo.setdefault(f["section"], []).append(f)
    next_fact = state.get("next_fact", 1)
    notes = state.get("verify_notes", {}) or {}
    user_notes = state.get("extractor_notes", {}) or {}
    budget = settings.get("max_facts_per_section", 12)
    outline = [dict(s) for s in state.get("outline", [])]
    classic_map = _classic_canonical(state)          # canonical check names only
    sections_to_extract = _sections({"outline": outline}, state.get("to_extract"))
    # 0. Enrich the kept sources of these sections with open-access full-text excerpts
    #    before any prompt is built, so the extractor sees the recommendations a bare
    #    abstract only hints at. Best-effort: failures are ignored and marked.
    enriched, kept = _enrich_fulltext(sources, sections_to_extract, settings)
    _log(f"full text: {enriched} of {kept} kept sources enriched")
    # 1. Build each section's prompt up front (cheap); sections with no work are
    #    logged and skipped exactly as before.
    jobs = []
    for sec in sections_to_extract:
        fixing = redo.get(sec["id"]) or []
        extracted = list(sec.get("extracted", []))
        if fixing:
            # FIX MODE: only the flagged facts and the sources they cite. A normal
            # new-source pass here is what inflated the facts on every fix round.
            send_ids = list(dict.fromkeys(i for f in fixing for i in f.get("source_ids", [])
                                          if i in state.get("sources", {})))
        else:
            send_ids = [i for i in sec.get("source_ids", []) if i not in extracted]
        if not send_ids:
            _log(f"{sec['id']} {sec['title'][:50]}: nothing new")
            continue
        have = [f for f in facts if f["section"] == sec["id"] and f["status"] == "verified"]
        room = budget - len(have)
        if not fixing:
            if room <= 0:
                _log(f"{sec['id']} {sec['title'][:50]}: fact budget reached")
                continue
            fix_part = f"\n\nEXTRACT AT MOST {room} NEW FACTS — the most valuable ones."
        else:
            fix_part = ("\n\n" + FIX_MODE + "\nFIX THESE FACTS:\n" + "\n".join(
                f"- {f['id']}: {f['claim']}\n  problem: {f.get('note', '')}\n  quote: \"{f.get('quote', '')}\""
                for f in fixing))
        user_prompt = f"""TOPIC: {state.get("topic", "")}
SECTION {sec['id']}: {sec['title']}
QUESTIONS TO ANSWER: {" | ".join(sec["questions"])}
CLASSIC CHECKS: {" | ".join(_classic_for_section(state, sec)) or "(none)"}
{_expert_hint(state)}
{("CREATOR'S REQUEST FOR THIS SECTION: " + user_notes[sec['id']]) if user_notes.get(sec['id']) else ""}
{("VERIFIER NOTES: " + notes[sec['id']]) if notes.get(sec['id']) else ""}
ALREADY VERIFIED (don't repeat): {" | ".join(f["claim"] for f in have) or "(none)"}{fix_part}

SOURCES (quote ONLY from these texts):
{chr(10).join(_source_block(state, sid) for sid in send_ids)}

Output ONLY the JSON object."""
        jobs.append((sec, fixing, extracted, send_ids, room, user_prompt))

    def extract_one(job):
        try:
            data, _ = call_llm_json(system_prompt, job[5], temperature=0.2, max_tokens=9000,
                                    required=["facts"])
        except UnreadableAnswer:
            data = {}
        return data

    # 2. Run the calls concurrently; 3. merge in the original section order so fact
    #    ids do not depend on which call happened to finish first.
    results = _parallel(extract_one, jobs, settings.get("parallel_calls", 4))
    seen = {_norm(f["claim"]) for f in facts}
    for (sec, fixing, extracted, send_ids, room, _), data in zip(jobs, results):
        if not fixing and data:
            # These sources are done; a later pass must not send them again (an
            # unreadable answer leaves them for the next pass).
            sec["extracted"] = list(dict.fromkeys(extracted + send_ids))
        added = 0
        limit = len(fixing) if fixing else room          # fix mode: corrections only
        fixing_ids = {f["id"] for f in fixing}
        linked = set()                                   # originals corrected in this section
        for raw in pick(data, "facts", default=[]) or []:
            if not isinstance(raw, dict) or not str(pick(raw, "claim", default="")).strip():
                continue
            claim = str(pick(raw, "claim")).strip()
            if _norm(claim) in seen:
                continue                       # already have it
            # Each correction names its original; link at most one correction to each.
            link = str(pick(raw, "fixes", default="") or "").strip()
            link = link if fixing and link in fixing_ids else ""
            if link and link in linked:
                continue
            seen.add(_norm(claim))
            ids = [str(i).strip("[] ") for i in (pick(raw, "source_ids", default=[]) or [])]
            kind = str(pick(raw, "kind", default="fact")).lower().replace(" ", "_")
            levels = [state["sources"][i].get("level", 6) for i in ids if i in state["sources"]]
            check = classic_map.get(str(pick(raw, "check", default="") or "").strip().lower(), "")
            if not check and link:                   # fix mode: keep the original's check
                orig = next((f for f in fixing if f["id"] == link), None)
                orig_check = str((orig or {}).get("check", "") or "").strip()
                if orig_check:
                    check = classic_map.get(orig_check.lower(), orig_check)
            new = {
                "id": f"F{next_fact}", "section": sec["id"], "kind": kind if kind in KINDS else "fact",
                "claim": claim, "quote": str(pick(raw, "quote", default="")).strip(),
                "source_ids": ids, "level": min(levels) if levels else 6,
                "question": str(pick(raw, "question", default="")), "status": "new", "note": ""}
            if check:
                new["check"] = check
            if link:
                new["fixes"] = link
                linked.add(link)
            facts.append(new)
            next_fact += 1
            added += 1
            if added >= limit:
                break
        _log(f"{sec['id']} {sec['title'][:50]}: {added} facts extracted")
    # A corrected fact that made it back (by id link or same wording) is no longer
    # "not included"; the rest stay in rejected with their note.
    fixed_ids = {f["fixes"] for f in facts if f.get("fixes")}
    kept = {_norm(f["claim"]) for f in facts}
    rejected = [r for r in rejected if not (r.get("note", "").endswith("(sent back for correction)")
                                            and (r.get("id") in fixed_ids or _norm(r["claim"]) in kept))]
    return {"facts": facts, "next_fact": next_fact, "rejected": rejected, "outline": outline,
            "sources": sources}


def fact_verifier(state: dict) -> dict:
    settings = state.get("settings", {})
    facts = [dict(f) for f in state.get("facts", [])]
    rejected = list(state.get("rejected", []))
    by_section = {}
    # 1. Code check: every new fact must be traceable to its source, word for word.
    for f in facts:
        if f["status"] != "new":
            continue
        why = quote_problem(f, state)
        if why:
            f["status"], f["note"] = "fix", why
        else:
            by_section.setdefault(f["section"], []).append(f)
    # 2. Model check: is each claim faithful to its quote, current, and consistent?
    system_prompt = load_prompt("research/fact_verifier")
    # The list of already-verified facts is taken once, before the parallel block:
    # with the calls running at once a section cannot see another's fresh verdicts.
    others = [f"{f['id']}: {f['claim']}" for f in facts if f["status"] == "verified"][:60]
    jobs = []
    for sid, group in by_section.items():
        sec = next((s for s in state.get("outline", []) if s["id"] == sid), {"title": sid})
        items = "\n\n".join(
            f"{f['id']} ({f['kind']}) CLAIM: {f['claim']}\nQUOTE: \"{f['quote']}\"\nSOURCE: "
            + "; ".join(f"[{i}] {state['sources'][i]['title']} ({state['sources'][i].get('year') or 'n.d.'}, "
                        f"{evidence.LEVELS.get(state['sources'][i].get('level', 6), '')})"
                        for i in f["source_ids"] if i in state["sources"])
            for f in group)
        user_prompt = f"""TOPIC: {state.get("topic", "")}
SECTION {sid}: {sec.get('title', '')}

FACTS TO VERIFY:
{items}

OTHER VERIFIED FACTS (check for contradictions):
{chr(10).join(others) or "(none)"}

Output ONLY the JSON object."""
        jobs.append((group, user_prompt))

    def verify_one(job):
        try:
            data, _ = call_llm_json(system_prompt, job[1], temperature=0.1, max_tokens=7000,
                                    required=["verdicts"])
        except UnreadableAnswer:
            data = {}
        return data

    # 3. Apply the verdicts sequentially, in section order.
    results = _parallel(verify_one, jobs, settings.get("parallel_calls", 4))
    for (group, _), data in zip(jobs, results):
        verdicts = {str(pick(v, "id", default="")): v for v in (pick(data, "verdicts", default=[]) or [])
                    if isinstance(v, dict)}
        for f in group:
            v = verdicts.get(f["id"])
            if v is None:
                f["status"], f["note"] = "fix", "the verifier gave no verdict"
                continue
            verdict = str(pick(v, "verdict", default="")).lower()
            reason = str(pick(v, "reason", default=""))
            corrected = str(pick(v, "corrected_claim", default="") or "").strip()
            strength = _norm_strength(pick(v, "strength"))
            strength_reason = str(pick(v, "strength_reason", default="") or "").strip()
            if verdict.startswith("verif"):
                f["status"] = "verified"
                if pick(v, "debated", kind=bool):
                    f["kind"] = "debated"
                f["strength"], f["strength_reason"] = strength, strength_reason
            elif verdict.startswith("fix") and corrected:
                candidate = dict(f, claim=corrected)
                if not quote_problem(candidate, state):
                    f.update(claim=corrected, status="verified", note="wording corrected by the verifier",
                             strength=strength, strength_reason=strength_reason)
                else:
                    f["status"], f["note"] = "fix", reason or "needs correction"
            elif verdict.startswith("fix"):
                f["status"], f["note"] = "fix", reason or "needs correction"
            else:
                f["status"], f["note"] = "rejected", reason or "rejected by the verifier"
    keep = []
    for f in facts:
        if f["status"] == "rejected":
            rejected.append(f)
        else:
            keep.append(f)
    rounds = state.get("verify_round", 0)
    fix_sections = sorted({f["section"] for f in keep if f["status"] == "fix"})
    more = bool(fix_sections) and rounds < settings.get("max_verify_rounds", 2)
    if not more:                    # out of rounds: what still isn't right is left out
        for f in keep:
            if f["status"] == "fix":
                f["status"] = "rejected"
                f["note"] = (f.get("note") or "") + " (unresolved)"
                rejected.append(f)
        keep = [f for f in keep if f["status"] != "rejected"]
    verified = sum(1 for f in keep if f["status"] == "verified")
    to_fix = sum(1 for f in keep if f["status"] == "fix")
    _log(f"verification round {rounds + 1}: {verified} verified, {to_fix} to fix, {len(rejected)} left out so far")
    notes = {s: "; ".join(f["note"] for f in keep if f["section"] == s and f["status"] == "fix") for s in fix_sections}
    return {"facts": keep, "rejected": rejected, "verify_round": rounds + 1, "need_fix": more,
            "to_extract": fix_sections if more else [], "verify_notes": notes if more else {}}


def fact_merger(state: dict) -> dict:
    """Merge duplicate verified facts (same section) into one, citing every source.

    One model call per eligible section; every group is validated by code — a group can
    only use verified facts of that section, one id is used once per run, and a kept
    fact gains the ordered union of the group's sources. Merged facts leave ``facts``
    and move to ``merged`` (they are not rejected). Any per-section error means that
    section is simply left unmerged; the run never fails."""
    settings = state.get("settings", {})
    outline = [dict(s) for s in state.get("outline", [])]
    facts = [dict(f) for f in state.get("facts", [])]     # copies: kept facts gain sources below
    merged = list(state.get("merged", []))
    by_section = {}
    for f in facts:
        if f.get("status") == "verified":
            by_section.setdefault(f.get("section"), []).append(f)
    wanted = set(state.get("work") or [])
    jobs = []
    for sec in outline:
        if wanted and sec["id"] not in wanted:
            continue
        group = by_section.get(sec["id"], [])
        if len(group) < 2:
            continue
        lines = [f"{f['id']} [{f.get('kind', 'fact')} · {f.get('strength', 'n/a')}] {f['claim']}"
                 f" — sources: {', '.join(f.get('source_ids', []))} "
                 f"({_source_names(state, f.get('source_ids', []))})"
                 for f in group]
        user = (f"TOPIC: {state.get('topic', '')}\n"
                f"SECTION {sec['id']}: {sec.get('title', '')}\n\n"
                + "\n".join(lines) + "\n\nOutput ONLY the JSON object.")
        jobs.append((sec, user))
    if not jobs:
        _log("merged 0 duplicate facts into 0")
        return {"facts": facts, "merged": merged}
    system_prompt = load_prompt("research/fact_merger")

    def merge_one(job):
        sec, user = job
        try:
            data, _ = call_llm_json(system_prompt, user, temperature=0.1, max_tokens=2000,
                                    required=["groups"])
            groups = pick(data, "groups", default=[]) or []
            return sec["id"], groups if isinstance(groups, list) else []
        except Exception as exc:                 # unreadable answer or any failure: skip section
            _log(f"merge: {sec['id']} skipped ({type(exc).__name__}: {exc})")
            return sec["id"], []

    results = _parallel(merge_one, jobs, settings.get("parallel_calls", 4))
    used = set()
    done = 0
    for sid, groups in results:
        side = {f["id"]: f for f in by_section.get(sid, [])}
        for g in groups:
            if not isinstance(g, dict):
                continue
            keep_id = str(pick(g, "keep", default="") or "").strip().strip("[]")
            merge_ids = [str(m).strip().strip("[]") for m in (pick(g, "merge", default=[]) or [])]
            if keep_id not in side or keep_id in used:
                continue
            valid = [i for i in merge_ids if i in side and i != keep_id and i not in used]
            valid = list(dict.fromkeys(valid))
            if not valid:
                continue
            used.add(keep_id)
            used.update(valid)
            keep_fact = side[keep_id]
            group_facts = [keep_fact] + [side[i] for i in valid]
            strengths = {str(f.get("strength", "n/a")) for f in group_facts}
            if len(strengths) == 1:              # all agree: keep the agreed label
                keep_fact["strength"] = next(iter(strengths))
            if not str(keep_fact.get("check", "") or "").strip():
                # The kept fact carries no classic check: take one from a merged fact.
                inherited = next((f.get("check") for f in group_facts[1:]
                                  if str(f.get("check", "") or "").strip()), "")
                if inherited:
                    keep_fact["check"] = inherited
            union = []
            for f in group_facts:
                for i in f.get("source_ids", []):
                    if i not in union:
                        union.append(i)
            keep_fact["source_ids"] = union
            supported = keep_fact.setdefault("also_supported_by", [])
            for i in valid:
                m = side[i]
                supported.append({"id": i, "source_ids": list(m.get("source_ids", [])),
                                  "quote": m.get("quote", "")})
                m["merged_into"] = keep_id
                merged.append(m)
            done += 1
    merged_ids = {m["id"] for m in merged if m.get("merged_into")}
    facts = [f for f in facts if f["id"] not in merged_ids]
    _log(f"merged {len(used) - done} duplicate facts into {done}")
    return {"facts": facts, "merged": merged}


def coverage_critic(state: dict) -> dict:
    settings = state.get("settings", {})
    lines = []
    for sec in state.get("outline", []):
        lines.append(f"## {sec['id']}: {sec['title']}\nQuestions: " + " | ".join(sec["questions"]))
        for f in state.get("facts", []):
            if f["section"] == sec["id"] and f["status"] == "verified":
                strength = f.get("strength", "n/a")
                tag = f" · {strength}" if strength and strength != "n/a" else ""
                lines.append(f"- {f['id']} [{f['kind']}{tag}] {f['claim']}")
    classic = state.get("classic_checks") or []
    from .dossier import classic_check_table      # lazy: dossier imports no research nodes
    verdict_by_item = {str(r.get("item", "")).strip().lower(): r.get("verdict", "")
                       for r in classic_check_table(state)}

    def classic_line(c) -> str:
        base = f"- {c['item']} ({c['section']})" if c.get("section") else f"- {c['item']}"
        verdict = verdict_by_item.get(str(c.get("item", "")).strip().lower())
        return f"{base} — {verdict}" if verdict else base

    classic_lines = [classic_line(c) for c in classic if isinstance(c, dict) and c.get("item")] or ["(none)"]
    special = state.get("special_groups") or []
    expert_points = [p for p in ((state.get("expert_brief") or {}).get("points") or [])
                     if isinstance(p, dict)]
    checklist_lines = [f"- {p.get('id', '')} [{p.get('confidence', '')}] {p.get('point', '')}"
                       for p in expert_points] or ["(none)"]
    system_prompt = load_prompt("research/coverage_critic")
    user_prompt = f"""TOPIC: {state.get("topic", "")}
ANGLE: {state.get("angle", "")}
TOPIC TYPE: {state.get("topic_type") or "(not set)"}
CLASSIC CHECKS:
{chr(10).join(classic_lines)}
SPECIAL GROUPS: {", ".join(str(g) for g in special) or "(none)"}
MUST COVER: {" | ".join(state.get("must_cover", [])) or "(see topic)"}
EXPERT CHECKLIST:
{chr(10).join(checklist_lines)}
{("CREATOR'S REVIEW NOTES: " + state["review"].get("notes", "")) if state.get("review", {}).get("notes") else ""}

VERIFIED FACTS BY SECTION:
{chr(10).join(lines)}

Output ONLY the JSON object."""
    try:
        data, raw = call_llm_json(system_prompt, user_prompt, temperature=0.2, max_tokens=5000,
                                  required=["coverage_score", "missing"])
    except UnreadableAnswer as e:
        data, raw = {}, e.raw
    # The expert checklist verdicts: only known K-ids and verified fact ids are kept; a
    # point left out of this round's answer keeps its earlier verdict (latest round wins
    # per id). A point the model never addressed stays absent (unknown → "missing").
    point_ids = {str(p.get("id", "")) for p in expert_points}
    valid_facts = {f["id"] for f in state.get("facts", []) if f.get("status") == "verified"}
    expert_status = dict(state.get("expert_status") or {})
    for raw_point in pick(data, "expert_points", default=[]) or []:
        if not isinstance(raw_point, dict):
            continue
        pid = str(pick(raw_point, "id", default="") or "").strip()
        if pid not in point_ids:
            continue
        status = str(pick(raw_point, "status", default="") or "").strip().lower()
        if status not in ("covered", "contradicted", "missing"):
            status = "missing"
        facts = [str(f).strip().strip("[]") for f in (pick(raw_point, "facts", default=[]) or [])]
        expert_status[pid] = {"status": status, "facts": [f for f in facts if f in valid_facts]}
    scores = {k: pick(data, k + "_score", kind=int) for k in ("coverage", "accuracy", "value")}
    pass_score = settings.get("coverage_pass_score", 7)
    passed = bool(pick(data, "pass", default=False, kind=bool)) or all((v or 0) >= pass_score
                                                                        for v in scores.values())
    rounds = state.get("coverage_round", 0)
    outline = [dict(s) for s in state.get("outline", [])]
    # Plateau: once scores have stopped moving, more searching is wasted work.
    prev = state.get("coverage_scores") or {}
    improved = any((scores.get(k) or 0) - (prev.get(k) or 0) >= 1 for k in scores)
    plateau = rounds > 0 and bool(prev) and not improved
    new_work = []
    if not passed and rounds < settings.get("max_coverage_rounds", 2) and not plateau:
        # Merge everything a section is missing into ONE work item: the critic may list
        # a section more than once and a later item used to wipe the earlier queries.
        merged, order = {}, []
        for m in pick(data, "missing", default=[]) or []:
            if not isinstance(m, dict):
                continue
            lit = [q for q in (pick(m, "literature_queries", default=[]) or []) if q]
            pub = [q for q in (pick(m, "public_queries", default=[]) or []) if q]
            if not (lit or pub):
                continue
            sid = str(pick(m, "section_id", default="")).strip()
            if not sid:
                # A brand-new section has no id yet: key it by title (or a unique
                # placeholder) so separate new sections don't merge into one.
                title = str(pick(m, "title", default="")).strip().lower()
                sid = "__new:" + (title or str(len(order)))
            if sid not in merged:
                merged[sid] = {"literature": [], "public": [], "questions": [], "raw": m}
                order.append(sid)
            e = merged[sid]
            e["literature"] = list(dict.fromkeys(e["literature"] + lit))
            e["public"] = list(dict.fromkeys(e["public"] + pub))
            e["questions"] = add_questions(
                e["questions"], [str(q) for q in (pick(m, "questions", default=[]) or [])])
        cap = settings.get("max_coverage_sections", 3)
        for sid in order[:cap]:
            e = merged[sid]
            target = next((s for s in outline if s["id"] == sid), None)
            if target is None:
                target = _new_section(state, len(outline), e["raw"])
                target["questions"] = add_questions([], e["questions"])
                target["pending"] = {"literature": list(e["literature"]), "public": list(e["public"])}
                outline.append(target)
            else:
                pending = target.setdefault("pending", {"literature": [], "public": []})
                pending["literature"] = list(dict.fromkeys(list(pending.get("literature", [])) + e["literature"]))
                pending["public"] = list(dict.fromkeys(list(pending.get("public", [])) + e["public"]))
                target["questions"] = add_questions(target.get("questions", []), e["questions"])
            new_work.append(target["id"])
    more = bool(new_work)
    if more:
        why = f"; researching more for {', '.join(new_work)}"
    elif not passed and plateau:
        why = "; scores stopped improving"
    else:
        why = ""
    _log(f"coverage round {rounds + 1}: scores {scores}" + why)
    out = {"coverage_round": rounds + 1, "coverage_pass": passed, "coverage_scores": scores,
           "coverage_report": str(pick(data, "report", default="") or raw or ""), "need_research": more,
           "outline": outline, "expert_status": expert_status}
    if more:
        out.update(focus=new_work, work=new_work, screen_round=0, verify_round=0)
    return out


def dossier_editor(state: dict) -> dict:
    """A short overview per section, written only from verified facts (every sentence
    must cite a fact id, which code checks)."""
    facts = [f for f in state.get("facts", []) if f["status"] == "verified"]
    if not facts:
        return {"summary": "", "section_notes": {}}
    lines = []
    for sec in state.get("outline", []):
        lines.append(f"## {sec['id']}: {sec['title']}")
        lines += [f"- {f['id']} [{f['kind']}] {f['claim']}" for f in facts if f["section"] == sec["id"]]
    system_prompt = load_prompt("research/dossier_editor")
    user_prompt = f"""TOPIC: {state.get("topic", "")}

VERIFIED FACTS:
{chr(10).join(lines)}

Output ONLY the JSON object."""
    try:
        data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.3, max_tokens=4000, required=["summary"])
    except UnreadableAnswer:
        data = {}
    valid = {f["id"] for f in facts}

    def cited_only(text: str) -> str:
        # Keep only sentences that cite a real verified fact.
        sentences = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
        kept = [s for s in sentences if (refs := re.findall(r"\[?(F\d+)\]?", s)) and all(r in valid for r in refs)]
        return " ".join(kept)

    notes = {str(k): cited_only(v) for k, v in (pick(data, "section_notes", default={}) or {}).items()}
    return {"summary": cited_only(pick(data, "summary", default="")), "section_notes": notes}


# --------------------------------------------------------------------------
# Patient Guide (Layer 2): the organised, patient-valued version of the verified
# facts that becomes the input to the video workflow.
# --------------------------------------------------------------------------
GUIDE_ACTION_KEYS = ("do_now", "daily", "works", "doesnt_work_or_risky", "see_doctor_if", "emergency_if")
# Weakest certainty first: an item is only as strong as its weakest cited fact.
GUIDE_STRENGTH_ORDER = ("harmful", "no_benefit", "untested", "weak_or_disputed", "probably_helps", "proven")


def _guide_ids(raw, valid) -> list:
    """The fact ids of a guide item that name a verified fact ("F12" or "[F12]")."""
    out = []
    for x in raw or []:
        s = str(x).strip().strip("[]").strip()
        if s in valid and s not in out:
            out.append(s)
    return out


def _guide_item_text(item: dict, fields) -> str:
    return " ".join(str(item.get(k, "")) for k in fields if item.get(k)).strip()


def _guide_weakest(strengths) -> str:
    """The weakest-certainty label among the cited facts ("" when all are n/a)."""
    present = [str(s).strip() for s in strengths if str(s).strip() in GUIDE_STRENGTH_ORDER]
    return min(present, key=lambda s: GUIDE_STRENGTH_ORDER.index(s)) if present else ""


# The model sometimes writes its own evidence label in the item text ("... (proven)")
# even though the code computes the real label from the cited facts. Those parentheses
# are removed so the text the patient sees never contradicts the stored strength.
_EVIDENCE_LABEL_RE = re.compile(
    r"\(\s*(?:"
    r"(?:not\s+)?proven"
    r"|(?:probably|likely)\s+helps?"
    r"|may\s+help"
    r"|untested"
    r"|(?:low[- ]quality|high[- ]quality|moderate|strong|weak|limited|mixed)\s+(?:evidence|certainty)"
    r")\s*\)",
    re.IGNORECASE)


def _strip_evidence_labels(text: str) -> tuple:
    """Remove parenthetical evidence labels and tidy the gaps they leave.

    Returns (cleaned_text, number_removed). Only parentheses whose whole content is an
    evidence phrase go; descriptive parentheses such as "(ACE inhibitors)" stay."""
    cleaned, removed = _EVIDENCE_LABEL_RE.subn("", text or "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    return cleaned.strip(), removed


# A safety claim is "safe ... in/during/for pregnancy|pregnant|breastfeeding|nursing"
# (within ~60 characters) or the compound "pregnancy-safe". It is only allowed when a
# cited fact actually says safe + pregnancy/breastfeeding.
_PREGNANCY_SAFE_RE = re.compile(
    r"(?:safe(?:ly)?\b[^.\n]{0,60}?\b(?:in|during|for)\s+"
    r"(?:pregnan(?:cy|t)|breastfeed\w*|nursing)\b"
    r"|pregnan(?:cy|t)[- ]safe\b)",
    re.IGNORECASE)


def _facts_say_pregnancy_safe(claims) -> bool:
    """True when a cited fact's claim contains "safe" together with pregnancy/breastfeeding."""
    for claim in claims:
        c = str(claim or "").lower()
        if "safe" in c and ("pregnan" in c or "breastfeed" in c or "nursing" in c):
            return True
    return False


_PROMISE_OVERCLAIM = re.compile(
    r"\b(within (a few )?(seconds|minutes)|in (\d+ )?seconds|instantly|immediately|cures?|guarantee[sd]?|"
    r"the only (way|solution)|forever)\b", re.IGNORECASE)


def _checked_promise(promise: str, facts: list, report: dict) -> str:
    """The promise cites no facts, so it gets its own check: no number the facts don't
    contain and no time-to-effect / cure wording. A failing promise is dropped (the video
    steps then write their own from the guide) — it once became "stops a cramp in 10 seconds"
    in a video title."""
    if not promise:
        return ""
    fact_numbers = set()
    for f in facts:
        fact_numbers |= _numbers(f.get("claim", ""))
    bad = sorted(_numbers(promise) - fact_numbers)
    hit = _PROMISE_OVERCLAIM.search(promise)
    if bad or hit:
        why = (f"number(s) {', '.join(bad)} not in the facts" if bad else "") + \
              ("; " if bad and hit else "") + (f"overclaim wording '{hit.group(0)}'" if hit else "")
        report["dropped"] += 1
        report["by_reason"]["promise_overclaim"] = report["by_reason"].get("promise_overclaim", 0) + 1
        report["dropped_items"].append({"kind": "promise", "text": promise, "reason": why})
        return ""
    return promise


def validate_guide(guide: dict, state: dict) -> tuple:
    """Code-check the model's patient guide and return (clean_guide, report).

    Every item (action card, section points, special groups, myths, complementary
    options / not-proven, key numbers, uncertain) must cite verified fact ids and
    invent no number outside its cited facts. Complementary options that the
    classic-checks table does not call helpful (or that are harmful) are moved to
    "not proven", never silently dropped. Each surviving item carries the weakest
    evidence label among its cited facts. Sections left with no points are dropped."""
    from .dossier import _EVIDENCE_OPTION_VERDICTS, classic_check_table, verified_facts

    guide = guide if isinstance(guide, dict) else {}
    facts = verified_facts(state)
    valid = {f["id"] for f in facts}
    by_id = {f["id"]: f for f in facts}
    report = {"kept": 0, "dropped": 0, "by_reason": {}, "dropped_items": [],
              "moved_to_not_proven": [], "labels_removed": 0}

    def drop(kind, text, reason, category):
        report["dropped"] += 1
        report["by_reason"][category] = report["by_reason"].get(category, 0) + 1
        report["dropped_items"].append({"kind": kind, "text": text, "reason": reason})

    def clean_item(item, kind, fields):
        if not isinstance(item, dict):
            drop(kind, str(item), "not an object", "not_object")
            return None
        out = dict(item)
        # The model's own parenthetical evidence labels are dropped from the text: the code
        # computes the real label below, and a text label would contradict it.
        for key in fields:
            if out.get(key):
                cleaned, removed = _strip_evidence_labels(str(out[key]))
                out[key] = cleaned
                report["labels_removed"] += removed
        text = _guide_item_text(out, fields)
        ids = _guide_ids(item.get("facts"), valid)
        if not ids:
            drop(kind, text or "(empty)", "no valid verified fact id", "no_valid_fact_id")
            return None
        # A warning ("not safe in pregnancy", "unsafe while breastfeeding") is never dropped.
        safe_claim = _PREGNANCY_SAFE_RE.search(text)
        if safe_claim and re.search(r"\b(?:not|never|isn't|aren't|unsafe|no longer)\b[^.\n]{0,20}$",
                                    text[:safe_claim.start()] + " ", re.IGNORECASE):
            safe_claim = None
        if safe_claim and \
                not _facts_say_pregnancy_safe(by_id[fid].get("claim", "") for fid in ids):
            drop(kind, text, "safety claim for pregnancy/breastfeeding not in the cited facts",
                 "unsupported_pregnancy_safety")
            return None
        claimed = set()
        for fid in ids:
            claimed |= _numbers(by_id[fid].get("claim", ""))
        missing = _numbers(text) - claimed
        if missing:
            drop(kind, text, "number(s) " + ", ".join(sorted(missing)) + " not in the cited facts",
                 "invented_number")
            return None
        out["facts"] = ids
        strength = _guide_weakest(by_id[fid].get("strength", "n/a") for fid in ids)
        if strength:
            out["strength"] = strength
        report["kept"] += 1
        return out

    clean = {"promise": _checked_promise(str(guide.get("promise", "") or "").strip(), facts, report)}
    ac = guide.get("action_card") if isinstance(guide.get("action_card"), dict) else {}
    clean["action_card"] = {
        key: [c for c in (clean_item(it, "action_card." + key, ("text",)) for it in (ac.get(key) or [])) if c]
        for key in GUIDE_ACTION_KEYS}
    # "Works" is a promise to the viewer: every cited fact must show real benefit
    # (proven / probably helps). Anything resting on weaker evidence leaves the list.
    works = []
    for item in clean["action_card"].get("works", []):
        strengths = {str(by_id[fid].get("strength", "n/a") or "n/a") for fid in item["facts"]}
        # Needs real benefit evidence; a safety fact (harmful) or a descriptive one (n/a) may
        # accompany it, but weak / untested / no-benefit evidence disqualifies the promise.
        if strengths & {"proven", "probably_helps"} and \
                not strengths & {"weak_or_disputed", "untested", "no_benefit"}:
            works.append(item)
        else:
            report["kept"] -= 1
            drop("action_card.works", _guide_item_text(item, ("text",)),
                 "evidence not strong enough for 'works' (" + ", ".join(sorted(strengths)) + ")",
                 "weak_evidence_in_works")
    clean["action_card"]["works"] = works

    sections = []
    for sec in guide.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        points = [p for p in (clean_item(it, "section.point", ("text",)) for it in (sec.get("points") or [])) if p]
        if not points:
            continue
        sections.append({"title": str(sec.get("title", "") or "").strip(),
                         "question": str(sec.get("question", "") or "").strip(),
                         "points": points})
    clean["sections"] = sections

    clean["special_groups"] = [g for g in (clean_item(it, "special_group", ("group", "advice"))
                                           for it in (guide.get("special_groups") or [])) if g]
    clean["myths"] = [m for m in (clean_item(it, "myth", ("myth", "truth"))
                                  for it in (guide.get("myths") or [])) if m]

    comp = guide.get("complementary") if isinstance(guide.get("complementary"), dict) else {}
    rows = {str(r.get("item", "")).strip().lower(): r for r in classic_check_table(state)}
    options, moved = [], []
    for it in comp.get("options") or []:
        name = str(it.get("item", "")).strip().lower() if isinstance(it, dict) else ""
        row = rows.get(name)
        if row and row.get("verdict") in _EVIDENCE_OPTION_VERDICTS and not row.get("harmful"):
            options.append(it)
        else:
            moved.append(it)
            report["moved_to_not_proven"].append(
                _guide_item_text(it, ("item", "text")) if isinstance(it, dict) else str(it))
    raw_not = list(comp.get("not_proven") or []) + moved
    clean["complementary"] = {
        "options": [o for o in (clean_item(it, "complementary.option", ("item", "text")) for it in options) if o],
        "not_proven": [o for o in (clean_item(it, "complementary.not_proven", ("item", "text"))
                                   for it in raw_not) if o]}

    clean["key_numbers"] = [n for n in (clean_item(it, "key_number", ("text",))
                                        for it in (guide.get("key_numbers") or [])) if n]
    uncertain = []
    for it in guide.get("uncertain") or []:
        text = _guide_item_text(it, ("text",)) if isinstance(it, dict) else str(it)
        if isinstance(it, dict) and not _guide_ids(it.get("facts"), valid) and text and not _numbers(text):
            # An open question ("the sources don't settle X") cites nothing by nature; it is
            # kept as long as it asserts no number of its own.
            report["kept"] += 1
            uncertain.append({"text": text.strip(), "facts": [], "open_question": True})
            continue
        u = clean_item(it, "uncertain", ("text",))
        if u:
            uncertain.append(u)
    clean["uncertain"] = uncertain
    return clean, report


def guide_editor(state: dict) -> dict:
    """One call that turns the verified facts (Layer 1) into the PATIENT GUIDE
    (Layer 2). The answer is checked in code by validate_guide. On an unreadable
    answer it is asked once more; if that fails too the guide is empty and the run
    continues (the video falls back to the full dossier)."""
    from .dossier import classic_check_table

    facts = [f for f in state.get("facts", []) if f.get("status") == "verified"]
    if not facts:
        _log("guide: no verified facts — no patient guide for this run")
        return {"patient_guide": {}, "guide_report": {"kept": 0, "dropped": 0, "by_reason": {},
                                                      "dropped_items": [], "moved_to_not_proven": [],
                                                      "labels_removed": 0}}
    rows = classic_check_table(state)
    classic_lines = [f"- {r['item']} — {r['verdict']} — facts {', '.join(r['fact_ids']) or '—'} — "
                     f"complementary {'yes' if r.get('complementary') else 'no'} — "
                     f"harmful {'yes' if r.get('harmful') else 'no'}" for r in rows] or ["(none)"]
    fact_lines = [f"- {f['id']} [{f.get('section', '')} · {f.get('kind', '')} · {f.get('strength', 'n/a')}] "
                  f"{f['claim']} — sources: {_source_names(state, f.get('source_ids', [])) or '—'}"
                  for f in facts]
    special = state.get("special_groups") or []
    # The planned viewer questions (from the outline) must each be answered somewhere; the
    # expert brief is deliberately NOT sent here — only the verified facts reach the guide.
    planned = [str(q).strip() for sec in state.get("outline", [])
               for q in (sec.get("questions") or []) if str(q).strip()]
    planned_lines = "\n".join(f"- {q}" for q in planned) or "(none)"
    user_prompt = f"""TOPIC: {state.get("topic", "")}
TOPIC TYPE: {state.get("topic_type") or "(not set)"}
ANGLE: {state.get("angle", "")}
SPECIAL GROUPS: {", ".join(str(g) for g in special) or "(none)"}
CLASSIC CHECKS (item — verdict — facts — complementary — harmful):
{chr(10).join(classic_lines)}

PLANNED VIEWER QUESTIONS (answer every one somewhere when the facts allow; put any the facts do not settle under "uncertain"):
{planned_lines}

VERIFIED FACTS (id [section · kind · strength] claim — sources):
{chr(10).join(fact_lines)}

Output ONLY the JSON object."""
    system_prompt = load_prompt("research/patient_guide")
    data, readable = {}, False
    for attempt in range(2):
        try:
            data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.3, max_tokens=12000,
                                    required=["action_card", "sections"])
            readable = True
            break
        except UnreadableAnswer as e:
            if attempt == 0:
                _log(f"guide: answer unreadable ({e}); asking once more")
            else:
                _log(f"guide: still unreadable after retry ({e}); no patient guide this run")
    if not readable:                      # no guide: the video falls back to the full dossier
        return {"patient_guide": {}, "guide_report": {"kept": 0, "dropped": 0, "by_reason": {},
                                                      "dropped_items": [], "moved_to_not_proven": [],
                                                      "labels_removed": 0}}
    clean, report = validate_guide(data, state)
    _log(f"guide: {report['kept']} items kept, {report['dropped']} dropped, "
         f"{len(report['moved_to_not_proven'])} moved to not proven")
    return {"patient_guide": clean, "guide_report": report}


def review_planner(state: dict) -> dict:
    """Turns the creator's review.md into research work for the next round."""
    review = state.get("review", {}) or {}
    outline = [dict(s) for s in state.get("outline", [])]
    facts = list(state.get("facts", []))
    system_prompt = load_prompt("research/review_planner")
    user_prompt = f"""TOPIC: {state.get("topic", "")}

CURRENT OUTLINE:
{chr(10).join(f"{s['id']}: {s['title']} — " + " | ".join(s['questions']) for s in outline)}

CURRENT VERIFIED FACTS:
{chr(10).join(f"{f['id']} ({f['section']}): {f['claim']}" for f in facts if f['status'] == 'verified')}

CREATOR'S REVIEW:
ADD: {review.get("add", "") or "(nothing)"}
CHANGE: {review.get("change", "") or "(nothing)"}
REMOVE: {review.get("remove", "") or "(nothing)"}
NOTES: {review.get("notes", "") or "(nothing)"}

Output ONLY the JSON object."""
    try:
        data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.3, max_tokens=5000,
                                required=["new_sections", "edit_sections"])
    except UnreadableAnswer:
        data = {}
    work = []
    remove_sections = {str(i) for i in (pick(data, "remove_sections", default=[]) or [])}
    remove_facts = {str(i) for i in (pick(data, "remove_facts", default=[]) or [])}
    outline = [s for s in outline if s["id"] not in remove_sections]
    facts = [f for f in facts if f["id"] not in remove_facts and f["section"] not in remove_sections]
    extractor_notes = {}
    for e in pick(data, "edit_sections", default=[]) or []:
        if not isinstance(e, dict):
            continue
        sec = next((s for s in outline if s["id"] == str(pick(e, "id", default=""))), None)
        if not sec:
            continue
        sec["questions"] = add_questions(
            sec["questions"], [str(q) for q in (pick(e, "add_questions", default=[]) or [])])
        sec["pending"] = {"literature": [q for q in (pick(e, "literature_queries", default=[]) or []) if q],
                          "public": [q for q in (pick(e, "public_queries", default=[]) or []) if q]}
        if pick(e, "note", default=""):
            extractor_notes[sec["id"]] = str(pick(e, "note"))
        work.append(sec["id"])
    for raw in pick(data, "new_sections", default=[]) or []:
        if isinstance(raw, dict):
            sec = _new_section(state, len(outline), raw)
            sec["questions"] = add_questions([], sec["questions"])
            # ids must stay unique even after removals
            while any(s["id"] == sec["id"] for s in outline):
                sec["id"] = sec["id"] + "2"
            outline.append(sec)
            work.append(sec["id"])
            if pick(raw, "note", default=""):
                extractor_notes[sec["id"]] = str(pick(raw, "note"))
    _log(f"review round {state.get('review_round', 0) + 1}: {len(work)} sections to research, "
         f"{len(remove_sections)} removed, {len(remove_facts)} facts removed")
    return {"outline": outline, "facts": facts, "focus": work, "work": work, "to_extract": [],
            "extractor_notes": extractor_notes, "screen_round": 0, "verify_round": 0, "coverage_round": 0,
            "review_round": state.get("review_round", 0) + 1,
            "history": state.get("history", []) + [{"round": state.get("review_round", 0) + 1, "review": review}]}
