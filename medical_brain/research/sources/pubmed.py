"""PubMed (NCBI E-utilities): search → full records with abstracts. No key needed
(NCBI_API_KEY in llm_keys.env raises the rate limit)."""
import datetime
import os
import xml.etree.ElementTree as ET

from . import evidence, http

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def _key_params() -> dict:
    key = os.environ.get("NCBI_API_KEY")
    return {"api_key": key} if key else {}


def search_ids(query: str, max_results: int = 8, years: int = 10) -> list:
    now = datetime.date.today().year
    data = http.get_json(BASE + "esearch.fcgi", params={
        "db": "pubmed", "term": query, "retmode": "json", "retmax": max_results, "sort": "relevance",
        "datetype": "pdat", "mindate": now - years, "maxdate": now, **_key_params()}, source="PubMed")
    return list((data or {}).get("esearchresult", {}).get("idlist", []))


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_records(xml_text: str) -> list:
    """PubmedArticleSet XML → source dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for art in root.iter("PubmedArticle"):
        cit = art.find("MedlineCitation")
        if cit is None:
            continue
        pmid = _text(cit.find("PMID"))
        a = cit.find("Article")
        if a is None:
            continue
        title = _text(a.find("ArticleTitle"))
        parts = []
        for t in a.findall("Abstract/AbstractText"):
            label = t.get("Label")
            parts.append((label + ": " if label else "") + _text(t))
        abstract = "\n".join(p for p in parts if p.strip())
        year = _text(a.find("Journal/JournalIssue/PubDate/Year")) or _text(
            a.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4]
        pub_types = [_text(p) for p in a.findall("PublicationTypeList/PublicationType")]
        doi = ""
        for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = _text(aid)
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        out.append({
            "key": f"doi:{doi.lower()}" if doi else f"pmid:{pmid}",
            "origin": "PubMed", "title": title, "url": url, "doi": doi, "pmid": pmid,
            "journal": _text(a.find("Journal/Title")),
            "year": int(year) if year.isdigit() else 0,
            "pub_types": pub_types,
            "level": evidence.level_of(pub_types, title),
            "text": abstract,
        })
    return out


def fetch(ids: list) -> list:
    if not ids:
        return []
    xml_text = http.get(BASE + "efetch.fcgi", params={
        "db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml", **_key_params()},
        source="PubMed")
    return parse_records(xml_text or "")


def search(query: str, max_results: int = 8, years: int = 10) -> list:
    return fetch(search_ids(query, max_results, years))
