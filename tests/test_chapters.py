"""Phase 5a: long videos are written chapter by chapter from a script blueprint and a
shared script memory, then checked as one whole by an integrity pass. A one-chapter
blueprint (≤ ~10 min) keeps exactly the old single-call behaviour."""
import json
import re
import unittest
from unittest import mock

from medical_brain.nodes import analysis, chapters, script_writing


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


class DialectChapterModeTest(unittest.TestCase):
    def _answers(self, system, user, temperature=None, max_tokens=None):
        label = re.search(r"SEGMENT LABEL: (.*)", user).group(1).strip()
        return f"[{label} rewritten]\n=== SEGMENT NOTES ===\nDialect 9 / Warmth 9"

    def _state(self, **over):
        body = ("=== الفصل الأول ===\nنص أول\n\n=== الفصل الثاني ===\nنص ثاني")
        return base_state(blueprint=BP, hook="هوك الأصلي", revised_body=body,
                          cta_output="كول تو أكشن", chapter_workers=1, **over)

    def test_segments_rewritten_joined_and_notes_collected(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(self._answers))
        out = script_writing.dialect_warmth_layer(self._state())
        self.assertEqual(len(fake.calls), 4)                       # hook + 2 chapters + CTA
        for piece in ("[HOOK rewritten]", "[الفصل الأول rewritten]",
                      "[الفصل الثاني rewritten]", "[CTA / OUTRO rewritten]"):
            self.assertIn(piece, out["dialect_warmth_output"])
        self.assertIn("## SEGMENT NOTES", out["dialect_warmth_output"])
        self.assertIn("### الفصل الأول", out["dialect_warmth_output"])
        self.assertNotIn("SEGMENT NOTES", out["dialect_script"])
        self.assertIn("=== الفصل الأول ===", fake.calls[1]["user"])       # header kept in the segment
        c2 = next(c for c in fake.calls if "SEGMENT LABEL: الفصل الثاني" in c["user"])
        self.assertIn("نص أول", c2["user"])                              # neighbour last lines
        self.assertEqual(fake.calls[0]["max_tokens"], chapters.budget_for_words(len("هوك الأصلي".split())))

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


class SingleChapterUnchangedTest(unittest.TestCase):
    """A one-chapter (or absent) blueprint must make exactly the old calls."""

    def test_body_prompt_unchanged(self):
        fake = use(self, script_writing, "call_llm", FakeLLM(lambda *a: "BODY"))
        script_writing.body_restructurer(base_state(blueprint={}))
        old = dict(fake.calls[0])
        fake.calls.clear()
        script_writing.body_restructurer(base_state(blueprint=ONE_CHAPTER))
        new = fake.calls[0]
        self.assertEqual(old["system"], new["system"])
        self.assertEqual(old["user"], new["user"])
        self.assertEqual(new["max_tokens"], 9000)
        self.assertNotIn("SCRIPT BLUEPRINT", new["user"])
        self.assertNotIn("CHAPTER MODE", new["system"])

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


if __name__ == "__main__":
    unittest.main()
