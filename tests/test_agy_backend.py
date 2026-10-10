"""The Antigravity (agy CLI) backend and its model-list parsing.

No real `agy` calls: subprocess.run is faked, so these run offline and make no
Google account calls."""
import json
import os
import subprocess
import unittest
from unittest import mock

from medical_brain.llm import checks
from medical_brain.llm.backends.agy import (MAX_PROMPT_CHARS, NO_TOOLS_PREAMBLE, AgyCLIBackend, agy_path)
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
            self.call(fake, user="x" * (MAX_PROMPT_CHARS + 1))
        self.assertEqual(fake.calls, [])             # the CLI was never started

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


class AgyModelsTest(unittest.TestCase):
    def setUp(self):
        checks._agy_models_cache.clear()
        self.addCleanup(checks._agy_models_cache.clear)

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

    def test_no_models_raises(self):
        fake = FakeRun(completed(stdout="Fetching available models...\n"))
        with mock.patch("medical_brain.llm.checks.subprocess.run", fake):
            with self.assertRaises(RuntimeError):
                checks.list_models("agy", self.cfg())

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
