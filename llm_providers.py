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
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Optional

CONFIG_PATH = "llm_variables.json"
KEYS_PATH = "llm_keys.env"

MODES = ("main", "single", "hybrid")
MODE_ALIASES = {"gemini": "main", "claude": "single", "auto": "hybrid"}

DEFAULT_PROVIDERS = {
    "gemini": {"type": "openai", "label": "Gemini Canvas proxy", "base_url": "http://localhost:8765/v1",
               "api_key_env": "OPENAI_API_KEY", "free": True},
    "claude": {"type": "claude", "label": "Claude", "transport": "claude_code", "effort": "high",
               "efforts": {}, "timeout_seconds": 900},
    "deepseek": {"type": "openai", "label": "DeepSeek", "base_url": "https://api.deepseek.com",
                 "api_key_env": "DEEPSEEK_API_KEY", "balance_path": "/user/balance"},
    "opencode": {"type": "openai", "label": "OpenCode Go", "base_url": "https://opencode.ai/zen/go/v1",
                 "api_key_env": "OPENCODE_API_KEY"},
}
DEFAULT_MAIN = "gemini:gemini-3-flash-preview"
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

# Name of the graph node currently running (set by track_node).
current_node = contextvars.ContextVar("current_node", default=None)


def track_node(name: str, fn):
    """Wraps a graph node so LLM calls made inside it know which node they belong to."""
    @functools.wraps(fn)
    def wrapper(state):
        token = current_node.set(name)
        try:
            return fn(state)
        finally:
            current_node.reset(token)
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
        "hybrid_routes": config["hybrid_routes"],
        "providers": providers_out,
    })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=4)
        f.write("\n")


def provider_key(cfg: dict) -> Optional[str]:
    return cfg.get("api_key") or (os.environ.get(cfg["api_key_env"]) if cfg.get("api_key_env") else None)


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
class OpenAICompatibleBackend:
    """Gemini Canvas proxy, DeepSeek, OpenCode Go, or any OpenAI-compatible API."""

    def __init__(self, provider: str, cfg: dict, model: str):
        from openai import OpenAI
        self.provider, self.cfg, self.model = provider, cfg, model
        key = provider_key(cfg)
        if not key and not cfg.get("free"):
            raise ProviderUnavailable(f"no API key: set {cfg.get('api_key_env')} in {KEYS_PATH} (run setup_models.py)")
        self.client = OpenAI(api_key=key or "not-needed", base_url=cfg["base_url"],
                             timeout=cfg.get("timeout_seconds", 600), max_retries=1)
        self.no_temperature = False

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float], max_tokens: Optional[int]) -> str:
        import openai
        params = dict(model=self.model, messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if temperature is not None and not self.no_temperature:
            params["temperature"] = temperature
        try:
            try:
                resp = self.client.chat.completions.create(**params)
            except openai.BadRequestError as e:
                # Some reasoning models reject temperature: retry once without it.
                if "temperature" in params and "temperature" in str(e).lower():
                    self.no_temperature = True
                    params.pop("temperature")
                    resp = self.client.chat.completions.create(**params)
                else:
                    raise
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError) as e:
            raise ProviderUnavailable(str(e))
        except openai.APIStatusError as e:
            if e.status_code in (402, 429, 529) or _looks_unavailable(str(e)):
                raise ProviderUnavailable(str(e))
            raise
        except openai.APIConnectionError as e:
            raise ProviderUnavailable(f"cannot reach {self.cfg['base_url']}: {e}")
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            print(f"  [warning] {self.provider}:{self.model} hit max_tokens; the text may be cut off")
        return choice.message.content or ""


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
    return OpenAICompatibleBackend(provider, cfg, model)


# --------------------------------------------------------------------------
# Provider checks (used by setup_models.py and the startup check)
# --------------------------------------------------------------------------
def _http_json(url: str, key: Optional[str], timeout: int = 15):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key or 'not-needed'}",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_models(provider: str, cfg: dict) -> list:
    """Model ids this provider offers (raises on failure)."""
    if cfg.get("type") == "claude":
        if cfg.get("transport") == "anthropic_api":
            import anthropic
            key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
            client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
            return [m.id for m in client.models.list()]
        return list(CLAUDE_SUBSCRIPTION_MODELS)
    data = _http_json(cfg["base_url"].rstrip("/") + "/models", provider_key(cfg))
    items = data.get("data", data) if isinstance(data, dict) else data
    ids = [m.get("id") if isinstance(m, dict) else str(m) for m in items or []]
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
    try:
        n = len(list_models(provider, cfg))
        return "active", (f"{n} models" + (f" · balance {detail}" if detail else ""))
    except Exception as e:
        return "error", _short(e)


def test_model(spec: str, providers: dict) -> tuple:
    """One tiny real call. (ok, detail)."""
    try:
        reply = make_backend(spec, providers).call("Reply with the single word: OK", "ping", None, 16)
        return (True, "answers") if reply.strip() else (False, "empty reply")
    except Exception as e:
        return False, _short(e)


def _short(e) -> str:
    return re.sub(r"\s+", " ", str(e))[:160]


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
            return [self.config["single_model"], self.main]
        if self.mode == "hybrid":
            return [self.main] + sorted(set(self.routes.values()) - {self.main})
        return [self.main]

    def describe(self) -> str:
        if self.mode == "main":
            return f"all nodes on {self.main}"
        if self.mode == "single":
            return f"all nodes on {self.config['single_model']} (falls back to {self.main})"
        routed = ", ".join(f"{n} → {m}" for n, m in self.routes.items())
        return f"{self.main} writes; {routed}"

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
                self._log(f"⚠️ main model {spec} did not answer ({detail}). The run will stop at the first step if it stays down.")
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
        result = self._backend(self.main).call(system_prompt, user_prompt, temperature, max_tokens)
        self._count(node, self.main)
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
