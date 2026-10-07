"""Optional web search, limited to trusted medical sites. Needs one key in llm_keys.env:
TAVILY_API_KEY (tavily.com) or BRAVE_API_KEY (api.search.brave.com). Without a key
the research uses the literature databases only."""
import html.parser
import os
import re

from . import evidence, http

TRUSTED_DOMAINS = [
    "who.int", "cdc.gov", "nih.gov", "medlineplus.gov", "nice.org.uk", "nhs.uk", "mayoclinic.org",
    "clevelandclinic.org", "hopkinsmedicine.org", "health.harvard.edu", "heart.org", "escardio.org",
    "acc.org", "diabetes.org", "cancer.gov", "cancer.org", "fda.gov", "ema.europa.eu",
    "cochranelibrary.com", "uspreventiveservicestaskforce.org", "kidney.org", "aafp.org",
]


def trusted_domains() -> list:
    extra = [d.strip().lower() for d in os.environ.get("RESEARCH_TRUSTED_DOMAINS", "").split(",") if d.strip()]
    return TRUSTED_DOMAINS + [d for d in extra if d not in TRUSTED_DOMAINS]


def is_trusted(url: str) -> bool:
    domain = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url or "").split("/")[0].lower())
    return any(domain == d or domain.endswith("." + d) for d in trusted_domains())


def available() -> str:
    """Which web search is configured: 'tavily', 'brave' or ''."""
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    if os.environ.get("BRAVE_API_KEY"):
        return "brave"
    return ""


class _TextExtractor(html.parser.HTMLParser):
    """Visible text of an article page: paragraphs, list items and headings."""
    SKIP = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg"}
    KEEP = {"p", "li", "h1", "h2", "h3", "h4", "td"}

    def __init__(self):
        super().__init__()
        self.skip = 0
        self.keep = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.KEEP:
            self.keep += 1
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.KEEP and self.keep:
            self.keep -= 1

    def handle_data(self, data):
        if self.keep and not self.skip:
            self.parts.append(data)


def page_text(html_text: str, limit: int = 8000) -> str:
    p = _TextExtractor()
    try:
        p.feed(html_text or "")
    except Exception:
        return ""
    lines = [re.sub(r"\s+", " ", l).strip() for l in "".join(p.parts).split("\n")]
    text = "\n".join(l for l in lines if len(l) > 30)
    return text[:limit]


def _source(title: str, url: str, text: str) -> dict:
    return {"key": f"url:{url.split('#')[0].rstrip('/')}", "origin": "Web", "title": title, "url": url,
            "journal": re.sub(r"^www\.", "", url.split("/")[2]) if "//" in url else "", "year": 0,
            "pub_types": ["Web page"], "level": evidence.level_of([], title, url), "text": text}


def search(query: str, max_results: int = 5, years: int = 10) -> list:
    engine = available()
    if engine == "tavily":
        data = http.get_json("https://api.tavily.com/search", source="Tavily", data={
            "api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": max_results,
            "search_depth": "advanced", "include_raw_content": True, "include_domains": trusted_domains()},
            headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"})
        out = []
        for r in (data or {}).get("results", []) or []:
            url = r.get("url", "")
            if not is_trusted(url):
                continue
            text = (r.get("raw_content") or r.get("content") or "").strip()
            out.append(_source(r.get("title", ""), url, re.sub(r"\s+\n", "\n", text)[:8000]))
        return out
    if engine == "brave":
        data = http.get_json("https://api.search.brave.com/res/v1/web/search", source="Brave",
                             params={"q": query, "count": 20},
                             headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"],
                                      "Accept": "application/json"})
        out = []
        for r in ((data or {}).get("web") or {}).get("results", []) or []:
            url = r.get("url", "")
            if not is_trusted(url):
                continue
            text = page_text(http.get(url, source="web page") or "")
            if text:
                out.append(_source(re.sub(r"<[^>]+>", "", r.get("title", "")), url, text))
            if len(out) >= max_results:
                break
        return out
    return []
