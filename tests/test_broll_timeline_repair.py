"""Mechanical B-roll timeline repair: clustered rows move/drop, long gaps get one
model-written row, and every design layer is timed by the spoken script's cues."""
import unittest
from unittest import mock

from medical_brain.exports import workbook as ew
from medical_brain.nodes import production


class FakeLLM:
    """Records every call and answers with fn(system, user, temperature, max_tokens)."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def __call__(self, system, user, temperature=None, max_tokens=None, **kwargs):
        self.calls.append({"system": system, "user": user,
                           "temperature": temperature, "max_tokens": max_tokens, **kwargs})
        return self.fn(system, user, temperature, max_tokens)


def use(test, module, name, fake):
    patcher = mock.patch.object(module, name, fake)
    patcher.start()
    test.addCleanup(patcher.stop)
    return fake


def script(sentences: int) -> str:
    return " ".join(" ".join(f"w{i * 5 + j}" for j in range(5)) + "." for i in range(sentences))


def sentence(i: int) -> str:
    return " ".join(f"w{i * 5 + j}" for j in range(5))


HEAD = ("| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |\n"
        "|---|---|---|---|---|---|\n")


def row(number: int, i: int, prompt: str = "a man in a clinic, slow push-in") -> str:
    words = sentence(i).split()
    return (f"| {number} | 9:59 | {sentence(i)} | {' '.join(words[-4:])} | "
            f"🎬 Video | {prompt} |\n")


def repair(table: str, text: str = None):
    with mock.patch("builtins.print"):
        return production.repair_broll_timeline({}, table, text or script(30), 60)


class ClusterRepairTest(unittest.TestCase):
    def test_cluster_moves_to_the_next_sentence_at_least_8s_later(self):
        table = HEAD + row(1, 0) + row(2, 1) + row(3, 4)
        out, report = repair(table)
        rows = ew.broll_rows(out)
        self.assertEqual(report, {"moved": 1, "dropped": 0, "filled": 0, "gaps_left": 0})
        self.assertEqual([r["time"] for r in rows], ["0:00", "0:10", "0:20"])
        self.assertEqual(rows[1]["start"], sentence(2))       # first sentence >= 8s after #1
        self.assertEqual(rows[1]["end"], " ".join(sentence(2).split()[-4:]))

    def test_cluster_with_no_room_is_dropped_and_rows_are_renumbered(self):
        table = HEAD + row(1, 0) + row(2, 1) + row(3, 2)
        out, report = repair(table)
        rows = ew.broll_rows(out)
        self.assertEqual(report, {"moved": 0, "dropped": 1, "filled": 0, "gaps_left": 0})
        self.assertEqual([r["num"] for r in rows], ["1", "2"])
        self.assertEqual([r["start"] for r in rows], [sentence(0), sentence(2)])
        self.assertNotIn(sentence(1), out)


class GapFillTest(unittest.TestCase):
    ANSWER = "| 9 | 9:59 | WRONG CUE | WRONG END | 🎬 Video | a clinic corridor, slow dolly |"

    def test_a_70s_gap_gets_one_inserted_row_with_forced_cues(self):
        table = HEAD + row(1, 0) + row(2, 14)                # 0s -> 70s
        fake = use(self, production, "call_llm", FakeLLM(lambda *a: self.ANSWER))
        out, report = repair(table)
        rows = ew.broll_rows(out)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(report, {"moved": 0, "dropped": 0, "filled": 1, "gaps_left": 0})
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["start"], sentence(7))       # closest to the gap's middle
        self.assertEqual(rows[1]["end"], " ".join(sentence(7).split()[-4:]))
        self.assertEqual(rows[1]["time"], "0:35")
        self.assertNotIn("WRONG", out)
        self.assertIn(sentence(7), fake.calls[0]["user"])     # the passage was sent in the prompt

    def test_a_failed_gap_fill_leaves_the_other_repairs_in_place(self):
        table = HEAD + row(1, 0) + row(2, 1) + row(3, 14)

        def fail(*args, **kwargs):
            raise RuntimeError("model down")

        use(self, production, "call_llm", FakeLLM(fail))
        out, report = repair(table)
        rows = ew.broll_rows(out)
        self.assertEqual(report["moved"], 1)                  # the 5s cluster was still fixed
        self.assertEqual(report["filled"], 0)
        self.assertEqual(report["gaps_left"], 1)
        self.assertEqual([r["start"] for r in rows],
                         [sentence(0), sentence(2), sentence(14)])

    def test_repair_is_idempotent(self):
        table = HEAD + row(1, 0) + row(2, 14)
        use(self, production, "call_llm", FakeLLM(lambda *a: self.ANSWER))
        first, report = repair(table)
        self.assertEqual(report["filled"], 1)
        fake = use(self, production, "call_llm",
                   FakeLLM(lambda *a: self.fail("repair must not call the model again")))
        second, again = repair(first)
        self.assertEqual(second, first)
        self.assertEqual(fake.calls, [])
        self.assertEqual(again, {"moved": 0, "dropped": 0, "filled": 0, "gaps_left": 0})


class OneClockTest(unittest.TestCase):
    def test_rewrite_cue_timestamps_fixes_transition_and_overlay_tables(self):
        text = (
            "## TRANSITION DESIGN MAP\n"
            "| # | Timestamp | From → To | START CUE | Transition Type |\n"
            "|---|---|---|---|---|\n"
            f"| 1 | 0:00 | A → B | {sentence(0)} | Cut |\n"
            f"| 2 | 0:00 | B → C | {sentence(2)} | Zoom |\n"
            "\n## TEXT ANIMATION & OVERLAY GUIDE\n"
            "| # | ⏱️ Timecode | Element | START CUE | END CUE |\n"
            "|---|---|---|---|---|\n"
            f"| 1 | 9:59 | POPUP | {sentence(3)} | {sentence(3)} |\n"
            "| 2 | 9:59 | TITLE | a cue not in the script | end |\n"
            "\n## ANOTHER TABLE\n"
            "| # | Time | Element | START CUE | END CUE |\n"
            "|---|---|---|---|---|\n"
            f"| 1 | 9:59 | QUOTE | {sentence(4)} | {sentence(4)} |\n")
        out = ew.rewrite_cue_timestamps(text, script(30), 60)
        self.assertIn("| 1 | 0:00 | A → B |", out)
        self.assertIn("| 2 | 0:10 | B → C |", out)
        self.assertIn("| 1 | 0:15 | POPUP |", out)
        self.assertIn("| 2 | 9:59 | TITLE |", out)            # unknown cue: unchanged
        self.assertIn("| 1 | 0:20 | QUOTE |", out)

    def test_designers_put_their_tables_on_the_script_clock(self):
        transition = ("## TRANSITION DESIGN MAP\n"
                      "| # | Timestamp | From → To | START CUE | Transition Type |\n"
                      "|---|---|---|---|---|\n"
                      f"| 1 | 5:00 | A → B | {sentence(2)} | Cut |\n")
        use(self, production, "call_llm", FakeLLM(lambda *a: transition))
        state = {"refined_script": script(30), "blueprint": {"words_per_minute": 60}}
        out = production.transition_designer(state)["transition_design"]
        self.assertIn("| 1 | 0:10 | A → B |", out)

        overlay = ("## TEXT ANIMATION & OVERLAY GUIDE\n"
                   "| # | ⏱️ Timecode | Element | START CUE | END CUE |\n"
                   "|---|---|---|---|---|\n"
                   f"| 1 | 5:00 | POPUP | {sentence(3)} | {sentence(3)} |\n")
        use(self, production, "call_llm", FakeLLM(lambda *a: overlay))
        out = production.text_animation_overlay_designer(state)["text_animation_overlay"]
        self.assertIn("| 1 | 0:15 | POPUP |", out)


class DanglingReferenceTest(unittest.TestCase):
    def test_unknown_broll_numbers_are_reported(self):
        broll = HEAD + row(1, 0) + row(2, 2)
        transition = ("| # | From → To | Detail |\n|---|---|---|\n"
                      "| 1 | A → B | cut to B-roll #1 |\n"
                      "| 2 | B → C | cut to B-Roll #5 |\n")
        overlay = "| 1 | POPUP | see B-roll 9 |\n"
        problems = production.broll_reference_problems(broll, transition, overlay)
        self.assertIn("Transition/overlay row references B-roll #5, which does not exist "
                      "in the B-roll table", problems)
        self.assertIn("Transition/overlay row references B-roll #9, which does not exist "
                      "in the B-roll table", problems)
        self.assertFalse(any("#1" in p or "#2" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
