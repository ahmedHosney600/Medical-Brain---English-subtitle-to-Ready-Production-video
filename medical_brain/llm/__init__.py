"""The model layer: one call_llm() for every step, any mix of models behind it.

A model is written "provider:model" (gemini:..., claude:..., deepseek:..., opencode:...).
The router picks the model for each step (main / single / hybrid modes) and falls
back to the main or backup model when a call fails. Settings live in
llm_variables.json; API keys in llm_keys.env (see setup_models.py).

  config.py     provider defaults, loading/saving settings, keys
  backends/     one module per API type (chat, responses, messages, Canvas proxy, Claude)
  router.py     LLMRouter
  checks.py     provider status, model lists, test calls
  progress.py   which step is running, live progress line, time-outs
  errors.py     the errors and how they're recognised
"""
from typing import Optional

from .backends import (AnthropicAPIBackend, AnthropicCompatibleBackend, CanvasProxyBackend,
                       ClaudeCodeBackend, OpenAICompatibleBackend, OpenAIResponsesBackend, make_backend)
from .backends.base import _OutputLimits
from .backends.canvas_proxy import _WANTS_JSON
from .checks import HTTPProblem, _http_json, list_models, provider_status, test_model
from .config import (CLAUDE_SUBSCRIPTION_MODELS, CONFIG_PATH, DEFAULT_BACKUP, DEFAULT_HYBRID_ROUTES,
                     DEFAULT_MAIN, DEFAULT_PROVIDERS, DEFAULT_SINGLE, KEYS_PATH, MODE_ALIASES, MODES,
                     OPENCODE_GO_DOC_MODELS, OPENCODE_SESSION_ID, _sanitize_no_proxy, load_keys,
                     load_llm_config, opencode_endpoint, parse_spec, provider_headers, provider_key,
                     save_key, save_llm_config, thinking_mode)
from .errors import (_UNAVAILABLE_MARKERS, AnswerCutOff, ProviderUnavailable, StepTimeout, _limit_refused,
                     _LimitTooHigh, _looks_unavailable, _short, _thinking_refused)
from .progress import _client_timeout, _node_state, _Progress, _step_timeout, current_node, track_node
from .router import LLMRouter

_router = None


def set_router(router) -> None:
    """Use this router (or any object with .call(system, user, temperature, max_tokens)) for call_llm."""
    global _router
    _router = router


def get_router():
    """The active router; a default one from llm_variables.json if none was set."""
    global _router
    if _router is None:
        _router = LLMRouter(load_llm_config())
    return _router


def call_llm(system_prompt: str, user_prompt: str, temperature: Optional[float] = None,
             max_tokens: Optional[int] = None) -> str:
    return get_router().call(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)


__all__ = [name for name in dir() if not name.startswith("__") and name not in ("Optional",)]
