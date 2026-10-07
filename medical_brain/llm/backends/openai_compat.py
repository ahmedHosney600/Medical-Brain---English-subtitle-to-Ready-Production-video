"""OpenAI-compatible chat API: DeepSeek, OpenCode Go (GLM, Kimi...), any /chat/completions service."""
import re
from typing import Optional

from ..config import KEYS_PATH, provider_headers, provider_key, thinking_mode
from ..errors import ProviderUnavailable, _LimitTooHigh, _limit_refused, _looks_unavailable, _thinking_refused
from ..progress import _Progress, _client_timeout, _step_timeout, current_node
from .base import _OutputLimits


# A step that wants JSON says so in its prompt ("Output ONLY the JSON object").
_WANTS_JSON = re.compile(r"(only\s+(valid\s+)?json|only\s+the\s+json|only\s+a\s+json|output\s+json)", re.IGNORECASE)


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
        # JSON mode wherever the API offers it (switched off for the run if this model refuses it).
        if (self.cfg.get("json_mode", True) and not getattr(self, "no_json_mode", False)
                and _WANTS_JSON.search(system_prompt + "\n" + user_prompt[-600:])):
            params["response_format"] = {"type": "json_object"}
        mode = None if getattr(self, "no_thinking", False) else thinking_mode(self.cfg, self.model)
        if mode == "off":
            params["extra_body"] = {"enable_thinking": False}      # Qwen-style switch
        elif mode == "low":
            params["reasoning_effort"] = "low"
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
            # A model without JSON mode: retry without it, and don't ask again this run.
            if "response_format" in params and re.search(r"response_format|json_object|json mode|json_schema",
                                                         str(e), re.IGNORECASE):
                self.no_json_mode = True
                params.pop("response_format")
                return self._create(params)
            # A model that doesn't accept the thinking switch: retry without it.
            if ("extra_body" in params or "reasoning_effort" in params) and _thinking_refused(e):
                self.no_thinking = True
                params.pop("extra_body", None)
                params.pop("reasoning_effort", None)
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
