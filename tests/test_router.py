"""Model layer against local fake servers (no internet, no real keys):
streaming on all three API types, step time-out → backup, cut-off answer → backup,
Qwen thinking switched off, OpenCode session header, provider fallbacks."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from medical_brain import llm  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
