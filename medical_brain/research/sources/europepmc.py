"""Europe PMC: literature search with abstracts and citation counts (covers PubMed
plus preprints, guidelines and open-access full text). No key needed."""
import datetime
import html
import re

from . import evidence, http

URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"


def parse_results(data: dict) -> list:
    out = []
    for r in ((data or {}).get("resultList") or {}).get("result", []) or []:
        title = re.sub(r"<[^>]+>", "", r.get("title") or "").strip()
        abstract = re.sub(r"<[^>]+>", " ", r.get("abstractText") or "")
        abstract = re.sub(r"\s+", " ", abstract).strip()
        pub_types = ((r.get("pubTypeList") or {}).get("pubType")) or []
        if isinstance(pub_types, str):
            pub_types = [pub_types]
        doi = (r.get("doi") or "").strip()
        pmid = (r.get("pmid") or "").strip()
        if pmid:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        elif doi:
            url = f"https://doi.org/{doi}"
        else:
            url = f"https://europepmc.org/article/{r.get('source', 'MED')}/{r.get('id', '')}"
        year = str(r.get("pubYear") or "")
        out.append({
            "key": f"doi:{doi.lower()}" if doi else (f"pmid:{pmid}" if pmid else f"url:{url}"),
            "origin": "Europe PMC", "title": title, "url": url, "doi": doi, "pmid": pmid,
            "pmcid": (r.get("pmcid") or "").strip(),
            "open_access": r.get("isOpenAccess") == "Y",
            "journal": (((r.get("journalInfo") or {}).get("journal") or {}).get("title") or ""),
            "year": int(year) if year.isdigit() else 0,
            "pub_types": pub_types,
            "level": evidence.level_of(pub_types, title),
            "cited_by": int(r.get("citedByCount") or 0),
            "text": abstract,
        })
    return out


# Block-level closing tags become paragraph breaks; everything else is stripped.
_BLOCK_END = re.compile(r"(?i)</(?:abstract|body|boxed-text|caption|disp-quote|fig|front|list-item"
                        r"|p|sec|table-wrap|title)>")
_LINE_BREAK = re.compile(r"(?i)<br\s*/?>")


def _strip_xml(xml: str) -> str:
    """Full-text XML → plain text with paragraph breaks preserved (not tag soup)."""
    text = _LINE_BREAK.sub("\n", xml or "")
    text = _BLOCK_END.sub("\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def fulltext(pmcid: str) -> str:
    """The plain text of an open-access article's full text ("" on any failure)."""
    pmcid = (pmcid or "").strip()
    if not pmcid:
        return ""
    raw = http.get(FULLTEXT_URL.format(pmcid=pmcid), source="Europe PMC")
    if not raw:
        return ""
    return _strip_xml(raw)


def lookup_pmcid(pmid: str) -> tuple:
    """(pmcid, open_access) for a PMID via the search endpoint (("", False) on failure)."""
    pmid = (pmid or "").strip()
    if not pmid:
        return "", False
    data = http.get_json(URL, params={"query": f"EXT_ID:{pmid} AND SRC:MED", "format": "json",
                                      "resultType": "core"}, source="Europe PMC")
    results = ((data or {}).get("resultList") or {}).get("result", []) or []
    if not results:
        return "", False
    hit = results[0]
    return (hit.get("pmcid") or "").strip(), hit.get("isOpenAccess") == "Y"


def search(query: str, max_results: int = 8, years: int = 10) -> list:
    now = datetime.date.today().year
    q = f"({query}) AND PUB_YEAR:[{now - years} TO {now}] AND HAS_ABSTRACT:y"
    data = http.get_json(URL, params={"query": q, "format": "json", "resultType": "core",
                                      "pageSize": max_results}, source="Europe PMC")
    return parse_results(data or {})


def search_older(query: str, max_results: int = 8, years: int = 10, oldest_years: int = 40) -> list:
    """Like ``search`` but over the window *older* than the normal one.

    The normal window is ``[now-years TO now]``; this looks at
    ``[now-oldest_years TO now-years-1]`` with the same relevance ordering, so
    long-established classics (e.g. the 1990s reports against sublingual nifedipine)
    surface even when a query's recent hits are only loosely related."""
    now = datetime.date.today().year
    q = f"({query}) AND PUB_YEAR:[{now - oldest_years} TO {now - years - 1}] AND HAS_ABSTRACT:y"
    data = http.get_json(URL, params={"query": q, "format": "json", "resultType": "core",
                                      "pageSize": max_results}, source="Europe PMC")
    return parse_results(data or {})
