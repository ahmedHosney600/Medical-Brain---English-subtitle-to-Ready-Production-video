"""Layer 3 (Script Sources Guide), offline: fake model, no network.

The guide is built AFTER the script is written, so these tests cover: splitting the
production script into numbered sentences with their section, mapping those sentences to
verified facts (with validation and retries), building the numbered source list, the
markdown/description rendering, and the run_video integration.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from medical_brain import cli
from medical_brain.exports import workbook as ew
from medical_brain.research import script_sources as ss
from medical_brain.utils.llm_json import UnreadableAnswer


def research_state():
    return {
        "settings": {"parallel_calls": 1},
        "sources": {
            "S1": {"title": "Cochrane magnesium", "journal": "cochranelibrary.com", "year": 2024,
                   "level": 2, "url": "https://cochrane/1"},
            "S2": {"title": "Mayo Clinic cramps", "journal": "mayoclinic.org", "year": 2023,
                   "level": 5, "url": "https://mayo/1"},
            "S3": {"title": "Merged source", "journal": "Some Journal", "year": 2020,
                   "level": 3, "url": "https://j/3"},
        },
        "facts": [
            {"id": "F1", "status": "verified", "claim": "Dehydration causes cramps",
             "strength": "proven", "source_ids": ["S1"]},
            {"id": "F2", "status": "verified", "claim": "Magnesium is no better than placebo",
             "strength": "no_benefit", "source_ids": ["S1"],
             "also_supported_by": [{"source_ids": ["S3"]}]},
            {"id": "F9", "status": "rejected", "claim": "rejected claim",
             "strength": "n/a", "source_ids": ["S2"]},
        ],
    }


def sentences(*texts, section="Sec A"):
    return [{"n": i + 1, "section": section, "text": t} for i, t in enumerate(texts)]


class FakeJson:
    """Stands in for script_sources.call_llm_json; records calls, returns canned JSON."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def __call__(self, system, user, temperature=None, max_tokens=None, required=()):
        self.calls += 1
        data = self.reply(user, self.calls) if callable(self.reply) else self.reply
        return data, json.dumps(data)


class SplitScriptTest(unittest.TestCase):
    def test_sections_cues_and_short_sentences(self):
        script = ("=== المقدمة ===\n"
                  "[D. Ahmed] الجفاف من أهم أسباب الشد العضلي المفاجئ. اشرب مياه كتير قبل التمرين.\n"
                  "(نبرة هادئة)\n"
                  "قصير.\n"
                  "# الفصل الثاني\n"
                  "هل الماغنسيوم مفيد فعلا للشد؟ الدراسات الكبيرة بتقول إنه مش أفضل من الدواء الوهمي.\n")
        out = ss.split_script(script)
        self.assertEqual([s["n"] for s in out], [1, 2, 3, 4])
        self.assertEqual([s["section"] for s in out],
                         ["المقدمة", "المقدمة", "الفصل الثاني", "الفصل الثاني"])
        self.assertTrue(out[0]["text"].startswith("الجفاف"))
        self.assertTrue(out[2]["text"].endswith("؟"))          # split on the Arabic question mark
        joined = " ".join(s["text"] for s in out)
        self.assertNotIn("D. Ahmed", joined)
        self.assertNotIn("نبرة هادئة", joined)
        self.assertNotIn("قصير", joined)

    def test_decimal_number_stays_in_one_sentence(self):
        out = ss.split_script("النسبة 94.2% من الحالات اتحسنت بشكل واضح جدا.")
        self.assertEqual(len(out), 1)
        self.assertIn("94.2%", out[0]["text"])

    def test_default_section_is_script(self):
        out = ss.split_script("هذه جملة طبية طويلة بما يكفي لتعد.")
        self.assertEqual(out[0]["section"], "Script")


class MapSourcesTest(unittest.TestCase):
    def test_validation_drops_bad_numbers_and_unknown_facts(self):
        st = research_state()
        reply = {"links": [{"sentence": 1, "facts": ["F1", "F99", "[F2]"]},
                           {"sentence": 999, "facts": ["F1"]},
                           {"sentence": 2, "facts": ["F77"]}],
                 "unsupported": [{"sentence": 3, "why": "no fact supports this"}]}
        with mock.patch.object(ss, "call_llm_json", FakeJson(reply)):
            out = ss.map_sources(sentences("one", "two", "three"), st, workers=1)
        self.assertEqual(out["links"], {1: ["F1", "F2"]})       # [F2] accepted, rest dropped
        self.assertEqual([u["n"] for u in out["unsupported"]], [3])
        self.assertEqual(out["unsupported"][0]["text"], "three")
        self.assertEqual(out["unsupported"][0]["section"], "Sec A")
        self.assertEqual(out["unsupported"][0]["why"], "no fact supports this")

    def test_unreadable_answer_retried_once_then_kept(self):
        st = research_state()
        calls = []

        def fake(system, user, temperature=None, max_tokens=None, required=()):
            calls.append(1)
            if len(calls) == 1:
                raise UnreadableAnswer("not json", "raw")
            return {"links": [{"sentence": 1, "facts": ["F1"]}]}, "{}"

        with mock.patch.object(ss, "call_llm_json", fake), mock.patch("builtins.print"):
            out = ss.map_sources(sentences("one"), st, workers=1)
        self.assertEqual(out["links"], {1: ["F1"]})
        self.assertEqual(len(calls), 2)

    def test_unreadable_twice_skips_the_batch(self):
        st = research_state()

        def boom(*a, **k):
            raise UnreadableAnswer("still bad", "raw")

        with mock.patch.object(ss, "call_llm_json", boom), mock.patch("builtins.print"):
            out = ss.map_sources(sentences("one", "two"), st, workers=1)
        self.assertEqual(out, {"links": {}, "unsupported": []})

    def test_batching_150_sentences_is_three_calls(self):
        st = research_state()
        fake = FakeJson({"links": []})
        many = [{"n": i + 1, "section": "S", "text": f"جملة رقم {i + 1} فيها كلمات كتير"} for i in range(150)]
        with mock.patch.object(ss, "call_llm_json", fake):
            ss.map_sources(many, st, batch_size=60, workers=1)
        self.assertEqual(fake.calls, 3)

    def test_empty_script_makes_no_calls(self):
        fake = FakeJson({"links": []})
        with mock.patch.object(ss, "call_llm_json", fake):
            out = ss.map_sources([], research_state())
        self.assertEqual(out, {"links": {}, "unsupported": []})
        self.assertEqual(fake.calls, 0)


class BuildSourcesGuideTest(unittest.TestCase):
    def test_order_first_appearance_also_supported_snippets_and_stats(self):
        long_text = ("هذه جملة عربية طويلة جدا فيها كلمات كتيرة جدا للاختبار " * 4).strip()
        sents = [{"n": 1, "section": "Sec A", "text": long_text},
                 {"n": 2, "section": "Sec A", "text": "جملة ثانية قصيرة هنا"},
                 {"n": 3, "section": "Sec B", "text": "جملة ثالثة قصيرة هنا"}]
        mapping = {"links": {1: ["F2"], 2: ["F1"], 3: ["F2"]},
                   "unsupported": [{"n": 4, "text": "x", "section": "Sec B", "why": "w"}]}
        guide = ss.build_sources_guide(sents, mapping, research_state())
        self.assertEqual([e["n"] for e in guide["entries"]], [1, 2])
        self.assertIn("Cochrane", guide["entries"][0]["ref"])
        self.assertIn("Merged source", guide["entries"][1]["ref"])       # also_supported_by included
        self.assertEqual(guide["entries"][0]["level"], "Systematic review / meta-analysis")
        self.assertEqual(guide["entries"][1]["sections"], ["Sec A", "Sec B"])
        for e in guide["entries"]:
            for snippet in e["supports"]:
                self.assertLessEqual(len(snippet), ss.MAX_SNIPPET)
        self.assertEqual(guide["sections"], ["Sec A", "Sec B"])
        self.assertEqual(guide["stats"],
                         {"sentences": 3, "linked": 3, "unsupported": 1, "sources": 2})

    def test_unlinked_facts_are_not_sources(self):
        sents = [{"n": 1, "section": "Sec A", "text": "جملة طبية طويلة بما يكفي"}]
        mapping = {"links": {1: ["F9"]}, "unsupported": []}   # F9 is rejected, not verified
        guide = ss.build_sources_guide(sents, mapping, research_state())
        self.assertEqual(guide["entries"], [])
        self.assertEqual(guide["stats"]["linked"], 0)         # F9 was ignored, so not linked


class RenderTest(unittest.TestCase):
    def guide(self, unsupported=None):
        return {"entries": [{"n": 1, "ref": "Cochrane magnesium — cochranelibrary.com, 2024 — https://cochrane/1",
                             "level": "Systematic review / meta-analysis", "sections": ["Sec A"],
                             "supports": ["الجفاف سبب مهم"], "sentence_count": 3}],
                "sections": ["Sec A"], "unsupported": unsupported or [], "stats": {}}

    def test_groups_by_section_and_omits_claims_block_when_clean(self):
        md = ss.render_markdown(self.guide())
        self.assertIn("مصادر هذا الفيديو", md)
        self.assertIn("#### Sec A", md)
        self.assertIn("**[1]**", md)
        self.assertIn("يدعم: «الجفاف سبب مهم»", md)
        self.assertNotIn("ادعاءات تحتاج", md)

    def test_claims_block_only_when_unsupported_and_never_in_description(self):
        g = self.guide([{"n": 2, "text": "الماغنسيوم يعالج الشد فورا", "section": "Sec B", "why": "no fact says so"}])
        md = ss.render_markdown(g)
        self.assertIn("ادعاءات تحتاج مراجعة", md)
        self.assertIn("no fact says so", md)
        desc = ss.description_block(g)
        self.assertIn("📚 المصادر:", desc)
        self.assertNotIn("ادعاءات", desc)
        self.assertNotIn("no fact", desc)

    def test_description_ranks_most_linked_then_best_evidence(self):
        g = {"entries": [
            {"n": 1, "ref": "weak", "level_num": 5, "sentence_count": 1, "supports": []},
            {"n": 2, "ref": "strong-but-rare", "level_num": 1, "sentence_count": 1, "supports": []},
            {"n": 3, "ref": "most-used", "level_num": 4, "sentence_count": 5, "supports": []}],
            "sections": [], "unsupported": [], "stats": {}}
        desc = ss.description_block(g, n=2)
        self.assertLess(desc.index("most-used"), desc.index("strong-but-rare"))
        self.assertNotIn("weak", desc)


class RunVideoSourcesIntegrationTest(unittest.TestCase):
    PACKAGE = ("## SCRIPT METADATA\n\nTitle: t\n\n---\n\n"
               "## YOUTUBE PACKAGING\n\nDescription:\n```text\nنص الوصف هنا\n```\n\n---\n\n"
               "## PRODUCTION SCRIPT\n\n=== المقدمة ===\n"
               "الجفاف من أهم أسباب الشد العضلي المفاجئ ونحتاج نعرف السبب الحقيقي.\n\n---\n\n"
               "## INTEGRATED PRODUCTION STORYBOARD\n\n| # | X |\n|---|---|\n| 1 | y |\n")

    class Router:
        def report(self):
            return {"mode": "main", "calls": 0}

    def _run(self, reply, research=None):
        research = research or research_state()
        root = tempfile.mkdtemp()
        with open(os.path.join(root, "input_fields.json"), "w", encoding="utf-8") as f:
            json.dump({}, f)
        out = os.path.join(root, "out")
        os.makedirs(out)
        state = {"final_package": self.PACKAGE, "recommended_title": "t"}
        fake = FakeJson(reply)
        with mock.patch.object(cli, "PROJECT_ROOT", root), \
                mock.patch.object(cli, "build_app", lambda: None), \
                mock.patch.object(cli, "run_graph", lambda *a, **k: state), \
                mock.patch.object(cli, "export_broll_prompt_files", lambda *a, **k: None), \
                mock.patch.object(cli, "export_whiteboard_prompt_files", lambda *a, **k: None), \
                mock.patch.object(cli, "_convert", lambda *a, **k: None), \
                mock.patch.object(ss, "call_llm_json", fake), \
                mock.patch("builtins.print"):
            cli.run_video(self.Router(), "original script", output_dir=out, research=research)
        with open(os.path.join(out, "3_video", "01_final_script.md"), encoding="utf-8") as f:
            doc = f.read()
        return doc, os.path.join(out, "3_video", "logs")

    def test_appendix_replaced_and_description_block_inserted(self):
        doc, out = self._run({"links": [{"sentence": 1, "facts": ["F1"]}]})
        self.assertIn("مصادر هذا الفيديو", doc)
        self.assertNotIn("VERIFIED SOURCES", doc)
        self.assertIn("📚 المصادر:", doc)                      # description list
        self.assertIn("cochranelibrary.com", doc)
        self.assertTrue(os.path.exists(os.path.join(out, "sources_map.json")))
        with open(os.path.join(out, "sources_map.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["mapping"]["links"], {"1": ["F1"]})   # JSON object keys are strings
        self.assertEqual(saved["guide"]["stats"]["linked"], 1)

    def test_total_failure_falls_back_to_full_reference_list(self):
        doc, out = self._run({"links": []})
        self.assertIn("VERIFIED SOURCES", doc)
        self.assertNotIn("مصادر هذا الفيديو", doc)
        self.assertFalse(os.path.exists(os.path.join(out, "sources_map.json")))


if __name__ == "__main__":
    unittest.main()
