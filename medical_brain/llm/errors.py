"""Errors the model layer raises, and how they're recognised."""
import re


class ProviderUnavailable(Exception):
    """The provider can't serve requests at all right now (limit, auth, billing, missing tool)."""


# Phrases that mean the provider can't serve anything for a while (quota, billing,
# login). Kept specific on purpose: a generic "limit" (e.g. "context limit exceeded")
# or a short-lived rate limit only fails the step, not the provider for the whole run.
_UNAVAILABLE_MARKERS = (
    "usage limit", "weekly limit", "daily limit", "monthly limit", "session limit",
    "hit your", "resets ", "out of extra usage", "insufficient balance", "insufficient_quota",
    "insufficient credit", "credit balance", "billing", "subscription", "not logged in",
    "please run /login", "invalid api key", "incorrect api key", "invalid x-api-key", "api key not valid",
    "authentication", "unauthorized", "quota", "error code: 401", "error code: 402", "error code: 403",
)


def _looks_unavailable(message: str) -> bool:
    m = (message or "").lower()
    return any(marker in m for marker in _UNAVAILABLE_MARKERS)


class StepFailed(RuntimeError):
    """This call failed in a way that may not repeat (rate limit, overload, server
    error): the router tries another model for this step, the provider stays in use."""


def api_failure(e, sdk, where: str = ""):
    """The exception to raise for an SDK error (openai or anthropic), or None to re-raise
    it as is. Only lasting problems (auth, billing, quota) take a provider out of the run."""
    message = str(e)
    if isinstance(e, (sdk.AuthenticationError, sdk.PermissionDeniedError)):
        return ProviderUnavailable(message)
    if isinstance(e, sdk.RateLimitError):
        return ProviderUnavailable(message) if _looks_unavailable(message) else \
            StepFailed(f"rate-limited ({_short(e)})")
    if isinstance(e, sdk.APITimeoutError):
        return StepTimeout(f"no answer from {where or 'the API'} in time ({_short(e)})")
    if isinstance(e, sdk.APIConnectionError):
        return ProviderUnavailable(f"cannot reach {where or 'the API'}: {e}")
    if isinstance(e, sdk.APIStatusError):
        if e.status_code == 402 or _looks_unavailable(message):
            return ProviderUnavailable(message)
        if e.status_code in (429, 500, 502, 503, 504, 529):
            return StepFailed(f"server error {e.status_code} ({_short(e)})")
    return None


class _LimitTooHigh(Exception):
    """The model refused the requested output limit."""


def _limit_refused(err) -> bool:
    m = str(err).lower()
    return ("max_tokens" in m or "max_output_tokens" in m or "maximum" in m) and "temperature" not in m


def _thinking_refused(err) -> bool:
    m = str(err).lower()
    return any(k in m for k in ("thinking", "enable_thinking", "reasoning"))


class AnswerCutOff(RuntimeError):
    """The answer still stopped at max_tokens. Carries the cut text so the router
    can use it as a last resort when no other model can answer."""

    def __init__(self, message: str, text: str):
        super().__init__(message)
        self.text = text


class StepTimeout(RuntimeError):
    """A call took longer than step_timeout_seconds and was abandoned."""


def _short(e) -> str:
    text = re.sub(r"\s+", " ", str(e))[:300]
    if "Invalid port" in text or "InvalidURL" in type(e).__name__:
        text += " (your NO_PROXY setting has an entry Python can't read; run: env | grep -i proxy)"
    return text
