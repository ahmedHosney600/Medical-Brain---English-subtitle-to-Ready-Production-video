"""Site-native search for the trusted medical sites, independent of Google.

Three engines, all returning the same result dict shape as ``google``
(``{"title", "href", "site", "snippet", "rank"}``) with ``href`` the final page URL:

* ``mayo_search``      — Mayo Clinic's own search page, through the user's real Chrome
                         (Akamai blocks plain HTTP clients).
* ``nhs_search``       — NHS site search over plain HTTP; the result anchors point at a
                         ``/search/click?url=…`` redirect, which is decoded and never
                         followed (the URL embedded in the query string is the page).
* ``catalog_search``   — the site's sitemap (Hopkins via Chrome, Cleveland via HTTP),
                         cached as JSON and searched offline by URL slug. Used where
                         robots.txt disallows the site's own ``/search``.

``search`` merges the four sources (Mayo, Hopkins, NHS, Cleveland), round-robin so each
site gets representation, dedupes by href and re-ranks. Every failure is logged to
``http.log`` and swallowed — research must carry on without a site.
"""
import html
import html.parser
import json
import os
import re
import time
import urllib.parse
from typing import Optional

from . import google, http

MAYO_HOST = "www.mayoclinic.org"
MAYO_SEARCH_URL = "https://www.mayoclinic.org/search/search-results?q={q}"
MAYO_GAP = 4.0                        # politeness gap for the search page (patched in tests)
NHS_SEARCH_URL = "https://www.nhs.uk/search/results?q={q}"

CATALOG_MAX_AGE = 7 * 24 * 3600       # a week; older catalogs are refreshed

# Site order for the merged search (interleave starts here).
SITE_ORDER = ["mayoclinic.org", "hopkinsmedicine.org", "nhs.uk", "my.clevelandclinic.org"]

# The sitemap spec per site: ``browser`` marks the ones only reachable through Chrome.
CATALOGS = {
    "hopkinsmedicine.org": {"url": "https://www.hopkinsmedicine.org/health-sitemap.xml",
                            "browser": True},
    "my.clevelandclinic.org": {"url": "https://my.clevelandclinic.org/site.xml",
                               "browser": False},
}

# Mayo sections that hold patient health content (everything else is search/navigation/
# appointments/giving/locations, which must not leak in). The fixture's real results are
# all under /symptoms/.
MAYO_CONTENT_PATHS = {"diseases-conditions", "healthy-lifestyle", "symptoms", "tests-procedures",
                      "drugs-supplements", "first-aid", "medical-professionals"}

# NHS paths that hold health content; /search/click itself is never a destination.
NHS_CONTENT_PATHS = {"conditions", "medicines", "symptoms", "tests-and-treatments",
                     "live-well", "mental-health"}

_MAYO_SUFFIX = re.compile(r"\s*[-–—]\s*Mayo Clinic\s*$", re.I)


def _load_browser():
    """Import browser.py lazily so this module imports without it (and is easy to fake)."""
    from . import browser
    return browser


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _site_of(url: str) -> str:
    host = urllib.parse.urlparse(url or "").netloc.lower()
    return re.sub(r"^www\.", "", host)


def _clean_url(url: str) -> str:
    """Absolute URL without query/fragment (tracking params create duplicate pages)."""
    url = (url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc:
        return ""
    return urllib.parse.urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path, "", "", ""))


def _browser_open(url: str, wait: float = 4.0) -> Optional[dict]:
    """Load one page through the shared Chrome; None (and a log line) on failure."""
    try:
        browser = _load_browser()
        if not browser.available():
            http.log.append(("Sites", url[:160], "no browser"))
            return None
        page = browser.get_browser().open(url, wait=wait)
    except Exception as exc:
        http.log.append(("Sites", url[:160], f"{type(exc).__name__}: {exc}"))
        return None
    if not page or page.get("status") != "ok":
        http.log.append(("Sites", url[:160], (page or {}).get("status", "no page")))
        return None
    return page


# --------------------------------------------------------------------------- Mayo

class _MayoSearchParser(html.parser.HTMLParser):
    """Collects each ``a.azsearchlink`` result and the following ``<p>`` description."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._cur = None
        self._in_link = 0
        self._in_p = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            if "azsearchlink" in (attrs.get("class") or ""):
                self._cur = {"href": attrs.get("href") or "", "title": [], "snippet": []}
                self.results.append(self._cur)
                self._in_link = 1
        elif tag == "p" and self._cur is not None:
            self._in_p = 1

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_link = 0
        elif tag == "p":
            self._in_p = 0

    def handle_data(self, data):
        if self._cur is None:
            return
        if self._in_link:
            self._cur["title"].append(data)
        elif self._in_p:
            self._cur["snippet"].append(data)


def _mayo_content(url: str) -> bool:
    if _site_of(url) not in ("mayoclinic.org", MAYO_HOST):
        return False
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    return bool(parts) and parts[0] in MAYO_CONTENT_PATHS


def parse_mayo(html_text: str) -> list:
    """Mayo search results: trusted content links, title suffix stripped, 1-based rank."""
    parser = _MayoSearchParser()
    try:
        parser.feed(html_text or "")
        parser.close()
    except Exception:
        return []
    out, seen = [], set()
    for raw in parser.results:
        href = _clean_url(raw.get("href", ""))
        if not href or not _mayo_content(href) or href in seen:
            continue
        title = _MAYO_SUFFIX.sub("", _norm(" ".join(raw.get("title", [])))).strip()
        if not title:
            continue
        seen.add(href)
        out.append({"title": title, "href": href, "site": "mayoclinic.org",
                    "snippet": _norm(" ".join(raw.get("snippet", []))), "rank": len(out) + 1})
    return out


def mayo_search(query: str, max_results: int = 8) -> list:
    """Mayo's own search through Chrome; [] on any failure (never raises)."""
    url = MAYO_SEARCH_URL.format(q=urllib.parse.quote_plus(query))
    try:
        http._throttle(MAYO_HOST, MAYO_GAP)
        page = _browser_open(url, wait=4.0)
    except Exception as exc:
        http.log.append(("Sites", url[:160], f"{type(exc).__name__}: {exc}"))
        return []
    if not page:
        return []
    return parse_mayo(page.get("html") or "")[:max_results]


# --------------------------------------------------------------------------- NHS

class _NhsSearchParser(html.parser.HTMLParser):
    """Collects each ``a.app-search-results-item`` and the following ``<p>`` description."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._cur = None
        self._in_link = 0
        self._in_p = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            if "app-search-results-item" in (attrs.get("class") or ""):
                self._cur = {"href": attrs.get("href") or "", "title": [], "snippet": []}
                self.results.append(self._cur)
                self._in_link = 1
        elif tag == "p" and self._cur is not None:
            self._in_p = 1

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_link = 0
        elif tag == "p":
            self._in_p = 0

    def handle_data(self, data):
        if self._cur is None:
            return
        if self._in_link:
            self._cur["title"].append(data)
        elif self._in_p:
            self._cur["snippet"].append(data)


def _nhs_target(href: str) -> str:
    """Decode a /search/click?url=… redirect to the real page URL; never follow it."""
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http"):
        return _clean_url(href)
    if not href.startswith("/"):
        return ""
    target = (urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("url") or [""])[0]
    if not target:
        return ""
    if target.startswith("http"):
        return _clean_url(target)
    if not target.startswith("/"):
        target = "/" + target
    return _clean_url("https://www.nhs.uk" + target)


def _nhs_content(url: str) -> bool:
    if _site_of(url) != "nhs.uk":
        return False
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    return bool(parts) and parts[0] in NHS_CONTENT_PATHS


def parse_nhs(html_text: str) -> list:
    """NHS search results, decoded to final page URLs; 1-based rank."""
    parser = _NhsSearchParser()
    try:
        parser.feed(html_text or "")
        parser.close()
    except Exception:
        return []
    out, seen = [], set()
    for raw in parser.results:
        url = _nhs_target(raw.get("href", ""))
        if not url or not _nhs_content(url) or url in seen:
            continue
        title = _norm(" ".join(raw.get("title", [])))
        if not title:
            continue
        seen.add(url)
        out.append({"title": title, "href": url, "site": "nhs.uk",
                    "snippet": _norm(" ".join(raw.get("snippet", []))), "rank": len(out) + 1})
    return out


def nhs_search(query: str, max_results: int = 8) -> list:
    """NHS site search over plain HTTP; [] on any failure (never raises)."""
    url = NHS_SEARCH_URL.format(q=urllib.parse.quote_plus(query))
    try:
        page = http.get(url, source="NHS search")
    except Exception as exc:
        http.log.append(("Sites", url[:160], f"{type(exc).__name__}: {exc}"))
        return []
    if not page:
        return []
    return parse_nhs(page)[:max_results]


# --------------------------------------------------------------------------- sitemap catalogs

def parse_sitemap(text: str) -> list:
    """Every <loc> URL in a sitemap, html-unescaped, in document order."""
    return [html.unescape(m.group(1)).strip()
            for m in re.finditer(r"<loc[^>]*>(.*?)</loc>", text or "", re.I | re.S)
            if html.unescape(m.group(1)).strip()]


def _health_url(url: str) -> bool:
    return urllib.parse.urlparse(url or "").path.startswith("/health/")


def _catalog_key(site: str) -> Optional[str]:
    key = re.sub(r"^https?://", "", str(site or "").strip().lower()).split("/")[0]
    key = re.sub(r"^www\.", "", key).rstrip(".")
    if key == "clevelandclinic.org":
        key = "my.clevelandclinic.org"
    return key if key in CATALOGS else None


def _catalog_dir() -> str:
    override = os.environ.get("MB_CATALOG_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".cache", "medical_brain", "catalog")


def _catalog_path(key: str) -> str:
    return os.path.join(_catalog_dir(), key + ".json")


def _read_catalog(path: str) -> Optional[list]:
    try:
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                return [str(u) for u in data]
    except (OSError, ValueError):
        pass
    return None


def _catalog_fresh(path: str) -> bool:
    try:
        return time.time() - os.path.getmtime(path) < CATALOG_MAX_AGE
    except OSError:
        return False


def _write_catalog(path: str, urls: list) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(urls, handle)
    except OSError:
        pass


def _fetch_catalog(key: str) -> Optional[list]:
    """Fetch and parse one sitemap -> /health/ URLs, or None on any failure."""
    spec = CATALOGS[key]
    if spec["browser"]:
        page = _browser_open(spec["url"], wait=4.0)
        if not page:
            return None
        text = page.get("html") or page.get("text") or ""
    else:
        try:
            text = http.get(spec["url"], source="sitemap")
        except Exception as exc:
            http.log.append(("Sites", spec["url"], f"{type(exc).__name__}: {exc}"))
            return None
        if text is None:
            return None
    urls = [u for u in parse_sitemap(text) if _health_url(u)]
    return urls or None


def catalog(site: str) -> list:
    """Full /health/ URL list for a site, from a JSON cache refreshed weekly.

    A fresh cache is returned as-is; a stale one is refreshed, falling back to the stale
    list (or []) when the fetch fails. Never raises."""
    key = _catalog_key(site)
    if key is None:
        return []
    path = _catalog_path(key)
    cached = _read_catalog(path)
    if cached is not None and _catalog_fresh(path):
        return cached
    try:
        fetched = _fetch_catalog(key)
    except Exception as exc:
        http.log.append(("Sites", key, f"{type(exc).__name__}: {exc}"))
        fetched = None
    if fetched:
        _write_catalog(path, fetched)
        return fetched
    if cached is not None:
        http.log.append(("Sites", key, "sitemap fetch failed; using stale cache"))
        return cached
    return []


# --------------------------------------------------------------------------- catalog search

def _slug_words(url: str) -> list:
    """Path tokens split on / - _, lower-cased, numeric ids dropped."""
    path = urllib.parse.urlparse(url or "").path.lower()
    return [t for t in re.split(r"[/\-_]+", path) if t and not t.isdigit()]


def _slug_title(url: str) -> str:
    """Humanise the last path segment ('muscle-spasms-muscle-cramps' -> 'Muscle spasms muscle cramps')."""
    path = urllib.parse.urlparse(url or "").path.rstrip("/")
    segment = path.rsplit("/", 1)[-1]
    text = " ".join(w for w in re.split(r"[-_]+", segment) if w and not w.isdigit())
    return text[:1].upper() + text[1:] if text else _site_of(url)


def _url_score(url: str, words: list) -> int:
    """How many query content words the URL slug matches.

    An exact slug word scores 2, a substring / 5-letter-prefix match scores 1, so
    "cramps" beats a "nighttime" that only contains "night"."""
    slug = _slug_words(url)
    score = 0
    for w in words:
        if w in slug:
            score += 2
        elif any(w in s or w[:5] in s for s in slug):
            score += 1
    return score


_SHORT_STOPWORDS = {"and", "the", "for", "are", "can", "you", "how", "why", "what", "who", "not", "too", "get", "use"}


def catalog_rank(urls: list, query: str, max_results: int = 8) -> list:
    """Rank sitemap URLs against the query by slug match, best first, score > 0 only."""
    # 3-letter words count here ("leg", "arm", "flu" matter in medicine), and a page must
    # match at least half the query's words so "night terrors" doesn't answer "night leg cramps".
    words = [w for w in re.findall(r"[a-z]+", (query or "").lower())
             if len(w) >= 3 and w not in google.STOPWORDS and w not in _SHORT_STOPWORDS]
    need = max(1, (len(words) + 1) // 2)
    scored = sorted(((_url_score(u, words), sum(1 for w in words if _url_score(u, [w]) > 0), i, u)
                     for i, u in enumerate(urls or [])), key=lambda t: (-t[1], -t[0], t[2]))
    out = []
    for score, matched, _order, url in scored:
        if score <= 0 or matched < need or len(out) >= max_results:
            break
        out.append({"title": _slug_title(url), "href": url, "site": _site_of(url),
                    "snippet": "", "rank": len(out) + 1})
    return out


def catalog_search(site: str, query: str, max_results: int = 8) -> list:
    """Search a site's cached sitemap by URL slug; [] if there is no catalog."""
    return catalog_rank(catalog(site), query, max_results)


# --------------------------------------------------------------------------- merged search

def _wanted_sites(sites) -> set:
    if sites is None:
        return set(SITE_ORDER)
    wanted = set()
    for raw in sites or []:
        key = re.sub(r"^https?://", "", str(raw or "").strip().lower()).split("/")[0]
        key = re.sub(r"^www\.", "", key).rstrip(".")
        for domain in SITE_ORDER:
            if domain == key or domain.endswith("." + key) or key.endswith("." + domain):
                wanted.add(domain)
    return wanted


def search(query: str, sites=None, max_results: int = 8) -> list:
    """Merged site-native results: Mayo, Hopkins, NHS, Cleveland, round-robin.

    Each site contributes up to ``max_results``; duplicates (same href) are dropped and
    the survivors re-ranked 1..N. Never raises — a dead site is logged and skipped."""
    try:
        wanted = _wanted_sites(sites)
        engines = {
            "mayoclinic.org": lambda: mayo_search(query, max_results),
            "hopkinsmedicine.org": lambda: catalog_search("hopkinsmedicine.org", query, max_results),
            "nhs.uk": lambda: nhs_search(query, max_results),
            "my.clevelandclinic.org": lambda: catalog_search("my.clevelandclinic.org", query, max_results),
        }
        lists = []
        for domain in SITE_ORDER:
            if domain not in wanted:
                continue
            try:
                lists.append(engines[domain]() or [])
            except Exception as exc:
                http.log.append(("Sites", domain, f"{type(exc).__name__}: {exc}"))
                lists.append([])
        merged, seen = [], set()
        if lists:
            for i in range(max(len(lst) for lst in lists)):
                for lst in lists:
                    if i >= len(lst):
                        continue
                    result = lst[i]
                    href = result.get("href")
                    if not href or href in seen:
                        continue
                    seen.add(href)
                    merged.append(result)
        for i, result in enumerate(merged, 1):
            result["rank"] = i
        return merged
    except Exception as exc:                     # never raise: research must carry on
        http.log.append(("Sites", (query or "")[:120], f"{type(exc).__name__}: {exc}"))
        return []
