"""The model backends, and make_backend() which picks one for a 'provider:model' spec."""
from ..config import CONFIG_PATH, opencode_endpoint, parse_spec
from ..errors import ProviderUnavailable
from .anthropic_compat import AnthropicCompatibleBackend
from .canvas_proxy import CanvasProxyBackend
from .claude import AnthropicAPIBackend, ClaudeCodeBackend
from .openai_compat import OpenAICompatibleBackend
from .responses import OpenAIResponsesBackend


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
