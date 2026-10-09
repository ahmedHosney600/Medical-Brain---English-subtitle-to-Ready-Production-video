"""Google search restricted to trusted medical sites, driven through the user's real
Chrome, plus a passage splitter for the pages it fetches.

The SERP parser is pure stdlib (``html.parser``) and parses by structure — an anchor whose
href is a Google redirect (``/goto?url=`` or ``/url?``) and which contains an ``<h3>``
title and a ``<cite>`` breadcrumb — never by the (obfuscated, ever-changing) class names.

There are no LLM calls in this module; the passage picker is wired up later.
"""
import html
import html.parser
import json
import os
import random
import re
import sys
import urllib.parse
from typing import Optional

from . import http, web

GOOGLE_HOST = "www.google.com"
SEARCH_URL = "https://www.google.com/search?q={q}&hl=en&gl=us"

# Politeness gap between Google queries (patched to 0 by the tests).
GOOGLE_GAP = 12.0
GOOGLE_JITTER = 5.0                      # extra random 0-5 s, so runs don't look robotic

MAX_SITES_PER_QUERY = 8                  # Google truncates very long queries (one site: each)
DEFAULT_MAX_RESULTS = 10

# Patient-health entry points (host + path), not whole domains: a bare
# site:hopkinsmedicine.org returns jobs/housing/department pages and a bare
# site:mayoclinic.org returns patient-forum and newsroom posts.
DEFAULT_SCOPES = [
    "mayoclinic.org/diseases-conditions",
    "mayoclinic.org/healthy-lifestyle",
    "hopkinsmedicine.org/health",
    "nhs.uk/conditions",
    "my.clevelandclinic.org/health",
    "medlineplus.gov",
    "cdc.gov",
    "who.int/news-room/fact-sheets",
]

# Subdomain labels that never hold patient-health content (jobs, forums, news, …).
BLOCKED_LABELS = {"connect", "jobs", "careers", "newsnetwork", "news", "offcampushousing",
                  "profiles", "scical", "community", "forums", "giving", "blogs"}

# Sites that only work through the real browser (JavaScript / bot-walled). Extend with
# the comma-separated MB_BROWSER_ONLY env var.
BROWSER_ONLY = {"mayoclinic.org", "hopkinsmedicine.org"}

_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"}
# paragraphs() uses a slightly wider view than web.page_text: real article pages (e.g. Mayo)
# wrap the whole body in one <form> and put the article <h1> in <header>, so skipping those
# wholesale would drop the very content we are after.
_BLOCK_SKIP_TAGS = _SKIP_TAGS - {"form", "header"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
              "param", "source", "track", "wbr"}

# Per-run state: a query counter (so a broken run can't hammer Google) and a hard disable
# flag set when Google shows a captcha we can't get past.
_run = {"queries": 0, "disabled": False}


def reset_run() -> None:
    """Forget the per-run query counter and re-enable Google (called at the start of a run)."""
    _run["queries"] = 0
    _run["disabled"] = False


def _load_browser():
    """Import browser.py lazily so this module imports without it (and is easy to fake)."""
    from . import browser
    return browser


# --------------------------------------------------------------------------- SERP parsing

class _Node:
    __slots__ = ("tag", "attrs", "children", "parent", "parts")

    def __init__(self, tag: str = "", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list = []
        self.parent: Optional["_Node"] = None
        self.parts: list = []


class _Tree(html.parser.HTMLParser):
    """A tiny forgiving DOM builder — enough to walk anchors and their containers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        node.parent = self.cur
        self.cur.children.append(node)
        if tag not in _VOID_TAGS:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        if data.strip():
            self.cur.parts.append(data)


def _descend(node, tag):
    for child in node.children:
        if child.tag == tag:
            yield child
        yield from _descend(child, tag)


def _contains(node, tag) -> bool:
    return any(True for _ in _descend(node, tag))


def _visible_text(node) -> str:
    if node.tag in _SKIP_TAGS:
        return ""
    out = list(node.parts)
    for child in node.children:
        text = _visible_text(child)
        if text:
            out.append(text)
    return " ".join(out)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _site_from_cite(cite_text: str) -> str:
    """'https://www.mayoclinic.org › Symptoms' -> 'mayoclinic.org' (no www.)."""
    match = re.search(r"(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})",
                      _norm(cite_text), re.I)
    return match.group(1).lower().rstrip(".") if match else ""


def _snippet(anchor: "_Node") -> str:
    """Best-effort snippet: the longest text block that is a sibling (at some level) of the
    title anchor within its result container, ignoring breadcrumb blocks that hold a <cite>."""
    node = anchor
    while node is not None and node.parent is not None:
        best = ""
        for sibling in node.parent.children:
            if sibling is node or sibling.tag in _SKIP_TAGS or _contains(sibling, "cite"):
                continue
            text = _norm(_visible_text(sibling))
            if len(text) > len(best):
                best = text
        if len(best) >= 25:
            return best
        node = node.parent
    return ""


def _is_redirect_href(href: str) -> bool:
    return href.startswith("/goto?url=") or href.startswith("/url?")


def parse_serp(html_text: str) -> list:
    """Organic results from a Google results page, parsed structurally.

    Each result: title, absolute href, site (from the cite breadcrumb), snippet, 1-based rank.
    """
    tree = _Tree()
    try:
        tree.feed(html_text or "")
        tree.close()
    except Exception:
        return []
    results, seen, rank = [], set(), 0
    for anchor in _descend(tree.root, "a"):
        href = (anchor.attrs.get("href") or "").strip()
        if not _is_redirect_href(href):
            continue
        h3 = next(_descend(anchor, "h3"), None)
        cite = next(_descend(anchor, "cite"), None)
        if h3 is None or cite is None:
            continue
        title = _norm(_visible_text(h3))
        if not title:
            continue
        absolute = href if href.startswith("http") else "https://www.google.com" + href
        if absolute in seen:                     # dedupe by href
            continue
        seen.add(absolute)
        rank += 1
        results.append({"title": title, "href": absolute, "site": _site_from_cite(_visible_text(cite)),
                        "snippet": _snippet(anchor), "rank": rank})
    return results


# --------------------------------------------------------------------------- query building

def build_query(query: str, sites: list) -> str:
    """'<query> (site:a OR site:b ...)'; strips any site: operators the caller already added."""
    text = re.sub(r"\bsite:\S+", " ", query or "", flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    sites = [s.strip() for s in (sites or []) if s and str(s).strip()]
    if not sites:
        return text
    clause = " OR ".join(f"site:{s}" for s in sites)
    return f"{text} ({clause})".strip()


def _default_sites() -> list:
    return list(DEFAULT_SCOPES)


def _site_trusted(site: str) -> bool:
    return bool(site) and web.is_trusted("https://" + site)


def _is_blocked_site(site: str) -> bool:
    """True for job/forum/news/department hosts that are not patient-health content."""
    if not site:
        return True
    if site.split(".", 1)[0] in BLOCKED_LABELS:
        return True
    return ".blogs." in site


# --------------------------------------------------------------------------- search

def _captcha_prompt(browser, url: str) -> bool:
    """Ask the user to clear Google's human check in the visible Chrome window."""
    if not sys.stdin.isatty():
        _disable("captcha (non-interactive)")
        return False
    try:
        browser.show(url)             # keep the captcha page open, on-screen, for the user
    except Exception:
        pass
    print("Google is asking for a human check — solve it in the Chrome window, then press Enter here.")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        _disable("captcha (cancelled)")
        return False
    finally:
        try:
            browser.hide()            # close the captcha tab again
        except Exception:
            pass
    return True


def _disable(reason: str) -> None:
    _run["disabled"] = True
    http.log.append(("Google", "", reason))


def _google_html(url: str) -> Optional[str]:
    """Fetch one results page through the browser, handling captcha/blocked. None on failure."""
    browser = _load_browser()
    if not browser.available():
        http.log.append(("Google", url[:160], "no browser"))
        return None
    client = browser.get_browser()
    try:
        page = client.open(url, wait=6.0)
    except browser.Blocked as exc:
        http.log.append(("Google", url[:160], f"blocked ({getattr(exc, 'kind', '')})"))
        return None
    if (page or {}).get("status") == "captcha":
        captcha_url = (page or {}).get("url") or url
        if not _captcha_prompt(client, captcha_url):
            return None
        try:
            page = client.open(url, wait=6.0)
        except browser.Blocked as exc:
            http.log.append(("Google", url[:160], f"blocked ({getattr(exc, 'kind', '')})"))
            return None
        if (page or {}).get("status") == "captcha":
            _disable("captcha persists")
            return None
    status = (page or {}).get("status", "")
    if status != "ok":
        http.log.append(("Google", url[:160], status or "not ok"))
        return None
    return page.get("html") or ""


def search(query: str, sites=None, max_results: int = DEFAULT_MAX_RESULTS) -> list:
    """Trusted-site Google results (browser-driven) or [] — never raises to the caller."""
    if _run["disabled"]:
        return []
    try:
        limit = int(os.environ.get("MB_MAX_GOOGLE_QUERIES", 40) or 40)
    except ValueError:
        limit = 40
    if _run["queries"] >= limit:
        http.log.append(("Google", "", f"run cap reached ({limit} queries)"))
        return []
    chosen_sites = list(sites) if sites is not None else _default_sites()
    chosen_sites = [s for s in chosen_sites if s][:MAX_SITES_PER_QUERY]
    url = SEARCH_URL.format(q=urllib.parse.quote(build_query(query, chosen_sites), safe=""))
    key = "google:" + url
    cached = http._cache_path(key)
    if cached and os.path.exists(cached):
        try:
            with open(cached, encoding="utf-8") as handle:
                return json.load(handle)
        except (ValueError, OSError):
            pass

    _run["queries"] += 1
    try:
        http._throttle(GOOGLE_HOST, GOOGLE_GAP + random.uniform(0, GOOGLE_JITTER))
        html_text = _google_html(url)
        if html_text is None:
            return []
        results = [r for r in parse_serp(html_text)
                   if _site_trusted(r["site"]) and not _is_blocked_site(r["site"])][:max_results]
        if cached:
            with open(cached, "w", encoding="utf-8") as handle:
                json.dump(results, handle)
        return results
    except Exception as exc:                     # never raise: research must carry on
        http.log.append(("Google", url[:160], f"{type(exc).__name__}: {exc}"))
        return []


# --------------------------------------------------------------------------- page fetching

def _browser_only() -> set:
    domains = set(BROWSER_ONLY)
    for domain in os.environ.get("MB_BROWSER_ONLY", "").split(","):
        domain = domain.strip().lower()
        if domain:
            domains.add(domain)
    return domains


def _is_browser_only(site: str) -> bool:
    return any(site == d or site.endswith("." + d) for d in _browser_only())


def _site_of(url: str) -> str:
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url or "").split("/")[0].lower())


def _is_google_redirect(url: str) -> bool:
    return bool(re.match(r"https?://(www\.)?google\.[a-z.]+/(goto|url)\?", url or "", re.I))


def _title_of(html_text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text or "", re.I | re.S)
    return _norm(re.sub(r"<[^>]+>", " ", match.group(1))) if match else ""


def _read_page_cache(url: str) -> Optional[dict]:
    cached = http._cache_path("page:" + url)
    if not cached or not os.path.exists(cached):
        return None
    try:
        with open(cached, encoding="utf-8") as handle:
            html_text = handle.read()
    except OSError:
        return None
    return {"url": url, "title": _title_of(html_text), "html": html_text} if html_text else None


def _write_page_cache(url: str, final_url: str, html_text: str) -> None:
    for key_url in {url, final_url}:
        path = http._cache_path("page:" + key_url)
        if not path:
            continue
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(html_text)
        except OSError:
            pass


def _browser_page(url: str) -> Optional[dict]:
    cached = _read_page_cache(url)
    if cached:
        return cached
    browser = _load_browser()
    if not browser.available():
        http.log.append(("Google", url[:160], "browser unavailable"))
        return None
    client = browser.get_browser()
    try:
        page = client.open(url, wait=6.0)
    except browser.Blocked as exc:
        http.log.append(("Google", url[:160], f"blocked ({getattr(exc, 'kind', '')})"))
        return None
    if not page or page.get("status") != "ok":
        http.log.append(("Google", url[:160], (page or {}).get("status", "no page")))
        return None
    final_url = page.get("url") or url
    if not web.is_trusted(final_url):
        http.log.append(("Google", final_url[:160], "untrusted after redirect"))
        return None
    html_text = page.get("html") or ""
    _write_page_cache(url, final_url, html_text)
    return {"url": final_url, "title": page.get("title") or _title_of(html_text), "html": html_text}


def fetch_page(result_or_url) -> Optional[dict]:
    """Open a Google result (following its redirect) or a plain URL → {url,title,html} or None."""
    url = result_or_url.get("href", "") if isinstance(result_or_url, dict) else (result_or_url or "")
    if not url:
        return None
    if _is_google_redirect(url) or _is_browser_only(_site_of(url)):
        return _browser_page(url)
    cached = _read_page_cache(url)
    if cached:
        return cached
    html_text = http.get(url, source="web page")
    if html_text:
        return {"url": url, "title": _title_of(html_text), "html": html_text}
    return _browser_page(url)


# --------------------------------------------------------------------------- blocks & source

class _Blocks(html.parser.HTMLParser):
    """Visible text as ordered blocks: headings (h1-h4) and p/li/td text."""

    HEAD = {"h1", "h2", "h3", "h4"}
    BLOCK = {"p", "li", "td"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.stack: list = []
        self.blocks: list = []

    def _flush(self) -> None:
        if not self.stack:
            return
        tag, parts = self.stack.pop()
        text = _norm(" ".join(parts))
        if text:
            self.blocks.append("## " + text if tag in self.HEAD else text)

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in self.HEAD or tag in self.BLOCK:
            self._flush()                     # implicitly close a previous unclosed block
            self.stack.append([tag, []])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.stack and not self.skip:
            self.stack[-1][1].append(data)

    def handle_endtag(self, tag):
        if tag in _BLOCK_SKIP_TAGS:
            if self.skip:
                self.skip -= 1
            return
        if tag not in self.HEAD and tag not in self.BLOCK:
            return
        while self.stack:
            top = self.stack[-1][0]
            self._flush()
            if top == tag:
                break

    def result(self) -> list:
        while self.stack:
            self._flush()
        return self.blocks


def paragraphs(html_text: str, limit_chars: int = 30000) -> list:
    """Ordered blocks for the LLM passage picker; drops short blocks, exact duplicates and
    hidden boilerplate, stopping once limit_chars is reached."""
    parser = _Blocks()
    try:
        parser.feed(html_text or "")
        parser.close()
    except Exception:
        return []
    out, seen, total = [], set(), 0
    for block in parser.result():
        heading = block.startswith("## ")
        if not heading and len(block) < 25:
            continue
        if block in seen:
            continue
        if total + len(block) > limit_chars:
            break
        seen.add(block)
        out.append(block)
        total += len(block)
    return out


def to_source(page: dict, keep: list, blocks: list) -> dict:
    """A research source from the blocks (1-based indices) the LLM chose, in page order."""
    indices = set()
    for value in keep or []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(blocks):
            indices.add(index)
    text = "\n".join(blocks[i - 1] for i in sorted(indices))[:6000]
    source = web._source(page.get("title", ""), page.get("url", ""), text)
    source["origin"] = "Web (Google)"
    return source
