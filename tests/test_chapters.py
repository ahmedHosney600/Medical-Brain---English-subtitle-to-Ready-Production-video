"""Phase 5a: long videos are written chapter by chapter from a script blueprint and a
shared script memory, then checked as one whole by an integrity pass. A one-chapter
blueprint (≤ ~10 min) keeps exactly the old single-call behaviour."""
import json
import re
import unittest
from unittest import mock

from medical_brain.nodes import analysis, chapters, script_writing
from medical_brain.prompts import load_prompt


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


def mem_block(summary, terms=None, facts=None, opened=None, closed=None):
    payload = {"summary": summary, "terms_defined": terms or [], "facts_used": facts or [],
               "loops_opened": opened or [], "loops_closed": closed or []}
    return "```memory\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


BP = {
    "target_minutes": 20, "words_per_minute": 135, "promise": "تعهد",
    "chapters": [
        {"id": "C1", "title": "الفصل الأول", "job": "j1", "words": 500, "covers": ["s1"],
         "opens_loops": ["لماذا؟"], "closes_loops": [], "depends_on_previous": True},
        {"id": "C2", "title": "الفصل الثاني", "job": "j2", "words": 500, "covers": ["s2"],
         "opens_loops": [], "closes_loops": ["لماذا؟"], "depends_on_previous": True},
    ],
}
ONE_CHAPTER = {"chapters": [{"id": "C1", "title": "الفصل الوحيد", "job": "j", "words": 300,
                             "covers": [], "opens_loops": [], "closes_loops": [],
                             "depends_on_previous": False}]}


def base_state(**over):
    state = {"hook": "هوك", "strategy_plan": "خطة", "source_analysis": "تحليل",
             "video_analysis": "", "original_script": "", "content_style": "",
             "dialect_register": "", "code_switching_level": "", "voice_style": "",
             "audience_level": "", "delivery_format": "", "broll_availability": "",
             "mandatory_mentions": "", "avoid_list": "", "sensitive_handling": "",
             "presenter_profile": "", "blueprint": {}, "translation_revision_count": 0}
    state.update(over)
    return state


class BlueprintTest(unittest.TestCase):
    PLAN = ("## RESTRUCTURE STRATEGY PLAN\n\n### Script Blueprint (machine-readable)\n"
            "```json\n" + json.dumps(BP, ensure_ascii=False) + "\n```\n")

    def test_good_blueprint_parses_and_validates(self):
        bp = chapters.parse_blueprint(self.PLAN)
        self.assertTrue(chapters.valid_blueprint(bp))
        self.assertTrue(chapters.chapter_mode_on(bp))
        self.assertEqual([c["id"] for c in bp["chapters"]], ["C1", "C2"])

    def test_last_json_block_under_the_marker_wins(self):
        text = ("```json\n{\"nope\": 1}\n```\n### Script Blueprint\n```json\n"
                + json.dumps(ONE_CHAPTER, ensure_ascii=False) + "\n```")
        self.assertEqual(chapters.parse_blueprint(text)["chapters"][0]["id"], "C1")

    def test_missing_or_invalid_is_off(self):
        self.assertEqual(chapters.parse_blueprint("no blueprint here"), {})
        self.assertFalse(chapters.valid_blueprint({}))
        self.assertFalse(chapters.valid_blueprint({"chapters": []}))
        self.assertFalse(chapters.valid_blueprint({"chapters": [{"id": "C1", "title": "t", "words": 0}]}))
        self.assertFalse(chapters.valid_blueprint({"chapters": [{"id": "", "title": "t", "words": 5}]}))
        self.assertFalse(chapters.valid_blueprint({"chapters": [{"id": "C1", "title": "", "words": 5}]}))

    def test_one_chapter_is_not_chapter_mode(self):
        self.assertTrue(chapters.valid_blueprint(ONE_CHAPTER))
        self.assertFalse(chapters.chapter_mode_on(ONE_CHAPTER))
        self.assertFalse(chapters.chapter_mode_on({}))

    def test_strategy_planner_adds_target_minutes_and_parses(self):
        fake = use(self, analysis, "call_llm", FakeLLM(lambda *a: self.PLAN))
        out = analysis.strategy_planner(base_state(target_minutes=20))
        self.assertEqual(fake.calls[0]["max_tokens"], 9000)
        self.assertIn("TARGET MINUTES: 20", fake.calls[0]["user"])
        self.assertTrue(chapters.chapter_mode_on(out["blueprint"]))

        analysis.strategy_planner(base_state(target_minutes=0))
        self.assertNotIn("TARGET MINUTES", fake.calls[-1]["user"])

    def test_strategy_planner_drops_an_invalid_blueprint(self):
        use(self, analysis, "call_llm", FakeLLM(lambda *a: "a plan with no blueprint"))
        out = analysis.strategy_planner(base_state(target_minutes=0))
        self.assertEqual(out["blueprint"], {})
        self.assertEqual(out["strategy_plan"], "a plan with no blueprint")


class MemoryTest(unittest.TestCase):
    def test_split_memory_strips_the_block(self):
        text = ("=== C1 ===\nنص الفصل\n\n"
                + mem_block("ملخص", [{"term": "سكر", "phrasing": "السكر"}], ["F1"], ["L1"]))
        body, memory = chapters.split_memory(text)
        self.assertNotIn("```", body)
        self.assertEqual(body, "=== C1 ===\nنص الفصل")
        self.assertEqual(memory["summary"], "ملخص")
        self.assertEqual(memory["terms_defined"][0]["term"], "سكر")

    def test_split_without_a_memory_block(self):
        body, memory = chapters.split_memory("=== C1 ===\nنص")
        self.assertEqual(memory, {})
        self.assertEqual(body, "=== C1 ===\nنص")

    def test_merge_terms_facts_and_loops(self):
        merged = chapters.merge_memory(
            {}, {"summary": "أ", "terms_defined": [{"term": "سكر", "phrasing": "السكر"}],
                 "facts_used": ["F1"], "loops_opened": ["L1"], "loops_closed": []}, "C1")
        merged = chapters.merge_memory(
            merged, {"summary": "ب", "terms_defined": [{"term": "سكر", "phrasing": "different"},
                                                       {"term": "ضغط", "phrasing": "الضغط"}],
                     "facts_used": ["F1", "F2"], "loops_opened": ["L2"], "loops_closed": ["L1"]}, "C2")
        self.assertEqual([t["term"] for t in merged["terms_defined"]], ["سكر", "ضغط"])
        self.assertEqual(merged["terms_defined"][0]["phrasing"], "السكر")   # first phrasing wins
        self.assertEqual(merged["facts_used"], ["F1", "F2"])
        self.assertEqual(merged["loops_open"], ["L2"])                     # L1 opened then closed
        self.assertEqual(merged["loops_closed"], ["L1"])
        self.assertEqual([s["chapter"] for s in merged["summaries"]], ["C1", "C2"])

    def test_last_lines_and_render(self):
        self.assertEqual(chapters.last_lines("a b c d", 2), "c d")
        self.assertEqual(chapters.last_lines("a b c d", 0), "")
        rendered = chapters.render_memory({"terms_defined": [{"term": "سكر", "phrasing": "السكر"}],
                                           "loops_open": ["L1"]})
        self.assertIn("سكر", rendered)
        self.assertIn("L1", rendered)

    def test_split_and_join_chapters(self):
        parts = chapters.split_by_chapters("preamble\n=== A ===\nbody A\n=== B ===\nbody B")
        self.assertEqual([t for t, _ in parts], ["", "A", "B"])
        self.assertEqual(parts[0][1], "preamble\n")
        self.assertIn("=== A ===", parts[1][1])
        joined = chapters.join_chapters([text for _, text in parts])
        for piece in ("preamble", "=== A ===", "body A", "=== B ===", "body B"):
            self.assertIn(piece, joined)

    def test_budget_for_words(self):
        self.assertEqual(chapters.budget_for_words(100), 3000)
        self.assertEqual(chapters.budget_for_words(400), 3780)
        self.assertEqual(chapters.budget_for_words(700), 4740)


class BodyChapterModeTest(unittest.TestCase):
    def _answers(self, system, user, temperature=None, max_tokens=None):
        if "id: C1" in user:
            return ("=== الفصل الأول ===\nنص الفصل الأول هنا.\n\n"
                    + mem_block("ملخص أول", [{"term": "سكر", "phrasing": "السكر"}], ["F1"], ["لماذا؟"]))
        return ("=== الفصل الثاني ===\nنص الفصل الثاني هنا.\n\n"
                + mem_block("ملخص تاني", [], ["F2"], [], ["لماذا؟"]))

    def test_writes_once_per_chapter_in_order(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(self._answers))
        out = script_writing.body_restructurer(base_state(blueprint=BP))
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("id: C1", fake.calls[0]["user"])
        self.assertIn("id: C2", fake.calls[1]["user"])
        self.assertEqual(fake.calls[0]["max_tokens"], chapters.budget_for_words(500))

        for marker in ("SCRIPT BLUEPRINT", "THIS CHAPTER", "SCRIPT MEMORY",
                       "LAST LINES OF THE PREVIOUS CHAPTER"):
            self.assertIn(marker, fake.calls[0]["user"])
        self.assertIn("CHAPTER MODE", fake.calls[0]["system"])

        # The second writer sees the first chapter's memory and its last lines.
        self.assertIn("سكر", fake.calls[1]["user"])
        self.assertIn("لماذا؟", fake.calls[1]["user"])
        self.assertIn("نص الفصل الأول هنا", fake.calls[1]["user"])

        self.assertIn("=== الفصل الأول ===", out["revised_body"])
        self.assertIn("=== الفصل الثاني ===", out["revised_body"])
        self.assertNotIn("```memory", out["revised_body"])
        self.assertEqual(len(out["chapter_memory"]), 2)
        self.assertEqual(out["script_memory"]["loops_open"], [])           # L1 closed by C2
        self.assertEqual([t["term"] for t in out["script_memory"]["terms_defined"]], ["سكر"])

    def test_revision_rewrites_only_flagged_chapters(self):
        fake = use(self, script_writing, "call_llm",
                   FakeLLM(lambda *a: "=== الفصل الثاني ===\nنسخة معدلة.\n\n" + mem_block("ملخص")))
        state = base_state(
            blueprint=BP, translation_revision_count=1,
            translation_report="C2 needs fixing: a calque in the opening.",
            revised_body="=== الفصل الأول ===\nنص أول قديم\n\n=== الفصل الثاني ===\nنص تاني قديم",
            chapter_memory={"C1": {"summary": "c1"}, "C2": {"summary": "c2"}})
        out = script_writing.body_restructurer(state)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("id: C2", fake.calls[0]["user"])
        self.assertIn("REVISION PASS", fake.calls[0]["user"])
        self.assertIn("نص تاني قديم", fake.calls[0]["user"])
        self.assertIn("C2 needs fixing", fake.calls[0]["user"])
        self.assertIn("نص أول قديم", out["revised_body"])       # unflagged: untouched
        self.assertIn("نسخة معدلة", out["revised_body"])

    def test_revision_with_no_identifiable_chapter_rewrites_all(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(self._answers))
        state = base_state(blueprint=BP, translation_revision_count=1, translation_report="general issues",
                           revised_body="=== الفصل الأول ===\nقديم\n\n=== الفصل الثاني ===\nقديم كمان")
        script_writing.body_restructurer(state)
        self.assertEqual(len(fake.calls), 2)

    def test_checkpoint_reuse_skips_written_chapters(self):
        fake = use(self, script_writing, "call_llm",
                   FakeLLM(lambda *a: "=== الفصل الثاني ===\nجديد.\n\n" + mem_block("تين")))
        state = base_state(blueprint=BP, chapter_drafts_round=0,
                           chapter_drafts_key=chapters.blueprint_signature(BP),
                           chapter_drafts={"C1": "=== الفصل الأول ===\nنص محفوظ"},
                           chapter_memory={"C1": {"summary": "محفوظ"}})
        out = script_writing.body_restructurer(state)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("id: C2", fake.calls[0]["user"])
        self.assertIn("نص محفوظ", fake.calls[0]["user"])          # last lines of the reused chapter
        self.assertIn("نص محفوظ", out["revised_body"])
        self.assertIn("جديد", out["revised_body"])


def frame_fake(opening="افتتاحية مختارة", disclaimer="تنبيه طبي",
               closing="خاتمة مختارة", chosen="HOOK A"):
    """A json call that answers like the script_frame step."""
    def answer(system, user, temperature=None, max_tokens=None, **kwargs):
        return ({"opening": opening, "disclaimer": disclaimer, "closing": closing,
                 "chosen_hook": chosen}, "raw")
    return FakeLLM(answer)


class DialectChapterModeTest(unittest.TestCase):
    def _answers(self, system, user, temperature=None, max_tokens=None):
        label = re.search(r"SEGMENT LABEL: (.*)", user).group(1).strip()
        return f"[{label} rewritten]\n=== SEGMENT NOTES ===\nDialect 9 / Warmth 9"

    def _state(self, **over):
        body = ("=== الفصل الأول ===\nنص أول\n\n=== الفصل الثاني ===\nنص ثاني")
        fields = dict(blueprint=BP, hook="هوك الأصلي", revised_body=body,
                      cta_output="كول تو أكشن", chapter_workers=1)
        fields.update(over)
        return base_state(**fields)

    def test_frame_builds_segments_from_opening_and_closing(self):
        frame = use(self, script_writing, "call_llm_json", frame_fake())
        fake = use(self, script_writing, "call_llm", FakeLLM(self._answers))
        state = self._state(
            hook="### HOOK A raw\nنص هوك أول\n### HOOK B raw\nنص هوك تاني",
            cta_output="### CTA v1\nنص CTA أول\n### CTA v2\nنص CTA تاني")
        out = script_writing.dialect_warmth_layer(state)

        # ONE frame call, with the right dials, and it sees the hook options, the CTA
        # material and the body's first/last lines.
        self.assertEqual(len(frame.calls), 1)
        self.assertEqual(frame.calls[0]["temperature"], 0.4)
        self.assertEqual(frame.calls[0]["max_tokens"], 3000)
        self.assertEqual(frame.calls[0]["required"], ["opening", "closing"])
        for marker in ("HOOK OPTIONS", "RETENTION, DISCLAIMER & CTA MATERIAL",
                       "SCRIPT BLUEPRINT", "FIRST CHAPTER START", "LAST CHAPTER END"):
            self.assertIn(marker, frame.calls[0]["user"])
        self.assertIn("نص هوك أول", frame.calls[0]["user"])
        self.assertIn("نص CTA تاني", frame.calls[0]["user"])
        self.assertIn("نص أول", frame.calls[0]["user"])                 # first chapter start

        # Segments: opening (+ disclaimer), the chapters, closing — not the raw hook/CTA.
        labels = [re.search(r"SEGMENT LABEL: (.*)", c["user"]).group(1).strip() for c in fake.calls]
        self.assertEqual(labels, ["OPENING", "الفصل الأول", "الفصل الثاني", "CLOSING"])
        self.assertIn("افتتاحية مختارة", fake.calls[0]["user"])
        self.assertIn("تنبيه طبي", fake.calls[0]["user"])               # disclaimer appended
        self.assertIn("خاتمة مختارة", fake.calls[-1]["user"])
        self.assertIn("[OPENING rewritten]", out["dialect_warmth_output"])
        self.assertIn("[CLOSING rewritten]", out["dialect_warmth_output"])
        self.assertNotIn("نص هوك أول", out["dialect_script"])
        self.assertNotIn("نص CTA تاني", out["dialect_script"])
        self.assertEqual(out["script_frame"]["opening"], "افتتاحية مختارة")
        self.assertEqual(out["script_frame"]["chosen_hook"], "HOOK A")

    def _grow_state(self):
        long_ch = " ".join(f"ك{i}" for i in range(100))
        body = f"=== الفصل الأول ===\n{long_ch}\n\n=== الفصل الثاني ===\nنص ثاني"
        return self._state(revised_body=body)

    def test_dialect_growth_is_redone_once(self):
        use(self, script_writing, "call_llm_json", frame_fake())

        def answer(system, user, temperature=None, max_tokens=None):
            if "SEGMENT LABEL: الفصل الأول" not in user:
                return self._answers(system, user)
            if "YOUR LAST REWRITE WAS" in user:
                return "=== الفصل الأول ===\n" + " ".join(f"ر{i}" for i in range(104))
            return "=== الفصل الأول ===\n" + " ".join(f"م{i}" for i in range(160))

        fake = use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.dialect_warmth_layer(self._grow_state())
        self.assertIn("ر103", out["dialect_script"])
        self.assertNotIn("م159", out["dialect_script"])
        self.assertEqual(sum("YOUR LAST REWRITE WAS 160" in c["user"] for c in fake.calls), 1)

    def test_dialect_growth_twice_keeps_the_segment_as_written(self):
        use(self, script_writing, "call_llm_json", frame_fake())

        def answer(system, user, temperature=None, max_tokens=None):
            if "SEGMENT LABEL: الفصل الأول" not in user:
                return self._answers(system, user)
            return "=== الفصل الأول ===\n" + " ".join(f"م{i}" for i in range(160))

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.dialect_warmth_layer(self._grow_state())
        self.assertIn("ك99", out["dialect_script"])
        self.assertNotIn("م159", out["dialect_script"])

    def test_revision_pass_allows_almost_no_growth(self):
        long_ch = " ".join(f"ك{i}" for i in range(100))
        refined = f"=== الفصل الأول ===\n{long_ch}\n\n=== الفصل الثاني ===\nنص ثاني"

        def answer(system, user, temperature=None, max_tokens=None):
            if "SEGMENT LABEL: الفصل الأول" not in user:
                return self._answers(system, user)
            return "=== الفصل الأول ===\n" + " ".join(f"م{i}" for i in range(108))   # +8%

        use(self, script_writing, "call_llm_json", frame_fake())
        use(self, script_writing, "call_llm", FakeLLM(answer))
        state = self._state(refined_script=refined, quality_revision_count=1,
                            script_frame={"opening": "o", "closing": "c"})
        with mock.patch("builtins.print"):
            out = script_writing.dialect_warmth_layer(state)
        self.assertIn("ك99", out["dialect_script"])

    def test_frame_failure_falls_back_to_the_raw_hook_and_cta(self):
        def boom(*a, **k):
            from medical_brain.utils.llm_json import UnreadableAnswer
            raise UnreadableAnswer("no json", "raw")
        use(self, script_writing, "call_llm_json", boom)
        fake = use(self, script_writing, "call_llm", FakeLLM(self._answers))
        state = self._state(hook="نص الهوك الخام", cta_output="نص الـ CTA الخام")
        with mock.patch("builtins.print") as printed:
            out = script_writing.dialect_warmth_layer(state)
        labels = [re.search(r"SEGMENT LABEL: (.*)", c["user"]).group(1).strip() for c in fake.calls]
        self.assertEqual(labels, ["HOOK", "الفصل الأول", "الفصل الثاني", "CTA / OUTRO"])
        self.assertIn("نص الهوك الخام", fake.calls[0]["user"])
        self.assertIn("نص الـ CTA الخام", fake.calls[-1]["user"])
        self.assertNotIn("script_frame", out)
        self.assertTrue(any("frame" in str(call.args[0]).lower()
                            for call in printed.call_args_list))

    def test_revision_reuses_the_frame_unless_the_critique_flags_it(self):
        frame = use(self, script_writing, "call_llm_json", frame_fake())
        fake = use(self, script_writing, "call_llm", FakeLLM(self._answers))
        refined = "=== الفصل الأول ===\nمصقول\n\n=== الفصل الثاني ===\nمصقول ٢"
        state = base_state(blueprint=BP, quality_revision_count=1, refined_script=refined,
                           cta_output="cta", self_critique_output="dialect wording only")
        script_writing.dialect_warmth_layer(state)
        self.assertEqual(len(frame.calls), 0)                     # frame reused, not re-chosen
        self.assertEqual(len(fake.calls), 2)

        frame.calls.clear()
        fake.calls.clear()
        flagged = dict(state, self_critique_output="the opening hook is weak and the closing CTA is rushed")
        out = script_writing.dialect_warmth_layer(flagged)
        self.assertEqual(len(frame.calls), 1)                     # critique mentions hook/CTA → re-run
        self.assertIn("افتتاحية مختارة", out["script_frame"]["opening"])
        self.assertEqual(out["script_frame"]["closing"], "خاتمة مختارة")

    def test_segments_rewritten_joined_and_notes_collected(self):
        use(self, script_writing, "call_llm_json", frame_fake())
        fake = use(self, script_writing, "call_llm", FakeLLM(self._answers))
        out = script_writing.dialect_warmth_layer(self._state())
        self.assertEqual(len(fake.calls), 4)                       # opening + 2 chapters + closing
        for piece in ("[OPENING rewritten]", "[الفصل الأول rewritten]",
                      "[الفصل الثاني rewritten]", "[CLOSING rewritten]"):
            self.assertIn(piece, out["dialect_warmth_output"])
        self.assertIn("## SEGMENT NOTES", out["dialect_warmth_output"])
        self.assertIn("### الفصل الأول", out["dialect_warmth_output"])
        self.assertNotIn("SEGMENT NOTES", out["dialect_script"])
        self.assertIn("=== الفصل الأول ===", fake.calls[1]["user"])       # header kept in the segment
        c2 = next(c for c in fake.calls if "SEGMENT LABEL: الفصل الثاني" in c["user"])
        self.assertIn("نص أول", c2["user"])                              # neighbour last lines
        opening_words = len("افتتاحية مختارة\n\nتنبيه طبي".split())
        self.assertEqual(fake.calls[0]["max_tokens"], chapters.budget_for_words(opening_words))

    def test_revision_segments_come_from_the_refined_script(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(self._answers))
        state = base_state(blueprint=BP, quality_revision_count=1,
                           refined_script="=== الفصل الأول ===\nمصقول\n\n=== الفصل الثاني ===\nمصقول ٢",
                           cta_output="cta", self_critique_output="critique")
        out = script_writing.dialect_warmth_layer(state)
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("مصقول", fake.calls[0]["user"])
        self.assertIn("REVISION PASS", fake.calls[0]["user"])
        self.assertEqual(out["dialect_script"], "[الفصل الأول rewritten]\n\n[الفصل الثاني rewritten]")


class RefinementChapterModeTest(unittest.TestCase):
    SCRIPT = ("=== C1 ===\nالفقرة الأولى موجودة هنا.\n\n"
              "=== C2 ===\nالفقرة الثانية موجودة مرتين. موجودة مرتين.")

    def _json_answer(self):
        return ({"edits": [
            {"chapter": "C1", "find": "الفقرة الأولى", "replace": "الفقرة المعدلة", "why": "seam"},
            {"chapter": "C2", "find": "موجودة مرتين", "replace": "Y", "why": "repeat"},
            {"chapter": "C9", "find": "zzz", "replace": "y", "why": "ghost"},
        ], "integrity_score": 8, "summary": "reads as one talk", "issues": []}, "raw")

    def test_edits_applied_once_only_and_ambiguous_skipped(self):
        fake_json = use(self, script_writing, "call_llm_json", FakeLLM(lambda *a: self._json_answer()))
        fake_llm = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "SHOULD NOT RUN"))
        out = script_writing.script_refinement(base_state(blueprint=BP, dialect_script=self.SCRIPT))
        self.assertEqual(len(fake_json.calls), 1)                 # one integrity call
        self.assertEqual(len(fake_llm.calls), 0)                  # no whole-script rewrite
        self.assertIn("الفقرة المعدلة", out["refined_script"])
        self.assertNotIn("Y", out["refined_script"])
        self.assertEqual(out["integrity_report"]["applied"], 1)
        self.assertEqual(out["integrity_report"]["skipped"], 2)
        self.assertEqual(out["integrity_report"]["score"], 8)
        self.assertEqual(fake_json.calls[0]["temperature"], 0.3)
        self.assertEqual(fake_json.calls[0]["max_tokens"], 6000)

    def test_short_script_goes_as_full_text_long_one_as_a_compact_view(self):
        short = script_writing._integrity_prompt(base_state(blueprint=BP), "كلمة " * 500, 1000)
        self.assertIn("FULL SCRIPT", short)
        long_script = " ".join(f"w{i}" for i in range(2300))
        long_prompt = script_writing._integrity_prompt(base_state(blueprint=BP), long_script, 1000)
        self.assertIn("COMPACT VIEW PER CHAPTER", long_prompt)
        self.assertNotIn("w2299", long_prompt)                    # the full text is not sent
        self.assertIn("TARGET WORDS: 1000", long_prompt)

    def test_assembles_the_dialect_output_without_its_notes(self):
        captured = {}

        def answer(system, user, temperature=None, max_tokens=None):
            captured["user"] = user
            return ({"edits": [], "integrity_score": 9, "summary": "ok", "issues": []}, "raw")

        use(self, script_writing, "call_llm_json", FakeLLM(answer))
        output = (self.SCRIPT + "\n\n## SEGMENT NOTES\n\n### C1\nDialect 9")
        out = script_writing.script_refinement(base_state(blueprint=BP, dialect_warmth_output=output))
        self.assertNotIn("SEGMENT NOTES", captured["user"])
        self.assertNotIn("SEGMENT NOTES", out["refined_script"])
        self.assertIn("الفقرة الأولى", out["refined_script"])

    def test_unreadable_integrity_answer_keeps_the_assembled_script(self):
        def boom(*a, **k):
            from medical_brain.utils.llm_json import UnreadableAnswer
            raise UnreadableAnswer("no json", "raw")
        use(self, script_writing, "call_llm_json", boom)
        with mock.patch("builtins.print"):
            out = script_writing.script_refinement(base_state(blueprint=BP, dialect_script=self.SCRIPT))
        self.assertEqual(out["refined_script"], self.SCRIPT)
        self.assertEqual(out["integrity_report"]["applied"], 0)


class ChapterPipelineTest(unittest.TestCase):
    def test_body_dialect_refinement_chain(self):
        def llm(system, user, temperature=None, max_tokens=None):
            if "THIS CHAPTER" in user:
                cid = "C1" if "id: C1" in user else "C2"
                title = "الفصل الأول" if cid == "C1" else "الفصل الثاني"
                return f"=== {title} ===\nنص {cid}.\n\n" + mem_block("ملخص " + cid)
            label = re.search(r"SEGMENT LABEL: (.*)", user).group(1).strip()
            return f"[{label} rewritten]\n=== SEGMENT NOTES ===\nDialect 9"

        def json_llm(system, user, temperature=None, max_tokens=None, **kwargs):
            if "FRAME" in system.upper():
                return ({"opening": "افتتاحية", "disclaimer": "تنبيه", "closing": "خاتمة",
                         "chosen_hook": "A"}, "raw")
            return ({"edits": [], "integrity_score": 9, "summary": "one talk", "issues": []}, "raw")

        use(self, script_writing, "call_llm", FakeLLM(llm))
        use(self, script_writing, "call_llm_json", FakeLLM(json_llm))

        state = base_state(blueprint=BP, chapter_workers=1)
        state.update(script_writing.body_restructurer(state))
        self.assertIn("=== الفصل الأول ===", state["revised_body"])
        state.update(script_writing.dialect_warmth_layer(state))
        self.assertIn("[الفصل الثاني rewritten]", state["dialect_warmth_output"])
        state.update(script_writing.script_refinement(state))
        self.assertIn("[الفصل الأول rewritten]", state["refined_script"])
        self.assertNotIn("SEGMENT NOTES", state["refined_script"])
        self.assertEqual(state["integrity_report"]["score"], 9)
        self.assertEqual(state["script_memory"]["summaries"][0]["summary"], "ملخص C1")


class CleanSpokenScriptTest(unittest.TestCase):
    def test_removes_labels_and_notes_and_keeps_chapter_headers(self):
        raw = ("## DIALECT & WARMTH REWRITE\n### Rewritten Script\n"
               "=== الفصل الأول ===\nنص أول.\n\n\n"
               "=== DISCLAIMER | Timestamp: 1:30 | Energy: 2/5 ===\n"
               "لا تهمل الأعراض.\n\n"
               "=== CTA | Timestamp: 9:35 | Energy: 3/5 ===\n"
               "اشترك في القناة.\n\n"
               "=== OUTRO | Timestamp: 9:50 ===\nخاتمة.\n\n"
               "=== الفصل الثاني ===\nنص ثاني.\n\n"
               "### Register Applied: Educated | Code-Switching: Moderate\n"
               "Score: 9\nDialect: 9\nWarmth: 9\nFlags: none\n\n"
               "## SEGMENT NOTES\n\n### الفصل الأول\nDialect 9\n")
        clean = chapters.clean_spoken_script(raw)
        self.assertIn("=== الفصل الأول ===", clean)
        self.assertIn("=== الفصل الثاني ===", clean)
        self.assertIn("نص أول.", clean)
        for gone in ("## DIALECT", "### Rewritten Script", "DISCLAIMER | Timestamp",
                     "CTA | Timestamp", "OUTRO | Timestamp", "Register Applied",
                     "Score:", "Dialect:", "Warmth:", "Flags:", "SEGMENT NOTES"):
            self.assertNotIn(gone, clean)
        # the spoken disclaimer/CTA/outro lines themselves are kept
        self.assertIn("لا تهمل الأعراض", clean)
        self.assertIn("اشترك في القناة", clean)
        self.assertIn("خاتمة.", clean)
        self.assertNotIn("\n\n\n", clean)

    def test_plain_script_is_unchanged(self):
        script = "=== C1 ===\nنص\n\n=== C2 ===\nنص تاني"
        self.assertEqual(chapters.clean_spoken_script(script), script)


class ChapterLengthGuardTest(unittest.TestCase):
    LONG = " ".join(f"w{i}" for i in range(2000))

    def _bp(self):
        return {"target_minutes": 20, "words_per_minute": 135, "chapters": [
            {"id": "C1", "title": "طويل", "job": "j", "words": 800, "covers": [],
             "opens_loops": [], "closes_loops": [], "depends_on_previous": False},
            {"id": "C2", "title": "ق", "job": "j", "words": 800, "covers": [],
             "opens_loops": [], "closes_loops": [], "depends_on_previous": True},
        ]}

    def _run(self, tight_text, writer=None):
        calls = []
        long_text = writer or self.LONG

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            if "TIGHTEN this chapter" in user:
                return f"=== طويل ===\n{tight_text}"
            if "id: C1" in user:
                return f"=== طويل ===\n{long_text}"
            return "=== ق ===\nنص قصير"

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.body_restructurer(
                base_state(blueprint=self._bp(), target_minutes=20))   # user gave a duration → strict
        return calls, out

    def test_overlong_chapter_is_tightened(self):
        calls, out = self._run(" ".join(f"t{i}" for i in range(850)))
        tighten = [c for c in calls if "TIGHTEN this chapter" in c]
        self.assertEqual(len(tighten), 1)
        self.assertIn("TIGHTEN this chapter to about 720 words (never more than 800)", tighten[0])
        self.assertIn("t849", out["revised_body"])
        self.assertNotIn("w1999", out["revised_body"])

    def test_chapter_slightly_over_is_tightened(self):
        # 15% over the target: below the old 1.25× trigger, above TIGHTEN_ABOVE.
        writer = " ".join(f"w{i}" for i in range(920))
        calls, out = self._run(" ".join(f"t{i}" for i in range(760)), writer=writer)
        self.assertEqual(len([c for c in calls if "TIGHTEN this chapter" in c]), 1)
        self.assertIn("t759", out["revised_body"])

    def test_out_of_range_tightening_keeps_the_original(self):
        calls, out = self._run(" ".join(f"t{i}" for i in range(100)))   # < 0.7 × 800
        self.assertEqual(len([c for c in calls if "TIGHTEN this chapter" in c]), 1)
        self.assertIn("w1999", out["revised_body"])
        self.assertNotIn("t99", out["revised_body"])

    def test_second_tightening_call_when_still_over(self):
        calls = []
        first = " ".join(f"a{i}" for i in range(1200))   # accepted, still > 1.1 × 800
        second = " ".join(f"b{i}" for i in range(700))   # within budget

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            if "TIGHTEN this chapter" in user:
                return f"=== طويل ===\n{second}" if "a1199" in user else f"=== طويل ===\n{first}"
            if "id: C1" in user:
                return "=== طويل ===\n" + self.LONG       # 2000 words
            return "=== ق ===\nنص قصير"

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.body_restructurer(
                base_state(blueprint=self._bp(), target_minutes=20))
        tighten = [c for c in calls if "TIGHTEN this chapter" in c]
        self.assertEqual(len(tighten), 2)
        self.assertIn("b699", out["revised_body"])
        self.assertNotIn("a1199", out["revised_body"])

    def test_tightening_uses_the_cut_only_system_prompt(self):
        seen = []

        def answer(system, user, temperature=None, max_tokens=None):
            seen.append((system, user))
            if "TIGHTEN this chapter" in user:
                return "=== طويل ===\n" + " ".join(f"t{i}" for i in range(850))
            if "id: C1" in user:
                return "=== طويل ===\n" + self.LONG
            return "=== ق ===\nنص قصير"

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            script_writing.body_restructurer(
                base_state(blueprint=self._bp(), target_minutes=20))
        tighten = [system for system, user in seen if "TIGHTEN this chapter" in user]
        self.assertEqual(len(tighten), 1)
        self.assertIn("Your only job is to CUT", tighten[0])


class IntegrityNormalisationTest(unittest.TestCase):
    def test_normalised_match_applied_once(self):
        script = ("=== C1 ===\nالكلمـة الطبيّة، مهمة جدا.\n\n"
                  "=== C2 ===\nالتشخيـص، السريع ثم التشخـيص السريع.\n")
        bp = {"chapters": [{"id": "C1", "title": "C1", "words": 5},
                           {"id": "C2", "title": "C2", "words": 5}]}
        out, applied, skipped, log = chapters.apply_integrity_edits(
            script, bp, [{"chapter": "C1", "find": "الكلمة الطبية مهمة", "replace": "REPLACED"}])
        self.assertEqual((applied, skipped), (1, 0))
        self.assertIn("REPLACED", out)
        self.assertNotIn("الطبيّة", out)
        self.assertTrue(any("normalis" in line for line in log))

    def test_ambiguous_normalised_match_is_skipped(self):
        script = "=== C1 ===\nالتشخيـص، السريع ثم التشخـيص السريع.\n"
        bp = {"chapters": [{"id": "C1", "title": "C1", "words": 5}]}
        out, applied, skipped, log = chapters.apply_integrity_edits(
            script, bp, [{"chapter": "C1", "find": "التشخيص السريع", "replace": "X"}])
        self.assertEqual((applied, skipped), (0, 1))
        self.assertNotIn("X", out)


class TotalLengthLineTest(unittest.TestCase):
    def test_single_chapter_refinement_gets_the_whole_video_target(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "REFINED"))
        bp = dict(ONE_CHAPTER, target_minutes=10, words_per_minute=135)
        script_writing.script_refinement(base_state(blueprint=bp, target_minutes=10))
        self.assertIn("TARGET LENGTH: about 1350 spoken words for the WHOLE video", fake.calls[0]["user"])

    def test_no_minutes_no_line(self):
        self.assertEqual(script_writing._total_length_line({}), "")


class SingleChapterUnchangedTest(unittest.TestCase):
    """A one-chapter (or absent) blueprint must make exactly the old calls."""

    def test_body_prompt_unchanged(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "BODY"))
        script_writing.body_restructurer(base_state(blueprint={}, target_minutes=5))
        old = dict(fake.calls[0])
        fake.calls.clear()
        script_writing.body_restructurer(base_state(blueprint=ONE_CHAPTER, target_minutes=5))
        new = fake.calls[0]
        self.assertEqual(old["system"], new["system"])
        # The only addition is the blueprint's word budget.
        self.assertIn("LENGTH BUDGET: about 300 spoken words", new["user"])
        self.assertEqual(old["user"], re.sub(r"\nLENGTH BUDGET:[^\n]*\n", "", new["user"]))
        self.assertEqual(new["max_tokens"], 9000)
        self.assertNotIn("SCRIPT BLUEPRINT", new["user"])
        self.assertNotIn("CHAPTER MODE", new["system"])

    def test_overlong_single_body_is_tightened(self):
        long_body = " ".join(f"w{i}" for i in range(600))      # 2 × the 300-word budget
        short_body = " ".join(f"t{i}" for i in range(280))

        def answer(system, user, temperature=None, max_tokens=None):
            return short_body if "TIGHTEN this chapter" in user else long_body

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.body_restructurer(
                base_state(blueprint=ONE_CHAPTER, target_minutes=5))
        self.assertEqual(out["revised_body"], short_body)

    def test_dialect_prompt_unchanged(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "DIALECT"))
        args = {"hook": "H", "revised_body": "B", "cta_output": "C"}
        script_writing.dialect_warmth_layer(base_state(blueprint={}, **args))
        old = dict(fake.calls[0])
        fake.calls.clear()
        script_writing.dialect_warmth_layer(base_state(blueprint=ONE_CHAPTER, **args))
        new = fake.calls[0]
        self.assertEqual(old["system"], new["system"])
        self.assertEqual(old["user"], new["user"])
        self.assertNotIn("SEGMENT", new["user"])
        self.assertEqual(new["max_tokens"], 9000)

    def test_refinement_prompt_unchanged(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "REFINED"))
        script_writing.script_refinement(base_state(blueprint={}, dialect_warmth_output="D"))
        old = dict(fake.calls[0])
        fake.calls.clear()
        script_writing.script_refinement(base_state(blueprint=ONE_CHAPTER, dialect_warmth_output="D"))
        new = fake.calls[0]
        self.assertEqual(old["system"], new["system"])
        self.assertEqual(old["user"], new["user"])
        self.assertNotIn("COMPACT VIEW", new["user"])
        self.assertEqual(new["max_tokens"], 9000)


class SingleChapterFrameTest(unittest.TestCase):
    """A one-chapter video still runs the frame step when the blueprint has a target:
    the raw hook options and CTA material must not reach the dialect/refinement models."""

    FRAME_BP = {"target_minutes": 10, "words_per_minute": 135,
                "chapters": [{"id": "C1", "title": "الفصل الوحيد", "job": "j", "words": 500,
                              "covers": [], "opens_loops": [], "closes_loops": [],
                              "depends_on_previous": False}]}

    def test_frame_is_used_in_single_chapter_dialect(self):
        frame = use(self, script_writing, "call_llm_json", frame_fake())
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "DIALECT"))
        state = base_state(blueprint=self.FRAME_BP,
                           hook="### HOOK A raw\nنص هوك أول\n### HOOK B raw\nنص هوك تاني",
                           revised_body="نص الجسم كامل هنا",
                           cta_output="### CTA v1\nنص سي تي ايه\n### CTA v2\nنص تاني")
        out = script_writing.dialect_warmth_layer(state)

        self.assertEqual(len(frame.calls), 1)
        # A single (unchaptered) body: the frame still sees its opening and ending.
        self.assertIn("نص الجسم كامل هنا", frame.calls[0]["user"])
        prompt = fake.calls[0]["user"]
        self.assertIn("OPENING (already chosen", prompt)
        self.assertIn("افتتاحية مختارة", prompt)
        self.assertIn("تنبيه طبي", prompt)                     # disclaimer appended
        self.assertIn("نص الجسم كامل هنا", prompt)
        self.assertIn("CLOSING (already chosen", prompt)
        self.assertIn("خاتمة مختارة", prompt)
        self.assertNotIn("نص هوك أول", prompt)                 # raw hook options absent
        self.assertNotIn("نص سي تي ايه", prompt)               # raw CTA material absent
        self.assertEqual(out["script_frame"]["opening"], "افتتاحية مختارة")

    def test_frame_failure_keeps_the_old_block(self):
        def boom(*a, **k):
            from medical_brain.utils.llm_json import UnreadableAnswer
            raise UnreadableAnswer("no json", "raw")
        use(self, script_writing, "call_llm_json", boom)
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "DIALECT"))
        state = base_state(blueprint=self.FRAME_BP, hook="نص الهوك الخام",
                           revised_body="نص الجسم", cta_output="نص الـ CTA الخام")
        with mock.patch("builtins.print"):
            out = script_writing.dialect_warmth_layer(state)
        prompt = fake.calls[0]["user"]
        self.assertIn("HOOK:\nنص الهوك الخام", prompt)
        self.assertIn("RETENTION, DISCLAIMER & CTA LAYER:\nنص الـ CTA الخام", prompt)
        self.assertNotIn("script_frame", out)

    def test_no_target_minutes_never_calls_the_frame(self):
        frame = use(self, script_writing, "call_llm_json", frame_fake())
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "DIALECT"))
        state = base_state(blueprint=ONE_CHAPTER, hook="H", revised_body="B", cta_output="C")
        out = script_writing.dialect_warmth_layer(state)
        self.assertEqual(len(frame.calls), 0)
        self.assertIn("HOOK:\nH", fake.calls[0]["user"])
        self.assertNotIn("script_frame", out)

    def test_refinement_uses_the_frame_opening(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "REFINED"))
        bp = dict(ONE_CHAPTER, target_minutes=10, words_per_minute=135)
        state = base_state(blueprint=bp, script_frame={"opening": "افتتاحية مختارة", "closing": "خاتمة"},
                           hook="نص هوك أول ونص هوك تاني", dialect_warmth_output="D")
        script_writing.script_refinement(state)
        prompt = fake.calls[0]["user"]
        self.assertIn("OPENING (chosen):\nافتتاحية مختارة", prompt)
        self.assertNotIn("HOOK:\n", prompt)
        self.assertNotIn("نص هوك أول", prompt)

    def test_length_warning_printed_when_refined_script_overruns(self):
        long_script = " ".join(f"w{i}" for i in range(1600))
        use(self, script_writing, "call_llm", FakeLLM(lambda *a: long_script))
        bp = dict(ONE_CHAPTER, target_minutes=10, words_per_minute=135)
        with mock.patch("builtins.print") as printed:
            out = script_writing.script_refinement(
                base_state(blueprint=bp, target_minutes=10, dialect_warmth_output="D"))
        self.assertEqual(out["refined_script"], long_script)
        text = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("1600", text)
        self.assertIn("1350", text)

    def test_no_length_warning_when_within_budget(self):
        short_script = " ".join(f"w{i}" for i in range(1400))
        use(self, script_writing, "call_llm", FakeLLM(lambda *a: short_script))
        bp = dict(ONE_CHAPTER, target_minutes=10, words_per_minute=135)
        with mock.patch("builtins.print") as printed:
            script_writing.script_refinement(base_state(blueprint=bp, dialect_warmth_output="D"))
        text = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertNotIn("refined script:", text)


class SingleChapterWholeScriptTightenTest(unittest.TestCase):
    """script_refinement tightens the whole non-chapter script when it overruns the
    whole-video budget (target_minutes × words_per_minute)."""

    BP = {"target_minutes": 10, "words_per_minute": 135,
          "chapters": [{"id": "C1", "title": "الفصل الوحيد", "job": "j", "words": 500,
                        "covers": [], "opens_loops": [], "closes_loops": [],
                        "depends_on_previous": False}]}

    def _words(self, prefix, n):
        return " ".join(f"{prefix}{i}" for i in range(n))

    def _state(self, **over):
        # target_minutes on the state means the user gave a duration → strict guards.
        # Pass target_minutes=0 to exercise soft mode (planner target only).
        over.setdefault("target_minutes", 10)
        return base_state(blueprint=self.BP, dialect_warmth_output="D", **over)

    def test_overlong_refined_script_is_tightened(self):
        long_script = self._words("w", 2000)          # target 1350, limit 1552.5
        tight = self._words("t", 1200)
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append({"user": user, "temperature": temperature, "max_tokens": max_tokens})
            return tight if "TIGHTEN this complete script" in user else long_script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print") as printed:
            out = script_writing.script_refinement(self._state())
        tighten = [c for c in calls if "TIGHTEN this complete script" in c["user"]]
        self.assertEqual(len(tighten), 1)
        self.assertIn("TIGHTEN this complete script to about 1215 spoken words "
                      "(never more than 1350)", tighten[0]["user"])
        self.assertEqual(tighten[0]["temperature"], 0.5)
        self.assertEqual(tighten[0]["max_tokens"], max(9000, chapters.budget_for_words(1350)))
        self.assertEqual(out["refined_script"], tight)
        text = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("whole script: 2000 → 1200 words", text)

    def test_second_tightening_only_when_still_over(self):
        long_script = self._words("w", 2000)
        first = self._words("a", 1800)                # accepted, still > 1552.5
        second = self._words("b", 1300)               # within budget
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            if "TIGHTEN this complete script" in user:
                return second if "a1799" in user else first
            return long_script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.script_refinement(self._state())
        tighten = [c for c in calls if "TIGHTEN this complete script" in c]
        self.assertEqual(len(tighten), 2)
        self.assertIn("a1799", tighten[1])            # the second call tightens the accepted first
        self.assertEqual(out["refined_script"], second)

    def test_out_of_range_tightening_keeps_the_original(self):
        long_script = self._words("w", 2000)
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return self._words("t", 100) if "TIGHTEN this complete script" in user else long_script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.script_refinement(self._state())
        self.assertEqual(len([c for c in calls if "TIGHTEN this complete script" in c]), 1)
        self.assertEqual(out["refined_script"], long_script)

    def test_no_target_minutes_never_tightens(self):
        long_script = self._words("w", 2000)
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return long_script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        out = script_writing.script_refinement(
            base_state(blueprint=ONE_CHAPTER, dialect_warmth_output="D"))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("TIGHTEN this complete script", calls[0])
        self.assertEqual(out["refined_script"], long_script)

    def test_tightening_uses_the_cut_only_system_prompt(self):
        long_script = self._words("w", 2000)
        tight = self._words("t", 1200)
        seen = []

        def answer(system, user, temperature=None, max_tokens=None):
            seen.append((system, user))
            return tight if "TIGHTEN this complete script" in user else long_script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            script_writing.script_refinement(self._state())
        tighten = [system for system, user in seen if "TIGHTEN this complete script" in user]
        self.assertEqual(len(tighten), 1)
        self.assertIn("Your only job is to CUT", tighten[0])

    # --- soft mode: no user duration, only the planner's own target ---

    def test_soft_mild_overrun_is_not_tightened(self):
        script = self._words("w", 1620)               # 1.2 × the 1350 plan
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        out = script_writing.script_refinement(self._state(target_minutes=0))
        self.assertEqual([c for c in calls if "TRIM this complete script" in c], [])
        self.assertEqual(out["refined_script"], script)

    def test_soft_far_overrun_gets_one_trim(self):
        script = self._words("w", 2025)               # 1.5 × the 1350 plan
        tight = self._words("t", 1300)
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return tight if "TRIM this complete script" in user else script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.script_refinement(self._state(target_minutes=0))
        trim = [c for c in calls if "TRIM this complete script" in c]
        self.assertEqual(len(trim), 1)
        self.assertIn("TRIM this complete script: remove repetition, padding and side tangents only",
                      trim[0])
        self.assertIn("keep the opening hook, the disclaimer", trim[0])
        self.assertIn("never cut a real point to get there.", trim[0])
        self.assertEqual(out["refined_script"], tight)

    def test_soft_trim_uses_the_padding_prompt_and_floor_sentence(self):
        script = self._words("w", 2025)               # 1.5 × the 1350 plan
        tight = self._words("t", 1300)
        seen = []

        def answer(system, user, temperature=None, max_tokens=None):
            seen.append((system, user))
            return tight if "TRIM this complete script" in user else script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            script_writing.script_refinement(self._state(target_minutes=0))
        trim = [(s, u) for s, u in seen if "TRIM this complete script" in u]
        self.assertEqual(len(trim), 1)
        self.assertEqual(trim[0][0], load_prompt("script_trim_padding"))
        self.assertIn("Do not go below 1282 words.", trim[0][1])   # int(1350 × 0.95)

    def test_soft_cut_below_the_floor_keeps_the_untrimmed_script(self):
        script = self._words("w", 2025)
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            # 1200 < 0.9 × 1350 (1215): real content was cut, so the trim is rejected.
            return self._words("t", 1200) if "TRIM this complete script" in user else script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print") as printed:
            out = script_writing.script_refinement(self._state(target_minutes=0))
        self.assertEqual(out["refined_script"], script)
        text = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("cut too deep; keeping the untrimmed text", text)

    def test_soft_trim_at_or_above_the_floor_is_accepted(self):
        script = self._words("w", 2025)
        tight = self._words("t", 1250)                # ≥ 0.9 × 1350 (1215), < before
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return tight if "TRIM this complete script" in user else script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.script_refinement(self._state(target_minutes=0))
        self.assertEqual(out["refined_script"], tight)


class SoftChapterLengthGuardTest(unittest.TestCase):
    """Without a user duration (state target_minutes == 0) a chapter's length is only a
    guide: 20% over its own plan is kept, 1.5× gets ONE trim pass with the TRIM wording."""

    def _bp(self):
        return {"target_minutes": 20, "words_per_minute": 135, "chapters": [
            {"id": "C1", "title": "طويل", "job": "j", "words": 800, "covers": [],
             "opens_loops": [], "closes_loops": [], "depends_on_previous": False},
            {"id": "C2", "title": "ق", "job": "j", "words": 800, "covers": [],
             "opens_loops": [], "closes_loops": [], "depends_on_previous": True},
        ]}

    def _run(self, writer):
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            if "TRIM this chapter" in user:
                return "=== طويل ===\n" + " ".join(f"t{i}" for i in range(900))
            if "id: C1" in user:
                return "=== طويل ===\n" + writer
            return "=== ق ===\nنص قصير"

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.body_restructurer(base_state(blueprint=self._bp()))
        return calls, out

    def test_twenty_percent_over_is_not_trimmed(self):
        writer = " ".join(f"w{i}" for i in range(960))     # 1.2 × 800
        calls, out = self._run(writer)
        self.assertEqual([c for c in calls if "TRIM this chapter" in c], [])
        self.assertIn("w959", out["revised_body"])

    def test_fifty_percent_over_gets_one_trim(self):
        writer = " ".join(f"w{i}" for i in range(1200))    # 1.5 × 800
        calls, out = self._run(writer)
        trim = [c for c in calls if "TRIM this chapter" in c]
        self.assertEqual(len(trim), 1)
        self.assertIn("TRIM this chapter: remove repetition, padding and side tangents only", trim[0])
        self.assertIn("keep every medical point, example that explains something, advice, red flag",
                      trim[0])
        self.assertIn("Aim for about 800 words, but never cut a real point to get there.", trim[0])
        self.assertIn("t899", out["revised_body"])
        self.assertNotIn("w1199", out["revised_body"])

    def _run_trim(self, writer, trim_word):
        seen = []

        def answer(system, user, temperature=None, max_tokens=None):
            seen.append((system, user))
            if "TRIM this chapter" in user:
                return "=== طويل ===\n" + trim_word
            if "id: C1" in user:
                return "=== طويل ===\n" + writer
            return "=== ق ===\nنص قصير"

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print") as printed:
            out = script_writing.body_restructurer(base_state(blueprint=self._bp()))
        return seen, out, printed

    def test_soft_trim_uses_the_padding_prompt_and_floor_sentence(self):
        writer = " ".join(f"w{i}" for i in range(1200))
        seen, _, _ = self._run_trim(writer, " ".join(f"t{i}" for i in range(900)))
        trim = [(s, u) for s, u in seen if "TRIM this chapter" in u]
        self.assertEqual(len(trim), 1)
        self.assertEqual(trim[0][0], load_prompt("script_trim_padding"))
        self.assertIn("Do not go below 760 words.", trim[0][1])   # int(800 × 0.95)

    def test_soft_cut_below_the_floor_keeps_the_untrimmed_chapter(self):
        writer = " ".join(f"w{i}" for i in range(1200))
        _, out, printed = self._run_trim(writer, " ".join(f"t{i}" for i in range(700)))  # < 720
        self.assertIn("w1199", out["revised_body"])
        self.assertNotIn("t699", out["revised_body"])
        text = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("cut too deep; keeping the untrimmed text", text)

    def test_soft_trim_at_or_above_the_floor_is_accepted(self):
        writer = " ".join(f"w{i}" for i in range(1200))
        _, out, _ = self._run_trim(writer, " ".join(f"t{i}" for i in range(740)))  # ≥ 720
        self.assertIn("t739", out["revised_body"])
        self.assertNotIn("w1199", out["revised_body"])


class LengthGuideSoftModeTest(unittest.TestCase):
    """Without a user duration the length lines are guidance; with one they are budgets."""

    def test_body_line_is_a_guide_without_a_user_duration(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "BODY"))
        script_writing.body_restructurer(base_state(blueprint=ONE_CHAPTER))
        prompt = fake.calls[0]["user"]
        self.assertIn("LENGTH GUIDE: about 300 spoken words planned for the body", prompt)
        self.assertIn("do not pad, do not compress real points", prompt)
        self.assertNotIn("LENGTH BUDGET", prompt)

    def test_body_line_is_a_budget_with_a_user_duration(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "BODY"))
        script_writing.body_restructurer(base_state(blueprint=ONE_CHAPTER, target_minutes=5))
        self.assertIn("LENGTH BUDGET: about 300 spoken words", fake.calls[0]["user"])

    def test_refinement_line_is_a_guide_without_a_user_duration(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "REFINED"))
        bp = dict(ONE_CHAPTER, target_minutes=10, words_per_minute=135)
        script_writing.script_refinement(base_state(blueprint=bp, dialect_warmth_output="D"))
        prompt = fake.calls[0]["user"]
        self.assertIn("LENGTH GUIDE: about 1350 spoken words planned for the whole video", prompt)
        self.assertNotIn("TARGET LENGTH", prompt)


class DialectLengthLineTest(unittest.TestCase):
    """The non-chapter dialect prompt names the script's spoken length, but only when
    the blueprint has a target (otherwise the prompt must stay byte-identical)."""

    BP = {"target_minutes": 10, "words_per_minute": 135,
          "chapters": [{"id": "C1", "title": "الفصل الوحيد", "job": "j", "words": 500,
                        "covers": [], "opens_loops": [], "closes_loops": [],
                        "depends_on_previous": False}]}

    LINE = ("LENGTH: the script above has {n} spoken words; keep your rewrite within ±5% — "
            "change wording, do not add new sentences, examples or asides "
            "(the video's length is already budgeted).")

    def test_length_line_present_with_a_target(self):
        frame = use(self, script_writing, "call_llm_json",
                    frame_fake(opening="a b c", disclaimer="d e", closing="j k"))
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "DIALECT"))
        state = base_state(blueprint=self.BP, hook="H", revised_body="f g h i", cta_output="C")
        script_writing.dialect_warmth_layer(state)
        self.assertEqual(len(frame.calls), 1)
        self.assertIn(self.LINE.format(n=11), fake.calls[0]["user"])

    def test_length_line_absent_without_a_target(self):
        frame = use(self, script_writing, "call_llm_json", frame_fake())
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "DIALECT"))
        script_writing.dialect_warmth_layer(
            base_state(blueprint=ONE_CHAPTER, hook="H", revised_body="B", cta_output="C"))
        self.assertEqual(len(frame.calls), 0)
        self.assertNotIn("LENGTH: the script above has", fake.calls[0]["user"])

    def test_length_line_present_in_a_revision_pass(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "DIALECT"))
        state = base_state(blueprint=self.BP, quality_revision_count=1,
                           refined_script="r s t", cta_output="c d",
                           self_critique_output="dialect wording only")
        script_writing.dialect_warmth_layer(state)
        prompt = fake.calls[0]["user"]
        self.assertIn("LENGTH: the script above has 3 spoken words", prompt)
        self.assertIn("REVISION PASS", prompt)


if __name__ == "__main__":
    unittest.main()


class MinWordsTest(unittest.TestCase):
    def test_short_two_chapter_blueprint_stays_single_call(self):
        from unittest import mock
        from medical_brain.nodes import chapters
        bp = {"chapters": [{"id": "C1", "title": "a", "words": 560}, {"id": "C2", "title": "b", "words": 620}]}
        with mock.patch.object(chapters, "CHAPTER_MODE_MIN_WORDS", 1600):
            self.assertFalse(chapters.chapter_mode_on(bp))
            bp["chapters"].append({"id": "C3", "title": "c", "words": 900})
            self.assertTrue(chapters.chapter_mode_on(bp))


class SingleChapterWrittenLengthGuardTest(unittest.TestCase):
    """In soft mode a polishing pass may not grow the writers' script by more than 12%;
    the words past that are padding and are trimmed back to the written length."""

    BP = {"target_minutes": 10, "words_per_minute": 135,
          "chapters": [{"id": "C1", "title": "الفصل الوحيد", "job": "j", "words": 500,
                        "covers": [], "opens_loops": [], "closes_loops": [],
                        "depends_on_previous": False}]}

    def _words(self, prefix, n):
        return " ".join(f"{prefix}{i}" for i in range(n))

    def _state(self, **over):
        # target_minutes=0 on the state means the user gave no duration → soft mode.
        over.setdefault("target_minutes", 0)
        return base_state(blueprint=self.BP, dialect_warmth_output="D", **over)

    def test_written_words_uses_the_frame(self):
        state = self._state(script_frame={"opening": self._words("o", 10), "disclaimer": "",
                                          "closing": self._words("c", 5)},
                            revised_body=self._words("b", 90))
        self.assertEqual(script_writing._written_words(state), 105)

    def test_written_words_without_a_frame_allows_one_hook_and_cta(self):
        state = self._state(revised_body=self._words("b", 100))
        self.assertEqual(script_writing._written_words(state), 350)

    def test_written_words_is_zero_without_a_body(self):
        state = self._state(script_frame={"opening": self._words("o", 10)})
        self.assertEqual(script_writing._written_words(state), 0)

    def test_growth_over_twelve_percent_is_trimmed_to_the_written_length(self):
        grown = self._words("g", 130)          # written = 100, 1.3 ×
        tight = self._words("t", 95)
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return tight if "TRIM this complete script" in user else grown

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print") as printed:
            out = script_writing.script_refinement(self._state(
                script_frame={"opening": self._words("o", 10), "disclaimer": "", "closing": ""},
                revised_body=self._words("b", 90)))

        trim = [c for c in calls if "TRIM this complete script" in c]
        self.assertEqual(len(trim), 1)
        self.assertIn("Aim for about 100 spoken words", trim[0])
        self.assertEqual(out["refined_script"], tight)
        text = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("polishing grew it 130 vs written 100", text)

    def test_growth_within_twelve_percent_is_kept(self):
        script = self._words("g", 110)         # written = 100, 1.1 ×
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return script

        use(self, script_writing, "call_llm", FakeLLM(answer))
        out = script_writing.script_refinement(self._state(
            script_frame={"opening": self._words("o", 10), "disclaimer": "", "closing": ""},
            revised_body=self._words("b", 90)))
        self.assertEqual([c for c in calls if "TRIM this complete script" in c], [])
        self.assertEqual(out["refined_script"], script)

    def test_without_a_frame_the_trim_target_is_body_plus_250(self):
        grown = self._words("g", 400)          # written = 100 + 250 = 350, 1.14 ×
        tight = self._words("t", 360)
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return tight if "TRIM this complete script" in user else grown

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.script_refinement(self._state(revised_body=self._words("b", 100)))

        trim = [c for c in calls if "TRIM this complete script" in c]
        self.assertEqual(len(trim), 1)
        self.assertIn("Aim for about 350 spoken words", trim[0])
        self.assertEqual(out["refined_script"], tight)

    def test_strict_mode_is_unchanged(self):
        grown = self._words("g", 130)          # would trigger the soft check
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return grown

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print") as printed:
            out = script_writing.script_refinement(self._state(
                target_minutes=10,
                script_frame={"opening": self._words("o", 10), "disclaimer": "", "closing": ""},
                revised_body=self._words("b", 90)))

        self.assertEqual([c for c in calls if "TRIM this complete script" in c], [])
        text = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertNotIn("polishing grew it", text)
        self.assertEqual(out["refined_script"], grown)
