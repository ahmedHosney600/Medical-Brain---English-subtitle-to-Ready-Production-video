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


SUBJECTS = ["nurse checking a monitor", "kettle boiling kitchen", "runner tying shoes park",
            "pharmacist shelves boxes", "grandmother knitting balcony", "cyclist crossing bridge",
            "chef slicing lemons", "child reading library", "doctor washing hands",
            "fisherman casting river", "student typing laptop", "baker shaping dough",
            "gardener watering roses", "driver adjusting mirror", "teacher writing chalkboard"]


def row(number: int, i: int, prompt: str = None) -> str:
    # Distinct pictures per row: identical prompts are near-duplicates and get re-imaged.
    prompt = prompt or f"{SUBJECTS[i % len(SUBJECTS)]}, slow push-in"
    words = sentence(i).split()
    return (f"| {number} | 9:59 | {sentence(i)} | {' '.join(words[-4:])} | "
            f"🎬 Video | {prompt} |\n")


def repair(table: str, text: str = None):
    with mock.patch("builtins.print"):
        return production.repair_broll_timeline({}, table, text or script(30), 60)


class ClusterRepairTest(unittest.TestCase):
    MOVED = "| 2 | 9:59 | WRONG CUE | WRONG END | 🎬 Video | NEW PICTURE for the new sentence, slow dolly |"

    def setUp(self):
        self.fake = use(self, production, "call_llm", FakeLLM(lambda *a: self.MOVED))

    def test_cluster_moves_to_the_next_sentence_at_least_8s_later(self):
        table = HEAD + row(1, 0) + row(2, 1) + row(3, 4)
        out, report = repair(table)
        rows = ew.broll_rows(out)
        self.assertEqual(report, {"moved": 1, "dropped": 0, "filled": 0, "gaps_left": 0})
        self.assertEqual([r["time"] for r in rows], ["0:00", "0:10", "0:20"])
        self.assertEqual(rows[1]["start"], sentence(2))       # first sentence >= 8s after #1
        self.assertEqual(rows[1]["end"], " ".join(sentence(2).split()[-4:]))
        # The moved row's picture is rewritten for its new sentence.
        self.assertIn("NEW PICTURE", out)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertIn(sentence(2), self.fake.calls[0]["user"])

    def test_a_one_word_sentence_is_never_a_cue(self):
        text = "w0 w1 w2 w3 w4. w5 w6 w7 w8 w9. ليه. " + " ".join(sentence(i) + "." for i in range(3, 30))
        table = HEAD + row(1, 0) + row(2, 1) + row(3, 6)
        out, _ = repair(table, text)
        self.assertNotIn("| ليه |", out)

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
        # With the model down the clustered row cannot get a matching picture: dropped.
        self.assertEqual(report["moved"], 0)
        self.assertEqual(report["dropped"], 1)
        self.assertEqual(report["filled"], 0)
        self.assertEqual(report["gaps_left"], 1)
        self.assertEqual([r["start"] for r in rows], [sentence(0), sentence(14)])

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
            "| # | Timestamp | START CUE | From → To | Transition Type |\n"
            "|---|---|---|---|---|\n"
            f"| 1 | 0:00 | {sentence(0)} | A → B | Cut |\n"
            f"| 2 | 0:00 | {sentence(2)} | B → C | Zoom |\n"
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
        self.assertIn("| 1 | 0:00 |", out)
        self.assertIn("| A → B | Cut |", out)
        self.assertIn("| 2 | 0:10 |", out)
        self.assertIn("| B → C | Zoom |", out)
        self.assertIn("| 1 | 0:15 | POPUP |", out)
        self.assertIn("| 2 | 9:59 | TITLE |", out)            # unknown cue: unchanged
        self.assertIn("| 1 | 0:20 | QUOTE |", out)

    def test_designers_put_their_tables_on_the_script_clock(self):
        transition = ("## TRANSITION DESIGN MAP\n"
                      "| # | Timestamp | START CUE | From → To | Transition Type |\n"
                      "|---|---|---|---|---|\n"
                      f"| 1 | 5:00 | {sentence(2)} | A → B | Cut |\n")
        use(self, production, "call_llm", FakeLLM(lambda *a: transition))
        state = {"refined_script": script(30), "blueprint": {"words_per_minute": 60}}
        out = production.transition_designer(state)["transition_design"]
        self.assertIn("| 1 | 0:10 |", out)
        self.assertIn("| A → B | Cut |", out)

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


class TrailingGapTest(unittest.TestCase):
    def test_the_tail_after_the_last_broll_is_filled_when_asked(self):
        # A real run's B-roll table stopped at 12:27 of a 16:23 script.
        from medical_brain.nodes import production as P
        script = ". ".join(" ".join(f"w{i * 10 + j}" for j in range(10)) for i in range(60)) + "."
        table = ("| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |\n|---|---|---|---|---|---|\n"
                 "| 1 | 0:00 | w0 w1 w2 w3 w4 | w5 w6 w7 w8 | 🎬 Video | close-up of a calf, slow push-in camera move |\n")
        row = "| 9 | 0:00 | x | y | 🎬 Video | a clinic hallway, slow dolly camera move, no text |"
        with mock.patch.object(P, "call_llm", lambda *a, **k: row), mock.patch("builtins.print"):
            _, without = P.repair_broll_timeline({}, table, script, 135)
            _, with_tail = P.repair_broll_timeline({}, table, script, 135, fill_tail=True)
        self.assertGreater(with_tail["filled"], without["filled"])


class EndCueFitTest(unittest.TestCase):
    HEAD = ("| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt | Duration |\n"
            "|---|---|---|---|---|---|---|\n")

    def row(self, number, start, end, duration="4s"):
        return (f"| {number} | 9:59 | {sentence(start)} | {' '.join(sentence(end).split()[-4:])} | "
                f"🎬 Video | a clinic, slow push-in | {duration} |\n")

    def test_an_end_cue_20s_after_a_4s_clip_is_pulled_in(self):
        table = self.HEAD + self.row(1, 0, 3) + self.row(2, 6, 6)       # #1 ends at 20 s
        out, _ = repair(table)
        rows = ew.broll_rows(out)
        self.assertEqual(rows[0]["end"], " ".join(sentence(0).split()[-4:]))   # 5 s ≈ 4 s clip
        self.assertIn("| 5s |", out)

    def test_a_fitting_end_cue_is_kept(self):
        table = self.HEAD + self.row(1, 0, 1, "8s") + self.row(2, 6, 6)       # 10 s for 8 s clip
        out, _ = repair(table)
        self.assertEqual(ew.broll_rows(out)[0]["end"], " ".join(sentence(1).split()[-4:]))


class DuplicateImageTest(unittest.TestCase):
    def test_the_later_of_two_identical_shots_is_re_imaged(self):
        same = "elderly father and son on a sofa at night with a blood pressure cuff, slow push-in"
        table = HEAD + row(1, 0, same) + row(2, 3) + row(3, 6, same)
        fake = use(self, production, "call_llm", FakeLLM(
            lambda *a: "| 9 | 9:59 | X | X | 🎬 Video | steady hand smoothing a deflated cuff on a kitchen table, dolly |"))
        out, report = repair(table)
        self.assertEqual(report["re_imaged"], 1)
        self.assertIn("kitchen table", out)
        self.assertEqual(out.count("sofa at night"), 1)
        self.assertIn("sofa at night", fake.calls[0]["user"])          # told what to avoid
        self.assertIn(sentence(6), out)                               # keeps its own cue


class RepeatedFamilyTest(unittest.TestCase):
    def test_family_repeats_after_the_first_are_re_imaged(self):
        table = HEAD + row(1, 0) + row(2, 3) + row(3, 6) + row(4, 9) + row(5, 12)
        audits = iter([({"repeats": [[2, 4]]}, "raw")])          # the second audit finds nothing new
        use(self, production, "call_llm_json", FakeLLM(lambda *a: next(audits, ({"repeats": []}, "raw"))))
        fake = use(self, production, "call_llm", FakeLLM(
            lambda *a: "| 9 | 9:59 | X | X | 🎬 Video | lighthouse keeper polishing a lens, dolly |"))
        out, report = repair(table)
        self.assertEqual(report["re_imaged"], 1)
        self.assertIn("lighthouse keeper", out)
        self.assertIn(SUBJECTS[3], fake.calls[0]["user"])            # told to avoid row 2's picture

    def test_a_closing_callback_to_the_opening_is_kept(self):
        table = HEAD + row(1, 0) + row(2, 3) + row(3, 6) + row(4, 9) + row(5, 12)
        use(self, production, "call_llm_json", FakeLLM(lambda *a: ({"repeats": [[1, 5]]}, "raw")))
        fake = use(self, production, "call_llm", FakeLLM(lambda *a: "| 9 | 9:59 | X | X | 🎬 Video | other |"))
        out, report = repair(table)
        self.assertNotIn("re_imaged", report)
        self.assertEqual(fake.calls, [])


class DrawingRepeatTest(unittest.TestCase):
    def test_broll_repeating_a_drawings_metaphor_is_re_imaged(self):
        table = HEAD + row(1, 0) + row(2, 3) + row(3, 6) + row(4, 9)
        guide = ("### Drawing Animations\nDrawing Animation 1: The switchboard\n"
                 "- **Subject Description**: a drawn electrical switchboard with two circuits\n")
        audits = iter([({"repeats": [], "repeats_drawing": [3]}, "raw")])
        use(self, production, "call_llm_json", FakeLLM(lambda *a: next(audits, ({"repeats": []}, "raw"))))
        fake = use(self, production, "call_llm", FakeLLM(
            lambda *a: "| 9 | 9:59 | X | X | 🎬 Video | woman stretching at sunrise, dolly |"))
        with mock.patch("builtins.print"):
            out, report = production.repair_broll_timeline({"text_animation_overlay": guide}, table, script(30), 60)
        self.assertEqual(report["re_imaged"], 1)
        self.assertIn("switchboard", fake.calls[0]["user"])


class DirectAddressTest(unittest.TestCase):
    def test_comment_question_sentence_is_blocked(self):
        sentences = production._script_sentences("كلام عادي هنا. اكتبلي في التعليقات بقالك قد إيه. كلام تاني.", 60)
        windows = production._direct_address_windows(sentences)
        self.assertEqual(len(windows), 1)
        self.assertLess(windows[0][0], sentences[1]["start_s"] + 0.01)
