"""The final package keeps its BEST attempt, not its last one: a missing whole section
must not tie with a couple of bad cues, and the returned text must match the issues
reported beside it (a real run shipped attempt 3's storyboard with attempt 1's warning)."""
import unittest
from unittest import mock

from medical_brain.nodes import final_package


def use(test, module, name, fake):
    patcher = mock.patch.object(module, name, fake)
    patcher.start()
    test.addCleanup(patcher.stop)
    return fake


MISSING = "MISSING: The INTEGRATED PRODUCTION STORYBOARD section is absent entirely."
CUES = "STORYBOARD CUES NOT EXACT (2): SB#2 (B-ROLL) ...; SB#3 (B-ROLL) ..."


class IssueWeightTest(unittest.TestCase):
    def test_a_missing_section_outweighs_a_fistful_of_cues(self):
        self.assertEqual(final_package._issue_weight([MISSING]), 100)
        self.assertEqual(final_package._issue_weight([CUES]), 2)
        self.assertEqual(final_package._issue_weight(["THIN STORYBOARD: 6 rows"]), 1)
        self.assertEqual(final_package._issue_weight([]), 0)
        self.assertGreater(final_package._issue_weight([MISSING]),
                           final_package._issue_weight([CUES, CUES, CUES]))


class Part2BestAttemptTest(unittest.TestCase):
    def _run(self, first, first_issues, later, later_issues):
        """_part2_single_call with attempt 1 returning `first` and every retry `later`."""
        count = {"n": 0}

        def answer(*args, **kwargs):
            count["n"] += 1
            return first if count["n"] == 1 else later

        use(self, final_package, "call_llm", answer)
        use(self, final_package, "_audit_part2",
            lambda text, script="": first_issues if text == first else later_issues)
        with mock.patch("builtins.print"):
            return final_package._part2_single_call("sys", "base", [], "", "script")

    def test_a_later_attempt_with_cues_beats_an_earlier_missing_section(self):
        part2, best = self._run("TEXT_1", [MISSING], "TEXT_2", [CUES, CUES])
        self.assertEqual(part2, "TEXT_2")
        self.assertEqual(best[1], "TEXT_2")             # the shipped text is the best one
        self.assertEqual(best[2], [CUES, CUES])         # ... and its own issues
        self.assertEqual(best[0], final_package._issue_weight(best[2]))

    def test_an_earlier_attempt_is_kept_when_the_retry_is_worse(self):
        part2, best = self._run("TEXT_1", [CUES], "TEXT_2", [MISSING])
        self.assertEqual(part2, "TEXT_1")
        self.assertEqual(best[1], part2)
        self.assertEqual(best[2], [CUES])
        self.assertEqual(best[0], final_package._issue_weight(best[2]))


if __name__ == "__main__":
    unittest.main()
