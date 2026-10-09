"""Per-provider max_concurrency: the router caps how many calls to one provider
run at once (so the local Gemini Canvas proxy, which serves one request at a time,
isn't flooded by the research workflow's parallel calls) without blocking calls to
other providers. Uses fake backends that sleep and record concurrent calls — no
network, no keys."""
import threading
import time
import unittest

from medical_brain import llm


class ConcurrencyTracker:
    """Records the highest number of simultaneous in-flight calls per provider."""

    def __init__(self):
        self._lock = threading.Lock()
        self.current = {}
        self.maximum = {}

    def enter(self, provider):
        with self._lock:
            now = self.current.get(provider, 0) + 1
            self.current[provider] = now
            self.maximum[provider] = max(self.maximum.get(provider, 0), now)

    def exit(self, provider):
        with self._lock:
            self.current[provider] = self.current.get(provider, 0) - 1


class FakeBackend:
    """Stands in for a real backend: counts overlap, then returns the spec."""

    def __init__(self, spec, tracker, delay=0.3):
        self.spec, self.tracker, self.delay = spec, tracker, delay

    def call(self, *args):
        provider = llm.parse_spec(self.spec)[0]
        self.tracker.enter(provider)
        try:
            time.sleep(self.delay)
        finally:
            self.tracker.exit(provider)
        return self.spec


def config(main, routes=None, **providers):
    """A router config built straight from DEFAULT_PROVIDERS (never touches
    llm_variables.json or llm_keys.env)."""
    all_providers = {name: dict(cfg) for name, cfg in llm.DEFAULT_PROVIDERS.items()}
    all_providers.update(providers)
    return {"provider": "main", "main_model": main, "single_model": main,
            "backup_model": "", "hybrid_routes": routes or {}, "providers": all_providers}


def run_nodes(router, nodes):
    """Run one router.call per node on its own thread, released together by a
    barrier so the calls really overlap."""
    barrier = threading.Barrier(len(nodes))
    errors = []

    def work(node):
        barrier.wait()
        try:
            llm.track_node(node, lambda state: router.call("s", "u", 0.3, 100))({})
        except Exception as e:      # surfaced after the joins
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in nodes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]


class MaxConcurrencyTest(unittest.TestCase):
    def test_limited_provider_runs_one_at_a_time(self):
        tracker = ConcurrencyTracker()
        c = config("solo:m", solo={"type": "openai", "max_concurrency": 1})
        router = llm.LLMRouter(c, provider="main")
        router._backends = {"solo:m": FakeBackend("solo:m", tracker)}

        run_nodes(router, ["step"] * 4)

        self.assertEqual(tracker.maximum.get("solo"), 1)          # serialized
        self.assertEqual(router.report()["calls"]["solo:m"], 4)   # all still answered

    def test_unlimited_provider_runs_parallel(self):
        tracker = ConcurrencyTracker()
        c = config("wide:m", wide={"type": "openai"})            # no max_concurrency
        router = llm.LLMRouter(c, provider="main")
        router._backends = {"wide:m": FakeBackend("wide:m", tracker)}

        run_nodes(router, ["step"] * 4)

        self.assertEqual(tracker.maximum.get("wide"), 4)

    def test_other_provider_not_blocked_by_a_limited_one(self):
        tracker = ConcurrencyTracker()
        c = config("solo:m",
                   routes={"limited_step": "solo:m", "free_step": "free:m"},
                   solo={"type": "openai", "max_concurrency": 1},
                   free={"type": "openai"})
        router = llm.LLMRouter(c, provider="hybrid")
        router._backends = {"solo:m": FakeBackend("solo:m", tracker, delay=0.5),
                            "free:m": FakeBackend("free:m", tracker, delay=0.5)}

        run_nodes(router, ["limited_step"] * 2 + ["free_step"] * 2)

        self.assertEqual(tracker.maximum.get("solo"), 1)   # capped
        self.assertEqual(tracker.maximum.get("free"), 2)   # still concurrent

    def test_gemini_default_is_one(self):
        self.assertEqual(llm.DEFAULT_PROVIDERS["gemini"]["max_concurrency"], 1)

    def test_no_semaphore_when_unset(self):
        router = llm.LLMRouter(config("wide:m", wide={"type": "openai"}), provider="main")
        self.assertIsNone(router._semaphore("wide"))

    def test_semaphore_shared_across_threads(self):
        router = llm.LLMRouter(
            config("solo:m", solo={"type": "openai", "max_concurrency": 2}), provider="main")

        seen = []
        barrier = threading.Barrier(8)

        def grab():
            barrier.wait()
            seen.append(router._semaphore("solo"))

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(map(id, seen))), 1)      # one semaphore for all threads


if __name__ == "__main__":
    unittest.main()
