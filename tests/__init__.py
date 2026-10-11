
# The router retries a failing main model after real pauses (10 s, 30 s); tests that
# simulate failures must not sleep through them.
from medical_brain.llm.router import LLMRouter as _Router  # noqa: E402
_Router.MAIN_RETRY_WAITS = (0, 0)

# Chapter-mode tests use small sample blueprints; the real minimum body length
# (CHAPTER_MODE_MIN_WORDS) is tested on its own in test_chapters.MinWordsTest.
from medical_brain.nodes import chapters as _chapters  # noqa: E402
_chapters.CHAPTER_MODE_MIN_WORDS = 0
