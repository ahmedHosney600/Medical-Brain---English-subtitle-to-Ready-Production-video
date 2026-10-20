import unittest
from unittest import mock
import httpx, openai
from medical_brain.llm.backends.openai_compat import OpenAICompatibleBackend
from medical_brain.llm.errors import ProviderUnavailable

class ConnectionRetryTest(unittest.TestCase):
    def test_a_dropped_connection_is_retried_before_the_provider_is_dropped(self):
        b = OpenAICompatibleBackend.__new__(OpenAICompatibleBackend)
        b.provider, b.cfg = "deepseek", {"base_url": "https://x"}
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise openai.APIConnectionError(request=httpx.Request("POST", "https://x"))
            return "ok"
        with mock.patch("time.sleep"), mock.patch("builtins.print"):
            self.assertEqual(b._map_errors(fn), "ok")
        calls.clear()
        def always():
            calls.append(1); raise openai.APIConnectionError(request=httpx.Request("POST", "https://x"))
        with mock.patch("time.sleep"), mock.patch("builtins.print"):
            self.assertRaises(ProviderUnavailable, b._map_errors, always)
        self.assertEqual(len(calls), 3)

