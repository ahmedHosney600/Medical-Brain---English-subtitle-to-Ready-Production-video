"""Claude: the subscription through the `claude` CLI, or a pay-per-token API key."""
import json
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from ..errors import AnswerCutOff, ProviderUnavailable, _looks_unavailable, api_failure


class ClaudeCodeBackend:
    """Claude through the Claude subscription: runs `claude -p` with all tools
    disabled, so it behaves like a plain text completion."""

    def __init__(self, cfg: dict, model: str):
        self.model = model
        self.effort = (cfg.get("efforts") or {}).get(model, cfg.get("effort"))
        self.timeout = cfg.get("timeout_seconds", 900)
        self.cli = cfg.get("cli_path") or shutil.which("claude")
        if not self.cli:
            raise ProviderUnavailable("the `claude` command was not found (install Claude Code and log in)")

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float], max_tokens: Optional[int]) -> str:
        # temperature / max_tokens can't be set through the CLI; Opus 5.5 also
        # doesn't accept temperature. Length is steered by the prompts themselves.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as sp:
            sp.write(system_prompt)
            system_file = sp.name
        cmd = [
            self.cli, "-p",
            "--model", self.model,
            "--system-prompt-file", system_file,
            "--output-format", "json",
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        if self.effort:
            cmd += ["--effort", self.effort]
        try:
            proc = subprocess.run(
                cmd, input=user_prompt, capture_output=True, text=True,
                encoding="utf-8", timeout=self.timeout,
                # Neutral working directory so no project CLAUDE.md or settings leak in.
                cwd=tempfile.gettempdir(),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude CLI timed out after {self.timeout}s")
        finally:
            try:
                os.unlink(system_file)
            except OSError:
                pass

        try:
            data = json.loads(proc.stdout)
        except Exception:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            if _looks_unavailable(detail):
                raise ProviderUnavailable(detail)
            raise RuntimeError(f"claude CLI returned unreadable output (exit {proc.returncode}): {detail}")

        text = data.get("result") or ""
        if data.get("is_error") or proc.returncode != 0:
            if _looks_unavailable(text) or _looks_unavailable(proc.stderr):
                raise ProviderUnavailable(text or proc.stderr)
            raise RuntimeError(f"claude CLI error: {text or proc.stderr}")
        return text


class AnthropicAPIBackend:
    """Claude through a pay-per-token Anthropic API key."""

    def __init__(self, cfg: dict, model: str):
        try:
            import anthropic
        except ImportError:
            raise ProviderUnavailable("the `anthropic` package is not installed (pip install anthropic)")
        self.anthropic = anthropic
        self.model = model
        self.effort = (cfg.get("efforts") or {}).get(model, cfg.get("effort"))
        api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float], max_tokens: Optional[int]) -> str:
        # Opus 5.5 always thinks and rejects `temperature`. Thinking tokens count
        # toward max_tokens, so give generous room on top of what the node asked
        # for, and stream so long Arabic outputs don't hit HTTP timeouts.
        budget = min(max(max_tokens or 0, 8000) + 32000, 64000)
        params = dict(
            model=self.model,
            max_tokens=budget,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            # On a safety-classifier refusal (medical topics can trip one), the
            # API re-runs the request on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        a = self.anthropic
        try:
            with self.client.beta.messages.stream(**params) as stream:
                message = stream.get_final_message()
        except a.APIError as e:
            mapped = api_failure(e, a, "the Anthropic API")
            if mapped is None:
                raise
            raise mapped from e

        if message.stop_reason == "refusal":
            raise RuntimeError("Claude declined this request (refusal) and no fallback model accepted it")
        text = "".join(b.text for b in message.content if b.type == "text")
        if message.stop_reason == "max_tokens":
            # A cut answer is usually broken (half a JSON object): let the router try another model.
            raise AnswerCutOff(f"{self.model} hit max_tokens ({budget}); the answer was cut off", text)
        return text
