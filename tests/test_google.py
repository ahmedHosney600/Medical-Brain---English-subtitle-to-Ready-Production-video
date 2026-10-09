"""Google source, offline: fixtures for the SERP parser, a fake browser for search/fetch.
No network, no real Chrome, no LLM."""
import math
import os
import re
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
    """Returns canned page dicts; records every open()/search(). A `sequence` overrides
    `pages`/`default` one response at a time (used for the degraded-then-good restart test).
    Google result pages go through search(); fetched result pages still go through open()."""

    def __init__(self, pages=None, default=None, sequence=None):
        self.pages = pages or {}
        self.default = default
        self.sequence = list(sequence or [])
        self.opens = []
        self.searches = []
        self.shown = 0
        self.shown_search = 0
        self.hidden = 0
        self.restarts = 0

    def _respond(self, url):
        if self.sequence:
            page = self.sequence.pop(0)
        else:
            page = self.pages.get(url, self.default)
        if page is None:
            page = {"url": url, "title": "", "html": "", "text": "", "status": "ok"}
        if isinstance(page, Exception):
            raise page
        return page

    def open(self, url, wait=6.0):
        self.opens.append(url)
        return self._respond(url)

    def search(self, url, settle=4.0):
        self.searches.append(url)
        return self._respond(url)

    def restart_fresh(self):
        self.restarts += 1

    def show(self, url=None):
        self.shown += 1
        self.shown_url = url

    def show_search(self):
        self.shown_search += 1

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
  <a href="/goto?url=AAA"><h3>Trusted cramp result</h3><cite>https://www.mayoclinic.org/x</cite></a>
  <div>This snippet is comfortably longer than the twenty five character floor.</div>
</div>
<div>
  <a href="/goto?url=AAB"><h3>Another trusted cramp result</h3><cite>https://www.nhs.uk/y</cite></a>
  <div>Another snippet comfortably longer than the twenty five character floor.</div>
</div>
<div>
  <a href="/goto?url=BBB"><h3>Untrusted result</h3><cite>https://webmd.com/y</cite></a>
</div>
"""

# The generic replay a throttled profile returned in the real run: same trusted hosts, but
# nothing to do with the query "how to stop a muscle cramp fast".
GENERIC_SERP = """
<div>
  <a href="/goto?url=G1"><h3>Drugs and Supplements</h3><cite>https://www.mayoclinic.org/drugs-supplements</cite></a>
  <div>Browse prescription and over-the-counter drugs and supplements.</div>
</div>
<div>
  <a href="/goto?url=G2"><h3>U.S. and International Locations</h3><cite>https://www.mayoclinic.org/locations</cite></a>
  <div>Find a Mayo Clinic location near you.</div>
</div>
<div>
  <a href="/goto?url=G3"><h3>MyChart</h3><cite>https://www.hopkinsmedicine.org/mychart</cite></a>
  <div>Log in to your patient account to see your records.</div>
</div>
<div>
  <a href="/goto?url=G4"><h3>Acupuncture</h3><cite>https://www.hopkinsmedicine.org/acupuncture</cite></a>
  <div>Acupuncture may help with some types of pain and nausea.</div>
</div>
"""

# PubMed/PMC/Europe PMC are trusted but already covered by the literature sources; two
# real Mayo results remain so the page still looks healthy after exclusion.
LITERATURE_SERP = """
<div>
  <a href="/goto?url=L1"><h3>Muscle cramp: MedlinePlus</h3><cite>https://pubmed.ncbi.nlm.nih.gov/123</cite></a>
  <div>A muscle cramp is a sudden tightening of a muscle and can be painful at night.</div>
</div>
<div>
  <a href="/goto?url=L2"><h3>Muscle cramps review</h3><cite>https://pmc.ncbi.nlm.nih.gov/articles/PMC1</cite></a>
  <div>Muscle cramps are common and usually harmless but can be frequent.</div>
</div>
<div>
  <a href="/goto?url=L3"><h3>Muscle cramps in sport</h3><cite>https://europepmc.org/article/MED/1</cite></a>
  <div>Exercise-associated muscle cramps affect many athletes during training.</div>
</div>
<div>
  <a href="/goto?url=L4"><h3>Muscle cramp - Symptoms and causes</h3><cite>https://www.mayoclinic.org/muscle-cramp</cite></a>
  <div>Muscle cramps are sudden involuntary contractions that commonly affect the calf.</div>
</div>
<div>
  <a href="/goto?url=L5"><h3>Muscle cramps: when to see a doctor</h3><cite>https://www.mayoclinic.org/muscle-cramps-doctor</cite></a>
  <div>Muscle cramps that are frequent or severe may need medical evaluation.</div>
</div>
"""


class GoogleTest(unittest.TestCase):
    def setUp(self):
        google.reset_run()
        http.log.clear()
        for name in ("MB_MAX_GOOGLE_QUERIES", "MB_BROWSER_ONLY", "RESEARCH_TRUSTED_DOMAINS",
                     "MB_GOOGLE_GROUPS"):
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

    def ignore_degradation(self):
        """Skip the degraded check: a shared fake SERP can't satisfy the new domain rule."""
        patcher = mock.patch.object(google, "is_degraded", lambda *a, **k: False)
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

    def test_build_query_strips_paths_and_caps_two_domains(self):
        # Google ignores paths and only honours two site: domains per query.
        self.assertEqual(google.build_query("cramps", ["mayoclinic.org/diseases-conditions",
                                                       "nhs.uk/conditions"]),
                         "cramps (site:mayoclinic.org OR site:nhs.uk)")
        self.assertEqual(google.build_query("cramps", ["a.com", "b.com", "c.com"]),
                         "cramps (site:a.com OR site:b.com)")

    # ---------------------------------------------------------------- search

    def test_search_uses_cache_on_second_call(self):
        browser = FakeBrowser(default=page(fixture("google_serp_mayo.html")))
        self.use_browser(browser)
        first = google.search("night leg cramps", sites=["mayoclinic.org"])
        second = google.search("night leg cramps", sites=["mayoclinic.org"])
        self.assertEqual(len(browser.searches), 1)       # cached the second time
        self.assertEqual(browser.opens, [])              # SERPs never open a fresh tab
        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertIn("site:mayoclinic.org", urllib.parse.unquote(browser.searches[0]))

    def test_serp_loads_use_search_not_open(self):
        """Every Google results-page load must reuse the persistent search tab via search()."""
        browser = FakeBrowser(default=page(fixture("google_serp_mayo.html")))
        self.use_browser(browser)
        google.search("night leg cramps", sites=["mayoclinic.org", "nhs.uk"])
        self.assertTrue(browser.searches, "the SERP was never fetched through search()")
        self.assertEqual(browser.opens, [], "a SERP must not open a fresh tab")

    def test_captcha_handoff_prefers_show_search(self):
        """The interactive hand-off shows the long-lived search tab, not a throwaway tab."""
        client = FakeBrowser(default=page("", status="captcha"))
        with mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("builtins.input", return_value=""):
            google._captcha_prompt(client, "https://www.google.com/search?q=x")
        self.assertEqual(client.shown_search, 1)
        self.assertEqual(client.shown, 0)
        self.assertEqual(client.hidden, 1)

    def test_search_filters_untrusted_results(self):
        browser = FakeBrowser(default=page(SERP_MIX))
        self.use_browser(browser)
        results = google.search("cramps", sites=["mayoclinic.org"])
        self.assertEqual([r["title"] for r in results],
                         ["Trusted cramp result", "Another trusted cramp result"])

    def test_search_groups_two_domains_per_query(self):
        browser = FakeBrowser(default=page(fixture("google_serp_mayo.html")))
        self.use_browser(browser)
        self.ignore_degradation()
        sites = web.trusted_domains()
        self.assertGreater(len(sites), 2)
        google.search("cramps", sites=sites)
        self.assertEqual(len(browser.searches), math.ceil(len(sites) / 2))
        for opened in browser.searches:
            query = urllib.parse.unquote(opened)
            self.assertLessEqual(query.count("site:"), 2)
            for token in re.findall(r"site:(\S+)", query):
                self.assertNotIn("/", token)             # no paths, domains only

    def test_site_groups_env_override(self):
        browser = FakeBrowser(default=page(fixture("google_serp_mayo.html")))
        self.use_browser(browser)
        self.ignore_degradation()
        with mock.patch.dict(os.environ, {"MB_GOOGLE_GROUPS": "nhs.uk,mayoclinic.org;medlineplus.gov"}):
            google.search("cramps")
        self.assertEqual(len(browser.searches), 2)
        self.assertIn("site:nhs.uk", urllib.parse.unquote(browser.searches[0]))
        self.assertIn("site:mayoclinic.org", urllib.parse.unquote(browser.searches[0]))
        self.assertIn("site:medlineplus.gov", urllib.parse.unquote(browser.searches[1]))

    def test_captcha_noninteractive_disables_further_searches(self):
        browser = FakeBrowser(default=page("", status="captcha"))
        self.use_browser(browser)
        with mock.patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(google.search("a", sites=["mayoclinic.org"]), [])
            self.assertTrue(google._run["disabled"])
            searched = len(browser.searches)
            self.assertEqual(google.search("b", sites=["nhs.uk"]), [])
        self.assertEqual(len(browser.searches), searched)   # disabled: no more Google traffic
        self.assertEqual(browser.opens, [])

    def test_run_cap_limits_queries(self):
        browser = FakeBrowser(default=page(""))
        self.use_browser(browser)
        with mock.patch.dict(os.environ, {"MB_MAX_GOOGLE_QUERIES": "2"}):
            google.search("a", sites=["mayoclinic.org"])
            google.search("b", sites=["mayoclinic.org"])
            self.assertEqual(google.search("c", sites=["mayoclinic.org"]), [])
        self.assertEqual(len(browser.searches), 2)

    def test_remaining_reports_budget(self):
        with mock.patch.dict(google._run, {"max": 5, "queries": 0}):
            self.assertEqual(google.remaining(), 5)
            google._run["queries"] = 2
            self.assertEqual(google.remaining(), 3)
            google._run["queries"] = 99
            self.assertEqual(google.remaining(), 0)

    # -------------------------------------------------------- exclusion & degradation

    def test_search_excludes_literature_hosts(self):
        browser = FakeBrowser(default=page(LITERATURE_SERP))
        self.use_browser(browser)
        results = google.search("muscle cramps", sites=["mayoclinic.org"])
        self.assertTrue(results)                                  # healthy page, not degraded
        self.assertEqual({r["site"] for r in results}, {"mayoclinic.org"})
        self.assertTrue(google._is_excluded_site("pubmed.ncbi.nlm.nih.gov"))
        self.assertTrue(google._is_excluded_site("pmc.ncbi.nlm.nih.gov"))
        self.assertTrue(google._is_excluded_site("europepmc.org"))
        self.assertFalse(google._is_excluded_site("mayoclinic.org"))

    def test_is_degraded_flags_the_generic_replay(self):
        query = "how to stop a muscle cramp fast"
        generic = [{"title": "Orlistat", "snippet": ""},
                   {"title": "Leptospirosis", "snippet": ""},
                   {"title": "Cyclospora infection", "snippet": ""},
                   {"title": "Acupuncture", "snippet": ""},
                   {"title": "MyChart", "snippet": ""}]
        self.assertTrue(google.is_degraded(query, generic))

    def test_is_degraded_false_for_relevant_results(self):
        query = "how to stop a muscle cramp fast"
        relevant = [{"title": "Muscle cramp - Symptoms and causes",
                     "snippet": "Muscle cramps are sudden involuntary contractions."},
                    {"title": "Muscle cramps: First aid",
                     "snippet": "Learn how to relieve a muscle cramp at night."}]
        self.assertFalse(google.is_degraded(query, relevant))

    def test_is_degraded_prefix_and_no_content_words(self):
        # the 5-letter-prefix rule: query "cramps" matches pages that only say "cramp"
        self.assertFalse(google.is_degraded("cramps", [
            {"title": "Cramp relief", "snippet": ""},
            {"title": "Muscle cramp causes", "snippet": ""}]))
        self.assertFalse(google.is_degraded("how to do it", [{"title": "x", "snippet": "y"}]))
        self.assertTrue(google.is_degraded("muscle cramps", [{"title": "Acupuncture", "snippet": ""}]))

    def test_is_degraded_flags_relaxed_site_operator(self):
        # Every result is on-topic, so only the domain rule can catch Google ignoring
        # site:mayoclinic.org and answering with other trusted hosts.
        query = "night leg cramps"
        relaxed = [{"title": "Night leg cramps - Mayo Clinic", "site": "mayoclinic.org",
                    "snippet": "night leg cramps are common"},
                   {"title": "Night leg cramps", "site": "nhs.uk", "snippet": "cramps at night"},
                   {"title": "Leg cramps", "site": "my.clevelandclinic.org", "snippet": "cramps"},
                   {"title": "Night cramps", "site": "hopkinsmedicine.org", "snippet": "cramps"},
                   {"title": "Cramp relief", "site": "medlineplus.gov", "snippet": "cramps"}]
        self.assertTrue(google.is_degraded(query, relaxed, ["mayoclinic.org"]))
        # Without the requested domains the old content-word behaviour is unchanged.
        self.assertFalse(google.is_degraded(query, relaxed))

    def test_is_degraded_domain_rule_accepts_a_majority(self):
        query = "night leg cramps"
        healthy = [{"title": "Night leg cramps", "site": "mayoclinic.org", "snippet": "cramps"},
                   {"title": "Night leg cramps", "site": "www.mayoclinic.org", "snippet": "cramps"},
                   {"title": "Night leg cramps", "site": "mayoclinic.org", "snippet": "cramps"},
                   {"title": "Night cramps", "site": "nhs.uk", "snippet": "cramps"},
                   {"title": "Cramp relief", "site": "www.hopkinsmedicine.org", "snippet": "cramps"}]
        # 3/5 come from the requested domains (the www. subdomain counts) -> not relaxed.
        self.assertFalse(google.is_degraded(query, healthy, ["mayoclinic.org", "hopkinsmedicine.org"]))

    def test_is_degraded_domain_rule_needs_results(self):
        self.assertFalse(google.is_degraded("night leg cramps", [], ["mayoclinic.org"]))
        self.assertFalse(google.is_degraded("night leg cramps", None, ["mayoclinic.org"]))

    def test_is_degraded_domain_rule_ignores_empty_request(self):
        row = [{"title": "Night leg cramps", "site": "nhs.uk", "snippet": "night cramps"}]
        self.assertFalse(google.is_degraded("night leg cramps", row, []))

    def test_degraded_page_restarts_once_then_retries(self):
        good = page(fixture("google_serp_mayo.html"))
        client = FakeBrowser(sequence=[page(GENERIC_SERP), good])
        self.use_browser(client)
        results = google.search("how to stop a muscle cramp fast", sites=["mayoclinic.org"])
        self.assertEqual(client.restarts, 1)
        self.assertTrue(results)
        self.assertFalse(google._run["disabled"])

    def test_degraded_twice_disables_google(self):
        client = FakeBrowser(default=page(GENERIC_SERP))
        self.use_browser(client)
        self.assertEqual(google.search("how to stop a muscle cramp fast", sites=["mayoclinic.org"]), [])
        self.assertEqual(client.restarts, 1)
        self.assertTrue(google._run["disabled"])
        searched = len(client.searches)
        self.assertEqual(google.search("another muscle cramp query", sites=["mayoclinic.org"]), [])
        self.assertEqual(len(client.searches), searched)          # disabled: no more traffic
        self.assertEqual(client.opens, [])

    def test_search_never_raises(self):
        self.use_browser(FakeBrowser(default=Blocked("blocked", "x")))
        self.assertEqual(google.search("a", sites=["mayoclinic.org"]), [])

    def test_default_site_groups_one_query_each(self):
        browser = FakeBrowser(default=page(fixture("google_serp_mayo.html")))
        self.use_browser(browser)
        self.ignore_degradation()
        google.search("night leg cramps")            # sites=None -> SITE_GROUPS
        self.assertEqual(len(browser.searches), len(google.SITE_GROUPS))
        for opened, group in zip(browser.searches, google.SITE_GROUPS):
            query = urllib.parse.unquote(opened)
            self.assertNotIn("num=", query)          # Google removed num in 2025
            for domain in group:
                self.assertIn(f"site:{domain}", query)
            for token in re.findall(r"site:(\S+)", query):
                self.assertNotIn("/", token)

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
        self.assertEqual(src["origin"], "Web")
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
