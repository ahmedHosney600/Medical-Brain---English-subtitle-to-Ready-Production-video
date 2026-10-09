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
import time
import urllib.parse
from typing import Optional

from . import http, web

GOOGLE_HOST = "www.google.com"
SEARCH_URL = "https://www.google.com/search?q={q}&hl=en&gl=us"

# Politeness gap between Google queries (patched to 0 by the tests).
GOOGLE_GAP = 18.0
GOOGLE_JITTER = 7.0                      # extra random 0-7 s, so runs don't look robotic

MAX_DOMAINS_PER_QUERY = 2                # Google honours site: for at most two domains per query
DEFAULT_MAX_RESULTS = 10

# Literature hosts already covered by the PubMed / Europe PMC sources: drop them from
# Google results so they don't eat the web-search slots. Matches subdomains (pubmed., pmc.).
EXCLUDE_DOMAINS = {"ncbi.nlm.nih.gov", "europepmc.org"}

# Patient-health domains, two per query: measured live, Google only honours `site:`
# for ≤2 domains and ignores paths (paths let Spotify/Wikipedia/WebMD leak in).
SITE_GROUPS = [
    ("mayoclinic.org", "hopkinsmedicine.org"),
    ("nhs.uk", "my.clevelandclinic.org"),
]

# Subdomain labels that never hold patient-health content (jobs, forums, news, …).
BLOCKED_LABELS = {"connect", "jobs", "careers", "newsnetwork", "news", "offcampushousing",
                  "profiles", "scical", "community", "forums", "giving", "blogs"}

# Words that carry no topic signal in a query, for the degraded-page check.
STOPWORDS = {"what", "when", "which", "with", "from", "that", "this", "have", "does", "about",
             "your", "should", "how", "why", "during", "after", "before", "fast", "best",
             "causes", "treatment"}

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

# Per-run state: a query counter (so a broken run can't hammer Google), the per-run cap,
# a hard disable flag set when Google shows a captcha we can't get past, and whether we
# already spent the one fresh-profile restart.
_run = {"queries": 0, "disabled": False, "max": 24, "restarted": False}


def reset_run(max_queries=None) -> None:
    """Forget the per-run query counter and re-enable Google (called at the start of a run).

    max_queries is the run cap; MB_MAX_GOOGLE_QUERIES still wins when it is set."""
    _run["queries"] = 0
    _run["disabled"] = False
    _run["restarted"] = False
    if max_queries is not None:
        try:
            _run["max"] = int(max_queries)
        except (TypeError, ValueError):
            pass


def _query_limit() -> int:
    raw = os.environ.get("MB_MAX_GOOGLE_QUERIES")
    try:
        return int(raw) if raw not in (None, "") else int(_run.get("max") or 24)
    except (TypeError, ValueError):
        return int(_run.get("max") or 24)


def remaining() -> int:
    """Queries left in this run (never negative) — lets callers reserve budget."""
    return max(0, _query_limit() - int(_run.get("queries") or 0))


def _gap() -> float:
    """The politeness gap plus jitter, evaluated live so tests can patch the constants."""
    return GOOGLE_GAP + random.uniform(0, GOOGLE_JITTER)


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

def _domain(site: str) -> str:
    """'mayoclinic.org/diseases-conditions' -> 'mayoclinic.org' (Google ignores paths)."""
    text = re.sub(r"^[a-z]+://", "", str(site or "").strip().lower())
    return text.split("/", 1)[0].strip().rstrip(".")


def build_query(query: str, sites: list) -> str:
    """'<query> (site:a OR site:b)'; strips site: operators and paths, ≤2 bare domains."""
    text = re.sub(r"\bsite:\S+", " ", query or "", flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    domains = [d for d in (_domain(s) for s in (sites or [])) if d][:MAX_DOMAINS_PER_QUERY]
    if not domains:
        return text
    clause = " OR ".join(f"site:{d}" for d in domains)
    return f"{text} ({clause})".strip()


def _site_groups() -> list:
    """Two-domain groups to query; MB_GOOGLE_GROUPS ('a,b;c,d') overrides SITE_GROUPS."""
    groups = []
    for group in os.environ.get("MB_GOOGLE_GROUPS", "").split(";"):
        domains = [d for d in (_domain(x) for x in group.split(",")) if d][:MAX_DOMAINS_PER_QUERY]
        if domains:
            groups.append(tuple(domains))
    return groups or list(SITE_GROUPS)


def _site_trusted(site: str) -> bool:
    return bool(site) and web.is_trusted("https://" + site)


def _is_blocked_site(site: str) -> bool:
    """True for job/forum/news/department hosts that are not patient-health content."""
    if not site:
        return True
    if site.split(".", 1)[0] in BLOCKED_LABELS:
        return True
    return ".blogs." in site


def _is_excluded_site(site: str) -> bool:
    """True for literature hosts already covered by the PubMed / Europe PMC sources."""
    site = (site or "").lower()
    return any(site == d or site.endswith("." + d) for d in EXCLUDE_DOMAINS)


def _filter_results(rows: list, max_results: int) -> list:
    """Keep only trusted patient-health results, minus the literature hosts."""
    return [r for r in (rows or [])
            if _site_trusted(r.get("site", ""))
            and not _is_blocked_site(r.get("site", ""))
            and not _is_excluded_site(r.get("site", ""))][:max_results]


def _content_words(query: str) -> list:
    """Query words that carry topic signal: >=4 letters, minus the stopword list."""
    return [w for w in re.findall(r"[a-z]+", (query or "").lower())
            if len(w) >= 4 and w not in STOPWORDS]


def _matches_domain(site: str, domains: list) -> bool:
    """True when `site` is one of the requested bare domains (or a subdomain of one)."""
    site = (site or "").lower()
    return any(site == d or site.endswith("." + d) for d in domains)


def is_degraded(query: str, results: list, domains=None) -> bool:
    """True when Google returned a generic page for an unrelated query, or ignored the
    requested domains.

    Content-word heuristic: of the top 5 results, fewer than 2 share any content word with
    the query (matched on title + snippet, allowing a 5-letter prefix so "cramp" matches
    "cramps"). Domain heuristic (when `domains` is passed): among the top 5 results, fewer
    than half come from the requested domains — this catches Google quietly relaxing
    ``site:``. No content words (or no results) -> never degraded."""
    top = results[:5] if results else []
    if not top:
        return False
    words = _content_words(query)
    if words:
        matched = 0
        for result in top:
            text = f"{result.get('title') or ''} {result.get('snippet') or ''}".lower()
            if any(word in text or word[:5] in text for word in words):
                matched += 1
        if matched < min(2, len(top)):              # a niche query may have just one hit
            return True
    wanted = [_domain(d) for d in (domains or []) if _domain(d)]
    if wanted:
        from_wanted = sum(1 for r in top if _matches_domain(r.get("site", ""), wanted))
        if from_wanted * 2 < len(top):             # strictly fewer than half
            return True
    return False


# --------------------------------------------------------------------------- search

def _captcha_prompt(browser, url: str) -> bool:
    """Ask the user to clear Google's human check in the visible Chrome window.

    Only the interactive hand-off: a non-TTY run returns False without disabling, so the
    caller can try a fresh profile before giving up on Google."""
    if not sys.stdin.isatty():
        return False
    try:
        show_search = getattr(browser, "show_search", None)
        if callable(show_search):
            show_search()             # the captcha lives in the long-lived search tab already
        else:
            browser.show(url)         # keep the captcha page open, on-screen, for the user
    except Exception:
        pass
    print("Google is asking for a human check — solve it in the Chrome window, then press Enter here.")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        return False
    finally:
        try:
            browser.hide()            # close the captcha tab again
        except Exception:
            pass
    return True


def _disable(reason: str) -> None:
    if _run["disabled"]:              # log the reason once
        return
    _run["disabled"] = True
    http.log.append(("Google", "", reason))


def _google_page(url: str):
    """Fetch one results page through the browser -> (html, status).

    status is "ok", "captcha", "blocked"/another page status, or "unavailable". For a
    captcha it offers the interactive hand-off (TTY only) before returning "captcha"."""
    browser = _load_browser()
    if not browser.available():
        http.log.append(("Google", url[:160], "no browser"))
        return None, "unavailable"
    client = browser.get_browser()
    try:
        page = client.search(url)          # one long-lived tab: a fresh tab per query gets throttled
    except browser.Blocked as exc:
        http.log.append(("Google", url[:160], f"blocked ({getattr(exc, 'kind', '')})"))
        return None, "blocked"
    if (page or {}).get("status") == "captcha":
        captcha_url = (page or {}).get("url") or url
        if not _captcha_prompt(client, captcha_url):
            return None, "captcha"
        try:
            page = client.search(url)
        except browser.Blocked as exc:
            http.log.append(("Google", url[:160], f"blocked ({getattr(exc, 'kind', '')})"))
            return None, "blocked"
        if (page or {}).get("status") == "captcha":
            return None, "captcha"
    status = (page or {}).get("status", "")
    if status != "ok":
        http.log.append(("Google", url[:160], status or "not ok"))
        return None, status or "not ok"
    return page.get("html") or "", "ok"


def _recover(failure: str, retry):
    """Degraded page or captcha: use the one fresh profile (restart + retry once), else disable.

    `retry` is a zero-arg callable returning (results, failure)."""
    if _run["restarted"]:
        _disable(f"{failure} persisted: disabled for the rest of the run")
        return [], failure
    _run["restarted"] = True
    try:
        _load_browser().get_browser().restart_fresh()
    except Exception as exc:          # a failed restart still gets one retry attempt
        http.log.append(("Google", "", f"restart_fresh failed ({type(exc).__name__}: {exc})"))
    time.sleep(_gap())
    results, again = retry()
    if again in ("captcha", "degraded"):
        _disable(f"{again} persisted after a fresh profile: disabled for the rest of the run")
        return [], again
    return results, ""


def _search_group(query: str, sites: list, max_results: int):
    """One Google query for one site group -> (results, failure).

    failure is "" on success or "captcha"/"degraded" so search() can recover. Healthy
    results are cached; degraded pages are not. Never raises."""
    if _run["disabled"]:
        return [], "disabled"
    url = SEARCH_URL.format(q=urllib.parse.quote(build_query(query, sites), safe=""))
    key = "google:" + url
    cached = http._cache_path(key)
    if cached and os.path.exists(cached):
        try:
            with open(cached, encoding="utf-8") as handle:
                return _filter_results(json.load(handle), max_results), ""
        except (ValueError, OSError):
            pass

    _run["queries"] += 1
    try:
        http._throttle(GOOGLE_HOST, _gap())
        html_text, status = _google_page(url)
        if html_text is None:
            return [], "captcha" if status == "captcha" else ""
        results = _filter_results(parse_serp(html_text), max_results)
        if is_degraded(query, results, sites):
            return results, "degraded"
        if cached:
            with open(cached, "w", encoding="utf-8") as handle:
                json.dump(results, handle)
        return results, ""
    except Exception as exc:                     # never raise: research must carry on
        http.log.append(("Google", url[:160], f"{type(exc).__name__}: {exc}"))
        return [], ""


def search(query: str, sites=None, max_results: int = DEFAULT_MAX_RESULTS) -> list:
    """Trusted-site Google results (browser-driven) or [] — never raises to the caller.

    sites=None runs one query per SITE_GROUPS group; an explicit list is chunked two
    domains per query. Results merge in group order, deduped by href, re-ranked 1..N.
    A degraded page or captcha triggers one fresh-profile restart + retry, then disables."""
    if _run["disabled"]:
        return []
    if sites is None:
        groups = [list(group) for group in _site_groups()]
    else:
        domains = [d for d in (_domain(s) for s in sites) if d]
        groups = [domains[i:i + MAX_DOMAINS_PER_QUERY]
                  for i in range(0, len(domains), MAX_DOMAINS_PER_QUERY)]
    limit, merged, seen = _query_limit(), [], set()
    for group in groups:
        if _run["queries"] >= limit:
            http.log.append(("Google", "", f"run cap reached ({limit} queries)"))
            break
        results, failure = _search_group(query, group, max_results)
        if failure in ("captcha", "degraded"):
            results, failure = _recover(failure,
                                        lambda: _search_group(query, group, max_results))
        for result in results:
            if result["href"] in seen or _is_excluded_site(result.get("site", "")):
                continue
            seen.add(result["href"])
            merged.append(result)
        if _run["disabled"]:
            break
    for i, result in enumerate(merged, 1):
        result["rank"] = i
    return merged


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
    source["origin"] = "Web"
    return source
