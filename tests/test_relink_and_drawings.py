"""Cross-layer links survive row renumbering, and whiteboard drawings own their
window: no B-roll starts (or is gap-filled) under a drawing."""
import contextlib
import io
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


def script(sentences: int = 30) -> str:
    return " ".join(sentence(i) + "." for i in range(sentences))


def sentence(i: int) -> str:
    return " ".join(f"w{i * 5 + j}" for j in range(5))


BROLL_HEAD = ("| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |\n"
              "|---|---|---|---|---|---|\n")
TRANS_HEAD = ("| # | Timestamp | ▶️ START CUE (Arabic) | From → To | Transition Type | Rationale |\n"
              "|---|---|---|---|---|---|\n")


def broll_row(number: int, i: int, prompt: str = "a clinic, slow push-in") -> str:
    words = sentence(i).split()
    return (f"| {number} | 9:59 | {sentence(i)} | {' '.join(words[-4:])} | "
            f"🎬 Video | {prompt} |\n")


def repair(table: str, state: dict = None, text: str = None):
    with mock.patch("builtins.print"):
        return production.repair_broll_timeline(state or {}, table, text or script(), 60)


def prose_drawing(start: int, duration: float = 10) -> str:
    return ("""## TEXT ANIMATION & OVERLAY GUIDE
### Drawing Animations
#### 1. Test drawing
- **Build sequence**:
    1. **Step 1**: outline of the organ (CUE: "%s").
    2. **Step 2 (Payoff)**: the arrow lands (CUE: "%s").
- **Duration**: %g seconds total.
- **Whiteboard image prompt**: plain white whiteboard, black marker.
- **Draw-on video prompt**: a hand draws the outline, then the arrow.
""" % (sentence(start), sentence(start + 1), duration))


def table_drawing(start: int, hold: float = 10) -> str:
    return ("""## TEXT ANIMATION & OVERLAY GUIDE
| # | Timestamp | Element Type | Content (text) | ▶️ START CUE (Arabic) | Hold (s) | Linked to Transition # |
|---|---|---|---|---|---|---|
| 1 | 9:59 | WHITEBOARD DRAWING | a mechanism | %s | %g | T#1 |
""" % (sentence(start), hold))


class RelinkTest(unittest.TestCase):
    def test_broll_references_and_link_cells_are_rewritten(self):
        text = (
            "Overlay says B-roll #17, B-Roll #2, B-rolls #3 and B-roll 4. "
            "A stale link may say T#36 but that is a transition.\n"
            "| # | Timestamp | Element Type | Linked to Transition # | Linked to B-roll # | "
            "Linked to Overlay # | Notes |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | 6:29 | POPUP | T#8 | B-roll #17 | #5 | 20 mg, 2024, DO NOT OVERLAP T#36 |\n"
            "| 2 | 6:30 | QUOTE | After T#1 | 3 | #5 | B-roll #2 |\n")
        out = production.relink_references(
            text, "broll", {17: 1, 2: None, 3: 4, 4: 5})
        self.assertIn("B-roll #1, —, B-rolls #4 and B-roll 5", out)
        self.assertIn("Linked to Transition # | Linked to B-roll #", out)
        self.assertIn("T#8 | B-roll #1 | #5 |", out)          # own table/overlay links stay
        self.assertIn("After T#1 | 4 | #5 | — |", out)
        self.assertIn("| 1 | 6:29 |", out)                     # own "#" column untouched
        self.assertIn("| 2 | 6:30 |", out)
        self.assertIn("T#36", out)
        self.assertIn("20 mg, 2024", out)

    def test_transition_references_and_link_cells_are_rewritten(self):
        text = (
            "DO NOT OVERLAP T#36; after Transition #2. A B-roll #17 ref is not ours.\n"
            "| # | Timestamp | Element Type | Linked to Transition # | Linked to B-roll # |\n"
            "|---|---|---|---|---|\n"
            "| 1 | 0:03 | POPUP | T#36 | B-roll #17 |\n"
            "| 2 | 0:04 | POPUP | #2 | #17 |\n"
            "| 3 | 0:05 | POPUP | T#4, T #5 | B-roll #17 |\n")
        out = production.relink_references(text, "transition", {36: 1, 2: None, 4: 3, 5: 4})
        self.assertIn("DO NOT OVERLAP T#1; after —", out)
        self.assertIn("B-roll #17", out)                       # not a transition reference
        self.assertIn("| T#1 | B-roll #17 |", out)
        self.assertIn("| — | #17 |", out)
        self.assertIn("| T#3, T #4 | B-roll #17 |", out)
        self.assertIn("| 1 | 0:03 |", out)


class RenumberMapTest(unittest.TestCase):
    def test_repair_reports_old_to_new_and_dropped_rows(self):
        table = BROLL_HEAD + broll_row(1, 0) + broll_row(2, 1) + broll_row(3, 2)
        out, report = repair(table)
        self.assertEqual(report.mapping, {1: 1, 2: None, 3: 2})
        self.assertEqual(report["broll_map"], {1: 1, 2: None, 3: 2})
        self.assertEqual([r["num"] for r in ew.broll_rows(out)], ["1", "2"])
        self.assertEqual(production.relink_references(
            "B-roll #2 and B-roll #3", "broll", report.mapping), "— and B-roll #2")


class NodeRelinkTest(unittest.TestCase):
    def test_node_result_carries_the_relinked_documents(self):
        transition = (
            "## TRANSITION DESIGN MAP\n"
            + TRANS_HEAD
            + f"| 1 | 9:59 | {sentence(0)} | A → B | Cut | cut to B-roll #3 |\n"
            + f"| 2 | 9:59 | {sentence(10)} | B → C | Zoom | starts after T#2 |\n")
        overlay = (
            "## TEXT ANIMATION & OVERLAY GUIDE\n"
            "| # | Element Type | Content (text) | START CUE | Hold (s) | Linked to Transition # |\n"
            "|---|---|---|---|---|---|\n"
            f"| 1 | POPUP | B-roll #3 and B-roll #2; DO NOT OVERLAP T#2 | {sentence(1)} | 4 | T#2 |\n")
        answer = (BROLL_HEAD + broll_row(1, 0)
                  + broll_row(2, 1)
                  + broll_row(3, 2, "a clinic After T#2, slow push-in"))
        use(self, production, "call_llm", FakeLLM(lambda *a: answer))
        state = {"refined_script": script(), "blueprint": {"words_per_minute": 60},
                 "transition_design": transition, "text_animation_overlay": overlay,
                 "production_revision_count": 0}

        out = production.broll_prompt_generator(state)

        self.assertIn("B-roll #2", out["transition_design"])
        self.assertNotIn("cut to B-roll #3", out["transition_design"])
        self.assertIn("B-roll #2 and —", out["text_animation_overlay"])
        self.assertIn("T#3", out["text_animation_overlay"])
        self.assertIn("After T#3", out["broll_prompts"])
        self.assertIn("text_animation_overlay", out)


class DanglingLinkTest(unittest.TestCase):
    def test_stale_transition_and_bare_link_cells_are_reported(self):
        broll = BROLL_HEAD + broll_row(1, 0)
        transition = TRANS_HEAD + f"| 1 | 0:00 | {sentence(0)} | A → B | Cut | opening |\n"
        overlay = (
            "| # | Element Type | Linked to Transition # | Notes |\n"
            "|---|---|---|---|\n"
            "| 1 | POPUP | T#9 | B-roll #7 and T#1 |\n"
            "| 2 | POPUP | #8 | — |\n")
        problems = production.broll_reference_problems(broll, transition, overlay)
        self.assertIn("Transition/overlay row references B-roll #7, which does not exist "
                      "in the B-roll table", problems)
        self.assertIn("Transition/overlay row references transition #9, which does not "
                      "exist in the transition map", problems)
        self.assertTrue(any("transition #8" in p for p in problems))
        self.assertFalse(any("#1" in p for p in problems))


class DrawingWindowTest(unittest.TestCase):
    def test_a_broll_inside_a_prose_drawing_moves_after_the_window(self):
        table = BROLL_HEAD + broll_row(1, 0) + broll_row(2, 5) + broll_row(3, 14)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            out, report = production.repair_broll_timeline(
                {"text_animation_overlay": prose_drawing(4, 10)},
                table, script(), 60)
        rows = ew.broll_rows(out)
        self.assertEqual(report["moved_out_of_drawings"], 1)
        self.assertEqual([r["start"] for r in rows],
                         [sentence(0), sentence(6), sentence(14)])
        self.assertIn("🔧 moved 1 B-rolls out of whiteboard windows", output.getvalue())

    def test_a_broll_inside_a_drawing_table_row_moves_too(self):
        table = BROLL_HEAD + broll_row(1, 0) + broll_row(2, 5) + broll_row(3, 14)
        out, report = repair(table, {"text_animation_overlay": table_drawing(4, 10)})
        self.assertEqual(report["moved_out_of_drawings"], 1)
        self.assertEqual(ew.broll_rows(out)[1]["start"], sentence(6))

    def test_no_room_before_the_next_broll_drops_the_row(self):
        table = BROLL_HEAD + broll_row(1, 0) + broll_row(2, 5) + broll_row(3, 7)
        out, report = repair(table, {"text_animation_overlay": prose_drawing(4, 10)})
        self.assertEqual(report["moved_out_of_drawings"], 0)
        self.assertEqual(report["dropped"], 1)
        self.assertEqual([r["start"] for r in ew.broll_rows(out)],
                         [sentence(0), sentence(7)])

    def test_gap_fill_never_lands_inside_a_drawing_window(self):
        table = BROLL_HEAD + broll_row(1, 0) + broll_row(2, 14)
        answer = "| 9 | 9:59 | WRONG | WRONG | 🎬 Video | a corridor, slow dolly |"
        use(self, production, "call_llm", FakeLLM(lambda *a: answer))
        out, report = repair(table, {"text_animation_overlay": prose_drawing(4, 20)})
        rows = ew.broll_rows(out)
        self.assertEqual(report["filled"], 1)
        self.assertEqual(rows[1]["start"], sentence(8))        # first sentence after 20–40 s
        self.assertEqual(rows[1]["time"], "0:40")

    def test_no_drawing_specs_leaves_the_repair_unchanged(self):
        table = BROLL_HEAD + broll_row(1, 0) + broll_row(2, 5) + broll_row(3, 14)
        plain, report = repair(table)
        other, again = repair(table, {"text_animation_overlay": "No drawings needed."})
        self.assertEqual(other, plain)
        self.assertEqual(report.mapping, again.mapping)
        self.assertEqual(report["moved_out_of_drawings"], 0)
        self.assertEqual(again["moved_out_of_drawings"], 0)


if __name__ == "__main__":
    unittest.main()
