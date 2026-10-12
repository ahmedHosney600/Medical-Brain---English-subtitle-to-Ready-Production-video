"""Real storyboard/B-roll timing: timecodes come from where each row's START CUE sits
in the spoken script, and the B-roll engagement checks run on that real timeline
(60s gaps, stacked starts, near-duplicate prompts) instead of the model's plan."""
import unittest
from unittest import mock

from medical_brain.exports import workbook as ew
from medical_brain.nodes import final_package, production


def words_script(n: int, prefix: str = "word") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


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


# --------------------------------------------------------------------------
# 1. cue_seconds
# --------------------------------------------------------------------------
class CueSecondsTest(unittest.TestCase):
    SCRIPT = words_script(100)

    def test_position_math_from_the_spoken_words_before_the_cue(self):
        self.assertAlmostEqual(ew.cue_seconds("word40 word41", self.SCRIPT, 60), 40.0)
        self.assertAlmostEqual(ew.cue_seconds("word40 word41", self.SCRIPT, 120), 20.0)
        self.assertAlmostEqual(ew.cue_seconds("word0", self.SCRIPT, 60), 0.0)

    def test_not_found_and_empty_are_none(self):
        self.assertIsNone(ew.cue_seconds("word500 word501", self.SCRIPT, 60))
        self.assertIsNone(ew.cue_seconds("", self.SCRIPT, 60))
        self.assertIsNone(ew.cue_seconds("   ", self.SCRIPT, 60))

    def test_matches_find_cue_after_normalisation(self):
        script = "أهلاً، بيكم في الفيديو ده. النقطة الأساسية هنا واضحة جداً."
        cue = "النقطة الأساسية هنا"
        norm = ew.normalize_ar(ew.spoken_text(script))
        self.assertGreater(ew.find_cue(cue, norm), 0)
        self.assertIsNotNone(ew.cue_seconds(cue, script, 135))
        self.assertIsNone(ew.cue_seconds("كلام مش موجود خالص", script, 135))

    def test_blueprint_wpm_default_and_clamp(self):
        self.assertEqual(ew.blueprint_wpm({}), 135)
        self.assertEqual(ew.blueprint_wpm(None), 135)
        self.assertEqual(ew.blueprint_wpm({"words_per_minute": 0}), 135)
        self.assertEqual(ew.blueprint_wpm({"words_per_minute": "nonsense"}), 135)
        self.assertEqual(ew.blueprint_wpm({"words_per_minute": 120}), 120)


# --------------------------------------------------------------------------
# 2. rewrite_timecodes
# --------------------------------------------------------------------------
class RewriteTimecodesTest(unittest.TestCase):
    SCRIPT = words_script(200)

    def test_storyboard_times_come_from_the_cues_and_unfound_are_kept(self):
        package = (
            "### 🎬 INTEGRATED PRODUCTION STORYBOARD\n"
            "| # | Act | ⏱️ Timecode | ▶️ START CUE | ⏹️ END CUE | Layer | Detail |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | 1 | 0:00 | word40 word41 | word45 | A-ROLL | first |\n"
            "| 2 | 1 | 0:11 | word100 word101 | word105 | A-ROLL | second |\n"
            "| 3 | 1 | 0:22 | not in the script at all | word199 | A-ROLL | keep |\n")
        out = ew.rewrite_timecodes(package, self.SCRIPT, 60)
        rows = ew.parse_storyboard(out)
        self.assertEqual(rows[0]["time"], "0:40")
        self.assertEqual(rows[1]["time"], "1:40")
        self.assertEqual(rows[2]["time"], "0:22")       # cue not found: original kept

    def test_broll_table_timestamp_rewritten_and_other_tables_untouched(self):
        text = (
            "### 🖼️ AI B-ROLL GENERATION PROMPTS\n\n"
            "### B-Roll Prompt Table\n"
            "| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |\n"
            "|---|---|---|---|---|---|\n"
            "| 1 | 0:01 | word40 word41 | word45 | 🎬 Video | clip one |\n"
            "| 2 | 0:02 | word160 word161 | word165 | 🖼️ Image | clip two |\n\n"
            "### B-Roll Density Summary\n"
            "| Act | Total | 🖼️ Images | 🎬 Videos |\n"
            "|---|---|---|---|\n"
            "| Hook | 2 | 1 | 1 |\n")
        out = ew.rewrite_timecodes(text, self.SCRIPT, 60)
        self.assertIn("| 1 | 0:40 |", out)
        self.assertIn("| 2 | 2:40 |", out)
        self.assertIn("| Hook | 2 | 1 | 1 |", out)      # the summary table has no cue column

    def test_without_a_script_nothing_changes(self):
        package = ("### 🎬 INTEGRATED PRODUCTION STORYBOARD\n"
                   "| # | Act | ⏱️ Timecode | ▶️ START CUE | ⏹️ END CUE | Layer | Detail |\n"
                   "|---|---|---|---|---|---|---|\n"
                   "| 1 | 1 | 7:07 | word40 word41 | word45 | A-ROLL | x |\n")
        self.assertEqual(ew.rewrite_timecodes(package, "", 60), package)


class FinalPackageTimingTest(unittest.TestCase):
    SCRIPT = words_script(120)
    PART2 = (
        "### 🎬 INTEGRATED PRODUCTION STORYBOARD\n"
        "| # | Act | ⏱️ Timecode | ▶️ START CUE | ⏹️ END CUE | Layer | Detail |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | 1 | 0:00 | word10 word11 | word20 | A-ROLL | x |\n"
        "| 2 | 1 | 9:59 | word60 word61 | word70 | A-ROLL | y |\n")
    BROLL = ("## AI B-ROLL GENERATION PROMPTS\n### B-Roll Prompt Table\n"
             "| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |\n"
             "|---|---|---|---|---|---|\n"
             "| 1 | 0:01 | word30 word31 | word35 | 🎬 Video | clip one, slow push-in |\n")

    def test_single_call_package_rewrites_storyboard_and_broll_times(self):
        def llm(system, user, temperature=None, max_tokens=None):
            if "Compile Part 1" in user:
                return "### 🎬 PRODUCTION SCRIPT\n" + self.SCRIPT + "\n### WHAT CHANGED\nx\n"
            return self.PART2

        use(self, final_package, "call_llm", FakeLLM(llm))
        use(self, final_package, "_audit_part1", lambda *a, **k: [])
        use(self, final_package, "_audit_part2", lambda *a, **k: [])
        out = final_package.final_script_package(
            {"refined_script": self.SCRIPT, "blueprint": {"words_per_minute": 60},
             "broll_prompts": self.BROLL})["final_package"]
        rows = ew.parse_storyboard(out)
        self.assertEqual(rows[0]["time"], "0:10")
        self.assertEqual(rows[1]["time"], "1:00")
        self.assertIn("| 1 | 0:30 |", out)              # inserted B-roll table timestamp


# --------------------------------------------------------------------------
# 3. engagement problems on the real timeline
# --------------------------------------------------------------------------
class BrollRealTimelineTest(unittest.TestCase):
    HEAD = ("| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Script Context | Type | Main Cue | AI Generation Prompt "
            "| Composition Notes | Dur |\n|---|---|---|---|---|---|---|---|---|---|\n")

    def _row(self, n, timestamp, cue, prompt):
        return f'| {n} | {timestamp} | {cue} | z | c | 🎬 Video | x | {prompt} | left | 4s |\n'

    def test_gap_measured_from_cues_even_when_timestamps_look_dense(self):
        script = words_script(240)                        # 240 words at 60 wpm = 4 minutes
        table = self.HEAD + (
            self._row(1, "0:05", "word10 word11", "a man checks his wrist, slow push-in")
            + self._row(2, "0:10", "word180 word181", "a man walks a street, slow dolly"))
        problems = " ".join(production.broll_engagement_problems(table, "", 10, script=script, wpm=60))
        self.assertIn("No B-roll between", problems)
        self.assertIn("0:10–3:00", problems)
        # The model's own timestamps are 5s apart: without the script, no gap is flagged.
        plain = " ".join(production.broll_engagement_problems(table, "", 10))
        self.assertNotIn("No B-roll between", plain)

    def test_stacked_starts_under_8s_are_flagged(self):
        script = words_script(120)
        table = self.HEAD + (
            self._row(1, "0:05", "word10 word11", "clip A of a wrist, slow push-in")
            + self._row(2, "0:05", "word14 word15", "clip B of a hand, slow dolly"))
        problems = " ".join(production.broll_engagement_problems(table, "", 5, script=script, wpm=60))
        self.assertIn("B-rolls #1 and #2", problems)
        self.assertIn("start 4s apart", problems)

    def test_near_identical_prompts_are_flagged(self):
        script = words_script(300)
        prompt = "a doctor holds a pill blister pack, warm colour, slow push-in"
        table = self.HEAD + (
            self._row(1, "0:05", "word10 word11", prompt)
            + self._row(2, "0:05", "word100 word101", prompt.replace("push-in", "dolly")))
        problems = " ".join(production.broll_engagement_problems(table, "", 8, script=script, wpm=60))
        self.assertIn("nearly the same image", problems)

    def test_different_prompts_are_not_called_repetition(self):
        script = words_script(300)
        table = self.HEAD + (
            self._row(1, "0:05", "word10 word11",
                      "a doctor explains a heart model in a bright clinic, slow push-in")
            + self._row(2, "0:05", "word100 word101",
                        "a patient walks a cairo street at sunset, slow dolly"))
        problems = " ".join(production.broll_engagement_problems(table, "", 8, script=script, wpm=60))
        self.assertNotIn("nearly the same image", problems)

    def test_without_a_script_repetition_and_stacking_are_not_checked(self):
        prompt = "a doctor holds a pill blister pack, warm colour, slow push-in"
        table = self.HEAD + (
            self._row(1, "0:03", "word10 word11", prompt)
            + self._row(2, "0:04", "word14 word15", prompt.replace("push-in", "dolly")))
        problems = " ".join(production.broll_engagement_problems(table, "", 10))
        self.assertNotIn("nearly the same image", problems)
        self.assertNotIn("apart", problems)

    def test_without_a_script_the_gap_threshold_is_still_90s(self):
        table = self.HEAD + (
            self._row(1, "0:00", "a b c", "a man checks his wrist, slow push-in")
            + self._row(2, "1:30", "d e f", "a man walks a street, slow dolly"))
        self.assertNotIn("No B-roll between",
                         " ".join(production.broll_engagement_problems(table, "", 10)))


# --------------------------------------------------------------------------
# 4. SCRIPT LENGTH line in the B-roll prompts
# --------------------------------------------------------------------------
class ScriptLengthLineTest(unittest.TestCase):
    def test_single_call_prompt_states_the_script_length(self):
        fake = use(self, production, "call_llm", FakeLLM(lambda *a: "table"))
        production.broll_prompt_generator({"refined_script": words_script(240),
                                           "blueprint": {"words_per_minute": 120}})
        prompt = fake.calls[-1]["user"]
        self.assertIn("SCRIPT LENGTH:", prompt)
        self.assertIn("about 240 spoken words", prompt)
        self.assertIn("2.0 minutes", prompt)
        self.assertIn("at 120 wpm", prompt)
        self.assertIn("plan about 3–4 B-roll moments", prompt)

    def test_chapter_prompt_uses_the_chapter_spans_words(self):
        bp = {"words_per_minute": 120,
              "chapters": [{"id": "C1", "title": "A", "words": 900},
                           {"id": "C2", "title": "B", "words": 900}]}
        script = f"=== A ===\n{words_script(900, 'a')}\n\n=== B ===\n{words_script(900, 'b')}"
        fake = use(self, production, "call_llm", FakeLLM(
            lambda *a: "| # | Timestamp | Type | AI Generation Prompt |\n|---|---|---|---|\n"
                       "| 1 | 0:05 | 🎬 Video | a clip |\n"))
        production.broll_prompt_generator({"refined_script": script, "blueprint": bp,
                                           "chapter_workers": 1})
        self.assertEqual(len(fake.calls), 2)
        for call in fake.calls:
            self.assertIn("SCRIPT LENGTH:", call["user"])
            self.assertIn("about 900 spoken words", call["user"])
            self.assertIn("7.5 minutes", call["user"])


class CritiqueTimelineTest(unittest.TestCase):
    def test_critique_hands_the_script_and_blueprint_wpm_to_the_checks(self):
        captured = {}

        def fake_problems(broll, overlay="", minutes=0, script="", wpm=135):
            captured.update(script=script, wpm=wpm)
            return []

        def fake_json(system, user, temperature=None, max_tokens=None, required=None):
            return {"production_grade": "PASS", "broll_relevance_score": 8}, "raw"

        with mock.patch.object(production, "broll_engagement_problems", fake_problems), \
                mock.patch.object(production, "call_llm_json", fake_json):
            production.production_quality_critique(
                {"broll_prompts": "", "refined_script": words_script(50),
                 "blueprint": {"words_per_minute": 123}})
        self.assertEqual(captured["script"], words_script(50))
        self.assertEqual(captured["wpm"], 123)


if __name__ == "__main__":
    unittest.main()


class EllipsisCueTest(unittest.TestCase):
    def test_cue_with_the_scripts_own_pause_is_exact(self):
        from medical_brain.exports import workbook as ew
        script = "لازم نهدى ونهدى... أول فكرة بتيجي في بالك هي القياس الصح في البيت."
        norm = ew.normalize_ar(ew.spoken_text(script))
        self.assertIsNone(ew.cue_problem("ونهدى... أول فكرة بتيجي في بالك", norm))
        self.assertEqual(ew.cue_problem("بداية الكلام ... هنا", norm), "placeholder, not a spoken sentence")

