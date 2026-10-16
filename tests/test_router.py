"""Model layer against local fake servers (no internet, no real keys):
streaming on all three API types, step time-out → backup, cut-off answer → backup,
Qwen thinking switched off, OpenCode session header, provider fallbacks."""
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from medical_brain import llm  # noqa: E402
from medical_brain.llm import checks  # noqa: E402
from medical_brain.llm.errors import StepFailed  # noqa: E402

FAKES = os.path.join(os.path.dirname(__file__), "fakes")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stop(proc):
    proc.kill()
    proc.wait()


def start(script, *args, env=None):
    proc = subprocess.Popen([sys.executable, os.path.join(FAKES, script), *map(str, args)],
                            env={**os.environ, **(env or {})},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    return proc


def config(main, backup="", routes=None, **providers):
    c = llm.load_llm_config()
    c["providers"].update(providers)
    c.update(main_model=main, backup_model=backup, hybrid_routes=routes or {})
    return c


def run_step(router, node="test_step", system="Write.", max_tokens=5000):
    return llm.track_node(node, lambda state: router.call(system, "u", 0.3, max_tokens))({})


class StreamingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fast_port, cls.slow_port = free_port(), free_port()
        cls.procs = [start("fake_stream.py", cls.fast_port, env={"DELAY": "0.01", "THINK": "2", "WORDS": "3"}),
                     start("fake_stream.py", cls.slow_port, env={"DELAY": "1.0", "THINK": "4", "WORDS": "4"})]

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            stop(p)

    def provider(self, port, **extra):
        return dict(llm.DEFAULT_PROVIDERS["opencode"], api_key="k",
                    base_url=f"http://127.0.0.1:{port}/zen/go/v1", **extra)

    def test_all_three_apis_stream(self):
        c = config("opencode:glm-5.3", opencode=self.provider(self.fast_port))
        for model in ("glm-5.3", "grok-4.7", "qwen3.8-max"):     # chat, responses, messages
            text = llm.make_backend(f"opencode:{model}", c["providers"]).call("s", "u", 0.3, 100)
            self.assertEqual(text.strip(), "w0 w1 w2", model)

    def test_json_mode_for_any_openai_compatible_api(self):
        c = config("x:any-model", x={"type": "openai", "base_url": f"http://127.0.0.1:{self.fast_port}/v1", "api_key": "k"})
        backend = llm.make_backend("x:any-model", c["providers"])
        self.assertTrue(backend.call("Output ONLY the JSON object.", "u", 0.3, 100).startswith("JSON"))
        self.assertFalse(backend.call("Write a script.", "u", 0.3, 100).startswith("JSON"))

    def test_json_mode_dropped_when_the_model_refuses_it(self):
        port = free_port()
        proc = start("fake_stream.py", port, env={"DELAY": "0.01", "THINK": "0", "WORDS": "2", "REJECT_JSON": "1"})
        self.addCleanup(stop, proc)
        c = config("x:any-model", x={"type": "openai", "base_url": f"http://127.0.0.1:{port}/v1", "api_key": "k"})
        backend = llm.make_backend("x:any-model", c["providers"])
        self.assertEqual(backend.call("Output ONLY the JSON object.", "u", 0.3, 100).strip(), "w0 w1")
        self.assertTrue(backend.no_json_mode)

    def test_slow_call_times_out_and_backup_answers(self):
        c = config("opencode:qwen3.8-max", backup="fast:glm-5.3",
                   opencode=self.provider(self.slow_port, step_timeout_seconds=2),
                   fast={"type": "openai", "base_url": f"http://127.0.0.1:{self.fast_port}/v1", "api_key": "k"})
        router = llm.LLMRouter(c, provider="main")
        self.assertEqual(run_step(router).strip(), "w0 w1 w2")
        self.assertEqual(router.report()["calls"], {"fast:glm-5.3": 1})


class OpenCodeMessagesTest(unittest.TestCase):
    def start_fake(self, **env):
        port = free_port()
        log = tempfile.mktemp(suffix=".log")
        proc = start("fake_opencode.py", "ua", port, log, env=env)
        self.addCleanup(stop, proc)
        provider = dict(llm.DEFAULT_PROVIDERS["opencode"], api_key="ockey",
                        base_url=f"http://127.0.0.1:{port}/zen/go/v1")
        return provider, log

    def read_log(self, log):
        if not os.path.exists(log):
            return ""
        with open(log) as f:
            return f.read()

    def test_qwen_thinking_off_and_session_header(self):
        provider, log = self.start_fake()
        c = config("opencode:qwen3.8-max", opencode=provider)
        self.assertEqual(run_step(llm.LLMRouter(c, provider="main")), "MSG:qwen3.8-max")
        self.assertIn("thinking={'type': 'disabled'}", self.read_log(log))
        self.assertIn("session=medical-brain-", self.read_log(log))

    def test_thinking_rejected_is_retried_without(self):
        provider, log = self.start_fake(REJECT_THINKING="1")
        c = config("opencode:qwen3.8-max", opencode=provider)
        self.assertEqual(run_step(llm.LLMRouter(c, provider="main")), "MSG:qwen3.8-max")
        self.assertIn("thinking=None", self.read_log(log))

    def test_cut_off_answer_goes_to_backup(self):
        provider, _ = self.start_fake(TRUNC_BELOW="99999")
        fast_port = free_port()
        fast = start("fake_stream.py", fast_port, env={"DELAY": "0.01", "THINK": "0", "WORDS": "2"})
        self.addCleanup(stop, fast)
        c = config("opencode:qwen3.8-max", backup="fast:glm-5.3", opencode=provider,
                   fast={"type": "openai", "base_url": f"http://127.0.0.1:{fast_port}/v1", "api_key": "k"})
        self.assertEqual(run_step(llm.LLMRouter(c, provider="main")).strip(), "w0 w1")

    def test_limits_raised_for_reasoning_models(self):
        provider, log = self.start_fake()
        c = config("opencode:qwen3.8-max", opencode=provider)
        run_step(llm.LLMRouter(c, provider="main"), max_tokens=300)
        self.assertIn("limit=16000", self.read_log(log))


class RouterLogicTest(unittest.TestCase):
    def test_hybrid_routes_and_unknown_provider_fallback(self):
        calls = []

        class Fake:
            def __init__(self, spec, fail=False):
                self.spec, self.fail = spec, fail

            def call(self, *a):
                calls.append(self.spec)
                if self.fail:
                    raise llm.ProviderUnavailable("You've hit your weekly limit · resets Oct 8")
                return self.spec

        c = config("w:writer", routes={"judge_step": "j:judge"})
        router = llm.LLMRouter(c, provider="hybrid")
        router._backends = {"w:writer": Fake("w:writer"), "j:judge": Fake("j:judge", fail=True)}
        self.assertEqual(run_step(router, "writer_step"), "w:writer")
        self.assertEqual(run_step(router, "judge_step"), "w:writer")       # limit → main model
        self.assertIn("j", router.report()["unavailable"])


class FreeGeminiProviderTest(unittest.TestCase):
    """The local free-gemini-api proxy is a normal OpenAI-compatible provider."""

    def test_defaults(self):
        cfg = llm.DEFAULT_PROVIDERS["freegemini"]
        self.assertEqual(cfg["type"], "openai")
        self.assertEqual(cfg["base_url"], "http://127.0.0.1:8001/v1")
        self.assertFalse(cfg["stream"])      # its streaming endpoint returns an empty stream
        # Every request must start a fresh Gemini conversation, and the proxy's
        # memory between calls is checked at startup.
        self.assertEqual(cfg["headers"], {"X-Fresh-Conversation": "1"})
        self.assertIs(cfg["leak_check"], True)
        self.assertEqual(llm.provider_headers(cfg), {"X-Fresh-Conversation": "1"})

    def test_geminiweb_is_leak_checked_too(self):
        self.assertIs(llm.DEFAULT_PROVIDERS["geminiweb"]["leak_check"], True)

    def test_a_partial_override_keeps_default_keys_like_headers(self):
        # A user's llm_variables.json may list only the provider's own settings; the
        # default "headers" (and "leak_check") must survive the merge.
        import json
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"provider": "main", "main_model": "freegemini:gemini-4-argon",
                       "providers": {"freegemini": {"base_url": "http://127.0.0.1:9001/v1"}}}, f)
            path = f.name
        self.addCleanup(os.remove, path)
        cfg = llm.load_llm_config(path)["providers"]["freegemini"]
        self.assertEqual(cfg["base_url"], "http://127.0.0.1:9001/v1")   # the override wins
        self.assertEqual(cfg["headers"], {"X-Fresh-Conversation": "1"})  # the default survives
        self.assertIs(cfg["leak_check"], True)

    def test_model_spec_resolves_to_the_provider(self):
        provider, model = llm.parse_spec("freegemini:gemini-3.8-flash")
        self.assertEqual(provider, "freegemini")
        self.assertEqual(model, "gemini-3.8-flash")
        self.assertIn(provider, llm.DEFAULT_PROVIDERS)


class NonStreamingProviderTest(unittest.TestCase):
    """free-gemini-api returns an empty event stream when "stream": true, so its
    provider is called without streaming; an empty non-streaming answer is a failure."""

    def _backend(self, text="ANSWER", finish_reason="stop", **over):
        cfg = dict(llm.DEFAULT_PROVIDERS["freegemini"], api_key="k", **over)
        backend = llm.OpenAICompatibleBackend("freegemini", cfg, "gemini-3.8-flash")
        calls = []

        class _Message:
            def __init__(self, content):
                self.content = content

        class _Choice:
            def __init__(self, content, finish_reason):
                self.message = _Message(content)
                self.finish_reason = finish_reason

        class _Response:
            def __init__(self, content, finish_reason):
                self.choices = [_Choice(content, finish_reason)]

        def fake_create(params):
            calls.append(params)
            return _Response(text, finish_reason)

        backend._create = fake_create
        backend._calls = calls
        return backend

    def test_stream_false_does_a_plain_call_without_stream(self):
        backend = self._backend(text="the answer")
        self.assertEqual(backend.call("s", "u", 0.2, 100), "the answer")
        self.assertEqual(len(backend._calls), 1)
        self.assertNotIn("stream", backend._calls[0])

    def test_empty_answer_raises_step_failed(self):
        backend = self._backend(text="")
        with self.assertRaises(StepFailed) as caught:
            backend.call("s", "u", 0.2, 100)
        self.assertIn("returned an empty answer", str(caught.exception))

    def test_cfg_without_stream_key_still_streams(self):
        cfg = {"type": "openai", "base_url": "http://127.0.0.1:1/v1", "api_key": "k"}
        backend = llm.OpenAICompatibleBackend("x", cfg, "m")
        seen = {}

        class _Delta:
            def __init__(self, content):
                self.content = content
                self.reasoning_content = None
                self.reasoning = None

        class _Choice:
            def __init__(self, content, finish_reason=None):
                self.delta = _Delta(content)
                self.finish_reason = finish_reason

        class _Chunk:
            def __init__(self, content, finish_reason=None):
                self.choices = [_Choice(content, finish_reason)]

        def fake_create(params):
            seen.update(params)
            return [_Chunk("hello"), _Chunk("", "stop")]

        backend._create = fake_create
        self.assertEqual(backend.call("s", "u", 0.2, 100), "hello")
        self.assertTrue(seen.get("stream"))


class CannedReplyTest(unittest.TestCase):
    """A short canned non-answer from Gemini's web app fails the call so the router
    retries and falls back."""

    CANNED = ("I'm having a hard time fulfilling your request. "
              "Can I help you with something else instead?")

    def backend(self, text, **over):
        # geminiweb still streams by default (freegemini is called without streaming;
        # its non-streaming path is covered by NonStreamingProviderTest).
        cfg = dict(llm.DEFAULT_PROVIDERS["geminiweb"], api_key="k", **over)
        backend = llm.OpenAICompatibleBackend("geminiweb", cfg, "gemini-3.8-flash")

        class _Delta:
            def __init__(self, content):
                self.content = content
                self.reasoning_content = None
                self.reasoning = None

        class _Choice:
            def __init__(self, content, finish_reason=None):
                self.delta = _Delta(content)
                self.finish_reason = finish_reason

        class _Chunk:
            def __init__(self, content, finish_reason=None):
                self.choices = [_Choice(content, finish_reason)]

        chunks = [_Chunk(text), _Chunk("", "stop")]
        backend._create = lambda params: chunks
        return backend

    def test_canned_reply_raises_step_failed(self):
        backend = self.backend(self.CANNED)
        with self.assertRaises(StepFailed) as caught:
            backend.call("s", "u", 0.2, 100)
        self.assertIn("canned non-answer", str(caught.exception))

    def test_canned_reply_without_the_flag_is_returned(self):
        backend = self.backend(self.CANNED, canned_replies=None)
        self.assertEqual(backend.call("s", "u", 0.2, 100).strip(), self.CANNED)

    def test_long_answer_containing_the_phrase_is_not_flagged(self):
        long_text = self.CANNED + " " + ("word " * 300)
        backend = self.backend(long_text)
        self.assertEqual(backend.call("s", "u", 0.2, 100).strip(), long_text.strip())

    def test_curly_apostrophe_is_normalised(self):
        canned = "I\u2019m having a hard time fulfilling your request."
        backend = self.backend(canned)
        with self.assertRaises(StepFailed):
            backend.call("s", "u", 0.2, 100)

    def test_both_web_providers_have_the_phrases(self):
        for name in ("freegemini", "geminiweb"):
            phrases = llm.DEFAULT_PROVIDERS[name]["canned_replies"]
            self.assertIn("i'm having a hard time fulfilling your request", phrases)
            self.assertIn("i can't help with that right now", phrases)


class _LeakBackend:
    """A backend that answers the ping, then either echoes the remembered code
    (leaks) or answers NONE. `error=True` makes every call raise."""

    def __init__(self, leak=False, error=False):
        self.leak = leak
        self.error = error
        self.code = ""

    def call(self, system, user, temperature=None, max_tokens=None):
        if "Remember this code" in user:
            if self.error:
                raise RuntimeError("backend exploded")
            match = re.search(r"MB-[0-9a-f]+", user)
            self.code = match.group(0) if match else ""
            return "OK"
        if "What code did I ask" in user:
            return self.code if self.leak else "NONE"
        return "OK"


class LeakCheckTest(unittest.TestCase):
    """A web-backed provider is checked for memory between calls: a code sent in one
    request must not come back in the next."""

    MODELS = ["gemini-4-argon", "gemini-3.8-flash"]

    def freegemini_cfg(self):
        return dict(llm.DEFAULT_PROVIDERS["freegemini"], api_key="k")

    def use_backend(self, **kw):
        patcher = mock.patch.object(checks, "make_backend", lambda spec, providers: _LeakBackend(**kw))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_leak_check_true_when_the_code_comes_back(self):
        self.use_backend(leak=True)
        leaked, message = checks.leak_check("freegemini", self.freegemini_cfg(), "gemini-4-argon")
        self.assertIs(leaked, True)
        self.assertIn("remembers earlier requests", message)

    def test_leak_check_false_when_it_answers_none(self):
        self.use_backend()
        leaked, message = checks.leak_check("freegemini", self.freegemini_cfg(), "gemini-4-argon")
        self.assertIs(leaked, False)
        self.assertEqual(message, "no memory between calls")

    def test_leak_check_skipped_on_error(self):
        self.use_backend(error=True)
        leaked, message = checks.leak_check("freegemini", self.freegemini_cfg(), "gemini-4-argon")
        self.assertIsNone(leaked)
        self.assertTrue(message.startswith("leak check skipped"))

    def _provider_status(self, **kw):
        self.use_backend(**kw)
        patcher = mock.patch.object(checks, "list_models", lambda provider, cfg: list(self.MODELS))
        patcher.start()
        self.addCleanup(patcher.stop)
        return checks.provider_status("freegemini", self.freegemini_cfg())

    def test_provider_status_errors_on_a_leaking_provider(self):
        state, detail = self._provider_status(leak=True)
        self.assertEqual(state, "error")
        self.assertIn("earlier prompts leak", detail)

    def test_provider_status_notes_no_memory(self):
        state, detail = self._provider_status()
        self.assertEqual(state, "active")
        self.assertIn("no memory between calls", detail)

    def test_provider_status_keeps_active_when_the_check_is_skipped(self):
        state, detail = self._provider_status(error=True)
        self.assertEqual(state, "active")
        self.assertIn("leak check skipped", detail)

    def test_provider_status_picks_gemini_4_argon(self):
        seen = {}

        def fake_leak(provider, cfg, model):
            seen["model"] = model
            return False, "no memory between calls"

        patcher = mock.patch.object(checks, "list_models", lambda provider, cfg: list(self.MODELS))
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch.object(checks, "leak_check", fake_leak):
            checks.provider_status("freegemini", self.freegemini_cfg())
        self.assertEqual(seen["model"], "gemini-4-argon")

    def test_run_start_drops_a_leaking_model(self):
        c = llm.load_llm_config()
        c.update(main_model="w:writer", backup_model="",
                 hybrid_routes={"judge": "freegemini:gemini-4-argon"})
        c["providers"]["freegemini"] = self.freegemini_cfg()
        router = llm.LLMRouter(c, provider="hybrid")
        self.use_backend(leak=True)
        router.select()
        self.assertIn("freegemini:gemini-4-argon", router.down_specs)
        self.assertFalse(router._is_down("w:writer"))
        self.assertTrue(any("remembers earlier requests" in e for e in router.events))

    def test_run_start_keeps_a_clean_provider(self):
        c = llm.load_llm_config()
        c.update(main_model="w:writer", backup_model="",
                 hybrid_routes={"judge": "freegemini:gemini-4-argon"})
        c["providers"]["freegemini"] = self.freegemini_cfg()
        router = llm.LLMRouter(c, provider="hybrid")
        self.use_backend()
        router.select()
        self.assertNotIn("freegemini:gemini-4-argon", router.down_specs)
        self.assertTrue(any("✅ freegemini:gemini-4-argon" in e for e in router.events))

    def test_run_start_warns_but_keeps_using_a_skipped_provider(self):
        c = llm.load_llm_config()
        c.update(main_model="w:writer", backup_model="",
                 hybrid_routes={"judge": "freegemini:gemini-4-argon"})
        c["providers"]["freegemini"] = self.freegemini_cfg()
        router = llm.LLMRouter(c, provider="hybrid")
        self.use_backend(error=True)
        router.select()
        self.assertNotIn("freegemini:gemini-4-argon", router.down_specs)
        self.assertFalse(router._is_down("freegemini:gemini-4-argon"))
        self.assertTrue(any("leak check skipped" in e for e in router.events))


if __name__ == "__main__":
    unittest.main()
