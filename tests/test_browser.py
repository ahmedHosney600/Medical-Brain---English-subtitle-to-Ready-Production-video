"""The Chrome/CDP driver. The page-status and port-parsing logic is offline; the one
end-to-end test really launches Chrome and is skipped unless RUN_BROWSER_TESTS=1."""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

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


class WaitReadyTest(unittest.TestCase):
    """_wait_ready polls a document.readyState callable; pure of Chrome, so it is offline-testable."""

    def test_returns_true_as_soon_as_complete(self):
        states = iter(["loading", "interactive", "complete", "complete"])
        calls = {"n": 0}

        def state():
            calls["n"] += 1
            return next(states)

        with mock.patch.object(browser.time, "sleep"):
            self.assertTrue(browser._wait_ready(state, timeout=5.0, interval=0.5))
        self.assertEqual(calls["n"], 3)                  # stopped the moment it was complete

    def test_times_out_while_loading(self):
        with mock.patch.object(browser.time, "sleep"):
            self.assertFalse(browser._wait_ready(lambda: "loading", timeout=0.0))

    def test_call_errors_are_treated_as_not_ready(self):
        calls = {"n": 0}

        def state():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("connection gone")
            return "complete"

        with mock.patch.object(browser.time, "sleep"):
            self.assertTrue(browser._wait_ready(state, timeout=5.0, interval=0.5))
        self.assertEqual(calls["n"], 2)


class DevToolsPortTest(unittest.TestCase):
    def test_parses_sample_file(self):
        self.assertEqual(browser.parse_devtools_port("9222\n/devtools/browser/abc-123\n"), 9222)

    def test_handles_blank_and_bad_input(self):
        self.assertIsNone(browser.parse_devtools_port(""))
        self.assertIsNone(browser.parse_devtools_port("\n\n"))
        self.assertIsNone(browser.parse_devtools_port("not-a-port\n"))


class ProfileLifecycleTest(unittest.TestCase):
    """The default profile is a throwaway temp dir, removed on close (per-profile throttling
    sticks, so each process must start flag-free)."""

    def test_default_profile_is_temp_and_removed_on_close(self):
        b = browser.Browser()
        self.assertTrue(b._temp_profile)
        self.assertTrue(b.profile_dir.startswith(tempfile.gettempdir()))
        self.assertTrue(os.path.basename(b.profile_dir).startswith("mb-chrome-"))
        self.assertTrue(os.path.isdir(b.profile_dir))
        b.close()
        self.assertFalse(os.path.exists(b.profile_dir))

    def test_fixed_profile_is_kept(self):
        with tempfile.TemporaryDirectory() as profile:
            b = browser.Browser(profile_dir=profile)
            self.assertFalse(b._temp_profile)
            self.assertEqual(b.profile_dir, profile)
            b.close()
            self.assertTrue(os.path.isdir(profile))      # a caller's dir is never deleted

    def test_new_temp_profile_is_unique_and_prefixed(self):
        first, second = browser.new_temp_profile(), browser.new_temp_profile()
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.basename(first).startswith("mb-chrome-"))
        self.assertTrue(os.path.isdir(first) and os.path.isdir(second))
        self.addCleanup(shutil.rmtree, first, ignore_errors=True)
        self.addCleanup(shutil.rmtree, second, ignore_errors=True)

    def test_restart_fresh_swaps_to_a_new_temp_profile(self):
        b = browser.Browser()
        first = b.profile_dir
        with mock.patch.object(browser.Browser, "start", lambda self: None):
            b.restart_fresh()
        self.assertNotEqual(b.profile_dir, first)
        self.assertTrue(b._temp_profile)
        self.assertFalse(os.path.exists(first))          # old flagged dir is gone
        self.assertTrue(os.path.isdir(b.profile_dir))    # fresh one exists
        b.close()
        self.assertFalse(os.path.exists(b.profile_dir))


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
