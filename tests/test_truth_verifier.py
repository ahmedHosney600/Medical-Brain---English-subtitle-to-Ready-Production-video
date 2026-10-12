"""The code-enforced overclaim fix pass in medical_truth_verifier (research-dossier runs):
overclaims the truth verifier left behind are fixed with ONE overclaim_fix call, applied
in code only when an edit's find text is unique."""
import unittest
from unittest import mock

from medical_brain.nodes import quality, script_writing


class FakeJSON:
    """Records every call and answers with fn(system, user)."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def __call__(self, system, user, temperature=None, max_tokens=None, required=(), **kwargs):
        self.calls.append({"system": system, "user": user, "temperature": temperature,
                           "max_tokens": max_tokens, "required": required, **kwargs})
        return self.fn(system, user)


def use(test, fake):
    patcher = mock.patch.object(quality, "call_llm_json", fake)
    patcher.start()
    test.addCleanup(patcher.stop)
    return fake


SCRIPT = "الحركة دي بتفك الانقباض في ثواني. اسأل دكتورك."


def state(**over):
    base = {"refined_script": SCRIPT, "research_dossier": "DOSSIER: the timing is not stated",
            "original_script": "", "source_analysis": "", "strategy_plan": "",
            "presenter_profile": "", "medical_disclaimer_requirements": "", "avoid_list": ""}
    base.update(over)
    return base


def verifier_answer(script, report="base report"):
    return {"truth_score": 9, "truth_pass": True,
            "truth_verification_report": report, "verified_script": script}


def printed_text(printed):
    return " ".join(str(call.args[0]) for call in printed.call_args_list)


class TruthVerifierOverclaimFixTest(unittest.TestCase):
    def test_unique_edit_is_applied_and_logged(self):
        fake = FakeJSON(lambda system, user: (
            ({"edits": [{"find": "بتفك الانقباض في ثواني",
                         "replace": "بتساعد تفك الانقباض", "reason": "x"}]}, "raw")
            if len(fake.calls) > 1 else (verifier_answer(SCRIPT), "raw")))
        use(self, fake)
        with mock.patch("builtins.print") as printed:
            out = quality.medical_truth_verifier(state())

        self.assertEqual(len(fake.calls), 2)
        self.assertNotIn("في ثواني", out["refined_script"])
        self.assertIn("بتساعد تفك الانقباض", out["refined_script"])
        self.assertIn("### Overclaim fix (code)", out["truth_verification_report"])
        self.assertIn("بتساعد تفك الانقباض", out["truth_verification_report"])
        self.assertIn("applied 1, skipped 0", printed_text(printed))
        # The fix call uses the overclaim_fix prompt and the required dials.
        self.assertEqual(fake.calls[1]["temperature"], 0.2)
        self.assertEqual(fake.calls[1]["max_tokens"], 4000)
        self.assertEqual(fake.calls[1]["required"], ["edits"])
        self.assertIn("FLAGGED SENTENCES", fake.calls[1]["user"])
        self.assertIn("RESEARCH DOSSIER", fake.calls[1]["user"])

    def test_missing_and_duplicate_edits_are_skipped(self):
        script = "الحركة دي بتفك الانقباض في ثواني. نص مكرر هنا. نص مكرر هنا."

        def answer(system, user):
            if len(fake.calls) > 1:
                return ({"edits": [
                    {"find": "بتفك الانقباض في ثواني", "replace": "بتساعد تفك الانقباض"},
                    {"find": "غير موجود هنا", "replace": "x"},
                    {"find": "نص مكرر", "replace": "y"}]}, "raw")
            return verifier_answer(script), "raw"

        fake = FakeJSON(answer)
        use(self, fake)
        with mock.patch("builtins.print") as printed:
            out = quality.medical_truth_verifier(state(refined_script=script))

        self.assertIn("بتساعد تفك الانقباض", out["refined_script"])
        self.assertNotIn("غير موجود", out["refined_script"])
        self.assertEqual(out["refined_script"].count("نص مكرر"), 2)
        self.assertIn("applied 1, skipped 2", printed_text(printed))

    def test_fix_failure_keeps_the_verified_script(self):
        def answer(system, user):
            if len(fake.calls) > 1:
                from medical_brain.utils.llm_json import UnreadableAnswer
                raise UnreadableAnswer("bad json", "raw")
            return verifier_answer(SCRIPT), "raw"

        fake = FakeJSON(answer)
        use(self, fake)
        with mock.patch("builtins.print") as printed:
            out = quality.medical_truth_verifier(state())

        self.assertEqual(out["refined_script"], SCRIPT)
        self.assertEqual(out["truth_verification_report"], "base report")
        self.assertIn("overclaim fix", printed_text(printed))

    def test_without_a_dossier_no_fix_call(self):
        fake = FakeJSON(lambda system, user: (verifier_answer(SCRIPT), "raw"))
        use(self, fake)
        out = quality.medical_truth_verifier(state(research_dossier=""))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(out["refined_script"], SCRIPT)
        self.assertEqual(out["truth_verification_report"], "base report")

    def test_no_overclaims_left_no_fix_call(self):
        clean = "الإطالة ممكن تساعد في تخفيف الشد. استشير الدكتور لو الشد اتكرر."
        fake = FakeJSON(lambda system, user: (verifier_answer(clean), "raw"))
        use(self, fake)
        quality.medical_truth_verifier(state(refined_script=clean))
        self.assertEqual(len(fake.calls), 1)


class TruthVerifierWholeTightenTest(unittest.TestCase):
    """After the overclaim pass, a single-chapter script that overran its whole-video
    budget is tightened again; chapter mode and target-less blueprints are not."""

    LONG = "كلمة " * 1700
    SHORT = "قصير " * 1000

    def test_single_chapter_with_target_is_tightened(self):
        fake = FakeJSON(lambda system, user: (verifier_answer(self.LONG), "raw"))
        use(self, fake)
        calls = []

        def fake_llm(system, user, temperature=None, max_tokens=None, **kwargs):
            calls.append(user)
            return self.SHORT

        with mock.patch.object(script_writing, "call_llm", fake_llm):
            out = quality.medical_truth_verifier(state(
                refined_script=self.LONG, research_dossier="",
                blueprint={"target_minutes": 10, "chapters": [
                    {"id": "1", "title": "الفصل", "words": 1350}]}))

        self.assertEqual(len(calls), 1)
        self.assertEqual(out["refined_script"], self.SHORT)

    def test_chapter_mode_is_not_tightened(self):
        fake = FakeJSON(lambda system, user: (verifier_answer(self.LONG), "raw"))
        use(self, fake)
        with mock.patch.object(script_writing, "call_llm",
                               lambda *a, **k: self.fail("chapter mode must not tighten")):
            out = quality.medical_truth_verifier(state(
                refined_script=self.LONG, research_dossier="",
                blueprint={"target_minutes": 10, "chapters": [
                    {"id": "1", "title": "أ", "words": 900},
                    {"id": "2", "title": "ب", "words": 900}]}))

        self.assertEqual(out["refined_script"], self.LONG.strip())

    def test_no_target_is_not_tightened(self):
        fake = FakeJSON(lambda system, user: (verifier_answer(self.LONG), "raw"))
        use(self, fake)
        with mock.patch.object(script_writing, "call_llm",
                               lambda *a, **k: self.fail("no target must not tighten")):
            out = quality.medical_truth_verifier(state(
                refined_script=self.LONG, research_dossier="",
                blueprint={"chapters": [{"id": "1", "title": "الفصل", "words": 3000}]}))

        self.assertEqual(out["refined_script"], self.LONG.strip())


if __name__ == "__main__":
    unittest.main()
