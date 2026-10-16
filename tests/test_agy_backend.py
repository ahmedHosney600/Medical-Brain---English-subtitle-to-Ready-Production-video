"""The Antigravity (agy CLI) backend and its model-list parsing.

No real `agy` calls: subprocess is faked, so these run offline and make no
Google account calls."""
import contextlib
import io
import json
import os
import signal
import subprocess
import threading
import time
import unittest
from unittest import mock

from medical_brain.llm import checks
from medical_brain.llm.backends.agy import (MAX_PROMPT_BYTES, NO_TOOLS_PREAMBLE, AgyCLIBackend, agy_path)
from medical_brain.llm.errors import ProviderUnavailable, StepFailed, StepTimeout


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["agy"], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """Stands in for subprocess.run; records each call's command and keyword args."""

    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.exc:
            raise self.exc
        return self.result


def result_json(text, status="SUCCESS"):
    return json.dumps({
        "conversation_id": "c1", "status": status, "response": text,
        "duration_seconds": 1.0, "num_turns": 1, "usage": {}})


class FakeProcess:
    """A stand-in for subprocess.Popen: scripted stdout/stderr plus an exit state.

    `exit=False` models agy printing the answer (or nothing) and then hanging;
    `ignore_term` additionally models a process that ignores SIGTERM so the group
    has to be force-killed."""

    def __init__(self, stdout="", stderr="", returncode=0, exit=True, ignore_term=False):
        self.pid = 4242
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = returncode if exit else None
        self.ignore_term = ignore_term
        self.signals = []
        self._exited = threading.Event()
        if exit:
            self._exited.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        if self._exited.wait(timeout):
            return self.returncode
        raise subprocess.TimeoutExpired("agy", timeout)

    def terminate(self):
        self.signals.append(signal.SIGTERM)
        if not self.ignore_term:
            self.returncode = -int(signal.SIGTERM)
            self._exited.set()

    def kill(self):
        self.signals.append(signal.SIGKILL)
        self.returncode = -int(signal.SIGKILL)
        self._exited.set()


class FakePopen:
    """Stands in for subprocess.Popen; records each call and returns a FakeProcess."""

    def __init__(self, process):
        self.process = process
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return self.process


@contextlib.contextmanager
def patched_backend(process, grace=0.0, kill_grace=0.0, overall_grace=0.0):
    """Patch Popen / process-group killing / grace constants for one call.

    Yields (popen_recorder, kill_list). The graces default to 0 so no test ever
    waits in real time."""
    popen = FakePopen(process)
    kills = []

    def fake_killpg(pid, sig):
        kills.append((pid, sig))
        (process.kill if sig == signal.SIGKILL else process.terminate)()

    with mock.patch("medical_brain.llm.backends.agy.subprocess.Popen", popen), \
         mock.patch("medical_brain.llm.backends.agy._killpg", fake_killpg), \
         mock.patch("medical_brain.llm.backends.agy.EXIT_GRACE_SECONDS", grace), \
         mock.patch("medical_brain.llm.backends.agy.KILL_GRACE_SECONDS", kill_grace), \
         mock.patch("medical_brain.llm.backends.agy.PROCESS_GRACE_SECONDS", overall_grace):
        yield popen, kills


class AgyBackendTest(unittest.TestCase):
    def backend(self, cfg=None):
        cfg = cfg or {"cli_path": "/fake/agy", "timeout_seconds": 900}
        return AgyCLIBackend(cfg, "gemini-3.8-flash-medium")

    def test_success_returns_text_and_uses_safe_plan_mode(self):
        backend = self.backend()
        with patched_backend(FakeProcess(stdout=result_json("hello world\n"))) as (popen, kills):
            text = backend.call("SYSTEM", "USER", None, None)
        self.assertEqual(text, "hello world\n")
        self.assertEqual(kills, [])                  # a clean exit is never killed

        cmd, kwargs = popen.calls[0]
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

        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertTrue(kwargs["start_new_session"])   # its own group, killable together
        self.assertEqual(kwargs["encoding"], "utf-8")

        workdir = kwargs["cwd"]                      # a fresh temp dir, removed when done
        self.assertFalse(os.path.exists(workdir))

    def test_effort_flag_passed_through(self):
        backend = AgyCLIBackend({"cli_path": "/fake/agy",
                                 "efforts": {"gemini-3.8-flash-medium": "high"}}, "gemini-3.8-flash-medium")
        with patched_backend(FakeProcess(stdout=result_json("x"))) as (popen, _kills):
            backend.call("S", "U", None, None)
        cmd = popen.calls[0][0]
        self.assertEqual(cmd[cmd.index("--effort") + 1], "high")

    def test_error_status_raises_runtime_error(self):
        backend = self.backend()
        with patched_backend(FakeProcess(stdout=result_json("boom", status="ERROR"))) as (_popen, kills):
            with self.assertRaises(RuntimeError):
                backend.call("S", "U", None, None)
        self.assertEqual(kills, [])                  # it exited on its own, nothing to kill

    def test_unavailable_status_raises_provider_unavailable(self):
        backend = self.backend()
        with patched_backend(FakeProcess(stdout=result_json("You are not logged in", status="ERROR"))):
            with self.assertRaises(ProviderUnavailable):
                backend.call("S", "U", None, None)

    def test_non_json_stdout_raises_runtime_error_with_detail(self):
        backend = self.backend()
        process = FakeProcess(stdout="not json", stderr="some useful detail", returncode=1)
        with patched_backend(process):
            with self.assertRaises(RuntimeError) as ctx:
                backend.call("S", "U", None, None)
        self.assertIn("some useful detail", str(ctx.exception))

    def test_empty_response_raises_step_failed(self):
        backend = self.backend()
        with patched_backend(FakeProcess(stdout=result_json("   "))):
            with self.assertRaises(StepFailed):
                backend.call("S", "U", None, None)

    def test_timeout_raises_step_timeout_and_cleans_up(self):
        backend = self.backend({"cli_path": "/fake/agy", "timeout_seconds": 0.2})
        process = FakeProcess(exit=False)            # never prints, never exits
        with patched_backend(process) as (popen, kills):
            with self.assertRaises(StepTimeout):
                backend.call("S", "U", None, None)
        self.assertEqual(kills[0][1], signal.SIGTERM)  # the process group was torn down
        self.assertFalse(os.path.exists(popen.calls[0][1]["cwd"]))

    def test_temp_dir_removed_even_when_the_call_errors(self):
        backend = self.backend()
        with patched_backend(FakeProcess(stdout=result_json("boom", status="ERROR"))) as (popen, _kills):
            with self.assertRaises(RuntimeError):
                backend.call("S", "U", None, None)
        self.assertFalse(os.path.exists(popen.calls[0][1]["cwd"]))

    def test_oversized_prompt_is_refused_before_running(self):
        backend = self.backend()
        with patched_backend(FakeProcess(stdout=result_json("x"))) as (popen, _kills):
            with self.assertRaises(StepFailed):
                backend.call("S", "x" * (MAX_PROMPT_BYTES + 1), None, None)
            self.assertEqual(popen.calls, [])        # the CLI was never started

    def test_limit_counts_utf8_bytes_not_characters(self):
        backend = self.backend()
        with patched_backend(FakeProcess(stdout=result_json("x"))) as (popen, _kills):
            with self.assertRaises(StepFailed):      # Arabic: 2 bytes per letter
                backend.call("S", "ص" * (MAX_PROMPT_BYTES // 2 + 1), None, None)
            self.assertEqual(popen.calls, [])

        with patched_backend(FakeProcess(stdout=result_json("x"))) as (popen, _kills):
            backend.call("S", "ص" * 225_000, None, None)   # the 20-minute script step that used to fail
        self.assertEqual(len(popen.calls), 1)

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


class AgyLingeringProcessTest(unittest.TestCase):
    """The bug: agy prints its final JSON, then lingers for minutes. The backend
    must return the finished answer instead of waiting for the overall timeout."""

    def backend(self, timeout=900):
        return AgyCLIBackend({"cli_path": "/fake/agy", "timeout_seconds": timeout},
                             "gemini-3.8-flash-medium")

    def test_complete_answer_then_hang_returns_quickly_and_kills_the_group(self):
        backend = self.backend()
        process = FakeProcess(stdout=result_json("the finished answer"), exit=False)
        started = time.monotonic()
        with patched_backend(process) as (popen, kills):
            text = backend.call("S", "U", None, None)
        elapsed = time.monotonic() - started
        self.assertEqual(text, "the finished answer")
        self.assertLess(elapsed, 2.0)                # not the 900 s timeout + 60 s
        self.assertEqual(process.signals, [signal.SIGTERM])
        self.assertEqual([sig for _pid, sig in kills], [signal.SIGTERM])
        self.assertFalse(os.path.exists(popen.calls[0][1]["cwd"]))

    def test_group_is_force_killed_when_sigterm_is_ignored(self):
        backend = self.backend()
        process = FakeProcess(stdout=result_json("done"), exit=False, ignore_term=True)
        with patched_backend(process) as (_popen, kills):
            text = backend.call("S", "U", None, None)
        self.assertEqual(text, "done")
        self.assertEqual([sig for _pid, sig in kills], [signal.SIGTERM, signal.SIGKILL])

    def test_complete_answer_with_a_normal_exit_is_not_killed(self):
        backend = self.backend()
        process = FakeProcess(stdout=result_json("clean"))
        with patched_backend(process) as (_popen, kills):
            text = backend.call("S", "U", None, None)
        self.assertEqual(text, "clean")
        self.assertEqual(process.signals, [])
        self.assertEqual(kills, [])

    def test_no_output_and_no_exit_times_out_without_hanging(self):
        backend = self.backend(timeout=0.2)
        process = FakeProcess(exit=False)
        started = time.monotonic()
        with patched_backend(process) as (_popen, kills):
            with self.assertRaises(StepTimeout):
                backend.call("S", "U", None, None)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertTrue(kills)

    def test_json_split_across_chunks_is_parsed_only_when_complete(self):
        backend = self.backend()
        full = result_json("chunked ok")
        stdout = "".join([full[:7], full[7:30], full[30:]])
        seen = []
        real_loads = json.loads

        def spy_loads(text, *args, **kwargs):
            seen.append(text)
            return real_loads(text, *args, **kwargs)

        process = FakeProcess(stdout=stdout)
        with mock.patch("medical_brain.llm.backends.agy.json.loads", spy_loads):
            with patched_backend(process) as (_popen, kills):
                text = backend.call("S", "U", None, None)
        self.assertEqual(text, "chunked ok")
        self.assertEqual(kills, [])
        # It only ever hands complete-looking buffers to json.loads, and only the
        # full document parses into a result with a status.
        self.assertTrue(seen)
        successful = []
        for candidate in seen:
            self.assertTrue(candidate.startswith("{"))
            self.assertTrue(candidate.endswith("}"))
            try:
                parsed = real_loads(candidate)
            except ValueError:
                continue                             # a partial chunk: correctly rejected
            if isinstance(parsed, dict) and "status" in parsed:
                successful.append(parsed)
        self.assertEqual(len(successful), 1)
        self.assertEqual(successful[0]["response"], "chunked ok")


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
