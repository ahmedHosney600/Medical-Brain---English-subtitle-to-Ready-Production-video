"""StatPearls (NCBI Bookshelf): clinician-authored review chapters, searched through the
same NCBI E-utilities as PubMed (``db=books``). A chapter is literature (origin/journal
"StatPearls", level 5 — a narrative review/health-authority page) whose readable text is
the Bookshelf page itself, pulled with ``web.page_text`` and capped like every source.

Robots.txt allows ``/books/NBK*``; requests reuse the shared NCBI throttle (≤3 req/s).
No key needed (NCBI_API_KEY in llm_keys.env raises the rate limit)."""
import datetime
import os
import re

from . import http, web

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
MAX_TEXT = 8000


def _key_params() -> dict:
    key = os.environ.get("NCBI_API_KEY")
    return {"api_key": key} if key else {}


def _text(value) -> str:
    if isinstance(value, list):
        return " ".join(_text(v) for v in value if v)
    return re.sub(r"\s+", " ", str(value or "")).strip()


def search_ids(query: str, max_results: int = 8, years: int = 10) -> list:
    # Relevance sort matters: without it the pool fills with recent tables/sections and a
    # chapter search can return no readable chapter at all.
    data = http.get_json(BASE + "esearch.fcgi", params={
        "db": "books", "term": f"({query}) AND statpearls[book]", "retmode": "json",
        "retmax": max_results, "sort": "relevance", **_key_params()}, source="StatPearls")
    ids = ((data or {}).get("esearchresult") or {}).get("idlist") or []
    return [str(i) for i in ids if i]


def parse_summary(data: dict) -> list:
    """esummary ``db=books`` JSON → literature-shaped dicts (text filled later).

    The record's ``uid`` is an internal number; the chapter's Bookshelf id is its
    ``accessionid`` (``NBK…``). Only chapter-level records are kept: a record whose
    ``rid`` names a table, figure or section (contains a ``/``) is not a chapter. URL is
    the Bookshelf page for that NBK id."""
    result = (data or {}).get("result") or {}
    out = []
    for uid in result.get("uids") or []:
        rec = result.get(uid)
        if not isinstance(rec, dict):
            continue
        rid = _text(rec.get("rid"))
        if "/" in rid:                       # e.g. NBK12345/table/T1 — not a chapter
            continue
        nbk = _text(rec.get("accessionid")) or _text(rec.get("bookaccessionid")) or rid \
            or _text(rec.get("uid"))
        if not nbk:
            continue
        year = 0
        m = re.search(r"\b(?:19|20)\d{2}\b", _text(rec.get("pubdate")))
        if m:
            year = int(m.group())
        out.append({
            "key": f"books:{rid or nbk}", "origin": "StatPearls", "title": _text(rec.get("title")),
            "url": f"https://www.ncbi.nlm.nih.gov/books/{nbk}/", "doi": "", "pmid": "",
            "journal": "StatPearls", "year": year, "pub_types": ["Review"],
            "level": 5, "cited_by": 0, "text": "",
        })
    return out


def search_europepmc(query: str, max_results: int = 8, years: int = 10) -> list:
    """StatPearls chapters through Europe PMC, which indexes each chapter with its summary
    (~2-6k characters). Used when NCBI Bookshelf pages answer with a CAPTCHA (it does from
    some networks); the summary is shorter than the chapter but quotable and on topic."""
    from . import europepmc
    now = datetime.date.today().year
    q = f'({query}) AND PUBLISHER:"StatPearls Publishing" AND PUB_YEAR:[{now - years} TO {now}]'
    data = http.get_json(europepmc.URL, params={"query": q, "format": "json", "resultType": "core",
                                                "pageSize": max_results}, source="StatPearls")
    out = []
    for rec in europepmc.parse_results(data or {}):
        if not rec.get("text"):
            continue
        rec.update(origin="StatPearls", journal="StatPearls", pub_types=["Review"], level=5)
        out.append(rec)
    return out


def search(query: str, max_results: int = 8, years: int = 10) -> list:
    found = _search_bookshelf(query, max_results, years)
    return found or search_europepmc(query, max_results, years)


def _search_bookshelf(query: str, max_results: int = 8, years: int = 10) -> list:
    # Most book hits are tables/sections; ask for a wider pool so the chapter filter
    # still leaves enough to return.
    ids = search_ids(query, max(max_results * 10, 60), years)
    if not ids:
        return []
    data = http.get_json(BASE + "esummary.fcgi", params={
        "db": "books", "id": ",".join(ids), "retmode": "json", **_key_params()},
        source="StatPearls")
    now = datetime.date.today().year
    out = []
    for rec in parse_summary(data or {}):
        if years and rec["year"] and rec["year"] < now - years:
            continue
        text = web.page_text(http.get(rec["url"], source="StatPearls") or "", limit=MAX_TEXT)
        if not text:
            continue
        rec["text"] = text
        out.append(rec)
        if len(out) >= max_results:
            break
    return out
