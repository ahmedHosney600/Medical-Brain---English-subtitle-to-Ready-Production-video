"""
LLM provider layer: one call_llm() for the whole pipeline, two paths behind it.

  gemini  - Gemini through the local Gemini Canvas proxy (OpenAI-compatible,
            http://localhost:8765/v1). Free, always available while the Canvas
            tab is open.
  claude  - Claude Opus 5.5. Two ways to reach it:
              * "claude_code"   - your Claude subscription, through the `claude`
                                  CLI in headless mode (`claude -p`). Needs
                                  Claude Code installed and logged in.
              * "anthropic_api" - pay-per-token Anthropic API key
                                  (ANTHROPIC_API_KEY).

Selection ("provider" in llm_variables.json, LLM_PROVIDER env var, or
--provider on the command line):

  auto    - run a tiny test call against Claude. If it answers, the run uses
            Claude; if it fails (not logged in, subscription inactive, usage
            limit, no key) the run uses Gemini Canvas.
  claude  - Claude; stop with an error if it is unavailable at the start.
  gemini  - Gemini Canvas only (the original behaviour).

If Claude stops working in the middle of a run (usage limit hit, subscription
lapsed) and "fallback_to_gemini" is true (the default), the remaining calls switch to Gemini so the run
still finishes. Every switch is printed and recorded in llm_provider_report().
"""

import json
import os
import shutil
import subprocess
import tempfile
from typing import Optional

CLAUDE_DEFAULT_MODEL = "claude-opus-5-5"
GEMINI_DEFAULT_MODEL = "gemini-3-flash-preview"
GEMINI_DEFAULT_BASE_URL = "http://localhost:8765/v1"

# Words in an error message that mean "Claude is not usable right now" rather
# than "this one request was bad". On these we fall back to Gemini.
_UNAVAILABLE_MARKERS = (
    "usage limit", "limit reached", "rate limit", "rate_limit", "overloaded",
    "credit balance", "billing", "subscription", "not logged in", "please run /login",
    "invalid api key", "authentication", "unauthorized", "permission", "forbidden",
    "401", "403", "429", "529", "quota",
)


class ProviderUnavailable(Exception):
    """Raised when a provider can't serve requests at all (auth, limits, missing tool)."""


def _looks_unavailable(message: str) -> bool:
    m = (message or "").lower()
    return any(marker in m for marker in _UNAVAILABLE_MARKERS)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def load_llm_config(path: str = "llm_variables.json") -> dict:
    """Reads llm_variables.json. The old flat format ({model, api_key, base_url,
    ...}) is still accepted and treated as the Gemini settings."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}

    gemini = dict(raw.get("gemini") or {})
    for key in ("model", "api_key", "base_url"):
        if key in raw and key not in gemini:
            gemini[key] = raw[key]
    gemini.setdefault("model", GEMINI_DEFAULT_MODEL)
    gemini.setdefault("base_url", GEMINI_DEFAULT_BASE_URL)

    claude = dict(raw.get("claude") or {})
    claude.setdefault("model", CLAUDE_DEFAULT_MODEL)
    claude.setdefault("transport", "claude_code")   # claude_code | anthropic_api
    claude.setdefault("effort", "high")             # low | medium | high | xhigh | max
    claude.setdefault("timeout_seconds", 900)

    return {
        "provider": raw.get("provider", "auto"),
        "fallback_to_gemini": raw.get("fallback_to_gemini", True),
        "gemini": gemini,
        "claude": claude,
    }


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
class GeminiCanvasBackend:
    name = "gemini"

    def __init__(self, cfg: dict):
        from langchain_openai import ChatOpenAI
        self.model = cfg["model"]
        self.llm = ChatOpenAI(
            model=cfg["model"],
            api_key=cfg.get("api_key") or os.environ.get("OPENAI_API_KEY") or "not-needed",
            base_url=cfg.get("base_url"),
        )

    def label(self) -> str:
        return f"Gemini Canvas proxy ({self.model})"

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float], max_tokens: Optional[int]) -> str:
        from langchain_core.messages import SystemMessage, HumanMessage
        kwargs = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = self.llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
            **kwargs,
        )
        return response.content


class ClaudeCodeBackend:
    """Claude through the Claude subscription: runs `claude -p` with all tools
    disabled, so it behaves like a plain text completion."""
    name = "claude"

    def __init__(self, cfg: dict):
        self.model = cfg["model"]
        self.effort = cfg.get("effort")
        self.timeout = cfg.get("timeout_seconds", 900)
        self.cli = cfg.get("cli_path") or shutil.which("claude")
        if not self.cli:
            raise ProviderUnavailable("the `claude` command was not found (install Claude Code and log in)")

    def label(self) -> str:
        return f"Claude {self.model} via Claude subscription (claude CLI)"

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
    name = "claude"

    def __init__(self, cfg: dict):
        try:
            import anthropic
        except ImportError:
            raise ProviderUnavailable("the `anthropic` package is not installed (pip install anthropic)")
        self.anthropic = anthropic
        self.model = cfg["model"]
        self.effort = cfg.get("effort")
        api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def label(self) -> str:
        return f"Claude {self.model} via Anthropic API"

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
        except (a.AuthenticationError, a.PermissionDeniedError, a.RateLimitError) as e:
            raise ProviderUnavailable(str(e))
        except a.APIStatusError as e:
            if e.status_code in (402, 529) or _looks_unavailable(str(e)):
                raise ProviderUnavailable(str(e))
            raise

        if message.stop_reason == "refusal":
            raise RuntimeError("Claude declined this request (refusal) and no fallback model accepted it")
        text = "".join(b.text for b in message.content if b.type == "text")
        if message.stop_reason == "max_tokens":
            print(f"  [warning] Claude output hit max_tokens ({budget}); the text may be cut off")
        return text


def _make_claude_backend(cfg: dict):
    transport = cfg.get("transport", "claude_code")
    if transport == "anthropic_api":
        return AnthropicAPIBackend(cfg)
    if transport == "claude_code":
        return ClaudeCodeBackend(cfg)
    raise ValueError(f"Unknown claude transport '{transport}' (use claude_code or anthropic_api)")


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------
class LLMRouter:
    def __init__(self, config: dict, provider: Optional[str] = None):
        self.config = config
        self.requested = (provider or os.environ.get("LLM_PROVIDER") or config["provider"]).lower()
        if self.requested not in ("auto", "claude", "gemini"):
            raise ValueError(f"Unknown provider '{self.requested}' (use auto, claude or gemini)")
        # Mid-run switch to Gemini if Claude stops working (auto and claude modes).
        self.allow_fallback = bool(config.get("fallback_to_gemini", True))
        self.events = []
        self.calls = {"claude": 0, "gemini": 0}
        self.active = None
        self._gemini = None

    def _gemini_backend(self):
        if self._gemini is None:
            self._gemini = GeminiCanvasBackend(self.config["gemini"])
        return self._gemini

    def _log(self, msg: str):
        print(f"🔀 [LLM] {msg}")
        self.events.append(msg)

    def select(self):
        """Pick the backend for this run. Called once, before the graph starts."""
        if self.requested == "gemini":
            self.active = self._gemini_backend()
            self._log(f"Using {self.active.label()}")
            return self.active

        try:
            backend = _make_claude_backend(self.config["claude"])
            reply = backend.call("Reply with the single word: OK", "ping", None, 16)
            if not reply.strip():
                raise ProviderUnavailable("empty reply to the test call")
            self.active = backend
            self._log(f"Claude is available. Using {backend.label()}")
        except Exception as e:
            if self.requested == "claude":
                raise RuntimeError(f"Claude was requested but is unavailable: {e}")
            self.active = self._gemini_backend()
            self._log(f"Claude unavailable ({str(e)[:200]}). Using {self.active.label()}")
        return self.active

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
        if self.active is None:
            self.select()
        try:
            result = self.active.call(system_prompt, user_prompt, temperature, max_tokens)
            self.calls[self.active.name] += 1
            return result
        except ProviderUnavailable as e:
            if self.active.name != "claude" or not self.allow_fallback:
                raise
            self._log(f"Claude stopped working mid-run ({str(e)[:200]}). "
                      f"Switching the remaining steps to Gemini Canvas.")
            self.active = self._gemini_backend()
            result = self.active.call(system_prompt, user_prompt, temperature, max_tokens)
            self.calls["gemini"] += 1
            return result

    def report(self) -> dict:
        return {
            "requested_provider": self.requested,
            "final_backend": self.active.label() if self.active else None,
            "calls": dict(self.calls),
            "events": list(self.events),
        }
