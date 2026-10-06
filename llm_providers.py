"""
LLM provider layer: one call_llm() for the whole pipeline, any mix of models behind it.

A model is written "provider:model", e.g.
    gemini:gemini-3-flash-preview     Gemini through the local Gemini Canvas proxy (free)
    claude:claude-opus-5-5            Claude (subscription via the `claude` CLI, or an API key)
    deepseek:deepseek-v4-pro          DeepSeek API (OpenAI-compatible)
    opencode:glm-5.1                  OpenCode Go subscription (OpenAI-compatible)
Any other OpenAI-compatible service can be added under "providers" in
llm_variables.json with "type": "openai". API keys live in llm_keys.env (not in git).

Three modes, picked from a menu when main.py starts (or --provider):
  main    - every node on main_model.
  single  - every node on single_model (a strong model for the whole run).
  hybrid  - (recommended) main_model writes; the nodes in hybrid_routes run on
            the judge model named there.

Every call first tries its model. If that fails for any reason, the same call
is re-run on main_model, so a Claude limit or a DeepSeek outage never stops a
run. If the failure means the provider is down (usage limit, auth, billing),
that provider is skipped for the rest of the run. Only a failure of the main
model itself stops the run.

Run `python3 setup_models.py` to see which providers are active, list their
models, and choose the models for each role.
"""

import contextvars
import functools
import sys
import time
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import uuid
import urllib.request
from typing import Optional

CONFIG_PATH = "llm_variables.json"
KEYS_PATH = "llm_keys.env"

MODES = ("main", "single", "hybrid")
MODE_ALIASES = {"gemini": "main", "claude": "single", "auto": "hybrid"}

DEFAULT_PROVIDERS = {
    # Only this provider gets the Canvas-proxy handling (retries on time-outs,
    # JSON mode, room for thinking tokens). Every other API is called normally.
    "gemini": {"type": "canvas_proxy", "label": "Gemini Canvas proxy", "base_url": "http://localhost:8765/v1",
               "api_key_env": "OPENAI_API_KEY", "free": True,
               "timeout_retries": 2, "retry_wait_seconds": 10, "min_max_tokens": 16000, "json_mode": True},
    "claude": {"type": "claude", "label": "Claude", "transport": "claude_code", "effort": "high",
               "efforts": {}, "timeout_seconds": 900},
    "deepseek": {"type": "openai", "label": "DeepSeek", "base_url": "https://api.deepseek.com",
                 "api_key_env": "DEEPSEEK_API_KEY", "balance_path": "/user/balance"},
    # OpenCode Go serves models through three different APIs (see opencode_endpoint).
    "opencode": {"type": "opencode_go", "label": "OpenCode Go", "base_url": "https://opencode.ai/zen/go/v1",
                 "api_key_env": "OPENCODE_API_KEY", "endpoints": {}},
}

# OpenCode Go model ids from https://opencode.ai/docs/go/ (used when /models can't be read).
OPENCODE_GO_DOC_MODELS = [
    "grok-4.7", "grok-4.6", "gpt-6-luna", "gpt-5.6-luna", "glm-5.3-flash", "glm-5.3", "glm-5.2",
    "kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "longcat-2.0", "longcat-2.5-preview-free",
    "deepseek-v4.1-flash", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
    "mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.5", "mimo-v2.5-pro", "minimax-m3", "minimax-m2.7",
    "muse-spark-1.3-contributor", "muse-spark-1.2-contributor", "qwen3.8-max", "qwen3.8-flash",
    "qwen3.7-plus", "hy4-preview", "hy3", "space-bunny",
]


def opencode_endpoint(model: str, cfg: Optional[dict] = None) -> str:
    """Which API an OpenCode Go model uses: 'chat' (/chat/completions),
    'responses' (OpenAI Responses) or 'messages' (Anthropic-compatible).
    Per-model overrides go in providers.opencode.endpoints."""
    override = ((cfg or {}).get("endpoints") or {}).get(model)
    if override:
        return override
    m = model.lower()
    if m.startswith(("grok-", "gpt-", "muse-spark")):
        return "responses"
    if m.startswith(("minimax-", "qwen")):
        return "messages"
    return "chat"
DEFAULT_MAIN = "gemini:gemini-3-flash-preview"
DEFAULT_BACKUP = ""   # e.g. "deepseek:deepseek-v4-flash": runs a step when the main model fails
DEFAULT_SINGLE = "claude:claude-opus-5-5"
DEFAULT_HYBRID_ROUTES = {
    # High-stakes medical checks: deepest reasoning.
    "medical_truth_verifier": "claude:claude-opus-5-5",
    "fidelity_auditor": "claude:claude-opus-5-5",
    # Critiques: strong, cheaper, a different model family from the writer.
    "self_critique": "claude:claude-sonnet-5-5",
    "packaging_honesty_ctr_auditor": "claude:claude-sonnet-5-5",
    "production_quality_critique": "claude:claude-sonnet-5-5",
}
# The `claude` CLI has no "list models" command.
CLAUDE_SUBSCRIPTION_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"]

# Name of the graph node currently running (set by track_node), and per-step
# state (whether its start line was printed).
current_node = contextvars.ContextVar("current_node", default=None)
_node_state = contextvars.ContextVar("_node_state", default=None)


def track_node(name: str, fn):
    """Wraps a graph node so LLM calls made inside it know which node they belong to."""
    @functools.wraps(fn)
    def wrapper(state):
        token = current_node.set(name)
        state_token = _node_state.set({"announced": False})
        try:
            return fn(state)
        finally:
            current_node.reset(token)
            _node_state.reset(state_token)
    return wrapper


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class ProviderUnavailable(Exception):
    """The provider can't serve requests at all right now (limit, auth, billing, missing tool)."""


_UNAVAILABLE_MARKERS = (
    "usage limit", "limit reached", "weekly limit", "daily limit", "session limit", "hit your",
    "resets ", "out of extra usage", "rate limit", "rate_limit", "overloaded", "insufficient",
    "credit balance", "balance", "billing", "subscription", "not logged in", "please run /login",
    "invalid api key", "api key", "authentication", "unauthorized", "permission", "forbidden",
    "401", "402", "403", "429", "529", "quota",
)


def _looks_unavailable(message: str) -> bool:
    m = (message or "").lower()
    return any(marker in m for marker in _UNAVAILABLE_MARKERS) or bool(re.search(r"\blimit\b", m))


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def parse_spec(spec: str, default_provider: str = "gemini") -> tuple:
    """'deepseek:deepseek-v4-pro' -> ('deepseek', 'deepseek-v4-pro'). A bare model
    name gets 'claude' if it starts with claude-, otherwise default_provider."""
    spec = (spec or "").strip()
    if ":" in spec:
        p, m = spec.split(":", 1)
        return p.strip(), m.strip()
    return ("claude" if spec.startswith("claude") else default_provider), spec


def _sanitize_no_proxy():
    """Some Macs (VPNs, proxy apps, Docker) put IPv6 ranges such as '::1/128' or
    'fe80::/10' in NO_PROXY. The httpx library used by the OpenAI/Anthropic SDKs
    can't read those and fails with "Invalid port: ':1'" before sending anything.
    Drop just those entries, for this process only; everything else is kept."""
    for var in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(var)
        if not value:
            continue
        kept = []
        for entry in value.split(","):
            e = entry.strip()
            if e and "://" not in e and ":" in e and ("/" in e or e.startswith("[")):
                continue  # IPv6 range or bracketed IPv6: unreadable for httpx
            kept.append(entry)
        cleaned = ",".join(kept)
        if cleaned != value:
            os.environ[var] = cleaned


_sanitize_no_proxy()


def load_keys(path: str = KEYS_PATH):
    """Loads API keys from llm_keys.env into the environment (without overriding)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
    except Exception:
        pass


def load_llm_config(path: str = CONFIG_PATH) -> dict:
    """Reads llm_variables.json. Older formats still work: the flat
    {model, api_key, base_url} file and the {gemini, claude, hybrid_routes} one."""
    load_keys()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}

    providers = {name: dict(cfg) for name, cfg in DEFAULT_PROVIDERS.items()}
    for name, cfg in (raw.get("providers") or {}).items():
        providers.setdefault(name, {"type": "openai", "label": name}).update(cfg)

    # Older formats
    legacy_gemini = dict(raw.get("gemini") or {})
    for key in ("model", "api_key", "base_url"):
        if key in raw and key not in legacy_gemini:
            legacy_gemini[key] = raw[key]
    for key in ("api_key", "base_url"):
        if legacy_gemini.get(key):
            providers["gemini"].setdefault(key, legacy_gemini[key])
            if key == "base_url":
                providers["gemini"]["base_url"] = legacy_gemini[key]
    legacy_claude = dict(raw.get("claude") or {})
    for key in ("transport", "effort", "efforts", "timeout_seconds", "cli_path", "api_key"):
        if key in legacy_claude:
            providers["claude"][key] = legacy_claude[key]

    main_model = raw.get("main_model") or (
        f"gemini:{legacy_gemini['model']}" if legacy_gemini.get("model") else DEFAULT_MAIN)
    single_model = raw.get("single_model") or (
        f"claude:{legacy_claude['model']}" if legacy_claude.get("model") else DEFAULT_SINGLE)
    main_provider = parse_spec(main_model)[0]
    routes = {}
    for node, spec in (raw.get("hybrid_routes") or DEFAULT_HYBRID_ROUTES).items():
        p, m = parse_spec(spec, main_provider)
        routes[node] = f"{p}:{m}"

    mode = (raw.get("provider") or "hybrid").lower()
    return {
        "provider": MODE_ALIASES.get(mode, mode),
        "main_model": main_model,
        "single_model": single_model,
        "backup_model": raw.get("backup_model", DEFAULT_BACKUP),
        "hybrid_routes": routes,
        "providers": providers,
    }


def save_llm_config(config: dict, path: str = CONFIG_PATH):
    """Writes the new format, keeping any fields already in the file that we don't manage."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    for legacy in ("model", "api_key", "base_url", "temperature", "gemini", "claude", "fallback_to_gemini"):
        raw.pop(legacy, None)
    providers_out = {}
    for name, cfg in config["providers"].items():
        default = DEFAULT_PROVIDERS.get(name, {})
        diff = {k: v for k, v in cfg.items() if default.get(k) != v}
        if diff or name not in DEFAULT_PROVIDERS:
            providers_out[name] = diff
    raw.update({
        "provider": config["provider"],
        "main_model": config["main_model"],
        "single_model": config["single_model"],
        "backup_model": config.get("backup_model", ""),
        "hybrid_routes": config["hybrid_routes"],
        "providers": providers_out,
    })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=4)
        f.write("\n")


# OpenCode Go refuses requests without an x-opencode-session header
# ("MissingSessionID"); it uses it to route a conversation's calls together.
# One id per run of the workflow.
OPENCODE_SESSION_ID = f"medical-brain-{uuid.uuid4()}"


def provider_headers(cfg: dict) -> dict:
    """Extra HTTP headers a provider needs: its configured "headers", plus the
    session header for OpenCode Go."""
    headers = dict(cfg.get("headers") or {})
    if cfg.get("type") == "opencode_go":
        headers.setdefault("x-opencode-session", OPENCODE_SESSION_ID)
    return headers


def provider_key(cfg: dict) -> Optional[str]:
    if cfg.get("api_key"):
        return str(cfg["api_key"]).strip()
    if cfg.get("token_file"):
        try:
            with open(os.path.expanduser(cfg["token_file"]), encoding="utf-8") as f:
                token = f.read().strip()
            if token:
                return token
        except OSError:
            pass
    value = os.environ.get(cfg["api_key_env"]) if cfg.get("api_key_env") else None
    return value.strip() if value else value


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
class _LimitTooHigh(Exception):
    """The model refused the requested output limit."""


def _limit_refused(err) -> bool:
    m = str(err).lower()
    return ("max_tokens" in m or "max_output_tokens" in m or "maximum" in m) and "temperature" not in m


class StepTimeout(RuntimeError):
    """A call took longer than step_timeout_seconds and was abandoned."""


class _Progress:
    """One live status line while a model streams: elapsed time, thinking or
    writing, and roughly how many tokens. Also enforces the step time-out."""

    def __init__(self, label: str, timeout: Optional[float]):
        self.label, self.timeout = label, timeout
        self.start = self.last = time.monotonic()
        self.thinking = self.writing = 0
        self.shown = False

    def tick(self, thinking: int = 0, writing: int = 0):
        self.thinking += thinking
        self.writing += writing
        now = time.monotonic()
        elapsed = now - self.start
        if self.timeout and elapsed > self.timeout:
            self.done()
            raise StepTimeout(f"{self.label} took more than {int(self.timeout)} s "
                              f"(step_timeout_seconds) and was stopped")
        if now - self.last >= 3:
            self.last = now
            what = f"writing ~{self.writing:,} tokens" if self.writing else \
                (f"thinking (~{self.thinking:,} tokens)…" if self.thinking else "waiting for the first words…")
            sys.stdout.write(f"\r  ⏳ {self.label} · {int(elapsed) // 60}m {int(elapsed) % 60:02d}s · {what}   ")
            sys.stdout.flush()
            self.shown = True

    def elapsed(self) -> float:
        return time.monotonic() - self.start

    def done(self):
        if self.shown:
            sys.stdout.write("\r" + " " * 90 + "\r")
            sys.stdout.flush()
            self.shown = False


def _step_timeout(cfg: dict) -> Optional[float]:
    value = cfg.get("step_timeout_seconds", 600)
    return float(value) if value else None


def _client_timeout(cfg: dict) -> float:
    """HTTP time-out: also covers a server that goes completely silent mid-stream."""
    limits = [float(cfg.get("timeout_seconds", 900))]
    if _step_timeout(cfg):
        limits.append(_step_timeout(cfg))
    return min(limits)


class _OutputLimits:
    """Output-length handling shared by the API backends (not the Gemini proxy,
    which has its own):
      - reasoning models (Qwen, DeepSeek, Kimi...) think before answering and
        those tokens count against max_tokens, so the step's limit (tuned for
        Gemini) is raised to at least `min_max_tokens` (default 16000). It is a
        ceiling, not a target: you only pay for tokens actually produced;
      - an answer that still stops at the limit is re-sent once with double the
        limit, up to `max_output_cap` (default 32000);
      - a model that refuses a limit that high is retried with 8192."""

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float], max_tokens: Optional[int]) -> str:
        floor = int(self.cfg.get("min_max_tokens", 16000) or 0)
        cap = int(self.cfg.get("max_output_cap", 32000) or 0)
        limit = max(max_tokens or 0, floor) or None
        started = time.monotonic()
        try:
            text, cut = self._once(system_prompt, user_prompt, temperature, limit)
        except _LimitTooHigh:
            limit = 8192
            text, cut = self._once(system_prompt, user_prompt, temperature, limit)
        timeout = _step_timeout(self.cfg)
        slow = bool(timeout) and time.monotonic() - started > timeout / 2
        if cut and limit and limit < cap and not slow:
            limit = min(limit * 2, cap)
            try:
                text, cut = self._once(system_prompt, user_prompt, temperature, limit)
            except _LimitTooHigh:
                pass  # keep the first (cut) answer
        if cut:
            node = current_node.get() or "this step"
            print(f"  ⚠️ {node}: {self.provider}:{self.model} hit max_tokens ({limit}); the text may be cut off")
        return text


class OpenAICompatibleBackend(_OutputLimits):
    """DeepSeek, OpenCode Go, or any OpenAI-compatible API: called normally,
    with the SDK's standard retries and a long timeout. No proxy limits."""

    def __init__(self, provider: str, cfg: dict, model: str, max_retries: int = 1, timeout: Optional[float] = None):
        from openai import OpenAI
        self.provider, self.cfg, self.model = provider, cfg, model
        key = provider_key(cfg)
        if not key and not cfg.get("free"):
            raise ProviderUnavailable(f"no API key: set {cfg.get('api_key_env')} in {KEYS_PATH} (run setup_models.py)")
        self.client = OpenAI(api_key=key or "not-needed", base_url=cfg["base_url"],
                             default_headers=provider_headers(cfg) or None,
                             timeout=timeout or _client_timeout(cfg), max_retries=max_retries)
        self.no_temperature = False

    def _params(self, system_prompt, user_prompt, temperature, max_tokens) -> dict:
        params = dict(model=self.model, messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if temperature is not None and not self.no_temperature:
            params["temperature"] = temperature
        return params

    def _create(self, params: dict):
        import openai
        try:
            return self.client.chat.completions.create(**params)
        except openai.BadRequestError as e:
            # Some reasoning models reject temperature: retry once without it.
            if "temperature" in params and "temperature" in str(e).lower():
                self.no_temperature = True
                params.pop("temperature")
                return self.client.chat.completions.create(**params)
            raise

    def _text(self, resp) -> str:
        """Used by the Gemini proxy backend."""
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            node = current_node.get() or "this step"
            print(f"  ⚠️ {node}: {self.provider}:{self.model} hit max_tokens; the text may be cut off")
        return choice.message.content or ""

    def _map_errors(self, fn):
        import openai
        try:
            return fn()
        except openai.BadRequestError as e:
            if _limit_refused(e):
                raise _LimitTooHigh(str(e))
            raise
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError) as e:
            raise ProviderUnavailable(str(e))
        except openai.APIStatusError as e:
            if e.status_code in (402, 429, 529) or _looks_unavailable(str(e)):
                raise ProviderUnavailable(str(e))
            raise
        except openai.APIConnectionError as e:
            raise ProviderUnavailable(f"cannot reach {self.cfg['base_url']}: {e}")

    def _once(self, system_prompt, user_prompt, temperature, limit) -> tuple:
        """Streams the answer so progress can be shown and a too-slow call stopped."""
        params = self._params(system_prompt, user_prompt, temperature, limit)
        params["stream"] = True
        progress = _Progress(self.model, _step_timeout(self.cfg))

        def run():
            stream = self._create(params)
            parts, finish = [], None
            try:
                for chunk in stream:
                    if not chunk.choices:
                        progress.tick()
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    content = getattr(delta, "content", None)
                    reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                    if content:
                        parts.append(content)
                    progress.tick(thinking=1 if reasoning else 0, writing=1 if content else 0)
                    if choice.finish_reason:
                        finish = choice.finish_reason
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
                progress.done()
            return "".join(parts), finish == "length"
        return self._map_errors(run)


_WANTS_JSON = re.compile(r"(only\s+(valid\s+)?json|only\s+the\s+json|only\s+a\s+json|output\s+json)", re.IGNORECASE)


class CanvasProxyBackend(OpenAICompatibleBackend):
    """Gemini through the local Gemini Canvas proxy (pranrichh/gemini-canvas-proxy).
    Handles the proxy's limits, and only the proxy's:
      - it gives each request 60 s, then answers 504 (Gemini keeps writing, the
        answer is lost): retry the step `timeout_retries` times;
      - Gemini 3's hidden thinking counts against max_tokens and can cut answers
        (and JSON) short: raise max_tokens to at least `min_max_tokens`;
      - the proxy supports JSON mode: switch it on when the prompt asks for JSON only.
    Safety blocks (502 with blockReason) are not retried: the same prompt gets the same answer."""

    def __init__(self, provider: str, cfg: dict, model: str):
        # Our own retry loop replaces the SDK's, so a time-out isn't silently doubled.
        super().__init__(provider, cfg, model, max_retries=0, timeout=cfg.get("timeout_seconds", 300))

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float], max_tokens: Optional[int]) -> str:
        import time
        import openai
        floor = self.cfg.get("min_max_tokens") or 0
        params = self._params(system_prompt, user_prompt, temperature, max(max_tokens or 0, floor) or None)
        if self.cfg.get("json_mode", True) and _WANTS_JSON.search(system_prompt + "\n" + user_prompt[-600:]):
            params["response_format"] = {"type": "json_object"}
        attempts = 1 + int(self.cfg.get("timeout_retries", 2))
        wait = self.cfg.get("retry_wait_seconds", 10)
        for attempt in range(1, attempts + 1):
            try:
                return self._text(self._create(params))
            except openai.AuthenticationError as e:
                raise ProviderUnavailable(f"proxy token rejected ({e}). Check the token in setup_models.py")
            except openai.APIStatusError as e:
                body = str(e)
                safety = "blockReason" in body or "finishReason" in body or "safety" in body.lower()
                if e.status_code in (502, 503, 504) and not safety and attempt < attempts:
                    why = "timed out after 60 s" if e.status_code == 504 else f"error {e.status_code}"
                    print(f"  ⏳ Gemini Canvas {why}; retrying ({attempt}/{attempts - 1}) in {wait}s...")
                    time.sleep(wait)
                    continue
                raise
            except (openai.APIConnectionError, openai.APITimeoutError) as e:
                if attempt < attempts:
                    print(f"  ⏳ Gemini Canvas proxy not reachable ({e}); retrying ({attempt}/{attempts - 1}) in {wait}s...")
                    time.sleep(wait)
                    continue
                raise ProviderUnavailable(f"cannot reach the Gemini Canvas proxy at {self.cfg['base_url']}: {e}")


class OpenAIResponsesBackend(OpenAICompatibleBackend):
    """OpenAI Responses API (/responses): OpenCode Go's Grok, GPT Luna and Muse Spark models."""

    def _once(self, system_prompt, user_prompt, temperature, limit) -> tuple:
        import openai
        params = dict(model=self.model, instructions=system_prompt, input=user_prompt, stream=True)
        if limit is not None:
            params["max_output_tokens"] = limit
        if temperature is not None and not self.no_temperature:
            params["temperature"] = temperature
        progress = _Progress(self.model, _step_timeout(self.cfg))

        def create():
            try:
                return self.client.responses.create(**params)
            except openai.BadRequestError as e:
                if "temperature" in params and "temperature" in str(e).lower():
                    self.no_temperature = True
                    params.pop("temperature")
                    return self.client.responses.create(**params)
                raise

        def run():
            stream = create()
            parts, incomplete, final_text = [], False, None
            try:
                for event in stream:
                    kind = getattr(event, "type", "")
                    if kind == "response.output_text.delta":
                        parts.append(event.delta)
                        progress.tick(writing=1)
                    elif "reasoning" in kind and kind.endswith(".delta"):
                        progress.tick(thinking=1)
                    elif kind == "response.incomplete":
                        incomplete = True
                        progress.tick()
                    elif kind == "response.completed":
                        final_text = getattr(event.response, "output_text", None)
                        progress.tick()
                    else:
                        progress.tick()
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
                progress.done()
            return (final_text if final_text is not None else "".join(parts)), incomplete
        return self._map_errors(run)


class AnthropicCompatibleBackend(_OutputLimits):
    """Anthropic-compatible /v1/messages API of a third-party service
    (OpenCode Go's MiniMax and Qwen models). Not used for Claude itself."""

    def __init__(self, provider: str, cfg: dict, model: str):
        try:
            import anthropic
        except ImportError:
            raise ProviderUnavailable("the `anthropic` package is not installed (pip install anthropic)")
        self.anthropic, self.provider, self.cfg, self.model = anthropic, provider, cfg, model
        key = provider_key(cfg)
        if not key:
            raise ProviderUnavailable(f"no API key: set {cfg.get('api_key_env')} in {KEYS_PATH} (run setup_models.py)")
        # The SDK appends /v1/messages, so drop a trailing /v1 from the base URL.
        base = re.sub(r"/v1/?$", "", cfg["base_url"].rstrip("/"))
        self.client = anthropic.Anthropic(api_key=key, base_url=base,
                                          default_headers={"Authorization": f"Bearer {key}", **provider_headers(cfg)},
                                          timeout=_client_timeout(cfg), max_retries=1)
        self.no_temperature = False

    def _once(self, system_prompt, user_prompt, temperature, limit) -> tuple:
        a = self.anthropic
        params = dict(model=self.model, max_tokens=limit or 16000, system=system_prompt,
                      messages=[{"role": "user", "content": user_prompt}])
        if temperature is not None and not self.no_temperature:
            # anthropic SDK 1.x has no temperature argument (current Claude models
            # reject it); third-party Anthropic-compatible APIs still accept it.
            params["extra_body"] = {"temperature": temperature}

        progress = _Progress(self.model, _step_timeout(self.cfg))

        def run():
            # Streamed so long outputs don't hit HTTP time-outs, with live progress.
            try:
                with self.client.messages.stream(**params) as stream:
                    for event in stream:
                        delta = getattr(event, "delta", None)
                        kind = getattr(delta, "type", "") if delta is not None else ""
                        progress.tick(thinking=1 if kind == "thinking_delta" else 0,
                                      writing=1 if kind == "text_delta" else 0)
                    return stream.get_final_message()
            finally:
                progress.done()
        try:
            try:
                message = run()
            except a.BadRequestError as e:
                if "extra_body" in params and "temperature" in str(e).lower():
                    self.no_temperature = True
                    params.pop("extra_body")
                    message = run()
                else:
                    raise
        except a.BadRequestError as e:
            if _limit_refused(e):
                raise _LimitTooHigh(str(e))
            raise
        except (a.AuthenticationError, a.PermissionDeniedError, a.RateLimitError) as e:
            raise ProviderUnavailable(str(e))
        except a.APIStatusError as e:
            if e.status_code in (402, 429, 529) or _looks_unavailable(str(e)):
                raise ProviderUnavailable(str(e))
            raise
        except a.APIConnectionError as e:
            raise ProviderUnavailable(f"cannot reach {self.cfg['base_url']}: {e}")
        text = "".join(getattr(b, "text", "") for b in message.content if b.type == "text")
        return text, message.stop_reason == "max_tokens"


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
        except (a.AuthenticationError, a.PermissionDeniedError, a.RateLimitError) as e:
            raise ProviderUnavailable(str(e))
        except a.APIStatusError as e:
            if e.status_code in (402, 529) or _looks_unavailable(str(e)):
                raise ProviderUnavailable(str(e))
            raise
        except a.APIConnectionError as e:
            raise ProviderUnavailable(str(e))

        if message.stop_reason == "refusal":
            raise RuntimeError("Claude declined this request (refusal) and no fallback model accepted it")
        text = "".join(b.text for b in message.content if b.type == "text")
        if message.stop_reason == "max_tokens":
            print(f"  [warning] Claude output hit max_tokens ({budget}); the text may be cut off")
        return text


def make_backend(spec: str, providers: dict):
    provider, model = parse_spec(spec)
    cfg = providers.get(provider)
    if cfg is None:
        raise ProviderUnavailable(f"unknown provider '{provider}' (add it under providers in {CONFIG_PATH})")
    if cfg.get("type") == "claude":
        if cfg.get("transport") == "anthropic_api":
            return AnthropicAPIBackend(cfg, model)
        return ClaudeCodeBackend(cfg, model)
    if cfg.get("type") == "canvas_proxy":
        return CanvasProxyBackend(provider, cfg, model)
    if cfg.get("type") == "opencode_go":
        endpoint = opencode_endpoint(model, cfg)
        if endpoint == "responses":
            return OpenAIResponsesBackend(provider, cfg, model)
        if endpoint == "messages":
            return AnthropicCompatibleBackend(provider, cfg, model)
    return OpenAICompatibleBackend(provider, cfg, model)


# --------------------------------------------------------------------------
# Provider checks (used by setup_models.py and the startup check)
# --------------------------------------------------------------------------
class HTTPProblem(Exception):
    pass


def _http_json(url: str, key: Optional[str], timeout: int = 15, extra_headers: Optional[dict] = None):
    # A normal User-Agent: some services (e.g. opencode.ai behind Cloudflare)
    # answer 403 to Python's default "Python-urllib/3.x".
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key or 'not-needed'}",
                                               "Accept": "application/json",
                                               "User-Agent": "medical-brain-workflow/1.0",
                                               **(extra_headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Show the server's own explanation, not just "Forbidden".
        try:
            body = e.read().decode("utf-8", "ignore")
            try:
                data = json.loads(body)
                err = data.get("error", data) if isinstance(data, dict) else data
                body = err.get("message", err) if isinstance(err, dict) else err
            except ValueError:
                body = re.sub(r"<[^>]+>", " ", body)
        except Exception:
            body = ""
        body = re.sub(r"\s+", " ", str(body)).strip()[:200]
        raise HTTPProblem(f"HTTP {e.code} {e.reason}" + (f": {body}" if body else ""))


def list_models(provider: str, cfg: dict) -> list:
    """Model ids this provider offers (raises on failure)."""
    if cfg.get("type") == "claude":
        if cfg.get("transport") == "anthropic_api":
            import anthropic
            key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
            client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
            return [m.id for m in client.models.list()]
        return list(CLAUDE_SUBSCRIPTION_MODELS)
    try:
        data = _http_json(cfg["base_url"].rstrip("/") + "/models", provider_key(cfg), extra_headers=provider_headers(cfg))
    except Exception:
        if cfg.get("type") == "opencode_go" and provider_key(cfg):
            return list(OPENCODE_GO_DOC_MODELS)   # documented list; each pick is test-called
        raise
    items = data.get("data", data) if isinstance(data, dict) else data
    ids = [m.get("id") if isinstance(m, dict) else str(m) for m in items or []]
    if cfg.get("type") == "canvas_proxy":
        ids = [i for i in ids if i and "image" not in i]  # image-generation models can't write scripts
    return sorted(i for i in ids if i)


def provider_status(provider: str, cfg: dict) -> tuple:
    """(state, detail) with state 'active', 'off' (not set up) or 'error'."""
    if cfg.get("type") == "claude":
        if cfg.get("transport") == "anthropic_api":
            if not (cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")):
                return "off", "no ANTHROPIC_API_KEY"
        elif not (cfg.get("cli_path") or shutil.which("claude")):
            return "off", "`claude` command not installed"
        ok, detail = test_model(f"{provider}:{CLAUDE_SUBSCRIPTION_MODELS[1]}", {provider: cfg})
        return ("active", "subscription answers") if ok else ("error", detail)
    if not provider_key(cfg) and not cfg.get("free"):
        return "off", f"no API key ({cfg.get('api_key_env')})"
    detail = ""
    if cfg.get("balance_path"):
        try:
            b = _http_json(cfg["base_url"].rstrip("/") + cfg["balance_path"], provider_key(cfg))
            infos = b.get("balance_infos") or []
            detail = ", ".join(f"{i.get('total_balance')} {i.get('currency')}" for i in infos)
            if b.get("is_available") is False:
                return "error", f"balance too low ({detail or 'no balance'})"
        except Exception as e:
            return "error", _short(e)
    if cfg.get("type") == "opencode_go":
        # Read /models; if that's refused, check the key with a real call instead.
        try:
            data = _http_json(cfg["base_url"].rstrip("/") + "/models", provider_key(cfg), extra_headers=provider_headers(cfg))
            items = data.get("data", data) if isinstance(data, dict) else data
            return "active", f"{len(items or [])} models"
        except Exception as e:
            listing_error = _short(e)
        ok, why = test_model(f"{provider}:glm-5.3-flash", {provider: cfg})
        if ok:
            return "active", f"key works; model list from the docs (/models said: {listing_error})"
        return "error", f"{why} (/models said: {listing_error})"
    try:
        n = len(list_models(provider, cfg))
        return "active", (f"{n} models" + (f" · balance {detail}" if detail else ""))
    except Exception as e:
        return "error", _short(e)


def test_model(spec: str, providers: dict) -> tuple:
    """One tiny real call. (ok, detail)."""
    started = time.monotonic()
    try:
        # 512 tokens: reasoning models spend some on thinking before answering.
        reply = make_backend(spec, providers).call("Reply with the single word: OK", "ping", None, 512)
        took = time.monotonic() - started
        return (True, f"answers ({took:.1f} s)") if reply.strip() else (False, "empty reply")
    except Exception as e:
        return False, _short(e)


def _short(e) -> str:
    text = re.sub(r"\s+", " ", str(e))[:300]
    if "Invalid port" in text or "InvalidURL" in type(e).__name__:
        text += " (your NO_PROXY setting has an entry Python can't read; run: env | grep -i proxy)"
    return text


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------
class LLMRouter:
    def __init__(self, config: dict, provider: Optional[str] = None):
        self.config = config
        mode = (provider or os.environ.get("LLM_PROVIDER") or config["provider"]).lower()
        mode = MODE_ALIASES.get(mode, mode)
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}' (use main, single or hybrid)")
        self.mode = mode
        self.main = config["main_model"]
        self.backup = config.get("backup_model") or ""
        if self.backup == self.main:
            self.backup = ""
        self.routes = dict(config["hybrid_routes"])
        self.down = {}          # provider -> reason, for the rest of the run
        self.events = []
        self.calls = {}
        self.node_models = {}
        self._backends = {}

    def model_for(self, node: Optional[str]) -> str:
        if self.mode == "single":
            return self.config["single_model"]
        if self.mode == "hybrid":
            return self.routes.get(node, self.main)
        return self.main

    def models_in_use(self) -> list:
        if self.mode == "single":
            return [self.config["single_model"], self.main] + ([self.backup] if self.backup else [])
        extra = [self.backup] if self.backup else []
        if self.mode == "hybrid":
            return [self.main] + sorted(set(self.routes.values()) - {self.main}) + extra
        return [self.main] + extra

    def describe(self) -> str:
        if self.mode == "main":
            return f"all nodes on {self.main}"
        if self.mode == "single":
            return f"all nodes on {self.config['single_model']} (falls back to {self.main})"
        routed = ", ".join(f"{n} → {m}" for n, m in self.routes.items())
        backup = f"; backup {self.backup}" if self.backup else ""
        return f"{self.main} writes; {routed}{backup}"

    def _backend(self, spec: str):
        if spec not in self._backends:
            self._backends[spec] = make_backend(spec, self.config["providers"])
        return self._backends[spec]

    def _log(self, msg: str):
        print(f"🔀 [LLM] {msg}")
        self.events.append(msg)

    def select(self, ask=None):
        """Quick check of every model this run will use. Never blocks: a model
        that fails here is simply skipped and its steps run on the main model."""
        self._log(f"Mode: {self.mode} · {self.describe()}")
        for spec in self.models_in_use():
            provider = parse_spec(spec)[0]
            if provider in self.down:
                continue
            ok, detail = test_model(spec, self.config["providers"])
            if ok:
                self._log(f"✅ {spec}")
            elif spec == self.main:
                then = f"steps will run on the backup model {self.backup}" if self.backup else \
                    "the run will stop at the first step if it stays down (set a backup model in setup_models.py)"
                self._log(f"⚠️ main model {spec} did not answer ({detail}). Each step will still try it first; if it fails, {then}.")
            else:
                self.down[provider] = detail
                self._log(f"⚠️ {spec} unavailable ({detail}). Its steps will run on {self.main}.")

    def _count(self, node, spec):
        self.calls[spec] = self.calls.get(spec, 0) + 1
        if node:
            self.node_models[node] = spec

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
        node = current_node.get()
        spec = self.model_for(node)
        state = _node_state.get()
        if node and state is not None and not state["announced"]:
            state["announced"] = True
            target = spec if parse_spec(spec)[0] not in self.down else self.main
            print(f"▶ {node} … ({target})", flush=True)
        if spec != self.main:
            provider = parse_spec(spec)[0]
            if provider not in self.down:
                try:
                    result = self._backend(spec).call(system_prompt, user_prompt, temperature, max_tokens)
                    self._count(node, spec)
                    return result
                except ProviderUnavailable as e:
                    self.down[provider] = _short(e)
                    self._log(f"⚠️ {node}: {spec} unavailable ({_short(e)}). "
                              f"This and later {provider} steps run on {self.main}.")
                except Exception as e:
                    self._log(f"⚠️ {node}: {spec} failed ({_short(e)}). Running this step on {self.main}.")
        try:
            result = self._backend(self.main).call(system_prompt, user_prompt, temperature, max_tokens)
            self._count(node, self.main)
            return result
        except Exception as e:
            if not self.backup or parse_spec(self.backup)[0] in self.down:
                raise
            self._log(f"⚠️ {node}: main model {self.main} failed ({_short(e)}). Running this step on the backup {self.backup}.")
        result = self._backend(self.backup).call(system_prompt, user_prompt, temperature, max_tokens)
        self._count(node, self.backup)
        return result

    def model_for_node(self, node: str) -> Optional[str]:
        return self.node_models.get(node)

    def report(self) -> dict:
        return {
            "mode": self.mode,
            "setup": self.describe(),
            "calls": dict(self.calls),
            "node_models": dict(self.node_models),
            "unavailable": dict(self.down),
            "events": list(self.events),
        }
