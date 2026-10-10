
# The router retries a failing main model after real pauses (10 s, 30 s); tests that
# simulate failures must not sleep through them.
from medical_brain.llm.router import LLMRouter as _Router  # noqa: E402
_Router.MAIN_RETRY_WAITS = (0, 0)
