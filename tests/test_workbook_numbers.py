"""The final package reports honest numbers: the Word Count row, Final Grade and the QA
RESULTS table are built by code from the real judge results, and a source file full of
[S#] citations is treated as a research dossier, not a plain subtitle."""
import unittest
from unittest import mock

from medical_brain import llm
from medical_brain.cli import looks_like_dossier
from medical_brain.nodes import final_package
from medical_brain.nodes.final_package import final_script_package


class Canned:
    def __init__(self, answer=""):
        self.answer, self.prompts = answer, []

    def call(self, system, user, temperature=None, max_tokens=None):
        self.prompts.append(user)
        return self.answer


def use_router(test, router):
    llm.set_router(router)
    test.addCleanup(llm.set_router, None)
    return router


class DossierDetectionTest(unittest.TestCase):
    def test_ten_or_more_citations_is_a_dossier(self):
        self.assertTrue(looks_like_dossier("".join(f"[S{i}] fact\n" for i in range(1, 11))))

    def test_fewer_than_ten_citations_is_not(self):
        self.assertFalse(looks_like_dossier("".join(f"[S{i}] fact\n" for i in range(1, 10))))

    def test_plain_subtitle_is_not_a_dossier(self):
        self.assertFalse(looks_like_dossier("A plain subtitle with no citations."))


PART1 = (
    "## SCRIPT PACKAGE\n\n### Script Metadata\n"
    "| Field | Value |\n|---|---|\n"
    "| Word Count | |\n| Final Grade | |\n\n"
    "### 🎬 PRODUCTION SCRIPT\n[[VERIFIED SCRIPT — inserted by code]]\n\n"
    "### QA RESULTS\n[[QA RESULTS — inserted by code]]\n\n"
    "### WHAT CHANGED FROM THE ORIGINAL\n- x\n"
)


class FinalPackageNumbersTest(unittest.TestCase):
    STATE = {
        "refined_script": " ".join(f"w{i}" for i in range(1613)),
        "blueprint": {"words_per_minute": 140,
                      "chapters": [{"id": "C1", "title": "x", "words": 1613}]},
        "quality_grade": "PASS", "production_grade": "NEEDS_REVISION", "packaging_grade": "B",
        "dialect_score": 9, "warmth_score": 8, "fidelity_score": 9, "medical_accuracy_pass": True,
        "truth_score": 9, "truth_pass": True, "naturalness_score": 9,
        "contextual_alignment_score": 9,
    }

    def _package(self, **over):
        router = use_router(self, Canned("Part 2"))
        router.call = lambda system, user, *a, **k: (
            PART1 if "Compile Part 1" in user else "Part 2")
        for name in ("_audit_part1", "_audit_part2"):
            patcher = mock.patch.object(final_package, name, lambda *a, **k: [])
            patcher.start()
            self.addCleanup(patcher.stop)
        state = dict(self.STATE)
        state.update(over)
        return final_script_package(state)["final_package"]

    def test_word_count_and_final_grade_are_real(self):
        out = self._package()
        self.assertIn("| Word Count | 1,613 words (~11.5 min at 140 wpm) |", out)
        self.assertIn("| Final Grade | PASS |", out)

    def test_qa_rows_reflect_the_real_scores(self):
        out = self._package()
        self.assertIn("| Production | NEEDS_REVISION | Fail |", out)
        self.assertIn("| Packaging | B | Warn |", out)
        self.assertIn("| Dialect authenticity | 9/10 | Pass |", out)
        self.assertIn("| Warmth | 8/10 | Pass |", out)
        self.assertIn("| Medical fidelity | 9/10 | Pass |", out)
        self.assertIn("| Final package issues | 0 | Pass |", out)

    def test_a_missing_judge_is_not_run_not_zero(self):
        out = self._package(fidelity_score=None, medical_accuracy_pass=None)
        self.assertIn("| Medical fidelity | — | Not run |", out)

    def test_the_production_critique_score_table_is_appended(self):
        critique = ("Strengths and weak points.\n"
                    "| Criterion | Score |\n|---|---|\n"
                    "| B-roll relevance | 5 |\n| Organic feel | 9 |\n")
        out = self._package(production_critique_output=critique)
        self.assertIn("| Production · B-roll relevance | 5/10 | Fail |", out)
        self.assertIn("| Production · Organic feel | 9/10 | Pass |", out)

    def test_only_the_criterion_score_table_is_read(self):
        # Real critiques carry numbered score rows plus issue tables (B-roll pairs,
        # timestamps); only the criterion/score columns of the score table count.
        critique = ("| # | Criterion | Score | Gate | Status |\n|---|---|---|---|---|\n"
                    "| 1 | Transition Appropriateness | 6 | ≥7 | ❌ |\n"
                    "| 2 | Density Balance | 3 | ≥8 | ❌ |\n\n"
                    "| Pair | Gap (s) |\n|---|---|\n| #3 & #10 | 10 |\n")
        out = self._package(production_critique_output=critique)
        self.assertIn("| Production · Transition Appropriateness | 6/10 | Fail |", out)
        self.assertIn("| Production · Density Balance | 3/10 | Fail |", out)
        self.assertNotIn("#3 & #10", out)

    def test_the_workbook_still_renders_the_qa_section(self):
        from medical_brain.exports import workbook as ew
        out = self._package()
        self.assertEqual(len(ew.table_rows(ew.find_section(out, "QA RESULTS"))), 10)


if __name__ == "__main__":
    unittest.main()
