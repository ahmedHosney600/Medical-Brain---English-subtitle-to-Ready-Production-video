"""MedlinePlus (U.S. National Library of Medicine): expert-reviewed health topic pages
written for the public — good for practical, patient-level facts. No key needed."""
import html
import re
import xml.etree.ElementTree as ET

from . import evidence, http

URL = "https://wsearch.nlm.nih.gov/ws/query"


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_results(xml_text: str) -> list:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for doc in root.iter("document"):
        url = doc.get("url", "")
        fields = {c.get("name"): _clean("".join(c.itertext())) for c in doc.findall("content")}
        title = fields.get("title", "")
        text = fields.get("FullSummary") or fields.get("snippet", "")
        if not url or not text:
            continue
        out.append({
            "key": f"url:{url}", "origin": "MedlinePlus", "title": title, "url": url,
            "journal": "MedlinePlus (NIH)", "year": 0, "pub_types": ["Health topic page"],
            "level": evidence.level_of([], title, url), "text": text,
        })
    return out


def search(query: str, max_results: int = 3, years: int = 10) -> list:
    xml_text = http.get(URL, params={"db": "healthTopics", "term": query, "retmax": max_results},
                        source="MedlinePlus")
    return parse_results(xml_text or "")
