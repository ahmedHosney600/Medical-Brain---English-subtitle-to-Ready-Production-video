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
import subprocess
import tempfile
from typing import Optional

from ..errors import ProviderUnavailable, StepFailed, StepTimeout, _looks_unavailable


# Where Antigravity installs by default when its folder isn't on PATH.
DEFAULT_AGY_PATH = os.path.expanduser("~/.local/bin/agy")


# macOS caps a single command-line argument at ~1 MB, and the whole prompt is
# passed as one argument to `-p`. Above this we refuse rather than risk an
# "argument list too long" error (large prompts are rare for a plain-text call).
MAX_PROMPT_CHARS = 200_000


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
        if len(prompt) > MAX_PROMPT_CHARS:
            raise StepFailed(
                f"agy prompt is {len(prompt)} characters, over the {MAX_PROMPT_CHARS} limit for the "
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
        try:
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8",
                    timeout=self.timeout + 60, cwd=workdir,
                )
            except subprocess.TimeoutExpired:
                raise StepTimeout(f"agy CLI timed out after {self.timeout}s")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        try:
            data = json.loads(proc.stdout)
        except Exception:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            if _looks_unavailable(detail):
                raise ProviderUnavailable(detail)
            raise RuntimeError(f"agy CLI returned unreadable output (exit {proc.returncode}): {detail}")

        text = data.get("response") or ""
        if str(data.get("status") or "").upper() != "SUCCESS" or proc.returncode != 0:
            detail = (text or proc.stderr or proc.stdout or "").strip()[:500]
            if _looks_unavailable(detail):
                raise ProviderUnavailable(detail)
            raise RuntimeError(
                f"agy CLI error (status {data.get('status')!r}, exit {proc.returncode}): {detail}")
        if not text.strip():
            raise StepFailed("agy CLI returned an empty response")
        return text
