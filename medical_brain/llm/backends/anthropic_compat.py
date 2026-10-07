"""Anthropic-style /messages API of a third-party service (OpenCode Go's Qwen and MiniMax)."""
import re

from ..config import KEYS_PATH, provider_headers, provider_key, thinking_mode
from ..errors import ProviderUnavailable, _LimitTooHigh, _limit_refused, _looks_unavailable, _thinking_refused
from ..progress import _Progress, _client_timeout, _step_timeout
from .base import _OutputLimits


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
        if thinking_mode(self.cfg, self.model) == "off" and not getattr(self, "no_thinking", False):
            params["thinking"] = {"type": "disabled"}

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
                elif "thinking" in params and _thinking_refused(e):
                    self.no_thinking = True
                    params.pop("thinking")
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
