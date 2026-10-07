"""Output-length handling shared by the API backends."""
import time
from typing import Optional

from ..errors import AnswerCutOff, _LimitTooHigh
from ..progress import _step_timeout


class _OutputLimits:
    """Output-length handling shared by the API backends (not the Gemini proxy,
    which has its own):
      - reasoning models (Qwen, DeepSeek, Kimi...) think before answering and
        those tokens count against max_tokens, so the step's limit (tuned for
        Gemini) is raised to at least `min_max_tokens` (default 16000). It is a
        ceiling, not a target: you only pay for tokens actually produced;
      - an answer that still stops at the limit is re-sent once with double the
        limit, up to `max_output_cap` (default 32000);
      - a model that refuses a limit that high is retried with 8192."""

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float], max_tokens: Optional[int]) -> str:
        floor = int(self.cfg.get("min_max_tokens", 16000) or 0)
        cap = int(self.cfg.get("max_output_cap", 32000) or 0)
        limit = max(max_tokens or 0, floor) or None
        started = time.monotonic()
        try:
            text, cut = self._once(system_prompt, user_prompt, temperature, limit)
        except _LimitTooHigh:
            limit = 8192
            text, cut = self._once(system_prompt, user_prompt, temperature, limit)
        timeout = _step_timeout(self.cfg)
        slow = bool(timeout) and time.monotonic() - started > timeout / 2
        if cut and limit and limit < cap and not slow:
            limit = min(limit * 2, cap)
            try:
                text, cut = self._once(system_prompt, user_prompt, temperature, limit)
            except _LimitTooHigh:
                pass  # keep the first (cut) answer
        if cut:
            # A cut answer is usually broken (half a JSON object, half a script):
            # let the router try the main/backup model instead.
            raise AnswerCutOff(f"{self.provider}:{self.model} hit max_tokens ({limit}); the answer was cut off", text)
        return text
