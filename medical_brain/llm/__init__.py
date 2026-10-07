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
from .backends.openai_compat import _WANTS_JSON
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


def call_llm_json(system_prompt: str, user_prompt: str, temperature: Optional[float] = None,
                  max_tokens: Optional[int] = None, required=()) -> tuple:
    """call_llm for steps that answer in JSON, the same for every model.
    `required`: fields the step's gate decides on — each a name or a tuple of
    alternative names. If the answer can't be read, or misses a required field, the
    same model gets ONE repair request with its own answer attached. Returns
    (data, raw_text); raises UnreadableAnswer (with .raw) if it is still unreadable."""
    from ..utils.llm_json import UnreadableAnswer, has, parse_llm_json

    def missing(data):
        return [names if isinstance(names, str) else names[0]
                for names in required
                if not has(data, *((names,) if isinstance(names, str) else names))]

    raw = call_llm(system_prompt, user_prompt, temperature, max_tokens)
    try:
        data = parse_llm_json(raw)
        problem = f"it is missing the fields {missing(data)}" if missing(data) else ""
    except ValueError as e:
        data, problem = None, f"it is not valid JSON ({e})"
    if not problem:
        return data, raw

    print(f"  🔧 {current_node.get() or 'step'}: answer unreadable — {problem}; asking the model to re-send it as JSON")
    repair = (f"Your previous answer (below, between the markers) could not be used: {problem}.\n"
              "Re-send the SAME content as ONE valid JSON object and nothing else: no markdown fence, "
              "no text before or after it, every field of the output format in the instructions above, "
              "and every double quote inside a string value escaped as \\\".\n\n"
              f"<<<PREVIOUS ANSWER\n{raw}\nPREVIOUS ANSWER>>>")
    raw2 = call_llm(system_prompt, repair, temperature, max_tokens)
    try:
        data2 = parse_llm_json(raw2)
    except ValueError as e:
        if data is not None:
            return data, raw         # the first answer was JSON, just incomplete
        raise UnreadableAnswer(str(e), raw2 or raw)
    if data is not None and len(missing(data2)) > len(missing(data)):
        return data, raw
    return data2, raw2


__all__ = [name for name in dir() if not name.startswith("__") and name not in ("Optional",)]
