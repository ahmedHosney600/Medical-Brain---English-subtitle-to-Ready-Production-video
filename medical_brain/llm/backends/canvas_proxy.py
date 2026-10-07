"""Gemini through the local Gemini Canvas proxy (pranrichh/gemini-canvas-proxy)."""
import re
from typing import Optional

from ..errors import ProviderUnavailable
from .openai_compat import OpenAICompatibleBackend


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
                raise ProviderUnavailable(
                    f"Gemini Canvas proxy token missing or wrong ({e.status_code}). Fix: put "
                    f"{self.cfg.get('api_key_env') or 'the token'}=<token> in llm_keys.env (the token is in "
                    f"<gemini-canvas-proxy folder>/native_host/.proxy_token), or run python3 setup_models.py")
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
