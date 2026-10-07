"""Gemini through the local Gemini Canvas proxy (pranrichh/gemini-canvas-proxy)."""
from typing import Optional

from ..errors import AnswerCutOff, ProviderUnavailable, StepTimeout
from .openai_compat import OpenAICompatibleBackend


class CanvasProxyBackend(OpenAICompatibleBackend):
    """Gemini through the local Gemini Canvas proxy (pranrichh/gemini-canvas-proxy).
    Handles the proxy's limits, and only the proxy's:
      - it gives each request 60 s, then answers 504 (Gemini keeps writing, the
        answer is lost): retry the step `timeout_retries` times;
      - Gemini 3's hidden thinking counts against max_tokens and can cut answers
        (and JSON) short: raise max_tokens to at least `min_max_tokens`, re-send a cut
        answer once with double the limit (up to `max_output_cap`), and if it is still
        cut raise AnswerCutOff so the router can try another model;
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
        attempts = 1 + int(self.cfg.get("timeout_retries", 2))
        wait = self.cfg.get("retry_wait_seconds", 10)
        cap = int(self.cfg.get("max_output_cap", 65536) or 0)
        regrown = False
        attempt = 0
        while attempt < attempts:
            attempt += 1
            try:
                resp = self._create(params)
                text = self._text(resp)
                if getattr(resp.choices[0], "finish_reason", None) != "length":
                    return text
                limit = params.get("max_tokens") or 0
                if not regrown and limit and limit < cap:
                    regrown = True
                    params["max_tokens"] = min(limit * 2, cap)
                    print(f"  ↻ re-sending with max_tokens {params['max_tokens']} (Gemini's thinking used the budget)")
                    attempt -= 1        # not a time-out retry
                    continue
                raise AnswerCutOff(f"{self.provider}:{self.model} hit max_tokens ({limit}); the answer was cut off", text)
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
                timed_out = isinstance(e, openai.APITimeoutError)
                why = "timed out" if timed_out else "not reachable"
                if attempt < attempts:
                    print(f"  ⏳ Gemini Canvas proxy {why} ({e}); retrying ({attempt}/{attempts - 1}) in {wait}s...")
                    time.sleep(wait)
                    continue
                if timed_out:
                    raise StepTimeout(f"the Gemini Canvas proxy timed out {attempts} times ({e})")
                raise ProviderUnavailable(f"cannot reach the Gemini Canvas proxy at {self.cfg['base_url']}: {e}")
