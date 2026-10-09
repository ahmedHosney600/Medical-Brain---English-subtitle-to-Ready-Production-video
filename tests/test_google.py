"""Google source, offline: fixtures for the SERP parser, a fake browser for search/fetch.
No network, no real Chrome, no LLM."""
import os
import tempfile
import unittest
import urllib.parse
from unittest import mock

from medical_brain.research.sources import google, http, web

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8", errors="replace") as handle:
        return handle.read()


# --------------------------------------------------------------------------- fake browser

class Blocked(Exception):
    def __init__(self, kind="", url=""):
        super().__init__(kind)
        self.kind = kind
        self.url = url


class FakeBrowser:
    """Returns canned page dicts; records every open()."""

    def __init__(self, pages=None, default=None):
        self.pages = pages or {}
        self.default = default
        self.opens = []
        self.shown = 0
        self.hidden = 0

    def open(self, url, wait=6.0):
        self.opens.append(url)
        page = self.pages.get(url, self.default)
        if page is None:
            page = {"url": url, "title": "", "html": "", "text": "", "status": "ok"}
        if isinstance(page, Exception):
            raise page
        return page

    def show(self, url=None):
        self.shown += 1
        self.shown_url = url

    def hide(self):
        self.hidden += 1


class FakeBrowserModule:
    Blocked = Blocked

    def __init__(self, client=None, available=True):
        self.client = client
        self._available = available

    def available(self):
        return self._available

    def get_browser(self):
        return self.client


def page(html, url="https://www.mayoclinic.org/x", title="Mayo", status="ok"):
    return {"url": url, "title": title, "html": html, "text": "", "status": status}


SERP_MIX = """
<div>
  <a href="/goto?url=AAA"><h3>Trusted result</h3><cite>https://www.mayoclinic.org/x</cite></a>
  <div>This snippet is comfortably longer than the twenty five character floor.</div>
</div>
<div>
  <a href="/goto?url=BBB"><h3>Untrusted result</h3><cite>https://webmd.com/y</cite></a>
</div>
"""


class GoogleTest(unittest.TestCase):
    def setUp(self):
        google.reset_run()
        http.log.clear()
        for name in ("MB_MAX_GOOGLE_QUERIES", "MB_BROWSER_ONLY", "RESEARCH_TRUSTED_DOMAINS"):
            os.environ.pop(name, None)
        self.tmp = tempfile.mkdtemp()
        http.set_cache_dir(self.tmp)
        self.addCleanup(http.set_cache_dir, None)
        for target, kwargs in (("medical_brain.research.sources.google.GOOGLE_GAP", 0.0),
                               ("medical_brain.research.sources.google.GOOGLE_JITTER", 0.0)):
            patcher = mock.patch(target, kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeper = mock.patch("time.sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def use_browser(self, client):
        patcher = mock.patch.object(google, "_load_browser", lambda: FakeBrowserModule(client))
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------------------------------------------------------------- parse_serp

    def test_parse_serp_both_fixtures(self):
        print("\n--- parsed SERP results ---")
        cases = [("google_serp_mayo.html", "mayoclinic.org"),
                 ("google_serp_hopkins.html", "hopkinsmedicine.org")]
        for name, domain in cases:
            results = google.parse_serp(fixture(name))
            print(f"{name}:")
            for r in results:
                print(f"  {r['rank']:>2}. {r['title']}  [{r['site']}]")
            self.assertGreaterEqual(len(results), 3)
            self.assertEqual([r["rank"] for r in results], list(range(1, len(results) + 1)))
            for r in results:
                self.assertTrue(r["title"])
                self.assertTrue(r["site"].endswith(domain), r["site"])
                self.assertTrue(r["href"].startswith("https://www.google.com/goto?url="))
            # at least one result is the plain trusted domain, not just a subdomain
            self.assertTrue(any(r["site"] == domain for r in results))
            self.assertTrue(any(r["snippet"] for r in results))
            self.assertEqual(len({r["href"] for r in results}), len(results))   # deduped

    def test_parse_serp_skips_anchors_without_h3(self):
        self.assertEqual(google.parse_serp('<a href="/goto?url=Z"><span>no title</span></a>'), [])

    # ---------------------------------------------------------------- build_query

    def test_build_query_parentheses_and_stripping(self):
        self.assertEqual(google.build_query("night leg cramps", ["mayoclinic.org", "nhs.uk"]),
                         "night leg cramps (site:mayoclinic.org OR site:nhs.uk)")
        self.assertEqual(google.build_query("cramps site:webmd.com also site:quack.io", ["nhs.uk"]),
                         "cramps also (site:nhs.uk)")
        self.assertEqual(google.build_query("cramps", []), "cramps")

    # ---------------------------------------------------------------- search

    def test_search_uses_cache_on_second_call(self):
        browser = FakeBrowser(default=page(fixture("google_serp_mayo.html")))
        self.use_browser(browser)
        first = google.search("night leg cramps", sites=["mayoclinic.org"])
        second = google.search("night leg cramps", sites=["mayoclinic.org"])
        self.assertEqual(len(browser.opens), 1)          # cached the second time
        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertIn("site:mayoclinic.org", urllib.parse.unquote(browser.opens[0]))

    def test_search_filters_untrusted_results(self):
        browser = FakeBrowser(default=page(SERP_MIX))
        self.use_browser(browser)
        results = google.search("cramps", sites=["mayoclinic.org"])
        self.assertEqual([r["title"] for r in results], ["Trusted result"])

    def test_search_limits_sites_to_eight(self):
        browser = FakeBrowser(default=page(fixture("google_serp_mayo.html")))
        self.use_browser(browser)
        sites = web.trusted_domains()
        self.assertGreater(len(sites), 8)
        google.search("cramps", sites=sites)
        self.assertEqual(urllib.parse.unquote(browser.opens[0]).count("site:"), 8)

    def test_captcha_noninteractive_disables_further_searches(self):
        browser = FakeBrowser(default=page("", status="captcha"))
        self.use_browser(browser)
        with mock.patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(google.search("a", sites=["mayoclinic.org"]), [])
            self.assertTrue(google._run["disabled"])
            opened = len(browser.opens)
            self.assertEqual(google.search("b", sites=["nhs.uk"]), [])
        self.assertEqual(len(browser.opens), opened)     # disabled: no more Google traffic

    def test_run_cap_limits_queries(self):
        browser = FakeBrowser(default=page(""))
        self.use_browser(browser)
        with mock.patch.dict(os.environ, {"MB_MAX_GOOGLE_QUERIES": "2"}):
            google.search("a", sites=["mayoclinic.org"])
            google.search("b", sites=["mayoclinic.org"])
            self.assertEqual(google.search("c", sites=["mayoclinic.org"]), [])
        self.assertEqual(len(browser.opens), 2)

    def test_search_never_raises(self):
        self.use_browser(FakeBrowser(default=Blocked("blocked", "x")))
        self.assertEqual(google.search("a", sites=["mayoclinic.org"]), [])

    def test_default_scopes_and_no_num_parameter(self):
        browser = FakeBrowser(default=page(fixture("google_serp_mayo.html")))
        self.use_browser(browser)
        google.search("night leg cramps")            # sites=None -> DEFAULT_SCOPES
        url = urllib.parse.unquote(browser.opens[0])
        self.assertNotIn("num=", url)                # Google removed num in 2025
        for scope in google.DEFAULT_SCOPES:
            self.assertIn(f"site:{scope}", url)
        self.assertEqual(url.count("site:"), 8)

    def test_search_drops_blocked_subdomains(self):
        browser = FakeBrowser(default=page(fixture("google_serp_hopkins.html")))
        self.use_browser(browser)
        results = google.search("muscle cramps", sites=["hopkinsmedicine.org/health"])
        sites = {r["site"] for r in results}
        # every job/housing/forum/news/blog host is gone …
        for bad in ("jobs.hopkinsmedicine.org", "offcampushousing.hopkinsmedicine.org",
                    "scical.hopkinsmedicine.org", "biomedicalodyssey.blogs.hopkinsmedicine.org"):
            self.assertNotIn(bad, sites)
        self.assertTrue(all(not google._is_blocked_site(r["site"]) for r in results))
        self.assertEqual([r for r in results if google._is_blocked_site(r["site"])], [])
        # … and no surviving title is a jobs/housing page
        titles = " ".join(r["title"] for r in results).lower()
        self.assertNotIn("jobs", titles)
        self.assertNotIn("housing", titles)

    # ---------------------------------------------------------------- fetch_page

    def test_fetch_page_browser_only_domain(self):
        client = FakeBrowser(pages={"https://www.mayoclinic.org/x": page("<p>Mayo</p>")})
        self.use_browser(client)
        with mock.patch("medical_brain.research.sources.http.get") as http_get:
            out = google.fetch_page("https://www.mayoclinic.org/x")
        self.assertEqual(out["url"], "https://www.mayoclinic.org/x")
        self.assertFalse(http_get.called)                # browser-only: http not tried
        self.assertEqual(len(client.opens), 1)

    def test_fetch_page_http_first_for_other_sites(self):
        client = FakeBrowser(default=page("<p>browser fallback</p>"))
        self.use_browser(client)
        with mock.patch("medical_brain.research.sources.http.get",
                        return_value="<title>NHS page</title><p>hi</p>") as http_get:
            out = google.fetch_page("https://www.nhs.uk/x")
        self.assertEqual(out["title"], "NHS page")
        self.assertTrue(http_get.called)
        self.assertEqual(client.opens, [])               # http succeeded, browser untouched

    def test_fetch_page_falls_back_to_browser(self):
        client = FakeBrowser(pages={"https://www.nhs.uk/x": page("<p>from browser</p>",
                                                                 url="https://www.nhs.uk/x",
                                                                 title="NHS")})
        self.use_browser(client)
        with mock.patch("medical_brain.research.sources.http.get", return_value=None):
            out = google.fetch_page("https://www.nhs.uk/x")
        self.assertEqual(out["title"], "NHS")
        self.assertEqual(len(client.opens), 1)

    def test_fetch_page_google_redirect_uses_browser(self):
        target = "https://www.mayoclinic.org/diseases/x"
        client = FakeBrowser(pages={"https://www.google.com/goto?url=ABC": page(
            "<p>resolved</p>", url=target, title="Mayo")})
        self.use_browser(client)
        out = google.fetch_page({"href": "https://www.google.com/goto?url=ABC"})
        self.assertEqual(out["url"], target)
        self.assertEqual(len(client.opens), 1)

    def test_fetch_page_rejects_untrusted_final_url(self):
        client = FakeBrowser(pages={"https://www.google.com/goto?url=EVIL": page(
            "<p>evil</p>", url="https://webmd.com/x", title="Evil")})
        self.use_browser(client)
        self.assertIsNone(google.fetch_page({"href": "https://www.google.com/goto?url=EVIL"}))

    # ---------------------------------------------------------------- paragraphs

    def test_paragraphs_blocks_and_filters(self):
        sample = """
        <html><head><title>T</title><style>.x{}</style></head><body>
        <nav>Navigation links here that should be skipped entirely.</nav>
        <h1>Night leg cramps</h1>
        <p>Most of the time there is no known cause for night leg cramps and they are harmless.</p>
        <p>tiny</p>
        <p>Most of the time there is no known cause for night leg cramps and they are harmless.</p>
        <script>var secret = "should not appear";</script>
        <ul><li>Stretching before bed may reduce night leg cramps for some people.</li></ul>
        </body></html>
        """
        blocks = google.paragraphs(sample)
        self.assertTrue(blocks[0].startswith("## Night leg cramps"))
        joined = "\n".join(blocks)
        self.assertIn("no known cause", joined)
        self.assertNotIn("tiny", joined)
        self.assertNotIn("secret", joined)
        self.assertNotIn("Navigation", joined)
        self.assertEqual(joined.count("no known cause"), 1)      # exact duplicate dropped
        self.assertIn("Stretching before bed", joined)

    def test_paragraphs_stops_at_limit(self):
        sample = "<p>" + ("x" * 400) + "</p><p>" + ("y" * 400) + "</p>"
        blocks = google.paragraphs(sample, limit_chars=500)
        self.assertEqual(len(blocks), 1)
        self.assertLessEqual(sum(len(b) for b in blocks), 500)

    def test_paragraphs_handles_body_wrapped_in_form(self):
        # Real Mayo pages wrap the whole article in one <form> and put the <h1> in <header>;
        # skipping those wholesale would return no blocks at all.
        sample = ("<html><body><form><header><h1>Muscle cramp</h1></header>"
                  "<p>A muscle cramp is a sudden, unexpected tightening of one or more "
                  "muscles and it can be very painful.</p></form></body></html>")
        blocks = google.paragraphs(sample)
        self.assertIn("## Muscle cramp", blocks)
        self.assertTrue(any(b.startswith("A muscle cramp is a sudden") for b in blocks))

    # ---------------------------------------------------------------- to_source

    def test_to_source_verbatim_page_order_and_bad_indices(self):
        page_dict = {"title": "Mayo", "url": "https://www.mayoclinic.org/x"}
        blocks = ["## Heading", "First body block text here.", "Second body block text here."]
        src = google.to_source(page_dict, [3, 1, 99, 0, -2, "nope"], blocks)
        self.assertEqual(src["text"], "## Heading\nSecond body block text here.")
        self.assertEqual(src["origin"], "Web (Google)")
        self.assertEqual(src["title"], "Mayo")
        self.assertEqual(src["url"], "https://www.mayoclinic.org/x")

    def test_to_source_caps_text(self):
        page_dict = {"title": "T", "url": "https://www.nhs.uk/x"}
        src = google.to_source(page_dict, [1], ["z" * 7000])
        self.assertEqual(len(src["text"]), 6000)


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1",
                     "set RUN_BROWSER_TESTS=1 to search Google with the real Chrome")
class RealGoogleSmokeTest(unittest.TestCase):
    """One live round: real Chrome, real Google, real page fetch. Run once (Google rate-limits)."""

    def test_real_search_then_fetch(self):
        google.reset_run()
        results = google.search("night leg cramps")
        if not results:
            print(f"[google live] search returned nothing; http.log={http.log[-5:]}")
        print("\n[google live] search('night leg cramps'):")
        for r in results:
            print(f"  {r['rank']:>2}. {r['site']}  —  {r['title']}")
        self.assertGreaterEqual(len(results), 3)
        for r in results:
            self.assertTrue(web.is_trusted("https://" + r["site"]), r["site"])
            self.assertFalse(google._is_blocked_site(r["site"]), r["site"])

        target = next((r for r in results
                       if r["site"].endswith(("mayoclinic.org", "hopkinsmedicine.org"))), None)
        if target is None:
            # the default scopes didn't surface one this time; still exercise the
            # browser-only fetch path on a known Mayo article.
            print("[google live] no Mayo/Hopkins result; fetching a known Mayo article directly")
            target = "https://www.mayoclinic.org/diseases-conditions/muscle-cramp/"
            target += "symptoms-causes/syc-20350820"
        page = google.fetch_page(target)
        self.assertIsNotNone(page, "fetch_page returned None")
        blocks = google.paragraphs(page["html"])
        print(f"[google live] fetched {page['url']!r} title={page['title']!r} paragraphs={len(blocks)}")
        self.assertTrue(page["html"])


if __name__ == "__main__":
    unittest.main()
