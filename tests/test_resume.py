"""Resuming a stopped run, offline: checkpoint "_resume" metadata, next_after routing,
graph entry, the per-session LLM answer cache, and a CLI --resume run end to end."""
import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from typing import TypedDict
from unittest import mock

from langgraph.graph import END, START, StateGraph

from medical_brain import cli, evals, llm
from medical_brain.graph import build_app
from medical_brain.graph import next_after as video_next_after
from medical_brain.llm import track_node
from medical_brain.llm.errors import StepFailed
from medical_brain.prompts import load_prompt
from medical_brain.research import nodes
from medical_brain.research.graph import build_research_app
from medical_brain.research.graph import next_after as research_next_after
from medical_brain.research.state import new_state


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Tiny(TypedDict, total=False):
    a: int
    b: int
    c: int


class FakeRouter:
    def model_for_node(self, node):
        return None

    def report(self):
        return {"mode": "main", "calls": {}, "cache_hits": 0, "node_models": {},
                "unavailable": {}, "events": []}


def tiny_app(fail_at="", checkpoint_path=None, snapshots=None):
    """a → b → c, with optional failure and a hook that reads the checkpoint from b."""
    g = StateGraph(Tiny)

    def snap():
        if snapshots is not None and checkpoint_path and os.path.exists(checkpoint_path):
            with open(checkpoint_path, encoding="utf-8") as f:
                snapshots.append(json.load(f)["_resume"])

    def a(state):
        snap()
        return {"a": 1}

    def b(state):
        snap()
        if fail_at == "b":
            raise ValueError("boom at b")
        return {"b": 2}

    def c(state):
        snap()
        return {"c": 3}

    for name, fn in (("a", a), ("b", b), ("c", c)):
        g.add_node(name, track_node(name, fn))
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", END)
    return g.compile()


# --------------------------------------------------------------------------
# 1. checkpoint metadata
# --------------------------------------------------------------------------
class CheckpointMetadataTest(unittest.TestCase):
    def test_written_after_every_node_and_last_node_tracks_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "checkpoint.json")
            snapshots = []
            cli.run_graph(tiny_app(checkpoint_path=path, snapshots=snapshots), {}, tmp, FakeRouter())
            self.assertEqual([s["last_node"] for s in snapshots], ["a", "b"])
            for snap in snapshots:
                self.assertEqual(snap["status"], "running")
                self.assertEqual(snap["graph"], "video")
                self.assertEqual(snap["label"], "workflow")
                self.assertIn("updated", snap)
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)["_resume"]
            self.assertEqual(meta["completed"], ["a", "b", "c"])
            self.assertEqual(meta["last_node"], "c")
            self.assertEqual(meta["status"], "running")     # callers mark it done after post-run

    def test_stopped_checkpoint_records_error_and_resume_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "checkpoint.json")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                cli.run_graph(tiny_app(fail_at="b"), {}, tmp, FakeRouter())
            meta = read_json(path)["_resume"]
            self.assertEqual(meta["status"], "stopped")
            self.assertEqual(meta["completed"], ["a"])
            self.assertEqual(meta["last_node"], "a")
            self.assertIn("boom at b", meta["error"])
            self.assertIn("python3 main.py --resume", out.getvalue())

    def test_load_checkpoint_strips_resume_and_keeps_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "checkpoint.json")
            cli.run_graph(tiny_app(), {}, tmp, FakeRouter())
            state, meta = cli.load_checkpoint(path)
            self.assertNotIn("_resume", state)
            self.assertEqual((state["a"], state["b"], state["c"]), (1, 2, 3))
            self.assertEqual(meta["completed"], ["a", "b", "c"])

    def test_mark_done_updates_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli.run_graph(tiny_app(), {}, tmp, FakeRouter())
            cli._mark_checkpoint_done(tmp)
            meta = read_json(os.path.join(tmp, "checkpoint.json"))["_resume"]
            self.assertEqual(meta["status"], "done")
            self.assertEqual(meta["completed"], ["a", "b", "c"])


# --------------------------------------------------------------------------
# 2. next_after routing
# --------------------------------------------------------------------------
class ResearchNextAfterTest(unittest.TestCase):
    def test_none_uses_route_start(self):
        self.assertEqual(research_next_after(None, {}), "expert_brief")
        self.assertEqual(research_next_after(None, {"review": {"approved": False}, "outline": [{"id": "A"}]}),
                         "review_planner")

    def test_every_edge(self):
        self.assertEqual(research_next_after("expert_brief", {}), "topic_planner")
        self.assertEqual(research_next_after("topic_planner", {}), "source_collector")
        self.assertEqual(research_next_after("review_planner", {"focus": ["A"]}), "source_collector")
        self.assertEqual(research_next_after("review_planner", {"focus": []}), "dossier_editor")
        self.assertEqual(research_next_after("source_collector", {}), "source_screener")
        self.assertEqual(research_next_after("source_screener", {"need_sources": True}), "source_collector")
        self.assertEqual(research_next_after("source_screener", {"need_sources": False}), "fact_extractor")
        self.assertEqual(research_next_after("fact_extractor", {}), "fact_verifier")
        self.assertEqual(research_next_after("fact_verifier", {"need_fix": True}), "fact_extractor")
        self.assertEqual(research_next_after("fact_verifier", {"need_fix": False}), "fact_merger")
        self.assertEqual(research_next_after("fact_merger", {}), "coverage_critic")
        self.assertEqual(research_next_after("coverage_critic", {"need_research": True}), "source_collector")
        self.assertEqual(research_next_after("coverage_critic", {"need_research": False}), "dossier_editor")
        self.assertEqual(research_next_after("dossier_editor", {}), "guide_editor")
        self.assertEqual(research_next_after("guide_editor", {}), END)


class VideoNextAfterTest(unittest.TestCase):
    def test_first_and_static_edges(self):
        self.assertEqual(video_next_after(None, {}), "source_script_analyzer")
        self.assertEqual(video_next_after("source_script_analyzer", {}), "seo_keyword_researcher")
        self.assertEqual(video_next_after("script_refinement", {}), "medical_truth_verifier")
        self.assertEqual(video_next_after("final_script_package", {}), "shorts_moment_identifier")

    def test_conditional_loops(self):
        self.assertEqual(video_next_after("translation_fidelity_auditor", {"translation_grade": "PASS",
                         "naturalness_score": 9, "contextual_alignment_score": 9}), "cta_retention_writer")
        self.assertEqual(video_next_after("translation_fidelity_auditor", {"translation_grade": "FAIL"}),
                         "body_restructurer")
        self.assertEqual(video_next_after("self_critique", {"quality_grade": "PASS", "truth_pass": True,
                         "truth_score": 9}), "packaging_creative_director")
        self.assertEqual(video_next_after("self_critique", {"quality_grade": "FAIL"}), "cta_retention_writer")
        self.assertEqual(video_next_after("packaging_honesty_ctr_auditor", {"packaging_grade": "PASS"}),
                         "text_animation_overlay_designer")
        self.assertEqual(video_next_after("packaging_honesty_ctr_auditor", {"packaging_grade": "NO"}),
                         "packaging_generator")
        self.assertEqual(video_next_after("production_quality_critique", {"production_grade": "PASS"}),
                         "final_script_package")
        self.assertEqual(video_next_after("production_quality_critique", {"production_grade": "NO"}),
                         "text_animation_overlay_designer")
        self.assertEqual(video_next_after("shorts_quality_gate", {"shorts_quality_grade": "PASS"}), END)
        self.assertEqual(video_next_after("shorts_quality_gate", {"shorts_quality_grade": "NO"}),
                         "shorts_script_extractor")


# --------------------------------------------------------------------------
# 3. graph entry
# --------------------------------------------------------------------------
EXTRACT = {"facts": [{"kind": "fact", "claim": "Smartwatches detect AF with 94.2% sensitivity.",
                      "quote": "Pooled sensitivity was 94.2%", "source_ids": ["S1"]}]}
VERIFY = {"verdicts": [{"id": "F1", "verdict": "verified", "reason": "ok", "strength": "proven"}]}
COVERAGE = {"coverage_score": 9, "accuracy_score": 9, "value_score": 9, "pass": True,
            "missing": [], "report": "complete"}
DOSSIER = {"summary": "Watches spot AF with high sensitivity [F1].",
           "section_notes": {"A": "Accuracy is high [F1]."}}
GUIDE = {
    "promise": "You will know how accurate a smartwatch is.",
    "action_card": {"do_now": [{"text": "Smartwatches detect AF with 94.2% sensitivity.", "facts": ["F1"]}],
                    "daily": [], "works": [], "doesnt_work_or_risky": [], "see_doctor_if": [], "emergency_if": []},
    "sections": [{"title": "Accuracy", "question": "How accurate?",
                  "points": [{"text": "Smartwatches detect AF with 94.2% sensitivity.", "facts": ["F1"]}]}],
    "special_groups": [], "myths": [], "complementary": {"options": [], "not_proven": []},
    "key_numbers": [], "uncertain": []}


class FakeNodeLlm:
    def __init__(self, answers):
        self.answers = answers
        self.lock = threading.Lock()
        self.systems = []

    def __call__(self, system, user, temperature=None, max_tokens=None, required=()):
        with self.lock:
            self.systems.append(system)
        for prompt, data in self.answers.items():
            if system == load_prompt("research/" + prompt):
                return data, json.dumps(data)
        raise AssertionError(f"unexpected prompt: {system[:60]}")


def research_state_one_section():
    return {
        "topic": "Smartwatches and AF", "settings": {"parallel_calls": 1, "web_search": "off"},
        "outline": [{"id": "A", "title": "Accuracy", "questions": ["How accurate?"],
                     "candidates": [], "source_ids": ["S1"], "extracted": [],
                     "pending": {"literature": [], "public": []}}],
        "sources": {"S1": {"key": "doi:1", "origin": "PubMed", "title": "Study", "year": 2023,
                           "level": 2, "text": "Pooled sensitivity was 94.2% and specificity 95.3% "
                           "for detecting atrial fibrillation."}},
        "facts": [], "merged": [], "rejected": [], "to_extract": ["A"], "work": ["A"],
    }


class GraphEntryTest(unittest.TestCase):
    def test_entry_starts_at_fact_extractor_without_earlier_nodes(self):
        fake = FakeNodeLlm({"fact_extractor": EXTRACT, "fact_verifier": VERIFY,
                            "coverage_critic": COVERAGE, "dossier_editor": DOSSIER,
                            "patient_guide": GUIDE})
        state = research_state_one_section()
        out = dict(state)
        with mock.patch.object(nodes, "call_llm_json", fake):
            for step in build_research_app(entry="fact_extractor").stream(state):
                for _key, update in step.items():
                    out.update(update or {})
        names = [next((k for k in fake.answers if load_prompt("research/" + k) == s), "?")
                 for s in fake.systems]
        self.assertEqual(names[0], "fact_extractor")
        self.assertIn("fact_verifier", names)
        self.assertIn("patient_guide", names)
        for early in ("topic_planner", "review_planner", "source_collector", "source_screener"):
            self.assertNotIn(early, names)
        self.assertEqual(len(out["facts"]), 1)
        self.assertTrue(out["patient_guide"])          # the guide ran after the dossier

    def test_build_app_accepts_an_entry(self):
        self.assertTrue(build_app(entry="medical_truth_verifier"))
        self.assertTrue(build_app())


# --------------------------------------------------------------------------
# 4. LLM answer cache
# --------------------------------------------------------------------------
class FakeBackend:
    def __init__(self, answer="answer", fail=None, delay=0.0):
        self.answer, self.fail, self.delay = answer, fail, delay
        self.calls = 0

    def call(self, system, user, temperature, max_tokens):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return self.answer


def fake_router(cache_dir=None, backend=None):
    config = llm.load_llm_config()
    config.update(provider="main", main_model="fake:model", backup_model="", hybrid_routes={})
    router = llm.LLMRouter(config, provider="main")
    router.MAIN_RETRY_WAITS = ()               # no sleeping between retries in tests
    router._backends = {"fake:model": backend or FakeBackend()}
    if cache_dir:
        router.set_cache_dir(cache_dir)
    return router


class LlmCacheTest(unittest.TestCase):
    def test_miss_then_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = FakeBackend("hello")
            r1 = fake_router(tmp, first)
            self.assertEqual(r1.call("system", "user", 0.2, 100), "hello")
            self.assertEqual(first.calls, 1)
            self.assertEqual(r1.report()["cache_hits"], 0)
            second = FakeBackend("never used")
            r2 = fake_router(tmp, second)
            self.assertEqual(r2.call("system", "user", 0.2, 100), "hello")
            self.assertEqual(second.calls, 0)
            self.assertEqual(r2.report()["cache_hits"], 1)

    def test_key_includes_the_sampling_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend("hello")
            router = fake_router(tmp, backend)
            router.call("s", "u", 0.2, 100)
            router.call("s", "u", 0.7, 100)         # different temperature → new call
            router.call("s", "u", 0.2, 200)         # different max_tokens → new call
            self.assertEqual(backend.calls, 3)
            self.assertEqual(router.report()["cache_hits"], 0)

    def test_empty_or_failed_answers_are_never_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = fake_router(tmp, FakeBackend(""))
            with self.assertRaises(StepFailed):
                empty.call("s", "u", 0.2, 100)
            self.assertEqual(os.listdir(tmp), [])
            broken = fake_router(tmp, FakeBackend(fail=RuntimeError("nope")))
            with self.assertRaises(RuntimeError):
                broken.call("s", "u", 0.2, 100)
            self.assertEqual(os.listdir(tmp), [])

    def test_thread_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend("shared", delay=0.02)
            router = fake_router(tmp, backend)
            results = []

            def worker(i):
                results.append(router.call("s", f"u{i}", 0.2, 100))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(results, ["shared"] * 10)
            self.assertEqual(len(os.listdir(tmp)), 10)       # one JSON file per distinct request
            for name in os.listdir(tmp):
                with open(os.path.join(tmp, name), encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["answer"], "shared")

        # The same request from many threads: one consistent answer, one valid file, and
        # every threaded call served from the cache (the file already existed).
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend("one")
            router = fake_router(tmp, backend)
            self.assertEqual(router.call("s", "same", 0.2, 100), "one")
            calls_after_first = backend.calls
            results = []
            threads = [threading.Thread(target=lambda: results.append(router.call("s", "same", 0.2, 100)))
                       for _ in range(12)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(results, ["one"] * 12)
            self.assertEqual(backend.calls, calls_after_first)   # all 12 were cache hits
            self.assertEqual(router.report()["cache_hits"], 12)
            files = os.listdir(tmp)
            self.assertEqual(len(files), 1)
            with open(os.path.join(tmp, files[0]), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["answer"], "one")

    def test_module_set_cache_dir_applies_to_the_active_router(self):
        with tempfile.TemporaryDirectory() as tmp:
            router = fake_router()
            llm.set_router(router)
            self.addCleanup(llm.set_router, None)
            self.addCleanup(llm.set_cache_dir, None)
            llm.set_cache_dir(tmp)
            self.assertEqual(router.cache_dir, tmp)


# --------------------------------------------------------------------------
# 5. CLI --resume, end to end
# --------------------------------------------------------------------------
SOURCE_A = {"key": "doi:1", "origin": "PubMed", "title": "Smartwatch AF study",
            "url": "https://pubmed.ncbi.nlm.nih.gov/1/", "journal": "Heart Rhythm", "year": 2023,
            "pub_types": ["Meta-Analysis"], "level": 2,
            "text": "Pooled sensitivity was 94.2% and specificity 95.3% for detecting atrial fibrillation."}
SOURCE_B = {"key": "url:https://medlineplus.gov/af", "origin": "MedlinePlus", "title": "Atrial Fibrillation",
            "url": "https://medlineplus.gov/af", "journal": "MedlinePlus (NIH)", "year": 0,
            "pub_types": [], "level": 5,
            "text": "See a doctor right away if you have chest pain, fainting or sudden shortness of breath."}


def fake_sources(kind, query, per_source, years):
    return [dict(SOURCE_A)] if kind == "literature" else [dict(SOURCE_B)]


class FakeResearchBackend:
    """Answers each research step; fails the FIRST time the section-B verifier runs."""

    def __init__(self, fail_verifier_b=False):
        self.fail_verifier_b = fail_verifier_b
        self.systems = []
        self.lock = threading.Lock()

    def call(self, system, user, temperature, max_tokens):
        with self.lock:
            self.systems.append(system)
        if (system == load_prompt("research/fact_verifier") and "SECTION B" in user
                and self.fail_verifier_b):
            with self.lock:
                self.fail_verifier_b = False
            time.sleep(0.3)              # let section A's verifier finish and cache first
            raise llm.ProviderUnavailable("weekly limit reached")
        return self.answer(system, user)

    def answer(self, system, user):
        name = next((k for k in ("expert_brief", "topic_planner", "source_screener", "fact_extractor",
                                 "fact_verifier", "fact_merger", "coverage_critic", "dossier_editor",
                                 "patient_guide") if system == load_prompt("research/" + k)), "")
        if name == "expert_brief":
            return json.dumps({"points": [
                {"id": "K1", "point": "Smartwatches can detect AF.", "confidence": "high",
                 "literature_queries": ["smartwatch AF"], "public_queries": ["AF symptoms"]},
                {"id": "K2", "point": "Chest pain is an emergency.", "confidence": "high",
                 "literature_queries": ["AF emergency"], "public_queries": ["AF warning signs"]}],
                "key_guidelines": [], "misconceptions": []})
        if name == "topic_planner":
            return json.dumps({"angle": "a", "must_cover": ["x"], "sections": [
                {"title": "Alpha", "questions": ["How?"], "literature_queries": ["a lit"], "public_queries": ["a pub"]},
                {"title": "Beta", "questions": ["Why?"], "literature_queries": ["b lit"], "public_queries": ["b pub"]}]})
        if name == "source_screener":
            return json.dumps({"sections": [{"id": "A", "keep": ["S1", "S2"], "gaps": []},
                                            {"id": "B", "keep": ["S1", "S2"], "gaps": []}]})
        if name == "fact_extractor":
            if "SECTION A" in user:
                claim, quote, sid = ("Smartwatches detect AF with 94.2% sensitivity.",
                                     "Pooled sensitivity was 94.2%", "S1")
            else:
                claim, quote, sid = ("See a doctor for chest pain or fainting.",
                                     "See a doctor right away if you have chest pain", "S2")
            return json.dumps({"facts": [{"kind": "fact", "claim": claim, "quote": quote,
                                          "source_ids": [sid]}]})
        if name == "fact_verifier":
            ids = [line.split(" ")[0] for line in user.splitlines() if line.startswith("F")]
            return json.dumps({"verdicts": [{"id": i, "verdict": "verified", "reason": "ok",
                                             "strength": "proven"} for i in ids]})
        if name == "fact_merger":
            return json.dumps({"groups": []})
        if name == "coverage_critic":
            return json.dumps({"coverage_score": 9, "accuracy_score": 9, "value_score": 9, "pass": True,
                               "missing": [], "report": "complete"})
        if name == "dossier_editor":
            return json.dumps({"summary": "Watches spot AF [F1] [F2].",
                               "section_notes": {"A": "High sensitivity [F1].", "B": "High sensitivity [F2]."}})
        if name == "patient_guide":
            return json.dumps(GUIDE)
        raise AssertionError("unexpected prompt")


RESEARCH_PROMPTS = ("expert_brief", "topic_planner", "review_planner", "source_screener", "fact_extractor",
                    "fact_verifier", "fact_merger", "coverage_critic", "dossier_editor", "patient_guide")


def names_of(systems):
    by_system = {load_prompt("research/" + k): k for k in RESEARCH_PROMPTS}
    return [by_system.get(system, "?") for system in systems]


class CliResumeTest(unittest.TestCase):
    def _make(self, backend, cache_dir):
        config = llm.load_llm_config()
        config.update(provider="main", main_model="fake:model", backup_model="", hybrid_routes={})
        router = llm.LLMRouter(config, provider="main")
        router.MAIN_RETRY_WAITS = ()
        router._backends = {"fake:model": backend}
        if cache_dir:
            router.set_cache_dir(cache_dir)
        return router

    def test_fails_at_verifier_then_resumes_and_reuses_cached_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = os.path.join(tmp, "20260101_000000")
            os.makedirs(session_dir)
            backend = FakeResearchBackend(fail_verifier_b=True)
            r1 = self._make(backend, None)
            llm.set_router(r1)
            self.addCleanup(llm.set_router, None)
            self.addCleanup(llm.set_cache_dir, None)

            state = new_state("Smartwatches and atrial fibrillation", settings={"web_search": "off",
                                                                               "parallel_calls": 4})
            state["workflow"] = "research"
            state["session"] = "20260101_000000"
            with mock.patch("medical_brain.research.sources.run_query", side_effect=fake_sources), \
                    mock.patch("medical_brain.research.sources.europepmc.search_older",
                               return_value=[]), \
                    mock.patch.object(cli, "_convert"), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.run_research(r1, state, session_dir)

            checkpoint = os.path.join(session_dir, "1_research", "logs", "research_checkpoint.json")
            meta = read_json(checkpoint)["_resume"]
            self.assertEqual(meta["status"], "stopped")
            self.assertEqual(meta["last_node"], "fact_extractor")
            self.assertEqual(meta["completed"][-1], "fact_extractor")

            # Resume: only the verifier and what follows runs; section A's answer is a cache hit.
            backend2 = FakeResearchBackend(fail_verifier_b=False)
            r2 = self._make(backend2, None)

            def factory():
                llm.set_router(r2)
                return r2

            with mock.patch("medical_brain.research.sources.run_query", side_effect=fake_sources), \
                    mock.patch("medical_brain.research.sources.europepmc.search_older",
                               return_value=[]), \
                    mock.patch.object(cli, "_convert"), \
                    contextlib.redirect_stdout(io.StringIO()):
                cli.resume_run(factory, session_dir)

            resumed = names_of(backend2.systems)
            for early in ("topic_planner", "source_screener", "fact_extractor"):
                self.assertNotIn(early, resumed)
            self.assertIn("fact_verifier", resumed)
            self.assertIn("patient_guide", resumed)
            self.assertGreaterEqual(r2.report()["cache_hits"], 1)
            done = read_json(checkpoint)["_resume"]
            self.assertEqual(done["status"], "done")
            self.assertIn("fact_verifier", done["completed"])
            self.assertIn("guide_editor", done["completed"])
            self.assertTrue(os.path.exists(os.path.join(session_dir, "1_research", "logs", "research_state.json")))
            self.assertTrue(os.path.exists(os.path.join(session_dir, "2_review", "01_review.md")))
            self.assertTrue(os.path.exists(os.path.join(session_dir, "2_review", "02_video_source.md")))
            self.assertTrue(os.path.exists(os.path.join(session_dir, "1_research", "00_START_HERE.md")))

    def test_latest_stopped_checkpoint_skips_done_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "output")
            old = os.path.join(output, "a", "research")
            new = os.path.join(output, "b", "research")
            os.makedirs(old)
            os.makedirs(new)
            for folder, status, when in ((old, "done", 1000), (new, "stopped", 2000)):
                path = os.path.join(folder, "research_checkpoint.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"_resume": {"status": status}}, f)
                os.utime(path, (when, when))
            with mock.patch.object(cli, "PROJECT_ROOT", tmp):
                self.assertEqual(cli._latest_stopped_checkpoint(),
                                 os.path.join(new, "research_checkpoint.json"))


# --------------------------------------------------------------------------
# 6. eval harness resume wiring
# --------------------------------------------------------------------------
class EvalsResumeTest(unittest.TestCase):
    def test_resume_subcommand_dispatches(self):
        with mock.patch.object(evals, "cmd_resume") as cmd:
            evals.main(["resume", "output/s1", "--slug", "muscle-cramps", "--label", "L"])
        cmd.assert_called_once_with("output/s1", "muscle-cramps", "L")

    def test_cmd_resume_resumes_then_scores(self):
        topics = {"topics": [{"slug": "muscle-cramps", "type": "symptom", "topic": "Muscle cramps"}]}
        with mock.patch.object(evals, "_configure_env"), \
                mock.patch.object(evals, "load_topics", return_value=topics), \
                mock.patch.object(evals, "fetch_deepseek_balance", return_value=1.0), \
                mock.patch.object(evals, "_resume_research", return_value="/tmp/sess") as resume, \
                mock.patch.object(evals, "score_session", return_value={"slug": "muscle-cramps"}) as score, \
                mock.patch("builtins.print"):
            result = evals.cmd_resume("/tmp/sess", "muscle-cramps", "L")
        resume.assert_called_once_with("/tmp/sess")
        self.assertEqual(score.call_args.args[0], "/tmp/sess")
        self.assertEqual(score.call_args.args[1], "muscle-cramps")
        self.assertEqual(result, {"slug": "muscle-cramps"})

    def test_resume_research_uses_next_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = os.path.join(tmp, "session")
            folder = os.path.join(session, "research")
            os.makedirs(folder)
            state = {"topic": "T", "settings": {}, "outline": [{"id": "A"}], "facts": []}
            path = os.path.join(folder, "research_checkpoint.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dict(state, _resume={"last_node": "fact_extractor", "completed": ["fact_extractor"],
                                               "status": "stopped", "label": "research"}), f)
            with mock.patch.object(evals, "_configure_env"), \
                    mock.patch("medical_brain.cli.start_router", return_value=object()), \
                    mock.patch("medical_brain.cli.resume_research") as resume, \
                    mock.patch("builtins.print"):
                folder_out = evals._resume_research(session)
            self.assertEqual(folder_out, session)
            # fact_extractor → fact_verifier, and the state was stripped of _resume.
            args = resume.call_args.args
            self.assertEqual(args[3], "fact_verifier")
            self.assertNotIn("_resume", args[1])


if __name__ == "__main__":
    unittest.main()
