"""The transition map shares the script's clock with the final B-roll table: its rows
carry a START CUE, every final B-roll cut gets a transition, and warning cards are
never cut away mid-hold."""
import unittest
from pathlib import Path
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


def script(sentences: int = 12) -> str:
    return " ".join(sentence(i) + "." for i in range(sentences))


def sentence(i: int) -> str:
    return " ".join(f"w{i * 5 + j}" for j in range(5))


def end_cue(i: int) -> str:
    return " ".join(sentence(i).split()[-4:])


BROLL_HEAD = ("| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |\n"
              "|---|---|---|---|---|---|\n")
TRANS_HEAD = ("| # | Timestamp | ▶️ START CUE (Arabic) | From → To | Transition Type | "
              "Direction | Easing | Duration (ms) | Range | Rationale |\n"
              "|---|---|---|---|---|---|---|---|---|---|\n")
OVERLAY_HEAD = ("| # | Timestamp | Element Type | Content (text) | ▶️ START CUE (Arabic) | "
                "⏹️ END CUE (Arabic) | Screen Position | Entry Animation | Hold (s) | "
                "Exit Animation | Linked to Transition # | Purpose |\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|\n")


def broll_row(number: int, start: int, end: int) -> str:
    return (f"| {number} | 9:59 | {sentence(start)} | {end_cue(end)} | "
            f"🎬 Video | a clinic, slow push-in |\n")


def broll_table(*rows: str) -> str:
    return "## AI B-ROLL GENERATION PROMPTS\n" + BROLL_HEAD + "".join(rows)


def transition_row(number: int, cue_index: int, from_to: str = "Pre-roll → Hook",
                   rationale: str = "opening") -> str:
    return (f"| {number} | 9:59 | {sentence(cue_index)} | {from_to} | Cut | — | — | "
            f"0 | — | {rationale} |\n")


def transition_table(*rows: str) -> str:
    return "## TRANSITION DESIGN MAP\n" + TRANS_HEAD + "".join(rows)


def overlay_table(*rows: str) -> str:
    return "## TEXT ANIMATION & OVERLAY GUIDE\n" + OVERLAY_HEAD + "".join(rows)


def warning_row(cue_index: int, hold: float) -> str:
    return (f"| 1 | 9:59 | WARNING BOX | تحذير | {sentence(cue_index)} | {end_cue(cue_index)} | "
            f"center | fade-in | {hold:g} | fade-out | — | safety |\n")


def transition_rows(text: str) -> list:
    blocks = production._transition_blocks(text)
    return blocks[0]["rows"] if blocks else []


def cells_for(text: str, index: int) -> list:
    return transition_rows(text)[index]["cells"]


def sync(transition: str, broll: str, state: dict = None, script_text: str = None,
         broll_out: list = None):
    return production.sync_transition_map(
        state or {}, transition, broll, script_text or script(), 60, broll_out=broll_out)


class TransitionPromptTest(unittest.TestCase):
    def test_prompt_table_has_the_start_cue_column(self):
        prompt = (Path(__file__).resolve().parents[1] / "prompts" / "transition_designer.md").read_text()
        self.assertIn("| # | Timestamp | ▶️ START CUE (Arabic) | From → To | Transition Type | "
                      "Direction | Easing | Duration (ms) | Range | Rationale |", prompt)
        self.assertIn("4–8 CONSECUTIVE spoken words", prompt)
        self.assertNotIn("| # | Timestamp | From → To | Transition Type |", prompt)

    def test_chapter_prompt_asks_for_the_start_cue(self):
        state = {"refined_script": script(), "blueprint": {"words_per_minute": 60}}
        spans = [{"id": "C1", "title": "A", "text": script(6), "start_s": 0, "end_s": 30}]
        prompt = production._transition_chapter_prompt(state, spans, 0, "", "")
        self.assertIn("START CUE", prompt)
        self.assertIn("4–8 consecutive spoken words", prompt)

    def test_designer_is_re_timed_from_the_start_cue(self):
        answer = transition_table(transition_row(1, 2).replace("9:59", "5:00"))
        use(self, production, "call_llm", FakeLLM(lambda *a: answer))
        out = production.transition_designer(
            {"refined_script": script(), "blueprint": {"words_per_minute": 60}})
        self.assertIn(f"| 1 | 0:10 | {sentence(2)} |", out["transition_design"])


class SyncBrollCutsTest(unittest.TestCase):
    def test_missing_cuts_are_added_sorted_and_renumbered(self):
        transition = transition_table(transition_row(1, 0))
        broll = broll_table(broll_row(1, 2, 4))
        out, added = sync(transition, broll)

        self.assertEqual(added, 2)
        rows = transition_rows(out)
        self.assertEqual([r["cells"][0] for r in rows], ["1", "2", "3"])
        self.assertEqual([r["cells"][2] for r in rows],
                         [sentence(0), sentence(2), end_cue(4)])
        self.assertEqual([r["cells"][1] for r in rows], ["0:00", "0:10", "0:21"])
        self.assertIn("Talking Head → B-roll #1", out)
        self.assertIn("B-roll #1 → Talking Head", out)
        self.assertIn("Code-added: back to the presenter", out)

    def test_nothing_is_added_when_a_transition_already_covers_the_cut(self):
        transition = transition_table(transition_row(1, 2), transition_row(2, 4))
        broll = broll_table(broll_row(1, 2, 4))
        out, added = sync(transition, broll)

        self.assertEqual(added, 0)
        self.assertEqual(len(transition_rows(out)), 2)
        self.assertNotIn("Code-added", out)

    def test_sync_is_idempotent(self):
        transition = transition_table(transition_row(1, 0))
        broll = broll_table(broll_row(1, 2, 4))
        first, added = sync(transition, broll)
        self.assertEqual(added, 2)
        second, again = sync(first, broll)
        self.assertEqual(again, 0)
        self.assertEqual(second, first)


class WarningHoldTest(unittest.TestCase):
    def test_a_transition_inside_a_warning_hold_moves_to_the_first_sentence_after_it(self):
        transition = transition_table(transition_row(1, 0), transition_row(2, 5))
        broll = broll_table(broll_row(1, 0, 0))
        state = {"text_animation_overlay": overlay_table(warning_row(4, 10))}
        out, added = sync(transition, broll, state=state)

        self.assertEqual(added, 0)                       # sentence(0) covers the B-roll
        rows = transition_rows(out)
        self.assertEqual([r["cells"][1] for r in rows], ["0:00", "0:30"])
        self.assertEqual(rows[1]["cells"][2], sentence(6))
        self.assertNotIn(sentence(5), out)

    def test_a_code_added_broll_cut_moves_the_broll_start_instead(self):
        transition = transition_table(transition_row(1, 0))
        broll = broll_table(broll_row(1, 5, 7))
        state = {"text_animation_overlay": overlay_table(warning_row(4, 10))}
        moved_broll = []
        out, added = sync(transition, broll, state=state, broll_out=moved_broll)

        self.assertEqual(added, 2)
        rows = transition_rows(out)
        added_cut = next(r for r in rows if "Code-added: cut to B-roll #1" in r["cells"][-1])
        self.assertEqual(added_cut["cells"][2], sentence(6))
        self.assertEqual(added_cut["cells"][1], "0:30")

        row = ew.broll_rows(moved_broll[-1])[0]
        self.assertEqual(row["start"], sentence(6))
        self.assertEqual(row["time"], "0:30")


class NodeSyncTest(unittest.TestCase):
    def test_single_mode_result_carries_the_updated_transition_map(self):
        answer = broll_table(broll_row(1, 2, 4))
        use(self, production, "call_llm", FakeLLM(lambda *a: answer))
        state = {"refined_script": script(), "blueprint": {"words_per_minute": 60},
                 "transition_design": transition_table(transition_row(1, 0)),
                 "text_animation_overlay": "", "production_revision_count": 0}
        out = production.broll_prompt_generator(state)

        self.assertIn("transition_design", out)
        self.assertIn("Code-added: cut to B-roll #1", out["transition_design"])
        self.assertIn("Code-added: back to the presenter", out["transition_design"])

    def test_chapter_mode_result_carries_the_updated_transition_map(self):
        bp = {"words_per_minute": 120,
              "chapters": [{"id": "C1", "title": "A", "words": 900},
                           {"id": "C2", "title": "B", "words": 900}]}
        a = " ".join(f"a{i}" for i in range(900))
        b = " ".join(f"b{i}" for i in range(900))
        script_text = f"=== A ===\n{a}\n\n=== B ===\n{b}"

        def answers(system, user, temperature=None, max_tokens=None):
            if "id: C1" in user:
                return (broll_table("| 1 | 9:59 | a10 a11 a12 a13 | a20 a21 a22 a23 | "
                                    "🎬 Video | clip one |\n"))
            return (broll_table("| 1 | 9:59 | b10 b11 b12 b13 | b20 b21 b22 b23 | "
                                "🎬 Video | clip two |\n"))

        use(self, production, "call_llm", FakeLLM(answers))
        use(self, production, "finalize_broll_table",
            lambda state, table, after=0, repair=True: table)
        state = {"refined_script": script_text, "blueprint": bp, "chapter_workers": 1,
                 "transition_design": transition_table(transition_row(1, 0)),
                 "text_animation_overlay": "", "production_revision_count": 0}
        out = production.broll_prompt_generator(state)

        self.assertIn("transition_design", out)
        self.assertIn("Code-added: cut to B-roll #1", out["transition_design"])
        self.assertIn("Code-added: cut to B-roll #2", out["transition_design"])
        self.assertEqual(len(transition_rows(out["transition_design"])), 5)


if __name__ == "__main__":
    unittest.main()


class RelabelAndDensityTest(unittest.TestCase):
    def test_a_row_at_a_broll_edge_that_names_no_broll_is_pointed_at_it(self):
        transition = transition_table(transition_row(1, 0, "A-roll → 2D Animation", "graphic"),
                                      transition_row(2, 3, "B-roll #9 → Talking Head", "back"))
        broll = broll_table(broll_row(1, 0, 2))
        with mock.patch("builtins.print"):
            out, added = sync(transition, broll)
        froms = [r["cells"][3] for r in transition_rows(out)]
        self.assertIn("A-roll → B-roll #1", froms)
        self.assertIn("B-roll #9 → Talking Head", froms)       # names a B-roll: left alone
        with mock.patch("builtins.print"):
            again, _ = sync(out, broll)
        self.assertEqual(again, out)

    def test_density_summary_is_rebuilt_from_the_table(self):
        text = ("=== أ ===\n" + script(6) + "\n=== ب ===\n" + " ".join(sentence(i) + "." for i in range(6, 12)))
        transition = (transition_table(transition_row(1, 0, "A → B"), transition_row(2, 7, "B → C"))
                      + "\n### Transition Density Summary\n\n| Act | Total |\n|---|---|\n| all | 46 |\n\nafter")
        out = production.rebuild_density_summary(transition, text, 60)
        self.assertNotIn("| all | 46 |", out)
        self.assertIn("| أ | 0:00–0:30 | 1 | 1 | 0 |", out)
        self.assertIn("| ب | 0:30–1:00 | 1 | 1 | 0 |", out)
        self.assertIn("**2**", out)
        self.assertTrue(out.rstrip().endswith("after"))


class OverlayReadTimeTest(unittest.TestCase):
    def test_short_holds_grow_to_reading_time(self):
        long_text = " ".join(["كلمة"] * 30)
        table = overlay_table(
            f"| 1 | 0:10 | WARNING BOX | {long_text} | {sentence(1)} | {end_cue(1)} | center | fade | 5.5 | fade | — | x |\n",
            f"| 2 | 0:20 | LOWER THIRD | د. أحمد | {sentence(3)} | {end_cue(3)} | bottom | fade | 4s | fade | — | x |\n")
        with mock.patch("builtins.print"):
            out = production.fix_overlay_read_times(table)
        rows = [line for line in out.splitlines() if line.startswith("| 1 ") or line.startswith("| 2 ")]
        self.assertIn("| 12 |", rows[0])                     # 30 words / 2.5 = 12 s
        self.assertIn("| 4s |", rows[1])                     # already long enough
        self.assertEqual(production.fix_overlay_read_times(out), out)
