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
                         [sentence(0), sentence(2), " ".join(sentence(5).split()[:4])])
        # The cut back is where the END CUE finishes (the next words), not where it begins.
        self.assertEqual([r["cells"][1] for r in rows], ["0:00", "0:10", "0:25"])
        self.assertIn("Hook → B-roll #1", out)          # chained from the previous row's state
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

        self.assertEqual(added, 1)                       # only the cut back after sentence(0)
        rows = transition_rows(out)
        self.assertEqual([r["cells"][1] for r in rows], ["0:00", "0:05", "0:30"])
        self.assertEqual(rows[2]["cells"][2], sentence(6))
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
        transition = transition_table(transition_row(1, 0, "Black screen → Presenter A-roll", "open"),
                                      transition_row(2, 3, "B-roll #9 → Talking Head", "back"))
        broll = broll_table(broll_row(1, 0, 2))
        with mock.patch("builtins.print"):
            out, added = sync(transition, broll)
        froms = [r["cells"][3] for r in transition_rows(out)]
        self.assertIn("Black screen → B-roll #1", froms)
        self.assertNotIn("Talking Head → Talking Head", froms)   # stale return dropped by the chain
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


class ChapterTableRepairTest(unittest.TestCase):
    def test_headerless_chapter_table_gets_its_header_back(self):
        text = (transition_table(transition_row(1, 0, "A → B"))
                + "\n### Transition Density Summary\n\n## TRANSITION DESIGN MAP\n### Transition Table\n"
                + transition_row(18, 6, "B → C"))
        fixed = production.restore_missing_headers(text)
        self.assertEqual(sum(len(b["rows"]) for b in production._transition_blocks(fixed)), 2)
        self.assertEqual(production.restore_missing_headers(fixed), fixed)

    def test_only_one_density_summary_remains(self):
        summary = "\n### Transition Density Summary\n\n| Act | Total |\n|---|---|\n| x | 9 |\n"
        text = (transition_table(transition_row(1, 0, "A → B")) + summary
                + "\n" + transition_table(transition_row(2, 7, "B → C")) + summary)
        out = production.rebuild_density_summary(text, script(), 60)
        self.assertEqual(out.count("Transition Density Summary"), 1)
        self.assertNotIn("| x | 9 |", out)

    def test_overlay_cue_not_in_script_is_re_anchored_by_its_rare_words(self):
        text_script = ("الضغط عالي شوية النهارده. خليك هادي واشرب مية. "
                       "لو معاه وجع في الصدر او ضيق نفس او تنميل روح المستشفى. شكرا ليكم.")
        row = ("| 1 | 8:50 | WARNING BOX | ألم بالصدر • ضيق نفس • تنميل | لو فيه أعراض | وطوارئ على طول. "
               "| center | fade | 5 | fade | — | x |\n")
        with mock.patch("builtins.print"):
            out = production.relocate_unfound_overlay_cues(overlay_table(row), text_script, 60)
        self.assertIn("| لو معاه وجع في الصدر", out)
        found = overlay_table(row.replace("لو فيه أعراض", "الضغط عالي شوية"))
        self.assertEqual(production.relocate_unfound_overlay_cues(found, text_script, 60), found)


class LinkRecomputeTest(unittest.TestCase):
    def test_links_follow_the_clock_and_dead_refs_go(self):
        transition = transition_table(transition_row(1, 0, "A → B"), transition_row(2, 6, "B → C"))
        overlay = overlay_table(
            f"| 1 | 9:59 | POPUP | نص | {sentence(6)} | {end_cue(6)} | top | fade | 3 | fade | T#77 | DO NOT OVERLAP T#36 |\n",
            f"| 2 | 9:59 | POPUP | نص | {sentence(3)} | {end_cue(3)} | top | fade | 3 | fade | T#1 | x |\n")
        broll = broll_table(broll_row(1, 0, 1))
        with mock.patch("builtins.print"):
            ov, b, t = production.recompute_link_columns(overlay, broll, transition, script(), 60)
        rows = [line for line in ov.splitlines() if line.startswith("| 1 ") or line.startswith("| 2 ")]
        self.assertIn("| T#2 |", rows[0])                  # same moment as transition 2 (sentence 6)
        self.assertIn("| — |", rows[1])                    # nothing within ±3 s of sentence 3
        self.assertNotIn("T#36", ov)                       # dead inline reference removed
        self.assertNotIn("T#77", ov)


class PresenterMoveAndIdTest(unittest.TestCase):
    def test_presenter_move_under_a_broll_goes_and_one_outside_stays(self):
        transition = transition_table(
            transition_row(1, 0, "Talking Head (Wide) → Talking Head (Punch-in)", "reframe"),
            transition_row(2, 6, "Talking Head (Medium) → Talking Head (Close-up)", "push"))
        broll = broll_table(broll_row(1, 0, 1))
        with mock.patch("builtins.print"):
            out, added = sync(transition, broll)
        froms = [r["cells"][3] for r in transition_rows(out)]
        self.assertNotIn("Talking Head (Wide) → Talking Head (Punch-in)", froms)   # hidden under B-roll #1
        self.assertTrue(any(f.endswith("→ Talking Head (Close-up)") for f in froms))   # presenter on screen
        self.assertIn("Talking Head → B-roll #1", froms)

    def test_a_wrong_broll_number_at_an_edge_is_corrected(self):
        transition = transition_table(transition_row(1, 0, "Talking Head → B-roll #20", "clinic"))
        broll = broll_table(broll_row(1, 0, 1))
        with mock.patch("builtins.print"):
            out, _ = sync(transition, broll)
        self.assertIn("Talking Head → B-roll #1", [r["cells"][3] for r in transition_rows(out)])


class GraphicOwnsItsMomentTest(unittest.TestCase):
    def test_a_cut_to_a_designed_graphic_is_not_relabelled(self):
        transition = transition_table(transition_row(1, 0, "A-roll → 2D Animation", "graphic"))
        with mock.patch("builtins.print"):
            out, _ = sync(transition, broll_table(broll_row(1, 0, 1)))
        self.assertIn("A-roll → 2D Animation", [r["cells"][3] for r in transition_rows(out)])

    def test_broll_repair_moves_a_broll_off_a_designed_graphic(self):
        from medical_brain.nodes import production as P
        transition = transition_table(transition_row(1, 6, "Talking Head → Dual-Bar Chart", "stat"))
        windows = P._designed_graphic_windows(transition, script(), 60)
        self.assertEqual(windows, [(27.0, 36.0)])                  # sentence 6 starts at 30 s


class CollisionTest(unittest.TestCase):
    def test_designed_rows_on_one_moment_are_reported(self):
        transition = transition_table(transition_row(1, 4, "Hose → 3D Artery", "a"),
                                      transition_row(2, 4, "3D Artery → Talking Head", "b"),
                                      transition_row(3, 8, "Talking Head → Split-Screen", "c"))
        found = production.transition_collisions(transition, script(), 60)
        self.assertEqual(len(found), 1)
        self.assertIn("2 transitions on the same moment (0:20)", found[0])


class GraphicDrawingOverlapTest(unittest.TestCase):
    def test_graphic_held_across_a_drawing_is_reported(self):
        guide = ("### Drawing Animations\nDrawing Animation 1: Hose\n"
                 f"* Step 1: Narration Cue: «{sentence(6)}»\n* **Total: 12s**\n* **Whiteboard Image Prompt**: hose\n")
        transition = transition_table(transition_row(1, 4, "Talking Head → 3D Artery Animation", "a"),
                                      transition_row(2, 10, "3D Artery Animation → Talking Head", "b"))
        found = production.graphic_drawing_overlaps(transition, guide, script(), 60)
        self.assertEqual(len(found), 1)
        self.assertIn("3D Artery Animation", found[0])
        entry = transition_table(transition_row(1, 6, "Talking Head → Analogy Graphic", "the drawing"))
        self.assertEqual(production.graphic_drawing_overlaps(entry, guide, script(), 60), [])


class GraphicBecomesDrawingTest(unittest.TestCase):
    def test_overlapping_graphic_is_renamed_to_the_drawing(self):
        guide = ("### Drawing Animations\nDrawing Animation 1: Stomach gate\n"
                 f"* Step 1: Narration Cue: «{sentence(6)}»\n* **Total: 12s**\n* **Whiteboard Image Prompt**: gate\n")
        transition = transition_table(transition_row(1, 5, "Talking Head → Animated Digestive Diagram", "a"),
                                      transition_row(2, 9, "Animated Digestive Diagram → Talking Head", "b"))
        with mock.patch("builtins.print"):
            out, _ = sync(transition, broll_table(broll_row(1, 0, 0)), state={"text_animation_overlay": guide})
        froms = [r["cells"][3] for r in transition_rows(out)]
        self.assertTrue(any(f.endswith("→ Whiteboard Drawing 1") for f in froms))
        self.assertIn("Whiteboard Drawing 1 → Talking Head", froms)
        self.assertEqual(production.graphic_drawing_overlaps(out, guide, script(), 60), [])


class ChainStatesTest(unittest.TestCase):
    def test_from_follows_the_previous_to_and_noop_rows_go(self):
        transition = transition_table(transition_row(1, 0, "Talking Head → Split-Screen", "a"),
                                      transition_row(2, 3, "Talking Head → B-roll #9", "b"),
                                      transition_row(3, 6, "B-roll #9 → Talking Head", "c"),
                                      transition_row(4, 8, "B-roll #2 → Talking Head", "stale"))
        out = production.chain_transition_states(transition)
        froms = [r["cells"][3] for r in transition_rows(out)]
        self.assertEqual(froms, ["Talking Head → Split-Screen", "Split-Screen → B-roll #9",
                                 "B-roll #9 → Talking Head"])


class ZeroLengthFlashTest(unittest.TestCase):
    def test_in_and_out_on_the_same_moment_are_dropped(self):
        transition = transition_table(transition_row(1, 0, "Talking Head → Split-Screen", "a"),
                                      transition_row(2, 4, "Split-Screen → Drawing 2", "b"),
                                      transition_row(3, 4, "Drawing 2 → Split-Screen", "c"),
                                      transition_row(4, 8, "Split-Screen → Talking Head", "d"))
        with mock.patch("builtins.print"):
            out = production.drop_zero_length_flashes(transition, script(), 60)
        froms = [r["cells"][3] for r in transition_rows(out)]
        self.assertNotIn("Split-Screen → Drawing 2", froms)
        self.assertEqual(len(froms), 2)


class TransitionsDesignedLastTest(unittest.TestCase):
    def test_prompt_lists_the_brolls_and_drawings_already_planned(self):
        guide = ("### Drawing Animations\nDrawing Animation 1: Gate\n"
                 f"* Step 1: Narration Cue: «{sentence(6)}»\n* **Total: 12s**\n* **Whiteboard Image Prompt**: gate\n")
        state = {"refined_script": script(), "blueprint": {"words_per_minute": 60},
                 "broll_prompts": broll_table(broll_row(1, 2, 3)), "text_animation_overlay": guide}
        block = production.visual_events_block(state)
        self.assertIn("B-roll #1 starts", block)
        self.assertIn("Whiteboard Drawing 1 on screen", block)
        fake = use(self, production, "call_llm", FakeLLM(lambda *a: transition_table(transition_row(1, 0, "A → B"))))
        with mock.patch("builtins.print"):
            out = production.transition_designer(state)
        self.assertIn("VISUAL EVENTS ALREADY ON THE TIMELINE", fake.calls[0]["user"])
        self.assertIn("B-roll #1", out["transition_design"])            # synced to the B-roll table

    def test_production_runs_overlays_then_broll_then_transitions(self):
        from medical_brain.graph import _STATIC_NEXT
        self.assertEqual(_STATIC_NEXT["text_animation_overlay_designer"], "broll_prompt_generator")
        self.assertEqual(_STATIC_NEXT["broll_prompt_generator"], "transition_designer")
        self.assertEqual(_STATIC_NEXT["transition_designer"], "production_quality_critique")


class FuzzyFlashAndDoctorTest(unittest.TestCase):
    def test_warning_card_in_and_out_on_one_moment_is_dropped(self):
        transition = transition_table(transition_row(1, 0, "Talking Head → B-roll #1", "a"),
                                      transition_row(2, 4, "Talking Head CU → Warning Card (Beta-Blockers)", "b"),
                                      transition_row(3, 4, "Warning Card → Talking Head", "c"))
        with mock.patch("builtins.print"):
            out = production.drop_zero_length_flashes(transition, script(), 60)
        self.assertEqual(len(transition_rows(out)), 1)

    def test_stand_in_doctor_pattern(self):
        self.assertTrue(production._STAND_IN_DOCTOR_RE.search("An Egyptian doctor gesturing at a desk, slow push-in"))
        self.assertFalse(production._STAND_IN_DOCTOR_RE.search("the doctor's hand holding a pill strip, macro"))
        self.assertTrue(production._DIRECT_ADDRESS_RE.search("أهلاً بيكم، أنا د. أحمد حسني"))


class EarlyExitTest(unittest.TestCase):
    def test_exit_a_second_after_entry_moves_to_the_broll_end(self):
        transition = transition_table(transition_row(1, 2, "Talking Head → B-roll #1", "in"),
                                      transition_row(2, 2, "B-roll #1 → Talking Head", "out too soon"))
        broll = broll_table(broll_row(1, 2, 3))                       # clip runs sentence 2 → end of 3
        with mock.patch("builtins.print"):
            out, _ = sync(transition, broll)
        rows = transition_rows(out)
        exit_row = [r for r in rows if r["cells"][3].startswith("B-roll #1")][0]
        self.assertEqual(exit_row["cells"][2], " ".join(sentence(4).split()[:4]))


class EndCueTimingTest(unittest.TestCase):
    def test_a_repeated_end_cue_resolves_after_the_clip_start(self):
        # sentence 1 is said again as sentence 8: the clip starting at sentence 7 ends there.
        text = " ".join((sentence(1) if i == 8 else sentence(i)) + "." for i in range(12))
        rows = [{"num": "1", "start": sentence(7), "end": end_cue(1)}]
        starts, ends = production._broll_row_times(rows, text, 60)
        self.assertEqual(starts, [35.0])
        self.assertEqual(ends, [45.0])                 # after the second occurrence finishes

    def test_phantom_rows_naming_a_far_broll_are_replaced(self):
        transition = transition_table(transition_row(1, 0, "Talking Head → B-roll #1", "in"),
                                      transition_row(2, 1, "B-roll #1 → Talking Head", "out"),
                                      transition_row(3, 6, "Talking Head → B-roll #9", "ghost"),
                                      transition_row(4, 7, "B-roll #9 → Talking Head", "ghost out"))
        broll = broll_table(broll_row(1, 0, 0))
        with mock.patch("builtins.print"):
            out, _ = sync(transition, broll)
        self.assertNotIn("B-roll #9", out)
        self.assertNotIn("ghost", out)

    def test_back_to_back_clips_become_one_cut(self):
        transition = transition_table(transition_row(1, 0, "Talking Head → B-roll #1", "in"))
        broll = broll_table(broll_row(1, 2, 3), broll_row(2, 4, 5))   # #1 ends where #2 starts
        with mock.patch("builtins.print"):
            out, _ = sync(transition, broll)
        moves = [r["cells"][3] for r in transition_rows(out)]
        self.assertIn("B-roll #1 → B-roll #2", moves)
        self.assertIn("B-roll #2 → Talking Head", moves)

    def test_same_visual_tells_broll_numbers_apart(self):
        self.assertFalse(production._same_visual("B-roll #2", "B-roll #3"))
        self.assertTrue(production._same_visual("B-roll #2", "Hook B-roll #2"))
        self.assertTrue(production._same_visual("Talking Head CU", "Talking Head"))


class OverlayOffDrawingTest(unittest.TestCase):
    def test_cards_inside_a_drawing_move_after_it_and_queue(self):
        guide = overlay_table(
            f"| 1 | 9:59 | WHITEBOARD DRAWING | diagram | {sentence(2)} | {end_cue(3)} | full | draw | 10 | cut | — | x |\n",
            f"| 2 | 9:59 | WARNING BOX | تحذير مهم | {sentence(3)} | {end_cue(3)} | center | fade | 4 | fade | — | x |\n",
            f"| 3 | 9:59 | TOP-RIGHT POPUP | DVT | {sentence(3)} | {end_cue(3)} | top | fade | 3 | fade | — | x |\n")
        with mock.patch("builtins.print"):
            out = production.move_overlays_out_of_drawings(guide, script(), 60)
        cues = [production._table_cells(line)[4] for line in out.splitlines()[3:] if line.startswith("|")]
        self.assertEqual(cues[0], sentence(2))                       # the drawing stays
        self.assertEqual(cues[1], sentence(4))                       # first sentence after 0:20
        self.assertEqual(cues[2], sentence(5))                       # queued after the warning
        self.assertEqual(production.move_overlays_out_of_drawings(out, script(), 60), out)


class ClipWordsTest(unittest.TestCase):
    def test_clip_words_span_start_to_end_cue_and_reach_the_prompt(self):
        layout = production._broll_layout(broll_table(broll_row(1, 3, 4)))
        norm = production.ew.normalize_ar(production.ew.spoken_text(script()))
        self.assertEqual(production._clip_words(layout["rows"][0], layout, norm),
                         " ".join(sentence(3).split() + sentence(4).split()))
        seen = []
        use(self, production, "call_llm", FakeLLM(lambda s, u, *a, **k: seen.append(u) or "| 1 | 0:00 | a | b | c | d |"))
        production._ask_gap_row(layout, layout["rows"], 3, production._script_sentences(script(), 60),
                                focus="w15 w16 w17")
        self.assertIn("«w15 w16 w17»", seen[0])
        self.assertIn("context only", seen[0])


class DrawingHoldParseTest(unittest.TestCase):
    def test_decimal_hold_is_seconds_not_digits(self):
        guide = overlay_table(
            f"| 1 | 9:59 | DRAWING ANIMATION 1 | spec | {sentence(2)} | {end_cue(3)} | full | cut | 12.0s | cut | — | x |\n")
        self.assertEqual(production._overlay_table_drawing_windows(guide, script(), 60), [(10.0, 22.0)])


class MoveOnCutTest(unittest.TestCase):
    def test_presenter_move_on_the_frame_of_a_cut_is_dropped(self):
        transition = transition_table(transition_row(1, 2, "B-roll #1 → Talking Head", "back"),
                                      transition_row(2, 2, "Talking Head → Talking Head (Close)", "punch-in"),
                                      transition_row(3, 6, "Talking Head → Talking Head (Medium)", "reframe later"))
        with mock.patch("builtins.print"):
            out = production.drop_moves_on_cuts(transition, script(), 60)
        self.assertNotIn("punch-in", out)
        self.assertIn("reframe later", out)


class ProseDrawingFormatTest(unittest.TestCase):
    def test_exact_spoken_line_format_and_next_chapter_heading(self):
        guide = ("### Drawing Animations\n\n#### Drawing Animation 1: Pump\n- **Build sequence**:\n"
                 "  - *Step 1 (0:10 – 0:15)*: hand draws a pump\n"
                 f"    *Spoken beat*: `«Pain is muted outside»`\n"
                 f"    *Exact spoken line*: `«{sentence(2)}»`\n"
                 f"  - *Step 2*: payoff\n    *Exact spoken line*: `«{sentence(3)}»`\n"
                 "- **Whiteboard image prompt**: a pump\n\n=== Chapter 2 ===\n### Drawing Animations\n\n"
                 f"#### Drawing Animation 2: Switch\n    *Exact spoken line*: `«{sentence(8)}»`\n"
                 "- **Whiteboard image prompt**: a switch\n")
        windows = production._drawing_windows(guide, script(), 60)
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0][0], 10.0)
        self.assertEqual(windows[1][0], 40.0)


class DrawingCutsTest(unittest.TestCase):
    def test_map_cuts_into_and_out_of_each_drawing(self):
        guide = ("### Drawing Animations\n#### Drawing Animation 1: Pump\n"
                 f"  *Exact spoken line*: «{sentence(4)}»\n  *Exact spoken line*: «{sentence(6)}»\n"
                 "- Duration: 12s total\n- **Whiteboard image prompt**: a pump\n")
        transition = transition_table(transition_row(1, 0, "Talking Head → B-roll #1", "in"),
                                      transition_row(2, 5, "Talking Head → Talking Head (Close)", "reframe inside"))
        broll = broll_table(broll_row(1, 0, 1))
        with mock.patch("builtins.print"):
            out, _ = sync(transition, broll, state={"text_animation_overlay": guide})
        moves = [(r["cells"][1], r["cells"][3]) for r in transition_rows(out)]
        self.assertIn(("0:20", "Talking Head → Whiteboard Drawing 1"), moves)
        self.assertTrue(any(m.startswith("Whiteboard Drawing 1 →") and t >= "0:32" for t, m in moves))
        self.assertNotIn("reframe inside", out)
