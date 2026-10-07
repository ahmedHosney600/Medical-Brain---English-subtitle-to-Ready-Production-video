"""Europe PMC: literature search with abstracts and citation counts (covers PubMed
plus preprints, guidelines and open-access full text). No key needed."""
import datetime
import re

from . import evidence, http

URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


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
            "journal": (((r.get("journalInfo") or {}).get("journal") or {}).get("title") or ""),
            "year": int(year) if year.isdigit() else 0,
            "pub_types": pub_types,
            "level": evidence.level_of(pub_types, title),
            "cited_by": int(r.get("citedByCount") or 0),
            "text": abstract,
        })
    return out


def search(query: str, max_results: int = 8, years: int = 10) -> list:
    now = datetime.date.today().year
    q = f"({query}) AND PUB_YEAR:[{now - years} TO {now}] AND HAS_ABSTRACT:y"
    data = http.get_json(URL, params={"query": q, "format": "json", "resultType": "core",
                                      "pageSize": max_results}, source="Europe PMC")
    return parse_results(data or {})
