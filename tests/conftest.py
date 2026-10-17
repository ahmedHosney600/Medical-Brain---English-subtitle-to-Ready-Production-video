"""Tests never reach a real model: the default router is replaced by one that fails
loudly. A test that needs answers sets its own router or patches call_llm. (Two drawing
tests once passed by quietly calling the configured live models.)"""
import pytest

from medical_brain import llm


class _NoRealModels:
    def call(self, *args, **kwargs):
        raise RuntimeError("a test tried to call a real model; patch call_llm or set a router")



@pytest.fixture(autouse=True)
def _no_real_models():
    saved = llm._router
    if saved is None:
        llm._router = _NoRealModels()
    yield
    if isinstance(llm._router, _NoRealModels):
        llm._router = saved
