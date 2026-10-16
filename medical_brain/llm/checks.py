"""Provider checks for setup_models.py and the startup check: status, model lists, test calls."""
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional

from .backends import make_backend
from .config import CLAUDE_SUBSCRIPTION_MODELS, OPENCODE_GO_DOC_MODELS, provider_headers, provider_key
from .errors import ProviderUnavailable, _short


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


_agy_models_cache: dict = {}

# The model ids `agy models` normally prints. Google's server occasionally stalls
# on the first request; when both tries fail we fall back to this list instead of
# calling the provider broken. Each fallback is remembered (cli path -> failure
# text) so provider_status can report a warning. It is NOT a cache: a later call
# retries the CLI for real.
AGY_KNOWN_MODELS = ["gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
                    "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium"]
AGY_MODELS_TIMEOUT = 30
_agy_models_fallback: dict = {}


def _run_agy_models(cli: str) -> tuple:
    """One `agy models` call. Returns (models, proc); [] when the output has no rows."""
    proc = subprocess.run([cli, "models"], capture_output=True, text=True, encoding="utf-8",
                          timeout=AGY_MODELS_TIMEOUT, stdin=subprocess.DEVNULL)
    models = []
    for line in (proc.stdout or "").splitlines():
        if "\t" not in line:            # skip "Fetching available models..." and blanks
            continue
        model = line.split("\t", 1)[0].strip()
        if model:
            models.append(model)
    return models, proc


def _list_agy_models(cfg: dict) -> list:
    """Antigravity's model ids: `agy models` prints '<id>\\t<label>' lines after a
    'Fetching available models...' notice. The list changes rarely, so it's cached.
    A single stall (timeout) or empty list is retried once; if it still fails the
    known list is returned as a fallback rather than raising."""
    from .backends.agy import agy_path
    cli = agy_path(cfg)
    if not cli:
        raise ProviderUnavailable("the `agy` command was not found (install Google Antigravity and log in)")
    if cli in _agy_models_cache:
        return list(_agy_models_cache[cli])
    failure = ""
    for _ in range(2):                          # one retry: a first request can hang
        try:
            models, proc = _run_agy_models(cli)
        except subprocess.TimeoutExpired:
            failure = f"`agy models` timed out after {AGY_MODELS_TIMEOUT}s"
            continue
        except Exception as e:
            # A real problem (not a stall): the CLI exists but can't be run.
            raise ProviderUnavailable(f"`agy models` failed: {_short(e)}")
        if models:
            _agy_models_cache[cli] = models
            _agy_models_fallback.pop(cli, None)
            return list(models)
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        failure = f"`agy models` returned no models: {detail}"
    _agy_models_fallback[cli] = failure        # not cached: the next call retries for real
    return list(AGY_KNOWN_MODELS)


def list_models(provider: str, cfg: dict) -> list:
    """Model ids this provider offers (raises on failure)."""
    if cfg.get("type") == "claude":
        if cfg.get("transport") == "anthropic_api":
            import anthropic
            key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
            client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
            return [m.id for m in client.models.list()]
        return list(CLAUDE_SUBSCRIPTION_MODELS)
    if cfg.get("type") == "agy":
        return _list_agy_models(cfg)
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


def _leak_model(models) -> str:
    """Which listed model the leak check uses: gemini-4-argon when offered, else the
    first model whose id contains "flash", else the first listed."""
    if "gemini-4-argon" in models:
        return "gemini-4-argon"
    flash = next((m for m in models if "flash" in str(m).lower()), None)
    return flash or models[0]


def provider_status(provider: str, cfg: dict) -> tuple:
    """(state, detail) with state 'active', 'off' (not set up), 'warning' or 'error'."""
    if cfg.get("type") == "claude":
        if cfg.get("transport") == "anthropic_api":
            if not (cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")):
                return "off", "no ANTHROPIC_API_KEY"
        elif not (cfg.get("cli_path") or shutil.which("claude")):
            return "off", "`claude` command not installed"
        ok, detail = test_model(f"{provider}:{CLAUDE_SUBSCRIPTION_MODELS[1]}", {provider: cfg})
        return ("active", "subscription answers") if ok else ("error", detail)
    if cfg.get("type") == "agy":
        from .backends.agy import agy_path
        cli = agy_path(cfg)
        if not cli:
            return "off", "`agy` command not installed"
        try:
            models = _list_agy_models(cfg)
        except Exception as e:
            return "error", _short(e)
        if cli in _agy_models_fallback:
            # `agy models` stalled twice: keep the provider usable with the known
            # list, but say so instead of failing the whole check.
            return "warning", (f"`agy models` didn't answer (Google's server was slow) — "
                               f"using the known model list: {len(models)} models")
        # A small, fast model for the ping; fall back to whatever is listed first.
        pick = next((m for m in models if m.startswith("gemini") and m.endswith("-flash-low")), None) or models[0]
        ok, detail = test_model(f"{provider}:{pick}", {provider: cfg})
        return ("active", f"{len(models)} models · subscription answers") if ok else ("error", detail)
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
        models = list_models(provider, cfg)
    except Exception as e:
        return "error", _short(e)
    summary = f"{len(models)} models" + (f" · balance {detail}" if detail else "")
    if cfg.get("leak_check") and models:
        # The provider's own answers are checked for memory between calls (a web proxy
        # may continue one conversation). A leaking provider is not usable: earlier
        # prompts would bleed into new answers.
        leaked, message = leak_check(provider, cfg, _leak_model(models))
        if leaked is True:
            return "error", ("❗ " + message + " — earlier prompts leak into new answers; "
                             "update the proxy (fresh conversations) before using it")
        if leaked is None:
            summary += f" ({message})"
        else:
            summary += " · no memory between calls"
    return "active", summary


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


def leak_check(provider: str, cfg: dict, model: str) -> tuple:
    """Does this provider remember earlier requests? Two calls to the same backend: the
    first asks it to keep a random code, the second asks for that code back. A proxy that
    starts a fresh conversation answers NONE; one that reuses a conversation echoes the
    code (earlier prompts would leak into later answers).

    Returns (leaked, message): True = it remembered, False = no memory between calls,
    None = the check could not run (its message explains why)."""
    code = "MB-" + secrets.token_hex(4)
    try:
        backend = make_backend(f"{provider}:{model}", {provider: cfg})
        backend.call("You are terse.",
                     f"Remember this code for later: {code}. Reply only: OK", 0, 50)
        answer = backend.call(
            "You are terse.",
            "What code did I ask you to remember earlier? Reply with the code only, "
            "or NONE if you have no earlier message.", 0, 50)
    except Exception as e:
        return None, f"leak check skipped: {_short(e)}"
    if code in (answer or ""):
        return True, "remembers earlier requests: a code sent in one call came back in the next"
    return False, "no memory between calls"
