"""Fixes from the whole-project review (run 20261007_170536 + code review of every layer)."""
import builtins
import inspect
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx
import openai

from medical_brain import llm
from medical_brain.exports import workbook as ew
from medical_brain.exports.prompt_files import export_broll_prompt_files, export_whiteboard_prompt_files
from medical_brain.llm.errors import StepFailed, StepTimeout, api_failure
from medical_brain.nodes import packaging, quality, script_writing, shorts
from medical_brain.routing import route_translation_fidelity
from tests.test_run_fixes import Canned, use_router

REQ = httpx.Request("POST", "http://127.0.0.1/v1/chat/completions")


def status_error(cls, code, message):
    return cls(message, response=httpx.Response(code, request=REQ), body=None)


class ModelLayerTest(unittest.TestCase):
    def canvas(self):
        cfg = dict(llm.DEFAULT_PROVIDERS["gemini"], api_key="k", retry_wait_seconds=0)
        return llm.make_backend("gemini:g", {"gemini": cfg})

    def reply(self, text, finish):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text))])

    def test_canvas_cut_answer_is_resent_bigger_then_reported(self):
        backend = self.canvas()
        limits = []
        backend._create = lambda params: (limits.append(params["max_tokens"]), self.reply("half", "length"))[1]
        with self.assertRaises(llm.AnswerCutOff) as cm:
            backend.call("s", "u", 0.3, 12000)
        self.assertEqual(limits, [16000, 32000])
        self.assertEqual(cm.exception.text, "half")
        backend._create = lambda params: self.reply("whole answer", "stop")
        self.assertEqual(backend.call("s", "u", 0.3, 12000), "whole answer")

    def test_errors_only_take_a_provider_out_when_they_last(self):
        self.assertIsInstance(api_failure(status_error(openai.RateLimitError, 429, "Rate limit reached for requests"),
                                          openai), StepFailed)
        self.assertIsInstance(api_failure(status_error(openai.RateLimitError, 429, "You exceeded your current quota"),
                                          openai), llm.ProviderUnavailable)
        self.assertIsInstance(api_failure(openai.APITimeoutError(request=REQ), openai), StepTimeout)
        self.assertIsInstance(api_failure(status_error(openai.InternalServerError, 503, "overloaded"), openai),
                              StepFailed)
        self.assertFalse(llm.errors._looks_unavailable("prompt is too long: context limit exceeded"))
        self.assertTrue(llm.errors._looks_unavailable("You've hit your weekly limit · resets Oct 8"))

    def test_bad_judge_model_does_not_take_its_provider_down(self):
        c = llm.load_llm_config()
        c.update(main_model="w:main", backup_model="d:good", hybrid_routes={"judge": "d:typo"})
        router = llm.LLMRouter(c, provider="hybrid")
        with mock.patch("medical_brain.llm.router.test_model",
                        lambda spec, providers: (spec != "d:typo", "model not found")):
            router.select()
        self.assertIn("d:typo", router.down_specs)
        self.assertFalse(router._is_down("d:good"))

    def test_empty_answer_falls_back(self):
        c = llm.load_llm_config()
        c.update(main_model="w:main", backup_model="b:backup", hybrid_routes={})
        router = llm.LLMRouter(c, provider="main")
        router._backends = {"w:main": SimpleNamespace(call=lambda *a: "  "),
                            "b:backup": SimpleNamespace(call=lambda *a: "answer")}
        self.assertEqual(router.call("s", "u"), "answer")

    def test_repair_call_carries_the_request_end_and_fields(self):
        answers = iter(["not json", '{"grade": "PASS"}'])
        router = use_router(self, Canned())
        router.call = lambda s, u, *a, **k: (router.prompts.append(u), next(answers))[1]
        llm.call_llm_json("s", "...long input...\nOutput ONLY the JSON object with grade.", required=["grade"])
        self.assertIn("Output ONLY the JSON object with grade.", router.prompts[1])
        self.assertIn("Fields the answer must contain: grade", router.prompts[1])

    def test_setup_stops_on_no_input(self):
        from medical_brain import setup_models
        with mock.patch.object(builtins, "input", side_effect=EOFError):
            with self.assertRaises(SystemExit):
                setup_models.ask("Re-pick? [Y/n] ", "y")

    def test_gitignore_line_is_not_glued_to_the_last_one(self):
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, ".gitignore"), "w") as f:
            f.write("__pycache__/")                     # no trailing newline
        os.environ.pop("MB_KEY_TEST", None)
        self.addCleanup(os.environ.pop, "MB_KEY_TEST", None)
        llm.save_key("MB_KEY_TEST", "x", os.path.join(folder, "llm_keys.env"))
        with open(os.path.join(folder, ".gitignore")) as f:
            self.assertEqual(f.read().splitlines(), ["__pycache__/", "llm_keys.env"])


class NodesTest(unittest.TestCase):
    def test_grades_with_plus_minus(self):
        self.assertEqual([quality._letter_grade(g) for g in ("A-", "B+", "A+", "PASS", "**A**", "", "FAIL")],
                         ["A", "B", "A+", "A", "A", "F", "F"])

    def test_last_failed_round_never_ships_an_unchecked_rewrite(self):
        use_router(self, Canned(json.dumps({"critique_grade": "B", "dialect_authenticity_score": 9, "warmth_score": 9,
                                            "fidelity_score": 9, "medical_accuracy_pass": True,
                                            "revised_script": "UNCHECKED REWRITE"})))
        state = {"refined_script": "VERIFIED", "fidelity_score": 9, "medical_accuracy_pass": True,
                 "truth_score": 8, "truth_pass": False, "quality_revision_count": 1, "max_quality_revision_count": 2}
        out = quality.self_critique(state)               # round 2 of 2, best so far, failed
        self.assertEqual(out["refined_script"], "VERIFIED")

    def test_critique_sees_the_judges_scores(self):
        router = use_router(self, Canned("{}"))
        quality.self_critique({"fidelity_score": 9, "medical_accuracy_pass": True, "truth_score": 9, "truth_pass": True})
        self.assertIn("Fidelity Score: 9/10 | Medical Accuracy Pass: True", router.prompts[0])

    def test_revisions_get_their_previous_output(self):
        router = use_router(self, Canned("x"))
        script_writing.cta_retention_writer({"quality_revision_count": 1, "cta_output": "OLD CTA"})
        shorts.shorts_script_extractor({"shorts_revision_count": 1, "shorts_quality_output": "fix clip 2",
                                        "shorts_scripts": "OLD SHORTS"})
        packaging.packaging_generator({"packaging_revision_count": 1, "packaging_critique_output": "fix title 3",
                                       "title_options": "OLD TITLES", "thumbnail_concepts": "OLD THUMBS"})
        for prompt, old in zip(router.prompts, ("OLD CTA", "OLD SHORTS", "OLD TITLES")):
            self.assertIn(old, prompt)

    def test_shorts_gate_reads_8_of_10(self):
        use_router(self, Canned(json.dumps({"shorts_quality_grade": "PASS", "per_clip_scores": [
            {"standalone_impact": "9/10", "scroll_stop_power": "8/10", "curiosity_gap": 8,
             "medical_responsibility": "PASS"}]})))
        self.assertEqual(shorts.shorts_quality_gate({})["shorts_quality_grade"], "PASS")

    def test_translation_route_respects_the_grade(self):
        state = {"naturalness_score": 9, "contextual_alignment_score": 9, "translation_revision_count": 1,
                 "max_translation_revision_count": 3}
        self.assertEqual(route_translation_fidelity(dict(state, translation_grade="NEEDS_REVISION")),
                         "body_restructurer")
        self.assertEqual(route_translation_fidelity(dict(state, translation_grade="PASS")), "cta_retention_writer")

    def test_unreadable_packaging_audit_clears_old_picks(self):
        use_router(self, Canned("not json"))
        out = packaging.packaging_honesty_ctr_auditor({"recommended_title": "old", "packaging_ab_test_set": "old"})
        self.assertEqual((out["recommended_title"], out["packaging_ab_test_set"]), ("", ""))


BROLL = ("## AI B-ROLL GENERATION PROMPTS\n\n### B-Roll Prompt Table\n"
         "| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt | Composition Notes |\n"
         "|---|---|---|---|---|---|---|\n"
         "| 1 | 0:05 | a | b | 🎬 Video | **Action**: artery wall layers thickening \\| slow push-in, macro lens | x |\n"
         "| 2 | 0:15 | a | b | 🎬 Video | A man holding an X-ray image up to a window, slow dolly | Payoff after Drawing #1 |\n"
         "| 3 | 0:25 | a | b | 🖼️ Image | A glass vial of blood on a steel tray in soft daylight | Ken Burns push-in |\n"
         "\n### B-Roll Density Summary\n| Act | Total |\n|---|---|\n| Hook | 3 |\n")


class ExportsTest(unittest.TestCase):
    def test_broll_export_keeps_every_row_and_classifies_by_type(self):
        folder = tempfile.mkdtemp()
        images, videos = export_broll_prompt_files("", fallback_broll=BROLL, output_dir=folder)
        self.assertEqual((len(images), len(videos)), (1, 2))
        self.assertIn("layers thickening / slow push-in", videos[0])      # escaped pipe kept, no markdown
        self.assertNotIn("**", videos[0])
        self.assertIn("X-ray image", videos[1])                          # "image" in a video prompt

    def test_converter_no_longer_overwrites_the_prompt_files(self):
        from medical_brain.exports import convert
        self.assertNotIn("export_broll_prompt_files_from_markdown(", inspect.getsource(convert.convert_markdown_file))

    def test_storyboard_rows_get_the_approved_prompt(self):
        sb = ("### 🎬 INTEGRATED PRODUCTION STORYBOARD\n| # | Act | T | S | E | Layer | Detail |\n|---|---|---|---|---|---|---|\n"
              "| 4 | 1 | 0:15 | a | b | B-ROLL | 🎬 Video | B-roll #2 | Dur: 4s |\n")
        self.assertIn("holding an X-ray image", ew.fill_broll_rows(sb, BROLL))

    def test_whiteboards_come_from_the_overlay_guide_when_the_storyboard_has_none(self):
        guide = ("#### 1. Heart\n- **Whiteboard image prompt**: plain white whiteboard, a heart outline\n"
                 "- **Draw-on video prompt**: a hand draws the heart, 8 seconds\n")
        images, videos = export_whiteboard_prompt_files("no storyboard", tempfile.mkdtemp(), overlay_guide=guide)
        self.assertEqual(images, ["plain white whiteboard, a heart outline"])
        self.assertEqual(videos, ["a hand draws the heart, 8 seconds"])

    def test_table_split_by_act_headings_is_one_table(self):
        block = ("| # | Act |\n|---|---|\n| 1 | a |\n\n#### ACT 2\n| # | Act |\n|---|---|\n| 2 | b |\n"
                 "\n#### Density\n| Act | n |\n|---|---|\n| Hook | 4 |\n")
        self.assertEqual(ew.table_rows(block), [["1", "a"], ["2", "b"]])

    def test_durations(self):
        self.assertEqual([ew._parse_duration(x) for x in ("1.5 min", "45 ثانية", "1m 40s", "1:05", "٢ دقائق")],
                         [90, 45, 100, 65, 120])

    def test_old_chapter_lines_in_any_form_are_replaced(self):
        text = ("### 🚀 YOUTUBE PACKAGING\n⏱️ الفصول (Chapters):\n- 0:00 قديم\n**1:30** قديم كمان\n\n#hashtag\n"
                "### 📹 VIDEO SECTIONS\n| # | Title | Start | End | Duration |\n|---|---|---|---|---|\n"
                "| 1 | ليه الساعة بتقلقك؟ | a | b | 1:30 |\n| 2 | الرقم الطبيعي كذبة | c | d | 2:00 |\n"
                "| 3 | إمتى تروح للدكتور | e | f | 1:00 |\n")
        out = ew.rebuild_description_chapters(text)
        self.assertNotIn("قديم", out)
        self.assertIn("0:00 ليه الساعة بتقلقك؟", out)

    def test_package_warning(self):
        self.assertIn("THIN STORYBOARD", ew.package_warning({"final_package_issues": ["Part 2: THIN STORYBOARD: 6 rows"]}))
        self.assertEqual(ew.package_warning({}), "")


if __name__ == "__main__":
    unittest.main()
