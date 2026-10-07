"""Errors the model layer raises, and how they're recognised."""
import re


class ProviderUnavailable(Exception):
    """The provider can't serve requests at all right now (limit, auth, billing, missing tool)."""


_UNAVAILABLE_MARKERS = (
    "usage limit", "limit reached", "weekly limit", "daily limit", "session limit", "hit your",
    "resets ", "out of extra usage", "rate limit", "rate_limit", "overloaded", "insufficient",
    "credit balance", "balance", "billing", "subscription", "not logged in", "please run /login",
    "invalid api key", "api key", "authentication", "unauthorized", "permission", "forbidden",
    "401", "402", "403", "429", "529", "quota",
)


def _looks_unavailable(message: str) -> bool:
    m = (message or "").lower()
    return any(marker in m for marker in _UNAVAILABLE_MARKERS) or bool(re.search(r"\blimit\b", m))


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
