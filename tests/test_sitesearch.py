"""Site-native search, offline: fixtures for the parsers, temp HOME for the catalog cache,
fake engines for the merged search. No network, no real Chrome, no LLM."""
import json
import os
import tempfile
import unittest
from unittest import mock

from medical_brain.research.sources import http, sitesearch

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
MAYO = "mayo_search_night_leg_cramps.html"
NHS = "nhs_search_night_leg_cramps.html"
HOPKINS_XML = "hopkins_health_sitemap_sample.xml"
CLEVELAND_XML = "cleveland_site_sitemap_sample.xml"
NCCIH_XML = "nccih_sitemap_sample.xml"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8", errors="replace") as handle:
        return handle.read()


def health_urls(name):
    return [u for u in sitesearch.parse_sitemap(fixture(name)) if sitesearch._health_url(u)]


# --------------------------------------------------------------------------- fake browser

class Blocked(Exception):
    def __init__(self, kind="", url=""):
        super().__init__(kind)
        self.kind = kind
        self.url = url


class FakeBrowser:
    def __init__(self, pages=None, default=None):
        self.pages = pages or {}
        self.default = default
        self.opens = []

    def open(self, url, wait=6.0):
        self.opens.append(url)
        return self.pages.get(url, self.default) or {"url": url, "title": "", "html": "",
                                                     "text": "", "status": "ok"}


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


class ParserTest(unittest.TestCase):
    def test_parse_mayo_fixture(self):
        results = sitesearch.parse_mayo(fixture(MAYO))
        print("\n--- parsed Mayo results ---")
        for r in results:
            print(f"  {r['rank']:>2}. {r['title']}  {r['href']}")
        self.assertTrue(results)
        titles = [r["title"] for r in results]
        self.assertIn("Night leg cramps - Causes and definition", titles)
        self.assertEqual([r["rank"] for r in results], list(range(1, len(results) + 1)))
        for r in results:
            self.assertTrue(r["href"].startswith("https://www.mayoclinic.org/"), r["href"])
            self.assertEqual(r["site"], "mayoclinic.org")
            self.assertTrue(r["title"])
            self.assertFalse(r["title"].endswith(" - Mayo Clinic"))
            path = r["href"].split("/")[3]
            self.assertIn(path, sitesearch.MAYO_CONTENT_PATHS, r["href"])
        joined = " ".join(r["href"] for r in results)
        for bad in ("/search/", "/appointment", "/giving", "/locations", "newsnetwork"):
            self.assertNotIn(bad, joined)
        self.assertTrue(any(r["snippet"] for r in results))

    def test_parse_mayo_drops_junk_and_empty(self):
        sample = ('<li><h3><a class="azsearchlink" href="/search/search-results?q=x">Search</a></h3></li>'
                  '<li><h3><a class="azsearchlink" href="/appointments">Appointments</a></h3></li>'
                  '<li><h3><a class="azsearchlink" href="https://www.mayoclinic.org/diseases-conditions/acne">'
                  'Acne - Mayo Clinic</a></h3><p>Acne is a skin condition that occurs when hair follicles plug.</p></li>')
        results = sitesearch.parse_mayo(sample)
        self.assertEqual([r["href"] for r in results],
                         ["https://www.mayoclinic.org/diseases-conditions/acne"])
        self.assertEqual(results[0]["title"], "Acne")
        self.assertEqual(sitesearch.parse_mayo(""), [])

    def test_parse_nhs_fixture(self):
        results = sitesearch.parse_nhs(fixture(NHS))
        print("\n--- parsed NHS results ---")
        for r in results:
            print(f"  {r['rank']:>2}. {r['title']}  {r['href']}")
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "Leg cramps")
        self.assertEqual(results[0]["href"], "https://www.nhs.uk/conditions/leg-cramps/")
        self.assertEqual(results[0]["rank"], 1)
        titles = [r["title"] for r in results]
        self.assertIn("Night sweats", titles)
        for r in results:
            self.assertEqual(r["site"], "nhs.uk")
            self.assertTrue(r["href"].startswith("https://www.nhs.uk/"))
            self.assertNotIn("/search/click", r["href"])
            self.assertIn(r["href"].split("/")[3], sitesearch.NHS_CONTENT_PATHS, r["href"])
        self.assertEqual(len(results), len({r["href"] for r in results}))

    def test_parse_nhs_ignores_non_content_paths(self):
        sample = ('<a class="app-search-results-item" href="/search/click?url=%2Fabout-us%2F&pos=1">About us</a>'
                  '<a class="app-search-results-item" href="/search/click?url=%2Fconditions%2Fasthma%2F&pos=2">'
                  'Asthma</a>')
        results = sitesearch.parse_nhs(sample)
        self.assertEqual([r["href"] for r in results], ["https://www.nhs.uk/conditions/asthma/"])


class SiteSearchCallsTest(unittest.TestCase):
    def setUp(self):
        http.log.clear()
        throttle = mock.patch("medical_brain.research.sources.http._throttle")
        throttle.start()
        self.addCleanup(throttle.stop)

    def test_mayo_search_uses_browser_open(self):
        client = FakeBrowser(default=page(fixture(MAYO)))
        with mock.patch.object(sitesearch, "_load_browser", lambda: FakeBrowserModule(client)):
            results = sitesearch.mayo_search("night leg cramps", max_results=3)
        self.assertEqual(len(results), 3)
        self.assertEqual(client.opens[0].split("?")[0],
                         "https://www.mayoclinic.org/search/search-results")
        self.assertIn("q=night+leg+cramps", client.opens[0])

    def test_mayo_search_without_browser_is_empty(self):
        with mock.patch.object(sitesearch, "_load_browser",
                               lambda: FakeBrowserModule(None, available=False)):
            self.assertEqual(sitesearch.mayo_search("x"), [])

    def test_nhs_search_uses_http(self):
        with mock.patch("medical_brain.research.sources.http.get",
                        return_value=fixture(NHS)) as http_get:
            results = sitesearch.nhs_search("night leg cramps", max_results=2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["href"], "https://www.nhs.uk/conditions/leg-cramps/")
        self.assertIn("search/results", http_get.call_args[0][0])

    def test_fetch_catalog_hopkins_through_browser(self):
        client = FakeBrowser(default=page(fixture(HOPKINS_XML)))
        with mock.patch.object(sitesearch, "_load_browser", lambda: FakeBrowserModule(client)):
            urls = sitesearch._fetch_catalog("hopkinsmedicine.org")
        self.assertTrue(urls)
        self.assertTrue(all(u.startswith("https://www.hopkinsmedicine.org/health/") for u in urls))
        self.assertIn("https://www.hopkinsmedicine.org/health/conditions-and-diseases/legionnaires-disease", urls)

    def test_fetch_catalog_cleveland_through_http(self):
        with mock.patch("medical_brain.research.sources.http.get",
                        return_value=fixture(CLEVELAND_XML)):
            urls = sitesearch._fetch_catalog("my.clevelandclinic.org")
        self.assertTrue(urls)
        self.assertTrue(all("/health/" in u for u in urls))
        self.assertNotIn("https://my.clevelandclinic.org/about", urls)

    def test_fetch_catalog_nccih_through_http(self):
        with mock.patch("medical_brain.research.sources.http.get",
                        return_value=fixture(NCCIH_XML)) as http_get:
            urls = sitesearch._fetch_catalog("nccih.nih.gov")
        self.assertTrue(urls)
        self.assertTrue(all(u.startswith("https://www.nccih.nih.gov/health/") for u in urls))
        self.assertIn("https://www.nccih.nih.gov/health/cupping", urls)
        self.assertIn("https://www.nccih.nih.gov/health/turmeric", urls)
        self.assertNotIn("https://www.nccih.nih.gov/about", urls)      # non-health dropped
        self.assertNotIn("https://www.nccih.nih.gov/grants", urls)
        self.assertIn("sitemap-0.xml", http_get.call_args[0][0])       # plain HTTP, no Chrome
        self.assertEqual(sitesearch._catalog_key("www.nccih.nih.gov"), "nccih.nih.gov")


class CatalogRankTest(unittest.TestCase):
    def test_catalog_rank_cleveland(self):
        results = sitesearch.catalog_rank(health_urls(CLEVELAND_XML), "night leg cramps")
        print("\n--- Cleveland catalog hits for 'night leg cramps' ---")
        for r in results:
            print(f"  {r['rank']:>2}. {r['title']}  {r['href']}")
        self.assertTrue(results)
        hrefs = [r["href"] for r in results]
        self.assertIn("https://my.clevelandclinic.org/health/diseases/14170-leg-cramps", hrefs[:2])
        self.assertEqual(hrefs[0], "https://my.clevelandclinic.org/health/diseases/14170-leg-cramps")
        self.assertEqual(results[0]["title"], "Leg cramps")
        self.assertEqual(results[0]["site"], "my.clevelandclinic.org")
        self.assertEqual([r["rank"] for r in results], list(range(1, len(results) + 1)))

    def test_catalog_rank_hopkins(self):
        results = sitesearch.catalog_rank(health_urls(HOPKINS_XML), "heat cramps")
        print("\n--- Hopkins catalog hits for 'heat cramps' ---")
        for r in results:
            print(f"  {r['rank']:>2}. {r['title']}  {r['href']}")
        self.assertTrue(results)
        self.assertEqual(results[0]["href"],
                         "https://www.hopkinsmedicine.org/health/conditions-and-diseases/"
                         "heatrelated-illnesses-heat-cramps-heat-exhaustion-heat-stroke")

    def test_catalog_rank_nccih(self):
        results = sitesearch.catalog_rank(health_urls(NCCIH_XML), "cupping")
        print("\n--- NCCIH catalog hits for 'cupping' ---")
        for r in results:
            print(f"  {r['rank']:>2}. {r['title']}  {r['href']}")
        self.assertEqual([r["href"] for r in results],
                         ["https://www.nccih.nih.gov/health/cupping"])
        self.assertEqual(results[0]["site"], "nccih.nih.gov")
        self.assertTrue(results[0]["title"])

        self.assertEqual(
            sitesearch.catalog_rank(health_urls(NCCIH_XML), "black cohosh")[0]["href"],
            "https://www.nccih.nih.gov/health/black-cohosh")
        self.assertEqual(
            sitesearch.catalog_rank(health_urls(NCCIH_XML), "massage therapy")[0]["href"],
            "https://www.nccih.nih.gov/health/massage-therapy-what-you-need-to-know")

    def test_catalog_rank_uses_slug_words_and_prefix(self):
        urls = ["https://my.clevelandclinic.org/health/diseases/14170-leg-cramps",
                "https://my.clevelandclinic.org/health/diseases/muscle-spasms-muscle-cramps",
                "https://my.clevelandclinic.org/health/diseases/22620-bronchospasm"]
        # "cramps" matches "cramps" exactly; "cramp" prefix would match too.
        results = sitesearch.catalog_rank(urls, "cramps")
        self.assertEqual({r["href"] for r in results}, set(urls[:2]))

    def test_catalog_rank_prefers_exact_slug_word(self):
        # "night" is only a substring of "nighttime": the exact "cramps" page must win.
        urls = ["https://my.clevelandclinic.org/health/drugs/daytimenighttime-cough-and-cold-combinations",
                "https://my.clevelandclinic.org/health/diseases/14170-leg-cramps"]
        results = sitesearch.catalog_rank(urls, "night leg cramps")
        self.assertEqual(results[0]["href"], urls[1])

    def test_catalog_rank_drops_no_score(self):
        self.assertEqual(sitesearch.catalog_rank(["https://www.hopkinsmedicine.org/health/acne"], "night leg cramps"), [])
        self.assertEqual(sitesearch.catalog_rank([], "night leg cramps"), [])


class CatalogCacheTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(os.environ.pop, "MB_CATALOG_DIR", None)
        os.environ.pop("MB_CATALOG_DIR", None)
        http.log.clear()

    def _write(self, site, urls, age=0):
        path = sitesearch._catalog_path(site)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(urls, handle)
        if age:
            old = os.path.getmtime(path) - age
            os.utime(path, (old, old))
        return path

    def test_cache_lives_under_home(self):
        path = sitesearch._catalog_path("hopkinsmedicine.org")
        self.assertTrue(path.startswith(self.home), path)
        self.assertTrue(path.endswith("hopkinsmedicine.org.json"))

    def test_fresh_cache_is_reused_without_fetch(self):
        self._write("hopkinsmedicine.org", ["https://www.hopkinsmedicine.org/health/x"])
        with mock.patch.object(sitesearch, "_fetch_catalog",
                               side_effect=AssertionError("must not fetch")):
            self.assertEqual(sitesearch.catalog("hopkinsmedicine.org"),
                             ["https://www.hopkinsmedicine.org/health/x"])

    def test_stale_cache_is_refreshed_and_written(self):
        path = self._write("hopkinsmedicine.org", ["https://www.hopkinsmedicine.org/health/old"],
                           age=sitesearch.CATALOG_MAX_AGE + 60)
        with mock.patch.object(sitesearch, "_fetch_catalog",
                               return_value=["https://www.hopkinsmedicine.org/health/new"]) as fetch:
            out = sitesearch.catalog("hopkinsmedicine.org")
        fetch.assert_called_once_with("hopkinsmedicine.org")
        self.assertEqual(out, ["https://www.hopkinsmedicine.org/health/new"])
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), ["https://www.hopkinsmedicine.org/health/new"])

    def test_fetch_failure_falls_back_to_stale_cache(self):
        self._write("hopkinsmedicine.org", ["https://www.hopkinsmedicine.org/health/old"],
                    age=sitesearch.CATALOG_MAX_AGE + 60)
        with mock.patch.object(sitesearch, "_fetch_catalog", return_value=None):
            self.assertEqual(sitesearch.catalog("hopkinsmedicine.org"),
                             ["https://www.hopkinsmedicine.org/health/old"])
        self.assertTrue(any("stale" in entry[2] for entry in http.log))

    def test_fetch_failure_without_cache_is_empty(self):
        with mock.patch.object(sitesearch, "_fetch_catalog", return_value=None):
            self.assertEqual(sitesearch.catalog("my.clevelandclinic.org"), [])

    def test_unknown_site_is_empty(self):
        self.assertEqual(sitesearch.catalog("webmd.com"), [])

    def test_cleveland_alias_maps_to_my_cleveland(self):
        self.assertEqual(sitesearch._catalog_key("clevelandclinic.org"), "my.clevelandclinic.org")
        self.assertEqual(sitesearch._catalog_key("www.hopkinsmedicine.org"), "hopkinsmedicine.org")


class MergedSearchTest(unittest.TestCase):
    """search() with fake engines: round-robin, dedupe by href, re-rank, sites filter."""

    def setUp(self):
        http.log.clear()
        self.calls = []

    def _fake(self, *, mayo=None, hopkins=None, nhs=None, cleveland=None, nccih=None, raiser=None):
        self.mayo = [dict(r) for r in (mayo or [])]
        self.hopkins = [dict(r) for r in (hopkins or [])]
        self.nhs = [dict(r) for r in (nhs or [])]
        self.cleveland = [dict(r) for r in (cleveland or [])]
        self.nccih = [dict(r) for r in (nccih or [])]
        calls = self.calls

        def mayo_search(query, max_results=8):
            calls.append(("mayo", query))
            if raiser == "mayo":
                raise RuntimeError("mayo down")
            return [dict(r) for r in self.mayo]

        def nhs_search(query, max_results=8):
            calls.append(("nhs", query))
            if raiser == "nhs":
                raise RuntimeError("nhs down")
            return [dict(r) for r in self.nhs]

        def catalog_search(site, query, max_results=8):
            calls.append(("catalog", site, query))
            if raiser == site:
                raise RuntimeError(f"{site} down")
            if site == "hopkinsmedicine.org":
                return [dict(r) for r in self.hopkins]
            if site == "my.clevelandclinic.org":
                return [dict(r) for r in self.cleveland]
            if site == "nccih.nih.gov":
                return [dict(r) for r in self.nccih]
            return []

        patchers = [mock.patch("medical_brain.research.sources.sitesearch.mayo_search", mayo_search),
                    mock.patch("medical_brain.research.sources.sitesearch.nhs_search", nhs_search),
                    mock.patch("medical_brain.research.sources.sitesearch.catalog_search", catalog_search)]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_interleave_dedupe_and_rank(self):
        self._fake(
            mayo=[{"title": "M1", "href": "https://www.mayoclinic.org/a", "site": "mayoclinic.org", "snippet": "", "rank": 1},
                  {"title": "M2", "href": "https://www.mayoclinic.org/b", "site": "mayoclinic.org", "snippet": "", "rank": 2}],
            hopkins=[{"title": "H1", "href": "https://www.hopkinsmedicine.org/a", "site": "hopkinsmedicine.org", "snippet": "", "rank": 1},
                     {"title": "Hdup", "href": "https://www.mayoclinic.org/a", "site": "mayoclinic.org", "snippet": "", "rank": 2}],
            nhs=[{"title": "N1", "href": "https://www.nhs.uk/conditions/x/", "site": "nhs.uk", "snippet": "", "rank": 1}],
            cleveland=[{"title": "C1", "href": "https://my.clevelandclinic.org/health/y", "site": "my.clevelandclinic.org", "snippet": "", "rank": 1}])
        results = sitesearch.search("leg cramps")
        self.assertEqual([r["title"] for r in results], ["M1", "H1", "N1", "C1", "M2"])
        self.assertEqual([r["rank"] for r in results], [1, 2, 3, 4, 5])
        self.assertEqual(len({r["href"] for r in results}), len(results))

    def test_sites_filter_limits_engines(self):
        self._fake(mayo=[{"title": "M1", "href": "https://www.mayoclinic.org/a", "site": "mayoclinic.org", "snippet": "", "rank": 1}],
                   nhs=[{"title": "N1", "href": "https://www.nhs.uk/conditions/x/", "site": "nhs.uk", "snippet": "", "rank": 1}],
                   cleveland=[{"title": "C1", "href": "https://my.clevelandclinic.org/health/y", "site": "my.clevelandclinic.org", "snippet": "", "rank": 1}])
        results = sitesearch.search("leg cramps", sites=["nhs.uk", "clevelandclinic.org"])
        self.assertEqual([r["title"] for r in results], ["N1", "C1"])
        kinds = [c[0] for c in self.calls]
        self.assertIn("nhs", kinds)
        self.assertIn("catalog", kinds)
        self.assertNotIn("mayo", kinds)

    def test_one_dead_site_does_not_stop_the_rest(self):
        self._fake(nhs=[{"title": "N1", "href": "https://www.nhs.uk/conditions/x/", "site": "nhs.uk", "snippet": "", "rank": 1}],
                   raiser="mayo")
        results = sitesearch.search("leg cramps")
        self.assertEqual([r["title"] for r in results], ["N1"])
        self.assertTrue(any(entry[0] == "Sites" for entry in http.log))

    def test_never_raises_when_every_engine_fails(self):
        def boom(*a, **k):
            raise RuntimeError("down")
        for name in ("mayo_search", "nhs_search", "catalog_search"):
            patcher = mock.patch("medical_brain.research.sources.sitesearch." + name, boom)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertEqual(sitesearch.search("x"), [])

    def test_wanted_sites_aliases(self):
        self.assertEqual(sitesearch._wanted_sites(None), set(sitesearch.SITE_ORDER))
        self.assertEqual(sitesearch._wanted_sites(["clevelandclinic.org"]), {"my.clevelandclinic.org"})
        self.assertEqual(sitesearch._wanted_sites(["https://www.mayoclinic.org/x"]), {"mayoclinic.org"})
        self.assertEqual(sitesearch._wanted_sites(["nccih.nih.gov"]), {"nccih.nih.gov"})
        self.assertEqual(sitesearch._wanted_sites(["www.nccih.nih.gov"]), {"nccih.nih.gov"})
        self.assertEqual(sitesearch._wanted_sites(["webmd.com"]), set())

    def test_nccih_is_searched_and_interleaves_last(self):
        self._fake(
            mayo=[{"title": "M1", "href": "https://www.mayoclinic.org/a", "site": "mayoclinic.org",
                   "snippet": "", "rank": 1}],
            cleveland=[{"title": "C1", "href": "https://my.clevelandclinic.org/health/y",
                        "site": "my.clevelandclinic.org", "snippet": "", "rank": 1}],
            nccih=[{"title": "NC1", "href": "https://www.nccih.nih.gov/health/cupping",
                    "site": "nccih.nih.gov", "snippet": "", "rank": 1}])
        results = sitesearch.search("cupping")
        titles = [r["title"] for r in results]
        self.assertIn("NC1", titles)
        self.assertLess(titles.index("C1"), titles.index("NC1"))    # NCCIH sits after Cleveland
        self.assertIn(("catalog", "nccih.nih.gov", "cupping"), self.calls)

    def test_nccih_filter_selects_only_nccih(self):
        self._fake(nccih=[{"title": "NC1", "href": "https://www.nccih.nih.gov/health/cupping",
                           "site": "nccih.nih.gov", "snippet": "", "rank": 1}],
                   mayo=[{"title": "M1", "href": "https://www.mayoclinic.org/a",
                          "site": "mayoclinic.org", "snippet": "", "rank": 1}])
        results = sitesearch.search("cupping", sites=["nccih.nih.gov"])
        self.assertEqual([r["title"] for r in results], ["NC1"])
        self.assertNotIn("mayo", [c[0] for c in self.calls])


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1",
                     "set RUN_BROWSER_TESTS=1 to search the real sites")
class RealSiteSearchSmokeTest(unittest.TestCase):
    """One live round: real Chrome (Mayo) + real HTTP (NHS/sitemaps). Run once."""

    def test_real_search(self):
        results = sitesearch.search("night leg cramps")
        if not results:
            print(f"[sites live] nothing; http.log={http.log[-6:]}")
        print("\n[sites live] search('night leg cramps'):")
        for r in results:
            print(f"  {r['rank']:>2}. {r['site']:<24} {r['title']}  —  {r['href']}")
        self.assertTrue(results)


if __name__ == "__main__":
    unittest.main()
