"""Phase 0 eval harness, offline: a small fake session folder and a scripted fake
model. No network, no real LLM."""
import json
import os
import tempfile
import unittest
from unittest import mock

from medical_brain import evals
from medical_brain.utils.llm_json import UnreadableAnswer


def fake_session(root):
    """output/<id>/research/{research_checkpoint.json,research_log.jsonl}."""
    folder = os.path.join(root, "research")
    os.makedirs(folder, exist_ok=True)
    sources = {
        "S1": {"origin": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/1/", "level": 2},
        "S2": {"origin": "Web", "url": "https://www.mayoclinic.org/diseases/cramps", "level": 5},
        "S3": {"origin": "MedlinePlus", "url": "https://medlineplus.gov/cramps.html", "level": 5},
    }
    state = {
        "topic": "Muscle cramps",
        "outline": [{"id": "A", "title": "What it is"}, {"id": "B", "title": "What to do"}],
        "sources": sources,
        "facts": [
            {"id": "F1", "section": "A", "kind": "fact", "claim": "A cramp is a sudden tightening.",
             "source_ids": ["S1"], "level": 2, "status": "verified"},
            {"id": "F2", "section": "A", "kind": "advice", "claim": "Stretch the muscle.",
             "source_ids": ["S2"], "level": 5, "status": "verified"},
            {"id": "F3", "section": "B", "kind": "red_flag", "claim": "See a doctor if it is frequent.",
             "source_ids": ["S2", "S3"], "level": 5, "status": "verified"},
            {"id": "F4", "section": "B", "kind": "fact", "claim": "rejected fact",
             "source_ids": ["S3"], "level": 5, "status": "rejected"},
        ],
        "rejected": [{"claim": "rejected fact", "note": "unverifiable"}],
        "coverage_scores": {"coverage": 6, "accuracy": 7, "value": 6},
        "source_log": [["Google", "", "degraded persisted: disabled"]],
    }
    with open(os.path.join(folder, "research_checkpoint.json"), "w", encoding="utf-8") as f:
        json.dump(state, f)
    with open(os.path.join(folder, "research_log.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": "2026-10-09T15:33:00", "node": "topic_planner"}) + "\n")
        f.write(json.dumps({"timestamp": "2026-10-09T15:43:00", "node": "dossier_editor"}) + "\n")
    return root, state


QUESTIONS = [
    {"id": "Q1", "q": "What is a cramp?", "importance": "core"},
    {"id": "Q2", "q": "When is it dangerous?", "importance": "core"},
    {"id": "Q3", "q": "What helps at night?", "importance": "useful"},
]

TOPIC_ENTRY = {"slug": "muscle-cramps", "type": "symptom", "topic": "muscle cramps",
               "questions": QUESTIONS}

JUDGE = {
    "questions": [{"id": "Q1", "answered": "yes", "fact_ids": ["F1"]},
                  {"id": "Q2", "answered": "partly", "fact_ids": ["F3"]},
                  {"id": "Q3", "answered": "yes", "fact_ids": ["F2"]}],
    "off_topic": ["F3"],
    "low_value": ["F2"],
    "duplicates": [["F1", "F2"]],
    "safety_gaps": ["when to go to the ER"],
    "overclaims": [{"id": "F2", "why": "stated as proven"}],
    "scores": {"answers": 7, "actionability": 6, "safety": 5, "focus": 6, "evidence_honesty": 7},
    "summary": "Solid basics; safety and night cramps are thin.",
}

GUIDE = {
    "promise": "You can handle night cramps.",
    "action_card": {"do_now": [{"text": "Stretch before bed.", "facts": ["F2"], "strength": "proven"}],
                    "daily": [], "works": [], "doesnt_work_or_risky": [],
                    "see_doctor_if": [], "emergency_if": []},
    "sections": [{"title": "What it is", "question": "What is a cramp?",
                  "points": [{"text": "A cramp is a sudden tightening.", "facts": ["F1"],
                              "strength": "proven"}]}],
    "special_groups": [], "myths": [],
    "complementary": {"options": [], "not_proven": []},
    "key_numbers": [], "uncertain": [],
}

GUIDE_JUDGE = {
    "questions": [{"id": "Q1", "answered": "yes"}, {"id": "Q2", "answered": "yes"},
                  {"id": "Q3", "answered": "no"}],
    "unsafe_or_misleading": [{"item": "Stretch.", "why": "overstated"}],
    "missing_from_action_card": ["ER signs"],
    "unclear": [], "padding": [],
    "scores": {"answers": 8, "actionability": 7, "safety": 9, "clarity": 8, "honesty": 7},
    "summary": "Good guide; night cramps are thin.",
}


class FakeLlmJson:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, system, user, temperature=None, max_tokens=None, required=()):
        self.calls.append({"system": system, "user": user, "temperature": temperature,
                           "max_tokens": max_tokens, "required": required})
        data = self.reply(user, len(self.calls)) if callable(self.reply) else self.reply
        return data, json.dumps(data)


class MetricsTest(unittest.TestCase):
    def test_code_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, state = fake_session(tmp)
            metrics = evals.compute_metrics(state, QUESTIONS, JUDGE)
            self.assertEqual(metrics["verified_facts"], 3)
            self.assertEqual(metrics["left_out"], 1)
            self.assertEqual(metrics["sources_used"], 3)
            self.assertEqual(metrics["facts_citing_web"], 2)   # F2, F3
            self.assertEqual(metrics["web_share"], 66.7)
            self.assertEqual(metrics["facts_by_site"], {"mayoclinic.org": 2})
            self.assertEqual(metrics["sections"], 2)
            self.assertEqual(metrics["coverage_scores"], {"coverage": 6, "accuracy": 7, "value": 6})
            self.assertEqual(metrics["google_status"], "attempted")
            # judge-derived
            self.assertEqual((metrics["core_answered"], metrics["core_total"], metrics["core_partly"]), (1, 2, 1))
            self.assertEqual(metrics["useful_answered"], 1)
            self.assertEqual((metrics["off_topic"], metrics["off_topic_pct"]), (1, 33.3))
            self.assertEqual((metrics["low_value"], metrics["low_value_pct"]), (1, 33.3))
            self.assertEqual((metrics["duplicates"], metrics["duplicate_facts"]), (1, 2))
            self.assertEqual(metrics["safety_gaps"], 1)
            self.assertEqual(metrics["overclaims"], 1)
            self.assertEqual(metrics["score_mean"], 6.2)

    def test_judge_null_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, state = fake_session(tmp)
            metrics = evals.compute_metrics(state, QUESTIONS, None)
            self.assertEqual(metrics["core_answered"], 0)
            self.assertIsNone(metrics["answers"])
            self.assertIsNone(metrics["score_mean"])

    def test_run_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _state = fake_session(tmp)
            self.assertEqual(evals._run_minutes(os.path.join(root, "research")), 10.0)


class GuideMetricsTest(unittest.TestCase):
    def test_guide_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, state = fake_session(tmp)
            state["patient_guide"] = GUIDE
            state["guide_report"] = {"dropped": 3, "dropped_items": [{"text": "x"}]}
            metrics = evals.compute_metrics(state, QUESTIONS, JUDGE, GUIDE_JUDGE)
            self.assertEqual((metrics["guide_items"], metrics["guide_dropped"]), (2, 3))
            self.assertEqual((metrics["guide_core_answered"], metrics["guide_core_total"]), (2, 2))
            self.assertEqual(metrics["guide_unsafe"], 1)
            self.assertEqual((metrics["guide_answers"], metrics["guide_clarity"]), (8, 8))
            self.assertEqual(metrics["guide_mean"], 7.8)

    def test_no_guide_gives_zero_and_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, state = fake_session(tmp)
            metrics = evals.compute_metrics(state, QUESTIONS, JUDGE, None)
            self.assertEqual(metrics["guide_items"], 0)
            self.assertEqual(metrics["guide_dropped"], 0)
            self.assertEqual(metrics["guide_unsafe"], 0)
            self.assertIsNone(metrics["guide_mean"])


class GuideJudgeTest(unittest.TestCase):
    def test_prompt_has_every_question_item_fact_and_strength(self):
        user = evals.build_guide_judge_user("Muscle cramps", QUESTIONS, GUIDE)
        self.assertIn("TOPIC: Muscle cramps", user)
        for q in QUESTIONS:
            self.assertIn(q["id"], user)
            self.assertIn(q["q"], user)
        self.assertIn("Stretch before bed.", user)
        self.assertIn("facts: F2", user)
        self.assertIn("strength: proven", user)

    def test_judge_temperature_and_required(self):
        fake = FakeLlmJson(GUIDE_JUDGE)
        with mock.patch.object(evals, "call_llm_json", fake):
            judge = evals.judge_guide_session("t", QUESTIONS, GUIDE)
        self.assertEqual(judge, GUIDE_JUDGE)
        self.assertEqual(fake.calls[0]["temperature"], 0)
        self.assertEqual(fake.calls[0]["max_tokens"], 8000)
        self.assertEqual(fake.calls[0]["required"], ["questions", "scores"])

    def test_unreadable_retries_once_then_null(self):
        fake = FakeLlmJson(None)
        fake.reply = lambda user, n: (_ for _ in ()).throw(UnreadableAnswer("bad", "raw"))
        with mock.patch.object(evals, "call_llm_json", fake), mock.patch("builtins.print"):
            self.assertIsNone(evals.judge_guide_session("t", QUESTIONS, GUIDE))
        self.assertEqual(len(fake.calls), 2)


class FrozenQuestionsTest(unittest.TestCase):
    def test_generated_once_and_written_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "topics.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"_about": "x", "topics": [{"slug": "migraine", "type": "disease",
                                                      "topic": "migraine"}]}, f)
            fake = FakeLlmJson({"questions": [{"id": "Q1", "q": "q?", "importance": "core"}]})
            with mock.patch.object(evals, "TOPICS_PATH", path), \
                    mock.patch.object(evals, "call_llm_json", fake), \
                    mock.patch("builtins.print"):
                first = evals.ensure_questions("migraine")
                second = evals.ensure_questions("migraine")
            self.assertEqual(len(fake.calls), 1)                # generated once
            self.assertEqual(first, second)
            self.assertEqual(first[0]["id"], "Q1")
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["topics"][0]["questions"], first)
            self.assertEqual(fake.calls[0]["temperature"], 0.2)
            self.assertIn("migraine", fake.calls[0]["user"])


class JudgePromptTest(unittest.TestCase):
    def test_prompt_has_every_fact_and_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, state = fake_session(tmp)
            facts = [f for f in state["facts"] if f["status"] == "verified"]
            user = evals.build_judge_user(state["topic"], QUESTIONS, facts, state["sources"])
            for fid in ("F1", "F2", "F3"):
                self.assertIn(fid, user)
            for q in QUESTIONS:
                self.assertIn(q["id"], user)
                self.assertIn(q["q"], user)
            self.assertIn("mayoclinic.org", user)              # site of the first source
            self.assertIn("(level 5)", user)
            self.assertNotIn("F4", user)                       # rejected fact excluded

    def test_unreadable_retries_once_then_null(self):
        fake = FakeLlmJson(None)
        fake.reply = lambda user, n: (_ for _ in ()).throw(UnreadableAnswer("bad", "raw"))
        with mock.patch.object(evals, "call_llm_json", fake), mock.patch("builtins.print"):
            self.assertIsNone(evals.judge_session("t", QUESTIONS, [], {}))
        self.assertEqual(len(fake.calls), 2)                   # one retry

    def test_retry_recovers(self):
        def reply(user, n):
            if n == 1:
                raise UnreadableAnswer("bad", "raw")
            return JUDGE
        fake = FakeLlmJson(reply)
        with mock.patch.object(evals, "call_llm_json", fake), mock.patch("builtins.print"):
            self.assertEqual(evals.judge_session("t", QUESTIONS, [], {}), JUDGE)
        self.assertEqual(fake.calls[0]["temperature"], 0)
        self.assertEqual(fake.calls[0]["max_tokens"], 8000)
        self.assertEqual(fake.calls[0]["required"], ["questions", "scores"])


class ScorecardTest(unittest.TestCase):
    def _result(self, **kw):
        result = {"date": "2026-10-09 16:00", "label": "v1-baseline", "slug": "muscle-cramps",
                  "git_commit": "e4fd55a", "run_minutes": 30.5, "cost_usd": 0.0123,
                  "verified_facts": 175, "left_out": 1, "web_share": 42.0,
                  "core_answered": 7, "core_total": 9, "core_partly": 1, "useful_answered": 3,
                  "off_topic_pct": 4.0, "low_value_pct": 6.0, "safety_gaps": 2,
                  "answers": 7, "actionability": 6, "safety": 5, "focus": 6,
                  "evidence_honesty": 7, "score_mean": 6.2, "judge": JUDGE}
        result.update(kw)
        return result

    def test_table_row_and_summary(self):
        topics = {"topics": [{"slug": "muscle-cramps", "questions": QUESTIONS}]}
        text = evals.render_scorecard([self._result()], topics)
        header_line = next(l for l in text.splitlines() if l.startswith("| date "))
        header = [c.strip() for c in header_line.strip("|").split("|")]
        self.assertIn("core answered", header)
        self.assertIn("mean", header)
        self.assertIn("| v1-baseline | muscle-cramps | e4fd55a |", text)
        self.assertIn("| 7/9 |", text)
        self.assertIn("$0.0123", text)
        self.assertIn(JUDGE["summary"], text)
        self.assertIn("Q2 (partly): When is it dangerous?", text)   # latest-per-slug open core

    def test_cost_null_when_balance_unavailable(self):
        topics = {"topics": [{"slug": "muscle-cramps", "questions": QUESTIONS}]}
        text = evals.render_scorecard([self._result(cost_usd=None)], topics)
        self.assertIn("| — |", text)
        self.assertIsNone(evals.balance_delta(None, 1.0))
        self.assertIsNone(evals.balance_delta(1.0, None))
        self.assertEqual(evals.balance_delta(1.0, 0.25), 0.75)

    def test_guide_columns_and_summary_with_a_guide(self):
        topics = {"topics": [{"slug": "muscle-cramps", "questions": QUESTIONS}]}
        result = self._result(guide_items=2, guide_core_answered=2, guide_core_total=2,
                              guide_mean=7.8, guide_unsafe=1, guide_judge=GUIDE_JUDGE)
        text = evals.render_scorecard([result], topics)
        header_line = next(l for l in text.splitlines() if l.startswith("| date "))
        header = [c.strip() for c in header_line.strip("|").split("|")]
        self.assertIn("guide core", header)
        self.assertIn("guide mean", header)
        self.assertIn("| 2/2 | 7.80 |", text)
        self.assertIn(GUIDE_JUDGE["summary"], text)
        self.assertIn("**Unsafe or misleading:**", text)

    def test_guide_columns_are_dashes_without_a_guide(self):
        topics = {"topics": [{"slug": "muscle-cramps", "questions": QUESTIONS}]}
        text = evals.render_scorecard([self._result()], topics)
        data_line = next(l for l in text.splitlines() if l.startswith("| 2026-10-09"))
        self.assertTrue(data_line.rstrip().endswith("| — | — |"))


class BalanceTest(unittest.TestCase):
    class _Resp:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_reads_usd_balance(self):
        payload = {"is_available": True, "balance_infos": [
            {"currency": "CNY", "total_balance": "70.00"},
            {"currency": "USD", "total_balance": "3.25"}]}
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), \
                mock.patch.object(evals.urllib.request, "urlopen", return_value=self._Resp(payload)):
            self.assertEqual(evals.fetch_deepseek_balance(), 3.25)

    def test_unavailable_is_none(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            self.assertIsNone(evals.fetch_deepseek_balance())
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), \
                mock.patch.object(evals.urllib.request, "urlopen", side_effect=OSError("blocked")):
            self.assertIsNone(evals.fetch_deepseek_balance())


class ScoreSessionTest(unittest.TestCase):
    def test_scores_writes_result_eval_and_scorecard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _state = fake_session(os.path.join(tmp, "session"))
            topics_path = os.path.join(tmp, "topics.json")
            results_path = os.path.join(tmp, "results.jsonl")
            scorecard_path = os.path.join(tmp, "scorecard.md")
            with open(topics_path, "w", encoding="utf-8") as f:
                json.dump({"topics": [TOPIC_ENTRY]}, f)
            fake = FakeLlmJson(JUDGE)
            with mock.patch.object(evals, "TOPICS_PATH", topics_path), \
                    mock.patch.object(evals, "RESULTS_PATH", results_path), \
                    mock.patch.object(evals, "SCORECARD_PATH", scorecard_path), \
                    mock.patch.object(evals, "call_llm_json", fake), \
                    mock.patch("builtins.print"):
                result = evals.score_session(root, "muscle-cramps", "v1-baseline", cost_usd=None)
            self.assertEqual(len(fake.calls), 1)                # judge only; questions already frozen
            self.assertEqual(result["slug"], "muscle-cramps")
            self.assertEqual(result["type"], "symptom")
            self.assertIsNone(result["cost_usd"])
            self.assertEqual(result["core_answered"], 1)
            self.assertEqual(result["run_minutes"], 10.0)
            self.assertTrue(result["git_commit"])               # inside a git repo
            with open(results_path, encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["score_mean"], 6.2)
            self.assertTrue(os.path.exists(os.path.join(root, "research", "eval.json")))
            with open(scorecard_path, encoding="utf-8") as f:
                self.assertIn("muscle-cramps", f.read())

    def test_scores_a_session_with_a_patient_guide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = fake_session(os.path.join(tmp, "session"))
            state["patient_guide"] = GUIDE
            state["guide_report"] = {"dropped": 1, "dropped_items": [{"text": "x"}]}
            with open(os.path.join(root, "research", "research_checkpoint.json"), "w", encoding="utf-8") as f:
                json.dump(state, f)
            topics_path = os.path.join(tmp, "topics.json")
            results_path = os.path.join(tmp, "results.jsonl")
            scorecard_path = os.path.join(tmp, "scorecard.md")
            with open(topics_path, "w", encoding="utf-8") as f:
                json.dump({"topics": [TOPIC_ENTRY]}, f)
            fake = FakeLlmJson(lambda user, n: JUDGE if n == 1 else GUIDE_JUDGE)
            with mock.patch.object(evals, "TOPICS_PATH", topics_path), \
                    mock.patch.object(evals, "RESULTS_PATH", results_path), \
                    mock.patch.object(evals, "SCORECARD_PATH", scorecard_path), \
                    mock.patch.object(evals, "call_llm_json", fake), \
                    mock.patch("builtins.print"):
                result = evals.score_session(root, "muscle-cramps", "phase3", cost_usd=None)
            self.assertEqual(len(fake.calls), 2)                # research judge + guide judge
            self.assertEqual(result["guide_items"], 2)
            self.assertEqual((result["guide_core_answered"], result["guide_core_total"]), (2, 2))
            self.assertEqual(result["guide_mean"], 7.8)
            self.assertEqual(result["guide_judge"], GUIDE_JUDGE)
            with open(scorecard_path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("| 2/2 | 7.80 |", text)
            self.assertIn(GUIDE_JUDGE["summary"], text)


if __name__ == "__main__":
    unittest.main()
