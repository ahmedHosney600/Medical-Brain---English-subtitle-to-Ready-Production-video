"""Provider checks for setup_models.py and the startup check: status, model lists, test calls."""
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from typing import Optional

from .backends import make_backend
from .config import CLAUDE_SUBSCRIPTION_MODELS, OPENCODE_GO_DOC_MODELS, provider_headers, provider_key
from .errors import _short


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
