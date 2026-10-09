"""The Chrome/CDP driver. The page-status and port-parsing logic is offline; the one
end-to-end test really launches Chrome and is skipped unless RUN_BROWSER_TESTS=1."""
import json
import os
import tempfile
import time
import unittest

from medical_brain.research.sources import browser

# Real bot-shield pages the reviewer captured from the blocked sites.
MAYO_BLOCKED = ("Access Denied", "Access Denied\nYou don't have permission to access this resource.",
                "https://www.mayoclinic.org/diseases-conditions/muscle-cramp/symptoms-causes/syc-20350820")
CLOUDFLARE = ("Attention Required! | Cloudflare", "Please enable cookies. Sorry, you have been blocked.",
              "https://www.hopkinsmedicine.org/health/conditions-and-diseases/muscle-cramps")
GOOGLE_SORRY = ("Sorry...", "Our systems have detected unusual traffic from your computer network.",
                "https://www.google.com/sorry/index?continue=https://www.google.com/search?q=cramp")
GOOGLE_TEXT = ("Google", "Our systems have detected unusual traffic from your computer network.",
               "https://www.google.com/search?q=muscle+cramp")
MAYO_OK = ("Muscle cramp - Symptoms and causes - Mayo Clinic",
           "Muscle cramps are sudden, involuntary contractions. They commonly occur in the calf muscles.",
           "https://www.mayoclinic.org/diseases-conditions/muscle-cramp/symptoms-causes/syc-20350820")


class PageStatusTest(unittest.TestCase):
    def test_blocks_are_classified(self):
        self.assertEqual(browser.page_status(*MAYO_BLOCKED), "blocked")
        self.assertEqual(browser.page_status(*CLOUDFLARE), "blocked")
        self.assertEqual(browser.page_status(*GOOGLE_SORRY), "captcha")
        self.assertEqual(browser.page_status(*GOOGLE_TEXT), "captcha")

    def test_normal_page_is_ok(self):
        self.assertEqual(browser.page_status(*MAYO_OK), "ok")

    def test_body_markers_ignored_on_long_articles(self):
        text = ("Muscle cramps are common and usually harmless. " * 200) + " just a moment "
        self.assertGreaterEqual(len(text), 3000)              # a real long article body
        self.assertIn("just a moment", text.lower())
        self.assertEqual(browser.page_status("Muscle cramp - Symptoms and causes", text, MAYO_OK[2]), "ok")


class DevToolsPortTest(unittest.TestCase):
    def test_parses_sample_file(self):
        self.assertEqual(browser.parse_devtools_port("9222\n/devtools/browser/abc-123\n"), 9222)

    def test_handles_blank_and_bad_input(self):
        self.assertIsNone(browser.parse_devtools_port(""))
        self.assertIsNone(browser.parse_devtools_port("\n\n"))
        self.assertIsNone(browser.parse_devtools_port("not-a-port\n"))


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1",
                     "set RUN_BROWSER_TESTS=1 to launch real Chrome")
class RealBrowserTest(unittest.TestCase):
    def test_nhs_page_loads(self):
        with tempfile.TemporaryDirectory() as profile:
            b = browser.Browser(profile_dir=profile)
            try:
                b.start()
                nhs = b.open("https://www.nhs.uk/conditions/leg-cramps/")
                self.assertEqual(nhs["status"], "ok", f"status={nhs['status']} title={nhs['title']!r}")
                self.assertIn("cramp", nhs["text"].lower())

                mayo = b.open("https://www.mayoclinic.org/diseases-conditions/muscle-cramp/"
                              "symptoms-causes/syc-20350820")
                print(f"\n[browser test] Mayo -> status={mayo['status']!r} title={mayo['title']!r}")

                self._test_captcha_handoff(b)
            finally:
                b.close()

    def _test_captcha_handoff(self, b):
        """show(url) keeps a tab open; hide() closes it and never raises."""
        page_ids = lambda: {i["id"] for i in json.loads(b._http(b.port, "/json/list"))
                            if i.get("type") == "page"}
        b.show("https://www.nhs.uk/conditions/leg-cramps/")
        shown = set(b._shown)
        self.assertEqual(len(shown), 1, "show() should keep exactly one tab open")
        self.assertTrue(shown <= page_ids(), "the shown tab is not in /json/list")

        b.hide()                                             # must not raise
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (shown & page_ids()):
            time.sleep(0.1)
        self.assertFalse(shown & page_ids(), "hide() left the shown tab open")


if __name__ == "__main__":
    unittest.main()
