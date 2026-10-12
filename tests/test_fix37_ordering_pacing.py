"""Fixes from a real 17-minute chapter-mode run (37 B-rolls).

A. order-aware cue checks: a cue repeated in the script is accepted when order picks
   exactly one occurrence at/after the previous row's position.
B. cue-based pacing messages (gap/stacking name the lines) and the B-roll table gets
   real M:SS timestamps right after the B-roll step produces it.
C. camera-move auto-repair for 🎬/video rows.
D. repeated-passage detection fed into the integrity pass.
"""
import unittest
from unittest import mock

from medical_brain.exports import workbook as ew
from medical_brain.nodes import chapters, production, script_writing


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


def words(n, prefix="w"):
    return " ".join(f"{prefix}{i}" for i in range(n))


# ==========================================================================
# A. Order-aware cues
# ==========================================================================
class OrderAwareCueTest(unittest.TestCase):
    # "alpha beta gamma" occurs twice (word positions 6 and 10); "beta gamma w9" is unique.
    SCRIPT = "w0 w1 w2 w3 w4 w5 alpha beta gamma w9 alpha beta gamma w20 w21 w22 w23"

    def test_check_cue_rows_accepts_a_repeat_disambiguated_by_order(self):
        norm = ew.normalize_ar(ew.spoken_text(self.SCRIPT))
        self.assertIn("appears 2 times", ew.cue_problem("alpha beta gamma", norm))
        rows = [("1", "beta gamma w9", "w0 w1 w2"),
                ("2", "alpha beta gamma", "w20 w21 w22")]
        self.assertEqual(ew.check_cue_rows(rows, self.SCRIPT), [])

    def test_check_cue_rows_still_reports_when_order_cannot_disambiguate(self):
        rows = [("1", "alpha beta gamma", "w20 w21 w22")]
        problems = ew.check_cue_rows(rows, self.SCRIPT)
        self.assertTrue(any("appears 2 times" in p for p in problems))

    def test_cue_seconds_after_picks_the_later_occurrence(self):
        self.assertAlmostEqual(ew.cue_seconds("alpha beta gamma", self.SCRIPT, 60), 6.0)
        self.assertAlmostEqual(ew.cue_seconds("alpha beta gamma", self.SCRIPT, 60, after=7), 10.0)

    def test_rewrite_timecodes_is_order_aware(self):
        package = (
            "### 🎬 INTEGRATED PRODUCTION STORYBOARD\n"
            "| # | Act | ⏱️ Timecode | ▶️ START CUE | ⏹️ END CUE | Layer | Detail |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | 1 | 0:00 | beta gamma w9 | w0 w1 w2 | A-ROLL | a |\n"
            "| 2 | 1 | 0:00 | alpha beta gamma | w20 w21 w22 | A-ROLL | b |\n")
        rows = ew.parse_storyboard(ew.rewrite_timecodes(package, self.SCRIPT, 60))
        self.assertEqual(rows[0]["time"], "0:07")
        self.assertEqual(rows[1]["time"], "0:10")            # the second occurrence, not 0:06

    def test_repair_cues_leaves_an_order_disambiguated_repeat_alone(self):
        table = ("## INTEGRATED PRODUCTION STORYBOARD\n"
                 "| # | Act | Time | START CUE | END CUE | Layer | Detail |\n"
                 "|---|---|---|---|---|---|---|\n"
                 "| 1 | 1 | 0:00 | beta gamma w9 | w0 w1 w2 | A-ROLL | a |\n"
                 "| 2 | 1 | 0:10 | alpha beta gamma | w20 w21 w22 | A-ROLL | b |\n")
        text, _, _ = ew.repair_cues(table, self.SCRIPT)
        rows = ew.parse_storyboard(text)
        self.assertEqual(rows[1]["start"], "alpha beta gamma")   # unchanged: order resolves it
        self.assertEqual(ew.check_cue_rows([(r["num"], r["start"], r["end"]) for r in rows],
                                           self.SCRIPT), [])


# ==========================================================================
# B. Cue-based pacing messages + real B-roll timestamps
# ==========================================================================
class BrollPacingMessagesTest(unittest.TestCase):
    HEAD = ("| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Script Context | Type | Main Cue | AI Generation Prompt "
            "| Composition Notes | Dur |\n|---|---|---|---|---|---|---|---|---|---|\n")

    def _row(self, n, ts, start, end, prompt):
        return f'| {n} | {ts} | {start} | {end} | c | 🎬 Video | x | {prompt} | left | 4s |\n'

    def test_gap_names_both_lines_and_keeps_the_times(self):
        script = words(240, "word")
        table = self.HEAD + (
            self._row(1, "0:05", "word10 word11", "word12 word13 word14",
                      "a man checks his wrist, slow push-in")
            + self._row(2, "3:00", "word180 word181", "end two three",
                        "a man walks a street, slow dolly"))
        problems = " ".join(production.broll_engagement_problems(table, "", 10, script=script, wpm=60))
        self.assertIn("No B-roll between «word12 word13 word14» and «word180 word181»", problems)
        self.assertIn("0:10–3:00", problems)
        self.assertIn("~170s", problems)

    def test_stacking_names_both_start_cues(self):
        script = words(120, "word")
        table = self.HEAD + (
            self._row(1, "0:05", "word10 word11", "a b", "clip A of a wrist, slow push-in")
            + self._row(2, "0:05", "word14 word15", "c d", "clip B of a hand, slow dolly"))
        problems = " ".join(production.broll_engagement_problems(table, "", 5, script=script, wpm=60))
        self.assertIn("B-rolls #1 and #2", problems)
        self.assertIn("«word10 word11»", problems)
        self.assertIn("«word14 word15»", problems)
        self.assertIn("start 4s apart", problems)


class BrollStepFinalizeTest(unittest.TestCase):
    SCRIPT = words(200, "word")

    def _single_table(self):
        return ("## AI B-ROLL GENERATION PROMPTS\n### B-Roll Prompt Table\n"
                "| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |\n"
                "|---|---|---|---|---|---|\n"
                "| 1 | 9:59 | word40 word41 | word45 | 🎬 Video | a man checks his watch. Avoid: text |\n"
                "| 2 | 9:59 | word160 word161 | word165 | 🖼️ Image | a clinic still, ken burns |\n")

    def test_single_call_rewrites_timestamps_and_adds_camera_move(self):
        use(self, production, "call_llm", FakeLLM(lambda *a: self._single_table()))
        with mock.patch("builtins.print") as printed:
            out = production.broll_prompt_generator(
                {"refined_script": self.SCRIPT, "blueprint": {"words_per_minute": 60}})
        table = out["broll_prompts"]
        rows = ew.broll_rows(table)
        self.assertEqual(rows[0]["time"], "0:40")
        self.assertEqual(rows[1]["time"], "2:40")
        self.assertIn("slow push-in camera move", table)
        self.assertLess(table.index("slow push-in camera move"), table.index("Avoid"))
        self.assertTrue(any("added a camera move to 1 video B-rolls" in str(c.args)
                            for c in printed.call_args_list))
        problems = " ".join(production.broll_engagement_problems(table, "", 5,
                                                                 script=self.SCRIPT, wpm=60))
        self.assertNotIn("no camera move", problems)

    def test_chapter_mode_finalizes_each_table(self):
        bp = {"words_per_minute": 60,
              "chapters": [{"id": "C1", "title": "A", "words": 900},
                           {"id": "C2", "title": "B", "words": 900}]}
        script = f"=== A ===\n{words(900, 'a')}\n\n=== B ===\n{words(900, 'b')}"

        def answers(system, user, temperature=None, max_tokens=None):
            header = ("## AI B-ROLL GENERATION PROMPTS\n"
                      "| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |\n"
                      "|---|---|---|---|---|---|\n")
            if "id: C1" in user:
                return header + "| 1 | 9:59 | a0 a1 | a5 a6 | 🎬 Video | clip one |\n"
            return header + "| 1 | 9:59 | b0 b1 | b5 b6 | 🎬 Video | clip two |\n"

        use(self, production, "call_llm", FakeLLM(answers))
        with mock.patch("builtins.print"):
            out = production.broll_prompt_generator(
                {"refined_script": script, "blueprint": bp, "chapter_workers": 1})
        table = out["broll_prompts"]
        self.assertEqual([r["time"] for r in ew.broll_rows(table)], ["0:00", "15:00"])
        self.assertEqual(table.count("slow push-in camera move"), 2)
        self.assertEqual(out["broll_by_chapter"], {"C1": [1], "C2": [2]})


# ==========================================================================
# C. Camera-move auto-repair
# ==========================================================================
class CameraMoveRepairTest(unittest.TestCase):
    TABLE = ("| # | Timestamp | Type | AI Generation Prompt |\n"
             "|---|---|---|---|\n"
             "| 1 | 0:05 | 🎬 Video | a man checks his watch. Avoid: text |\n"
             "| 2 | 0:10 | 🎬 Video | a woman walks a street, slow dolly |\n"
             "| 3 | 0:15 | 🖼️ Image | a clinic still |\n")

    def test_inserts_before_avoid_and_skips_existing_moves(self):
        repaired, fixed = production.auto_repair_camera_moves(self.TABLE)
        rows = ew.broll_rows(repaired)
        self.assertEqual(fixed, 1)
        self.assertIn("slow push-in camera move", rows[0]["prompt"])
        self.assertLess(rows[0]["prompt"].index("slow push-in camera move"),
                        rows[0]["prompt"].index("Avoid"))
        self.assertEqual(rows[1]["prompt"], "a woman walks a street, slow dolly")
        self.assertNotIn("slow push-in camera move", rows[2]["prompt"])

    def test_appended_when_there_is_no_avoid_note(self):
        repaired, fixed = production.auto_repair_camera_moves(
            "| # | Timestamp | Type | AI Generation Prompt |\n|---|---|---|---|\n"
            "| 1 | 0:05 | 🎬 Video | a man checks his watch |\n")
        self.assertEqual(fixed, 1)
        self.assertTrue(ew.broll_rows(repaired)[0]["prompt"].endswith(", slow push-in camera move"))


# ==========================================================================
# D. Repeated passages
# ==========================================================================
SENT = "القلب بيبعت الدم لكل الجسم في دورة كاملة"     # 8 words


class RepeatedSentencesTest(unittest.TestCase):
    def test_identical_sentences_across_chapters(self):
        script = f"=== الفصل الأول ===\n{SENT}.\n\n=== الفصل الثاني ===\n{SENT}.\n"
        repeats = chapters.repeated_sentences(script)
        self.assertEqual(len(repeats), 1)
        sentence, first, second = repeats[0]
        self.assertEqual(sentence, SENT)
        self.assertEqual((first, second), ("الفصل الأول", "الفصل الثاني"))

    def test_near_duplicate_word_set_is_reported(self):
        script = f"=== A ===\n{SENT} سريعة.\n\n=== B ===\n{SENT}.\n"
        repeats = chapters.repeated_sentences(script)
        self.assertEqual(len(repeats), 1)
        self.assertEqual((repeats[0][1], repeats[0][2]), ("A", "B"))

    def test_short_sentences_are_ignored(self):
        script = "=== A ===\nكلمتين بس\n\n=== B ===\nكلمتين بس\n"
        self.assertEqual(chapters.repeated_sentences(script), [])

    def test_cues_and_stage_directions_are_ignored(self):
        script = f"=== A ===\n[الكاميرا قريبة] {SENT}.\n\n=== B ===\n{SENT}.\n"
        self.assertEqual(len(chapters.repeated_sentences(script)), 1)

    def test_opening_and_closing_labels(self):
        script = (f"{SENT}.\n\n=== الفصل الأول ===\nجملة تانية خالص مختلفه تماما.\n\n"
                  f"=== CLOSING ===\n{SENT}.\n")
        repeats = chapters.repeated_sentences(script)
        self.assertEqual((repeats[0][1], repeats[0][2]), ("OPENING", "CLOSING"))


class IntegrityRepeatsTest(unittest.TestCase):
    SCRIPT = f"=== C1 ===\n{SENT}.\n\n=== C2 ===\n{SENT}.\n"

    def test_integrity_prompt_lists_repeated_sentences(self):
        prompt = script_writing._integrity_prompt({"blueprint": {}}, self.SCRIPT, 1000)
        self.assertIn("REPEATED SENTENCES (code check)", prompt)
        self.assertIn(SENT, prompt)
        self.assertIn("C1", prompt)
        self.assertIn("C2", prompt)

    def test_integrity_prompt_has_no_block_without_repeats(self):
        prompt = script_writing._integrity_prompt({"blueprint": {}},
                                                  "=== C1 ===\nكلمة واحدة هنا\n", 100)
        self.assertNotIn("REPEATED SENTENCES", prompt)

    def test_refinement_logs_how_many_repeats_remain(self):
        bp = {"chapters": [{"id": "C1", "title": "الفصل الأول", "words": 500},
                           {"id": "C2", "title": "الفصل الثاني", "words": 500}]}
        state = {"blueprint": bp, "dialect_script": self.SCRIPT}
        answer = ({"edits": [{"chapter": "C2", "find": SENT, "replace": "نص جديد مختلف تماما",
                              "why": "repeat"}],
                   "integrity_score": 9, "summary": "ok", "issues": []}, "raw")
        use(self, script_writing, "call_llm_json", FakeLLM(lambda *a: answer))
        with mock.patch("builtins.print") as printed:
            out = script_writing.script_refinement(state)
        self.assertEqual(out["integrity_report"]["applied"], 1)
        self.assertTrue(any("0 repeated sentence(s) remain" in str(c.args)
                            for c in printed.call_args_list))


if __name__ == "__main__":
    unittest.main()
