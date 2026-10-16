"""The Antigravity (agy CLI) backend and its model-list parsing.

No real `agy` calls: subprocess.run is faked, so these run offline and make no
Google account calls."""
import json
import os
import subprocess
import unittest
from unittest import mock

from medical_brain.llm import checks
from medical_brain.llm.backends.agy import (MAX_PROMPT_BYTES, NO_TOOLS_PREAMBLE, AgyCLIBackend, agy_path)
from medical_brain.llm.errors import ProviderUnavailable, StepFailed, StepTimeout


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["agy"], returncode=returncode, stdout=stdout, stderr=stderr)


def ok_response(text):
    return completed(stdout=json.dumps({
        "conversation_id": "c1", "status": "SUCCESS", "response": text,
        "duration_seconds": 1.0, "num_turns": 1, "usage": {}}))


class FakeRun:
    """Stands in for subprocess.run; records each call's command and keyword args."""

    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.exc:
            raise self.exc
        return self.result


class AgyBackendTest(unittest.TestCase):
    def backend(self, cfg=None):
        cfg = cfg or {"cli_path": "/fake/agy", "timeout_seconds": 900}
        return AgyCLIBackend(cfg, "gemini-3.8-flash-medium")

    def call(self, fake, backend=None, system="SYSTEM", user="USER"):
        with mock.patch("medical_brain.llm.backends.agy.subprocess.run", fake):
            return (backend or self.backend()).call(system, user, None, None)

    def test_success_returns_text_and_uses_safe_plan_mode(self):
        fake = FakeRun(ok_response("hello world\n"))
        text = self.call(fake)
        self.assertEqual(text, "hello world\n")

        cmd, kwargs = fake.calls[0]
        for flag in ("--mode", "plan", "--output-format", "json", "--disable-slash-commands",
                     "--print-timeout", "900s", "--model", "gemini-3.8-flash-medium"):
            self.assertIn(flag, cmd)
        self.assertNotIn("--dangerously-skip-permissions", cmd)   # an agent must never act on files

        prompt = cmd[cmd.index("-p") + 1]
        self.assertTrue(prompt.startswith(NO_TOOLS_PREAMBLE))
        self.assertIn("=== INSTRUCTIONS ===", prompt)
        self.assertIn("SYSTEM", prompt)
        self.assertIn("=== INPUT ===", prompt)
        self.assertIn("USER", prompt)

        workdir = kwargs["cwd"]                      # a fresh temp dir, removed when done
        self.assertFalse(os.path.exists(workdir))

    def test_effort_flag_passed_through(self):
        fake = FakeRun(ok_response("x"))
        backend = AgyCLIBackend({"cli_path": "/fake/agy",
                                 "efforts": {"gemini-3.8-flash-medium": "high"}}, "gemini-3.8-flash-medium")
        self.call(fake, backend=backend)
        cmd = fake.calls[0][0]
        self.assertEqual(cmd[cmd.index("--effort") + 1], "high")

    def test_error_status_raises_runtime_error(self):
        fake = FakeRun(completed(stdout=json.dumps({"status": "ERROR", "response": "boom"})))
        with self.assertRaises(RuntimeError):
            self.call(fake)

    def test_unavailable_status_raises_provider_unavailable(self):
        fake = FakeRun(completed(stdout=json.dumps({"status": "ERROR", "response": "You are not logged in"})))
        with self.assertRaises(ProviderUnavailable):
            self.call(fake)

    def test_non_json_stdout_raises_runtime_error_with_detail(self):
        fake = FakeRun(completed(stdout="not json", stderr="some useful detail", returncode=1))
        with self.assertRaises(RuntimeError) as ctx:
            self.call(fake)
        self.assertIn("some useful detail", str(ctx.exception))

    def test_empty_response_raises_step_failed(self):
        fake = FakeRun(ok_response("   "))
        with self.assertRaises(StepFailed):
            self.call(fake)

    def test_timeout_raises_step_timeout_and_cleans_up(self):
        fake = FakeRun(exc=subprocess.TimeoutExpired("agy", 960))
        with self.assertRaises(StepTimeout):
            self.call(fake)
        self.assertFalse(os.path.exists(fake.calls[0][1]["cwd"]))

    def test_temp_dir_removed_even_when_the_call_errors(self):
        fake = FakeRun(completed(stdout=json.dumps({"status": "ERROR", "response": "boom"})))
        with self.assertRaises(RuntimeError):
            self.call(fake)
        self.assertFalse(os.path.exists(fake.calls[0][1]["cwd"]))

    def test_oversized_prompt_is_refused_before_running(self):
        fake = FakeRun(ok_response("x"))
        with self.assertRaises(StepFailed):
            self.call(fake, user="x" * (MAX_PROMPT_BYTES + 1))
        self.assertEqual(fake.calls, [])             # the CLI was never started

    def test_limit_counts_utf8_bytes_not_characters(self):
        fake = FakeRun(ok_response("x"))
        with self.assertRaises(StepFailed):          # Arabic: 2 bytes per letter
            self.call(fake, user="ص" * (MAX_PROMPT_BYTES // 2 + 1))
        self.assertEqual(fake.calls, [])
        fake = FakeRun(ok_response("x"))
        self.call(fake, user="ص" * 225_000)          # the 20-minute script step that used to fail
        self.assertEqual(len(fake.calls), 1)

    def test_not_installed_raises_provider_unavailable(self):
        with mock.patch("medical_brain.llm.backends.agy.shutil.which", return_value=None), \
             mock.patch("medical_brain.llm.backends.agy.DEFAULT_AGY_PATH", "/nonexistent/agy-xyz"):
            with self.assertRaises(ProviderUnavailable):
                AgyCLIBackend({"cli_path": ""}, "gemini-3.8-flash-low")

    def test_cli_path_resolution(self):
        with mock.patch("medical_brain.llm.backends.agy.shutil.which", return_value="/usr/bin/agy"):
            self.assertEqual(agy_path({}), "/usr/bin/agy")
        with mock.patch("medical_brain.llm.backends.agy.shutil.which", return_value=None), \
             mock.patch("medical_brain.llm.backends.agy.DEFAULT_AGY_PATH", "/fake/home/agy"), \
             mock.patch("medical_brain.llm.backends.agy.os.path.exists", return_value=True):
            self.assertEqual(agy_path({}), "/fake/home/agy")


class FlakyRun:
    """subprocess.run fake that raises each given exception in turn before returning
    the next CompletedProcess; records calls like FakeRun."""

    def __init__(self, results):
        self.results, self.calls = list(results), []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        nxt = self.results.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class AgyModelsTest(unittest.TestCase):
    def setUp(self):
        checks._agy_models_cache.clear()
        checks._agy_models_fallback.clear()
        self.addCleanup(checks._agy_models_cache.clear)
        self.addCleanup(checks._agy_models_fallback.clear)

    def cfg(self):
        return {"type": "agy", "cli_path": "/fake/agy"}

    def test_parses_tab_lines_skips_the_notice_and_caches(self):
        stdout = ("Fetching available models...\n"
                  "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
                  "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
                  "\n"
                  "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n")
        fake = FakeRun(completed(stdout=stdout))
        with mock.patch("medical_brain.llm.checks.subprocess.run", fake):
            models = checks.list_models("agy", self.cfg())
            again = checks.list_models("agy", self.cfg())
        self.assertEqual(models, ["gemini-3.8-flash-low", "claude-sonnet-4-6", "gpt-oss-120b-medium"])
        self.assertEqual(again, models)
        self.assertEqual(len(fake.calls), 1)          # the second read came from the cache
        self.assertNotIn("Fetching available models...", models)

    def test_no_models_twice_falls_back_to_the_known_list(self):
        fake = FakeRun(completed(stdout="Fetching available models...\n"))
        with mock.patch("medical_brain.llm.checks.subprocess.run", fake):
            models = checks.list_models("agy", self.cfg())
        self.assertEqual(models, checks.AGY_KNOWN_MODELS)
        self.assertEqual(len(fake.calls), 2)              # empty list was retried once
        self.assertNotIn("/fake/agy", checks._agy_models_cache)   # the fallback is not cached

    def test_timeout_then_success_returns_real_list_and_retries(self):
        stdout = ("gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
                  "claude-sonnet-4-6\tClaude Sonnet 4.6\n")
        fake = FlakyRun([subprocess.TimeoutExpired("agy", 30), completed(stdout=stdout)])
        with mock.patch("medical_brain.llm.checks.subprocess.run", fake):
            models = checks.list_models("agy", self.cfg())
        self.assertEqual(models, ["gemini-3.8-flash-low", "claude-sonnet-4-6"])
        self.assertEqual(len(fake.calls), 2)              # one stall, then a retry
        for _cmd, kwargs in fake.calls:
            self.assertEqual(kwargs.get("timeout"), 30)
            self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertEqual(checks._agy_models_fallback, {})  # a real list: no warning

    def test_two_timeouts_fall_back_and_provider_status_warns(self):
        timeouts = [subprocess.TimeoutExpired("agy", 30)] * 4
        fake = FlakyRun(timeouts)
        with mock.patch("medical_brain.llm.checks.subprocess.run", fake), \
             mock.patch("medical_brain.llm.backends.agy.agy_path", return_value="/fake/agy"):
            models = checks.list_models("agy", self.cfg())
            state, detail = checks.provider_status("agy", self.cfg())
        self.assertEqual(models, checks.AGY_KNOWN_MODELS)
        self.assertNotIn("/fake/agy", checks._agy_models_cache)   # not cached
        self.assertIn("/fake/agy", checks._agy_models_fallback)   # fallback remembered
        self.assertEqual(state, "warning")
        self.assertNotEqual(state, "error")
        self.assertIn("didn't answer", detail)
        self.assertIn(str(len(checks.AGY_KNOWN_MODELS)), detail)

    def test_fallback_is_not_cached_so_the_next_call_retries(self):
        fake = FlakyRun([subprocess.TimeoutExpired("agy", 30), subprocess.TimeoutExpired("agy", 30)])
        with mock.patch("medical_brain.llm.checks.subprocess.run", fake):
            self.assertEqual(checks.list_models("agy", self.cfg()), checks.AGY_KNOWN_MODELS)
        self.assertEqual(len(fake.calls), 2)
        # A later call tries again for real (a real list clears the fallback marker).
        stdout = "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
        fake2 = FakeRun(completed(stdout=stdout))
        with mock.patch("medical_brain.llm.checks.subprocess.run", fake2):
            self.assertEqual(checks.list_models("agy", self.cfg()), ["gemini-3.8-flash-low"])
        self.assertEqual(len(fake2.calls), 1)
        self.assertNotIn("/fake/agy", checks._agy_models_fallback)
        self.assertIn("/fake/agy", checks._agy_models_cache)

    def test_warning_provider_stays_selectable(self):
        from medical_brain import setup_models
        config = {"providers": {"agy": self.cfg()},
                  "main_model": "agy:gemini-3.8-flash-high",
                  "single_model": "agy:gemini-3.8-flash-high",
                  "hybrid_routes": {}, "backup_model": ""}
        with mock.patch.object(setup_models.lp, "list_models",
                               return_value=["gemini-3.8-flash-high", "gemini-3.8-flash-low"]):
            specs = setup_models.collect_models(config, {"agy": "warning"})
        self.assertIn("agy:gemini-3.8-flash-high", specs)
        self.assertEqual(setup_models.ICON["warning"], "⚠️")

    def test_not_installed_raises_provider_unavailable(self):
        with mock.patch("medical_brain.llm.backends.agy.shutil.which", return_value=None), \
             mock.patch("medical_brain.llm.backends.agy.DEFAULT_AGY_PATH", "/nonexistent/agy-xyz"):
            with self.assertRaises(ProviderUnavailable):
                checks.list_models("agy", {"type": "agy"})

    def test_provider_status_off_when_not_installed(self):
        with mock.patch("medical_brain.llm.backends.agy.shutil.which", return_value=None), \
             mock.patch("medical_brain.llm.backends.agy.DEFAULT_AGY_PATH", "/nonexistent/agy-xyz"):
            state, detail = checks.provider_status("agy", {"type": "agy"})
        self.assertEqual(state, "off")
        self.assertIn("agy", detail)

    def test_provider_status_active_uses_a_flash_low_ping(self):
        seen = {}

        def fake_test(spec, providers):
            seen["spec"] = spec
            return True, "answers (1.0 s)"

        with mock.patch("medical_brain.llm.backends.agy.agy_path", return_value="/fake/agy"), \
             mock.patch.object(checks, "_list_agy_models", return_value=["claude-opus-4-6-thinking",
                                                                        "gemini-3.8-flash-low"]), \
             mock.patch.object(checks, "test_model", fake_test):
            state, detail = checks.provider_status("agy", {"type": "agy"})
        self.assertEqual(state, "active")
        self.assertEqual(seen["spec"], "agy:gemini-3.8-flash-low")


class AgyRegistrationTest(unittest.TestCase):
    def test_default_provider_and_make_backend(self):
        from medical_brain.llm import DEFAULT_PROVIDERS, make_backend
        self.assertEqual(DEFAULT_PROVIDERS["agy"]["type"], "agy")
        self.assertTrue(DEFAULT_PROVIDERS["agy"]["free"])
        backend = make_backend("agy:gemini-3.8-flash-low",
                               {"agy": {"type": "agy", "cli_path": "/fake/agy"}})
        self.assertIsInstance(backend, AgyCLIBackend)
        self.assertEqual(backend.model, "gemini-3.8-flash-low")


if __name__ == "__main__":
    unittest.main()
