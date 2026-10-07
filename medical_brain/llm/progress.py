"""Which step is running (track_node), the live progress line, and the per-call time-out."""
import contextvars
import functools
import sys
import time
from typing import Optional

from .errors import StepTimeout


current_node = contextvars.ContextVar("current_node", default=None)


_node_state = contextvars.ContextVar("_node_state", default=None)


def track_node(name: str, fn):
    """Wraps a graph node so LLM calls made inside it know which node they belong to."""
    @functools.wraps(fn)
    def wrapper(state):
        token = current_node.set(name)
        state_token = _node_state.set({"announced": False})
        try:
            return fn(state)
        finally:
            current_node.reset(token)
            _node_state.reset(state_token)
    return wrapper


class _Progress:
    """One live status line while a model streams: elapsed time, thinking or
    writing, and roughly how many tokens. Also enforces the step time-out."""

    def __init__(self, label: str, timeout: Optional[float]):
        self.label, self.timeout = label, timeout
        self.start = self.last = time.monotonic()
        self.thinking = self.writing = 0
        self.shown = False

    def tick(self, thinking: int = 0, writing: int = 0):
        self.thinking += thinking
        self.writing += writing
        now = time.monotonic()
        elapsed = now - self.start
        if self.timeout and elapsed > self.timeout:
            self.done()
            raise StepTimeout(f"{self.label} took more than {int(self.timeout)} s "
                              f"(step_timeout_seconds) and was stopped")
        if now - self.last >= 3:
            self.last = now
            what = f"writing ~{self.writing:,} tokens" if self.writing else \
                (f"thinking (~{self.thinking:,} tokens)…" if self.thinking else "waiting for the first words…")
            sys.stdout.write(f"\r  ⏳ {self.label} · {int(elapsed) // 60}m {int(elapsed) % 60:02d}s · {what}   ")
            sys.stdout.flush()
            self.shown = True

    def elapsed(self) -> float:
        return time.monotonic() - self.start

    def done(self):
        if self.shown:
            sys.stdout.write("\r" + " " * 90 + "\r")
            sys.stdout.flush()
            self.shown = False


def _step_timeout(cfg: dict) -> Optional[float]:
    value = cfg.get("step_timeout_seconds", 600)
    return float(value) if value else None


def _client_timeout(cfg: dict) -> float:
    """HTTP time-out: also covers a server that goes completely silent mid-stream."""
    limits = [float(cfg.get("timeout_seconds", 900))]
    if _step_timeout(cfg):
        limits.append(_step_timeout(cfg))
    return min(limits)
