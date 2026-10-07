"""Provider settings: defaults, llm_variables.json, API keys (llm_keys.env), model specs."""
import fnmatch
import json
import os
import uuid
from typing import Optional

from ..paths import PROJECT_ROOT


CONFIG_PATH = os.path.join(PROJECT_ROOT, "llm_variables.json")


KEYS_PATH = os.path.join(PROJECT_ROOT, "llm_keys.env")


MODES = ("main", "single", "hybrid")


MODE_ALIASES = {"gemini": "main", "claude": "single", "auto": "hybrid"}


DEFAULT_PROVIDERS = {
    # Only this provider gets the Canvas-proxy handling (retries on time-outs,
    # JSON mode, room for thinking tokens). Every other API is called normally.
    "gemini": {"type": "canvas_proxy", "label": "Gemini Canvas proxy", "base_url": "http://localhost:8765/v1",
               "api_key_env": "OPENAI_COMPATIBLE_API_KEY", "free": True,
               "timeout_retries": 2, "retry_wait_seconds": 10, "min_max_tokens": 16000, "json_mode": True},
    "claude": {"type": "claude", "label": "Claude", "transport": "claude_code", "effort": "high",
               "efforts": {}, "timeout_seconds": 900},
    "deepseek": {"type": "openai", "label": "DeepSeek", "base_url": "https://api.deepseek.com",
                 "api_key_env": "DEEPSEEK_API_KEY", "balance_path": "/user/balance"},
    # OpenCode Go serves models through three different APIs (see opencode_endpoint).
    "opencode": {"type": "opencode_go", "label": "OpenCode Go", "base_url": "https://opencode.ai/zen/go/v1",
                 "api_key_env": "OPENCODE_API_KEY", "endpoints": {},
                 # Qwen 3.8 Max spent its whole 16k budget thinking on step 1; write without it.
                 "thinking": {"qwen*": "off"}},
}


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


def thinking_mode(cfg: dict, model: str) -> Optional[str]:
    """'off' / 'low' / None from the provider's "thinking" setting,
    e.g. {"qwen*": "off", "deepseek-v4-pro": "low"} (wildcards allowed)."""
    for pattern, mode in (cfg.get("thinking") or {}).items():
        if fnmatch.fnmatch(model.lower(), pattern.lower()):
            return None if str(mode).lower() in ("", "default", "on") else str(mode).lower()
    return None


DEFAULT_MAIN = "gemini:gemini-3-flash-preview"


DEFAULT_BACKUP = ""


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


CLAUDE_SUBSCRIPTION_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"]


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


def save_key(env_name: str, value: str, path: str = KEYS_PATH):
    """Adds or replaces NAME=value in llm_keys.env and keeps that file out of git."""
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if not l.startswith(env_name + "=")]
    value = value.strip()
    lines.append(f"{env_name}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.environ[env_name] = value
    gitignore = os.path.join(os.path.dirname(path), ".gitignore")
    name = os.path.basename(path)
    existing = open(gitignore, encoding="utf-8").read().splitlines() if os.path.exists(gitignore) else []
    if name not in existing:
        with open(gitignore, "a", encoding="utf-8") as f:
            f.write(name + "\n")


def save_llm_config(config: dict, path: str = CONFIG_PATH, keys_path: str = KEYS_PATH):
    """Writes the new format, keeping any fields already in the file that we don't manage.
    API keys never go into this (tracked) file: an inline "api_key" is moved to
    llm_keys.env under the provider's api_key_env."""
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
        key = diff.pop("api_key", None)
        env = cfg.get("api_key_env")
        if key and env:
            if os.environ.get(env) != str(key).strip():
                save_key(env, str(key), keys_path)
        elif key:
            print(f"⚠️  Not saving the {name} API key in {os.path.basename(path)}: "
                  f"set \"api_key_env\" for it and put the key in {os.path.basename(keys_path)}.")
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
