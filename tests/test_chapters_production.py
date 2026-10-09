"""Phase 5b: the whole-script steps (translation auditor, the three production
designers and the Part 2 storyboard) also work chapter by chapter for long videos,
so no single model answer ever has to cover the whole script. A one-chapter
blueprint keeps exactly the old single-call behaviour."""
import unittest
from unittest import mock

from medical_brain.nodes import chapters, final_package, production, quality


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


BP = {
    "target_minutes": 20, "words_per_minute": 120, "promise": "تعهد",
    "chapters": [
        {"id": "C1", "title": "الفصل الأول", "job": "j1", "words": 500, "covers": ["s1"],
         "opens_loops": [], "closes_loops": [], "depends_on_previous": True},
        {"id": "C2", "title": "الفصل الثاني", "job": "j2", "words": 500, "covers": ["s2"],
         "opens_loops": [], "closes_loops": [], "depends_on_previous": True},
    ],
}
ONE_CHAPTER = {"chapters": [{"id": "C1", "title": "الفصل الوحيد", "job": "j", "words": 300,
                             "covers": [], "opens_loops": [], "closes_loops": [],
                             "depends_on_previous": False}]}

BODY = "=== الفصل الأول ===\nنص أول\n\n=== الفصل الثاني ===\nنص ثاني"
FULL_SCRIPT = ("هوك هنا\n=== الفصل الأول ===\nنص أول هنا\n\n"
               "=== الفصل الثاني ===\nنص ثاني هنا\nكول تو أكشن")


def base_state(**over):
    state = {"hook": "هوك", "strategy_plan": "خطة", "source_analysis": "تحليل",
             "refined_script": FULL_SCRIPT, "revised_body": BODY, "original_script": "",
             "code_switching_level": "", "avoid_list": "", "broll_availability": "",
             "target_platform": "", "production_revision_count": 0,
             "chapter_workers": 1, "blueprint": {}}
    state.update(over)
    return state


# --------------------------------------------------------------------------
# 1. chapter_spans timing
# --------------------------------------------------------------------------
class ChapterSpansTest(unittest.TestCase):
    def test_spans_cover_hook_first_and_cta_last(self):
        wpm = 120
        body1 = " ".join(f"a{i}" for i in range(60))
        body2 = " ".join(f"b{i}" for i in range(60))
        script = f"هوك\n=== A ===\n{body1}\n=== B ===\n{body2}\nCTA أخير"
        bp = {"words_per_minute": wpm,
              "chapters": [{"id": "C1", "title": "A"}, {"id": "C2", "title": "B"}]}
        spans = chapters.chapter_spans(script, bp)
        self.assertEqual([s["id"] for s in spans], ["C1", "C2"])
        self.assertIn("هوك", spans[0]["text"])                 # hook joins the first span
        self.assertIn("CTA أخير", spans[1]["text"])            # CTA joins the last span
        first_words = len(("هوك\n\n=== A ===\n" + body1).split())
        second_words = len(("=== B ===\n" + body2 + "\nCTA أخير").split())
        self.assertEqual(spans[0]["start_s"], 0)
        self.assertAlmostEqual(spans[0]["end_s"], first_words / wpm * 60)
        self.assertAlmostEqual(spans[1]["start_s"], first_words / wpm * 60)
        self.assertAlmostEqual(spans[1]["end_s"], (first_words + second_words) / wpm * 60)
        self.assertEqual(chapters.fmt_time(0), "0:00")
        self.assertEqual(chapters.fmt_time(125), "2:05")

    def test_one_chapter_and_empty(self):
        bp = {"words_per_minute": 135, "chapters": [{"id": "C1", "title": "A"}]}
        self.assertEqual(len(chapters.chapter_spans("=== A ===\nhello", bp)), 1)
        self.assertEqual(chapters.chapter_spans("", bp), [])


# --------------------------------------------------------------------------
# 2. translation_fidelity_auditor chapter mode
# --------------------------------------------------------------------------
class TranslationChapterModeTest(unittest.TestCase):
    def test_per_chapter_combine_min_scores_and_headers(self):
        def answers(system, user, temperature=None, max_tokens=None):
            if "id: C1" in user:
                return ({"translation_grade": "PASS", "naturalness_score": 9,
                         "contextual_alignment_score": 10, "translation_report": "r1",
                         "revised_body": "=== الفصل الأول ===\nنص أول معدل"}, "raw1")
            return ({"translation_grade": "NEEDS_REVISION", "naturalness_score": 7,
                     "contextual_alignment_score": 9, "translation_report": "r2"}, "raw2")  # no revised_body

        fake = use(self, quality, "call_llm_json", FakeLLM(answers))
        out = quality.translation_fidelity_auditor(base_state(blueprint=BP))
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("CHAPTER MODE", fake.calls[0]["system"])
        self.assertIn("id: C1", fake.calls[0]["user"])
        self.assertEqual(fake.calls[0]["max_tokens"],
                         chapters.budget_for_words(len("=== الفصل الأول ===\nنص أول".split())) + 2000)
        self.assertEqual(fake.calls[1]["max_tokens"],
                         chapters.budget_for_words(len("=== الفصل الثاني ===\nنص ثاني".split())) + 2000)
        self.assertEqual(out["naturalness_score"], 7)                # min across chapters
        self.assertEqual(out["contextual_alignment_score"], 9)
        self.assertEqual(out["translation_grade"], "NEEDS_REVISION")  # one chapter failed
        self.assertIn("=== الفصل الأول ===", out["revised_body"])
        self.assertIn("نص أول معدل", out["revised_body"])
        self.assertIn("=== الفصل الثاني ===", out["revised_body"])   # header ensured on fallback
        self.assertIn("نص ثاني", out["revised_body"])
        self.assertIn("[C1 · الفصل الأول]", out["translation_report"])
        self.assertIn("[C2 · الفصل الثاني]", out["translation_report"])

    def test_all_pass_gives_pass_and_min_scores(self):
        def answers(system, user, temperature=None, max_tokens=None):
            base = {"translation_grade": "PASS", "contextual_alignment_score": 9,
                    "translation_report": "ok", "revised_body": ""}
            if "id: C1" in user:
                return (dict(base, naturalness_score=9), "r")
            return (dict(base, naturalness_score=8, contextual_alignment_score=10), "r")

        use(self, quality, "call_llm_json", FakeLLM(answers))
        out = quality.translation_fidelity_auditor(base_state(blueprint=BP))
        self.assertEqual(out["translation_grade"], "PASS")
        self.assertEqual(out["naturalness_score"], 8)
        self.assertEqual(out["contextual_alignment_score"], 9)

    def test_fail_grade_propagates(self):
        def answers(system, user, temperature=None, max_tokens=None):
            if "id: C1" in user:
                return ({"translation_grade": "PASS", "naturalness_score": 9,
                         "contextual_alignment_score": 9, "translation_report": "ok"}, "r")
            return ({"translation_grade": "FAIL", "naturalness_score": 9,
                     "contextual_alignment_score": 9, "translation_report": "bad"}, "r")

        use(self, quality, "call_llm_json", FakeLLM(answers))
        out = quality.translation_fidelity_auditor(base_state(blueprint=BP))
        self.assertEqual(out["translation_grade"], "NEEDS_REVISION")

    def test_missing_chapter_text_falls_back_to_input(self):
        def answers(system, user, temperature=None, max_tokens=None):
            return ({"translation_grade": "PASS", "naturalness_score": 9,
                     "contextual_alignment_score": 9, "translation_report": "ok"}, "r")

        use(self, quality, "call_llm_json", FakeLLM(answers))
        out = quality.translation_fidelity_auditor(base_state(blueprint=BP))
        self.assertIn("نص أول", out["revised_body"])
        self.assertIn("نص ثاني", out["revised_body"])


# --------------------------------------------------------------------------
# 3. production steps join + renumber + broll_by_chapter
# --------------------------------------------------------------------------
TRANSITION_TABLE = """## TRANSITION DESIGN MAP
### Transition Table
| # | Timestamp | From → To | Transition Type |
|---|---|---|---|
| 1 | 0:00 | A → B | Cut |
| 2 | 0:10 | B → C | Zoom-in |"""


class ProductionChapterModeTest(unittest.TestCase):
    def test_transition_join_one_header_and_global_numbers(self):
        def answers(system, user, temperature=None, max_tokens=None):
            prefix = "C1" if "id: C1" in user else "C2"
            return TRANSITION_TABLE.replace("A → B", f"A{prefix} → B{prefix}")

        fake = use(self, production, "call_llm", FakeLLM(answers))
        out = production.transition_designer(base_state(blueprint=BP))
        text = out["transition_design"]
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("CHAPTER MODE", fake.calls[0]["system"])
        self.assertEqual(fake.calls[0]["user"].count("chapter 1 of 2"), 1)
        self.assertNotIn("PRODUCTION STYLE", fake.calls[0]["user"])     # chapter 1 has none
        self.assertIn("PRODUCTION STYLE", fake.calls[1]["user"])        # later chapters reuse ch1
        self.assertIn("AC1", text)
        self.assertEqual(text.count("| # | Timestamp | From → To | Transition Type |"), 1)
        self.assertEqual(text.count("|---|---|---|---|"), 1)
        for number in ("| 1 |", "| 2 |", "| 3 |", "| 4 |"):
            self.assertIn(number, text)
        self.assertIn("=== الفصل الأول ===", text)
        self.assertIn("=== الفصل الثاني ===", text)

    def test_broll_by_chapter_and_global_renumbering(self):
        def answers(system, user, temperature=None, max_tokens=None):
            header = ("## AI B-ROLL GENERATION PROMPTS\n### B-Roll Prompt Table\n"
                      "| # | Timestamp | Type | AI Generation Prompt |\n|---|---|---|---|\n")
            if "id: C1" in user:
                return header + ("| 1 | 0:05 | 🎬 Video | PROMPT_ONE slow push-in |\n"
                                 "| 2 | 0:20 | 🖼️ Image | PROMPT_TWO |\n")
            return header + "| 1 | 3:00 | 🎬 Video | PROMPT_THREE |\n"

        use(self, production, "call_llm", FakeLLM(answers))
        out = production.broll_prompt_generator(base_state(blueprint=BP))
        self.assertEqual(out["broll_by_chapter"], {"C1": [1, 2], "C2": [3]})
        text = out["broll_prompts"]
        self.assertEqual(text.count("| # | Timestamp | Type | AI Generation Prompt |"), 1)
        self.assertEqual(text.count("|---|---|---|---|"), 1)
        for number in ("| 1 |", "| 2 |", "| 3 |"):
            self.assertIn(number, text)
        self.assertNotIn("| 4 |", text)
        from medical_brain.exports import workbook as ew
        rows = ew.broll_rows(text)
        self.assertEqual([r["num"] for r in rows], ["1", "2", "3"])   # contiguous across chapters
        self.assertEqual(rows[2]["prompt"], "PROMPT_THREE")

    def test_revision_reruns_only_flagged_chapters(self):
        stored = {
            "C1": "=== الفصل الأول ===\nOLD C1 TABLE",
            "C2": "=== الفصل الثاني ===\nOLD C2 TABLE",
        }
        fake = use(self, production, "call_llm",
                   FakeLLM(lambda *a: "=== الفصل الثاني ===\nNEW C2 TABLE"))
        state = base_state(blueprint=BP, production_revision_count=1,
                           production_critique_output="C2 needs a transition fix",
                           production_chapter_outputs={"transition_design": stored},
                           transition_design="=== الفصل الأول ===\nOLD C1 TABLE")
        out = production.transition_designer(state)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("id: C2", fake.calls[0]["user"])
        self.assertIn("REVISION PASS", fake.calls[0]["user"])
        self.assertIn("C2 needs a transition fix", fake.calls[0]["user"])
        self.assertIn("OLD C1 TABLE", out["transition_design"])      # unflagged: untouched
        self.assertIn("NEW C2 TABLE", out["transition_design"])


# --------------------------------------------------------------------------
# 4. Part 2 storyboard chapter mode
# --------------------------------------------------------------------------
STORYBOARD_ROW = ("| {n} | 1 | {time} | {cue} | {cue} | B-ROLL | 🎬 Video | B-roll #{br} | Dur: 4s |")
STORYBOARD_HEAD = ("| # | Act | ⏱️ Timecode | ▶️ START CUE | ⏹️ END CUE | Layer | Detail |\n"
                   "|---|---|---|---|---|---|---|")


class Part2ChapterModeTest(unittest.TestCase):
    def _answers(self):
        def fn(system, user, temperature=None, max_tokens=None):
            if "id: C1" in user:
                row = STORYBOARD_ROW.format(n=1, time="0:00", cue="نص أول", br=1)
            else:
                row = STORYBOARD_ROW.format(n=1, time="3:00", cue="نص ثاني", br=3)
            return f"### 🎬 INTEGRATED PRODUCTION STORYBOARD\n{STORYBOARD_HEAD}\n{row}\n"
        return FakeLLM(fn)

    def _broll_table(self):
        return ("## AI B-ROLL GENERATION PROMPTS\n### B-Roll Prompt Table\n"
                "| # | Timestamp | Type | AI Generation Prompt |\n|---|---|---|---|\n"
                "| 1 | 0:05 | 🎬 Video | PROMPT_ONE |\n"
                "| 2 | 0:20 | 🖼️ Image | PROMPT_TWO |\n"
                "| 3 | 3:00 | 🎬 Video | PROMPT_THREE |\n")

    def test_stitch_one_heading_renumber_and_per_chapter_broll(self):
        fake = self._answers()
        use(self, final_package, "call_llm", fake)
        use(self, final_package, "_audit_part2", lambda *a, **k: [])
        state = base_state(blueprint=BP, broll_prompts=self._broll_table(),
                           broll_by_chapter={"C1": [1, 2], "C2": [3]})
        part2, best = final_package._part2_chapter_mode(
            state, "SYSTEM", FULL_SCRIPT, [], "")
        self.assertEqual(part2.count("### 🎬 INTEGRATED PRODUCTION STORYBOARD"), 1)
        self.assertEqual(part2.count("| # | Act | ⏱️ Timecode |"), 1)
        self.assertIn("| 1 |", part2)
        self.assertIn("| 2 |", part2)
        self.assertLess(part2.index("نص أول"), part2.index("نص ثاني"))    # chapter order
        c1 = next(c["user"] for c in fake.calls if "id: C1" in c["user"])
        c2 = next(c["user"] for c in fake.calls if "id: C2" in c["user"])
        self.assertIn("PROMPT_ONE", c1)
        self.assertIn("PROMPT_TWO", c1)
        self.assertNotIn("PROMPT_THREE", c1)
        self.assertIn("PROMPT_THREE", c2)
        self.assertNotIn("PROMPT_ONE", c2)
        self.assertIn("CHAPTER MODE", fake.calls[0]["system"])

    def test_attributed_chapters(self):
        issues = ["STORYBOARD CUES NOT EXACT (2): SB#2 (B-ROLL) ...; SB#3 (B-ROLL) ...",
                  "Some global issue"]
        mapping = {1: 0, 2: 1, 3: 1, 4: 0}
        self.assertEqual(final_package._attributed_chapters(issues, mapping, {0: [1], 1: [2, 2]}, 2), [1])
        self.assertEqual(final_package._attributed_chapters(["global"], mapping, {}, 2), [0, 1])

    def test_final_package_chapter_mode_end_to_end(self):
        def llm(system, user, temperature=None, max_tokens=None):
            if "Compile Part 1" in user:
                return ("### 🎬 PRODUCTION SCRIPT\n" + FULL_SCRIPT + "\n### WHAT CHANGED\nx\n")
            if "id: C1" in user:
                row = STORYBOARD_ROW.format(n=1, time="0:00", cue="نص أول", br=1)
            else:
                row = STORYBOARD_ROW.format(n=1, time="3:00", cue="نص ثاني", br=3)
            return f"### 🎬 INTEGRATED PRODUCTION STORYBOARD\n{STORYBOARD_HEAD}\n{row}\n"

        use(self, final_package, "call_llm", FakeLLM(llm))
        use(self, final_package, "_audit_part1", lambda *a, **k: [])
        use(self, final_package, "_audit_part2", lambda *a, **k: [])
        out = final_package.final_script_package(
            base_state(blueprint=BP, broll_prompts=self._broll_table(),
                       broll_by_chapter={"C1": [1, 2], "C2": [3]}))
        pkg = out["final_package"]
        self.assertEqual(pkg.count("### 🎬 INTEGRATED PRODUCTION STORYBOARD"), 1)
        self.assertLess(pkg.index("نص أول"), pkg.index("نص ثاني"))
        self.assertIn("### 🎬 PRODUCTION SCRIPT", pkg)


# --------------------------------------------------------------------------
# 5. length check with a blueprint
# --------------------------------------------------------------------------
class LengthCheckTest(unittest.TestCase):
    def _text(self, words):
        body = " ".join(f"كلمة{i}" for i in range(words))
        return f"### 🎬 PRODUCTION SCRIPT\n{body}\n### WHAT CHANGED\nx\n"

    def test_blueprint_length_flags_out_of_range(self):
        short = final_package._audit_part1_all(self._text(100), blueprint=BP)
        self.assertTrue(any(i.startswith("PRODUCTION SCRIPT TOO SHORT") for i in short))
        long = final_package._audit_part1_all(self._text(3000), blueprint=BP)
        self.assertTrue(any(i.startswith("PRODUCTION SCRIPT TOO LONG") for i in long))
        ok = final_package._audit_part1_all(self._text(2400), blueprint=BP)
        self.assertFalse(any(i.startswith("PRODUCTION SCRIPT TOO") for i in ok))

    def test_one_chapter_blueprint_keeps_today_check(self):
        issues = final_package._audit_part1_all(self._text(100), blueprint=ONE_CHAPTER)
        self.assertFalse(any(i.startswith("PRODUCTION SCRIPT TOO") for i in issues))


# --------------------------------------------------------------------------
# 6. one-chapter mode is unchanged
# --------------------------------------------------------------------------
class OneChapterUnchangedTest(unittest.TestCase):
    def test_translation_auditor_prompt_unchanged(self):
        fake = use(self, quality, "call_llm_json",
                   FakeLLM(lambda *a: ({"translation_grade": "PASS", "naturalness_score": 9,
                                        "contextual_alignment_score": 9}, "raw")))
        quality.translation_fidelity_auditor(base_state(blueprint={}))
        old = [dict(c) for c in fake.calls]
        fake.calls.clear()
        quality.translation_fidelity_auditor(base_state(blueprint=ONE_CHAPTER))
        new = [dict(c) for c in fake.calls]
        self.assertEqual(old, new)
        self.assertEqual(old[0]["max_tokens"], 6000)
        self.assertNotIn("CHAPTER MODE", old[0]["system"])

    def test_production_prompts_unchanged(self):
        for fn in (production.transition_designer, production.text_animation_overlay_designer,
                   production.broll_prompt_generator):
            with self.subTest(fn=fn.__name__):
                fake = use(self, production, "call_llm", FakeLLM(lambda *a: "ANSWER"))
                fn(base_state(blueprint={}))
                old = [dict(c) for c in fake.calls]
                fake.calls.clear()
                fn(base_state(blueprint=ONE_CHAPTER))
                new = [dict(c) for c in fake.calls]
                self.assertEqual(old, new)
                self.assertNotIn("CHAPTER MODE", old[0]["system"])

    def test_final_package_prompts_unchanged(self):
        def canned(system, user, temperature=None, max_tokens=None):
            if "Compile Part 1" in user:
                return "### 🎬 PRODUCTION SCRIPT\nنص\n### WHAT CHANGED\nx\n"
            return "### 🎬 INTEGRATED PRODUCTION STORYBOARD\nx\n"
        fake = use(self, final_package, "call_llm", FakeLLM(canned))
        final_package.final_script_package(base_state(blueprint={}))
        old = [dict(c) for c in fake.calls]
        fake.calls.clear()
        final_package.final_script_package(base_state(blueprint=ONE_CHAPTER))
        new = [dict(c) for c in fake.calls]
        self.assertEqual(old, new)


if __name__ == "__main__":
    unittest.main()
