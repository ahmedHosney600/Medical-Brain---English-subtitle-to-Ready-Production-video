"""Antigravity: Google's `agy` CLI used as a plain text model.

`agy` is an agent, not a chat endpoint, so every call is run in a fresh empty
temporary directory, in plan mode, with no tools permitted. The prompt itself
tells the model not to use tools; there is no separate system-prompt flag on the
CLI, so the system prompt is folded into the prompt text. temperature / max_tokens
cannot be set through the CLI and are ignored (like the Claude Code backend).
"""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Optional

from ..errors import ProviderUnavailable, StepFailed, StepTimeout, _looks_unavailable


# Where Antigravity installs by default when its folder isn't on PATH.
DEFAULT_AGY_PATH = os.path.expanduser("~/.local/bin/agy")


# macOS caps the command line (arguments + environment) at 1 MB, and the whole
# prompt is passed as one argument to `-p`. The limit is in UTF-8 bytes because
# Arabic text takes ~2 bytes per character; a 600 KB prompt was tested working.
# Above this we refuse rather than risk an "argument list too long" error.
MAX_PROMPT_BYTES = 800_000


# agy prints its final print-mode JSON as soon as the model is done, but then its
# own helpers can linger for minutes ("Waiting for migrations to complete"). Once
# the result JSON is complete we give the process this long to exit by itself
# before tearing down its process group.
EXIT_GRACE_SECONDS = 5
# How long to wait after SIGTERM (then SIGKILL) before giving up on the group.
KILL_GRACE_SECONDS = 3
# Extra time on top of the CLI's own --print-timeout before we abandon the whole
# process, so a hung agy can never outlive its configured timeout by much.
PROCESS_GRACE_SECONDS = 60


NO_TOOLS_PREAMBLE = (
    "You are being used as a plain language model inside a program. "
    "Do NOT use any tools, do not read or write files, do not run commands — "
    "answer directly from the text below."
)


def agy_path(cfg: Optional[dict] = None) -> Optional[str]:
    """The `agy` executable: an explicit cli_path, PATH, or the default install."""
    cfg = cfg or {}
    return (cfg.get("cli_path") or shutil.which("agy")
            or (DEFAULT_AGY_PATH if os.path.exists(DEFAULT_AGY_PATH) else None))


def _killpg(pid: int, sig: int) -> None:
    """Signal a whole process group (agy was started with start_new_session)."""
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _terminate_process_group(proc) -> None:
    """SIGTERM then, if it is still alive after KILL_GRACE_SECONDS, SIGKILL the
    process group so agy's helpers can't be orphaned."""
    if proc.poll() is not None:
        return
    _killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    _killpg(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


class AgyCLIBackend:
    """Antigravity through the `agy` CLI: a plain text completion with all tools
    denied (empty temp dir, plan mode, never --dangerously-skip-permissions)."""

    def __init__(self, cfg: dict, model: str):
        self.model = model
        self.effort = (cfg.get("efforts") or {}).get(model, cfg.get("effort"))
        self.timeout = cfg.get("timeout_seconds", 900)
        self.cli = agy_path(cfg)
        if not self.cli:
            raise ProviderUnavailable("the `agy` command was not found (install Google Antigravity and log in)")

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float], max_tokens: Optional[int]) -> str:
        # temperature / max_tokens can't be set through the CLI; length is steered
        # by the prompts themselves (same as the Claude Code backend).
        prompt = (NO_TOOLS_PREAMBLE + "\n\n=== INSTRUCTIONS ===\n" + (system_prompt or "")
                  + "\n\n=== INPUT ===\n" + (user_prompt or ""))
        size = len(prompt.encode("utf-8"))
        if size > MAX_PROMPT_BYTES:
            raise StepFailed(
                f"agy prompt is {size} bytes, over the {MAX_PROMPT_BYTES} limit for the "
                "command line; this step's input is too large for the agy backend.")

        cmd = [
            self.cli, "-p", prompt,
            "--model", self.model,
            "--output-format", "json",
            "--disable-slash-commands",
            "--mode", "plan",
            "--print-timeout", f"{self.timeout}s",
        ]
        if self.effort:
            cmd += ["--effort", self.effort]

        # A fresh empty dir: agy is an agent, so it must never see the user's files.
        workdir = tempfile.mkdtemp(prefix="agy-")
        proc = None
        try:
            # Its own process group: when the answer is ready and agy's helpers
            # refuse to exit, the whole group can be killed together.
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", cwd=workdir, start_new_session=True,
            )
            data, stdout, stderr, killed = self._wait_for_result(proc)
        finally:
            if proc is not None and proc.poll() is None:
                _terminate_process_group(proc)
            shutil.rmtree(workdir, ignore_errors=True)

        if data is None:
            try:
                data = json.loads(stdout)
            except Exception:
                detail = (stderr or stdout or "").strip()[:500]
                if _looks_unavailable(detail):
                    raise ProviderUnavailable(detail)
                raise RuntimeError(f"agy CLI returned unreadable output (exit {proc.returncode}): {detail}")

        # A process we killed after a complete SUCCESS answer is not an error: the
        # answer is already in hand, the process just failed to wind down.
        returncode = proc.returncode
        if killed and str(data.get("status") or "").upper() == "SUCCESS":
            returncode = 0

        text = data.get("response") or ""
        if str(data.get("status") or "").upper() != "SUCCESS" or returncode != 0:
            detail = (text or stderr or stdout or "").strip()[:500]
            if _looks_unavailable(detail):
                raise ProviderUnavailable(detail)
            raise RuntimeError(
                f"agy CLI error (status {data.get('status')!r}, exit {returncode}): {detail}")
        if not text.strip():
            raise StepFailed("agy CLI returned an empty response")
        return text

    def _wait_for_result(self, proc):
        """Read stdout/stderr while agy runs. As soon as the final print-mode JSON
        object appears, stop waiting for the process to exit on its own.

        Returns (data_or_None, stdout, stderr, killed).
        """
        stdout_chars, stderr_chars = [], []
        io_lock = threading.Lock()
        found = threading.Event()
        box = {}

        def check_complete():
            if found.is_set():
                return
            with io_lock:
                text = "".join(stdout_chars).strip()
            if not text.startswith("{"):
                return
            try:
                parsed = json.loads(text)
            except Exception:
                return
            if isinstance(parsed, dict) and "status" in parsed:
                box["data"] = parsed
                found.set()

        def pump(stream, chars, is_stdout):
            try:
                while True:
                    ch = stream.read(1)
                    if not ch:
                        break
                    with io_lock:
                        chars.append(ch)
                    # A JSON object can only be complete once its closing brace has
                    # arrived, so that is the only moment worth parsing the buffer.
                    if is_stdout and ch == "}":
                        check_complete()
            except (OSError, ValueError):
                pass

        out_thread = threading.Thread(
            target=pump, args=(proc.stdout, stdout_chars, True), daemon=True)
        err_thread = threading.Thread(
            target=pump, args=(proc.stderr, stderr_chars, False), daemon=True)
        out_thread.start()
        err_thread.start()

        deadline = time.monotonic() + self.timeout + PROCESS_GRACE_SECONDS
        while not found.is_set() and proc.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if proc.poll() is None:
                    _terminate_process_group(proc)
                raise StepTimeout(f"agy CLI timed out after {self.timeout}s")
            found.wait(min(remaining, 0.1))

        data = box.get("data")
        killed = False
        if data is not None:
            # The answer is complete; wait briefly for agy to exit by itself.
            try:
                proc.wait(timeout=EXIT_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _terminate_process_group(proc)
                killed = True

        out_thread.join(timeout=1.0)
        err_thread.join(timeout=1.0)
        return data, "".join(stdout_chars), "".join(stderr_chars), killed
