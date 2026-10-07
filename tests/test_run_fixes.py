"""Fixes found by reviewing a real run (output/20261007_121509): cue repair,
quality-loop revisions, the critique/auditor verdict, keys out of git, small export bugs."""
import json
import os
import tempfile
import unittest

from medical_brain import llm
from medical_brain.cli import with_heading
from medical_brain.exports import workbook as ew
from medical_brain.nodes.final_package import final_script_package, whiteboard_problems
from medical_brain.nodes.quality import self_critique
from medical_brain.nodes.script_writing import dialect_warmth_layer


class Canned:
    """Router that records prompts and answers with a fixed text."""

    def __init__(self, answer=""):
        self.answer, self.prompts = answer, []

    def call(self, system, user, temperature=None, max_tokens=None):
        self.prompts.append(user)
        return self.answer


def use_router(test, router):
    llm.set_router(router)
    test.addCleanup(llm.set_router, None)
    return router


SCRIPT = """### 🎬 PRODUCTION SCRIPT
(الكاميرا قريبة، نبرة هادئة)
تعالوا ناخد مثال حي بيقلق ناس كتير: "الرجفان الأذيني" أو الـ (AF).
الفرق بين "صورة سيلفي" و"كاميرا مراقبة" شغالة 24 ساعة.
لما بتروح المعمل بتاخد صورة واحدة بس من يومك، فابقوا معايا.
"""

STORYBOARD = """## INTEGRATED PRODUCTION STORYBOARD
| # | Act | Time | START CUE | END CUE | Layer | Detail |
|---|---|---|---|---|---|---|
| 1 | 1 | 0:00 | تعالوا ناخد مثال حي | أو الـ (AF) | A-ROLL | talk |
| 2 | 1 | 0:10 | صورة سيلفي وكاميرا مراقبة | 24 ساعة | B-ROLL | watch |
| 3 | 1 | 0:20 | المعمل بتاخد صورة | فابقوا معايا | A-ROLL | talk |
"""


class CueTest(unittest.TestCase):
    def test_spoken_terms_in_parentheses_are_kept(self):
        norm = ew.normalize_ar(ew.spoken_text(SCRIPT))
        self.assertIn("af", norm.lower())
        self.assertNotIn("الكاميرا قريبه", norm)          # stage direction dropped
        self.assertIsNone(ew.cue_problem("ناس كتير الرجفان الأذيني أو الـ (AF)", norm))
        self.assertIsNone(ew.cue_problem("صورة سيلفي وكاميرا مراقبة", norm))   # quotes glued to words

    def test_repair_makes_every_cue_exact(self):
        self.assertTrue(ew.check_cue_rows(
            [(r["num"], r["start"], r["end"]) for r in ew.parse_storyboard(STORYBOARD)], SCRIPT))
        text, fixed, _ = ew.repair_cues(STORYBOARD, SCRIPT)
        rows = ew.parse_storyboard(text)
        self.assertGreater(fixed, 0)
        self.assertEqual(ew.check_cue_rows([(r["num"], r["start"], r["end"]) for r in rows], SCRIPT), [])
        self.assertTrue(rows[2]["end"].endswith("فابقوا معايا"))    # short END cue grew to the left
        self.assertNotIn('"', text.split("INTEGRATED")[1])


    def test_cue_copied_from_a_visual_note_goes_next_to_it(self):
        script = ("### 🎬 PRODUCTION SCRIPT\nالعلم بيحدد الطبيعي بطريقة إحصائية واضحة جداً.\n"
                  "[KINETIC TEXT: الطبيعي = اختيار إحصائي]\n"
                  "المشكلة بقى إن الفحوصات اللي متعودين عليها مجرد لقطة سريعة.\n")
        table = ("## INTEGRATED PRODUCTION STORYBOARD\n| # | Act | Time | START CUE | END CUE | Layer | Detail |\n"
                 "|---|---|---|---|---|---|---|\n| 1 | 1 | 0:00 | الطبيعي = اختيار إحصائي | الطبيعي = اختيار إحصائي | KINETIC | x |\n")
        row = ew.parse_storyboard(ew.repair_cues(table, script)[0])[0]
        self.assertTrue(row["start"].startswith("المشكلة بقى"))
        # END from the same note can't come before START: it closes START's sentence.
        self.assertTrue(row["end"].endswith("لقطة سريعة"))

    def test_cue_from_a_stage_direction_and_waw_prefix(self):
        script = ("### 🎬 PRODUCTION SCRIPT\nده عامل خطر معروف للجلطات والسكتة الدماغية عند الكبار.\n\n"
                  "**(أنيميشن لملعب كرة، وحكم راية بيرفع التسلل)**\n\n"
                  "الموضوع عامل زي حكم الراية في الكورة بالظبط.\n")
        table = ("## INTEGRATED PRODUCTION STORYBOARD\n| # | Act | Time | START CUE | END CUE | Layer | Detail |\n"
                 "|---|---|---|---|---|---|---|\n"
                 "| 1 | 1 | 0:00 | ده عامل خطر معروف | السكتة الدماغية | B-ROLL | x |\n"
                 "| 2 | 1 | 0:10 | الموضوع عامل زي حكم | التسلل | B-ROLL | y |\n")
        text, _, _ = ew.repair_cues(table, script)
        rows = ew.parse_storyboard(text)
        self.assertEqual(ew.check_cue_rows([(r["num"], r["start"], r["end"]) for r in rows], script), [])
        self.assertTrue(rows[0]["end"].endswith("والسكتة الدماغية"))
        self.assertTrue(rows[1]["end"].endswith("في الكورة بالظبط"))

    def test_filler_words_alone_do_not_place_a_cue(self):
        idx = ew._ScriptIndex("تحس فجأة إن قلبك خبط في صدرك، زي ما تكون زغطة بسيطة.")
        self.assertIsNone(ew._locate(ew.normalize_ar("زي ما شفنا في قصة").split(), idx, 0, 5, 9))


class VerifiedScriptTest(unittest.TestCase):
    REFINED = ("SCRIPT: عنوان\nWord Count: 1620 words\nMedical Fidelity Score: 10/10\n\n---\n\n[HOOK]\n"
               "تخيل كدة وإنت قاعد في أمان الله وساعتك بتزن في إيدك.\n\n---\n\n"
               "POLISH SUMMARY:\n- Word count: ~1620\n")

    def test_filming_script_drops_metadata_and_summary(self):
        script = ew.filming_script(self.REFINED)
        self.assertTrue(script.startswith("[HOOK]"))
        self.assertIn("ساعتك بتزن", script)
        for gone in ("Word Count", "POLISH", "Fidelity Score"):
            self.assertNotIn(gone, script)

    def test_replace_section_with_and_without_heading(self):
        with_heading = "### Script Metadata\nm\n### 🎬 PRODUCTION SCRIPT\nREWRITTEN\n### WHAT CHANGED\nw\n"
        out = ew.replace_section(with_heading, "PRODUCTION SCRIPT", "VERIFIED")
        self.assertIn("### 🎬 PRODUCTION SCRIPT", out)
        self.assertEqual(ew._strip_rules(ew.production_script(out)), "VERIFIED")
        self.assertNotIn("REWRITTEN", out)
        out = ew.replace_section("### Script Metadata\nm\n### WHAT CHANGED\nw\n", "PRODUCTION SCRIPT", "VERIFIED")
        self.assertLess(out.index("VERIFIED"), out.index("WHAT CHANGED"))

    def test_final_package_films_the_verified_script(self):
        part1 = ("## SCRIPT PACKAGE\n### 🎬 PRODUCTION SCRIPT\nسكريبت تاني خالص كتبه الموديل من دماغه.\n"
                 "### WHAT CHANGED FROM THE ORIGINAL\n- x\n")
        router = use_router(self, Canned(part1))
        router.call = lambda system, user, *a, **k: part1 if "Compile Part 1" in user else "Part 2"
        out = final_script_package({"refined_script": self.REFINED})["final_package"]
        self.assertIn("ساعتك بتزن في إيدك", ew.production_script(out))
        self.assertNotIn("كتبه الموديل", out)
        self.assertNotIn("POLISH SUMMARY", out)


class QualityLoopTest(unittest.TestCase):
    def test_critique_cannot_raise_the_auditor_verdict(self):
        use_router(self, Canned(json.dumps({
            "critique_grade": "A", "dialect_authenticity_score": 9, "warmth_score": 9,
            "fidelity_score": 10, "medical_accuracy_pass": True, "revised_script": "x"})))
        out = self_critique({"fidelity_score": 7, "medical_accuracy_pass": False,
                             "truth_score": 9, "truth_pass": True})
        self.assertEqual(out["fidelity_score"], 7)
        self.assertFalse(out["medical_accuracy_pass"])
        self.assertNotEqual(out["quality_grade"], "PASS")

    def test_revision_pass_keeps_the_fact_checked_script(self):
        router = use_router(self, Canned("rewritten"))
        state = {"revised_body": "OLD BODY", "cta_output": "OLD CTA", "hook": "HOOK",
                 "refined_script": "FACT-CHECKED SCRIPT", "truth_verification_report": "TRUTH REPORT",
                 "fidelity_audit_output": "FIDELITY REPORT", "self_critique_output": "CRITIQUE"}
        dialect_warmth_layer(state)
        first = router.prompts[-1]
        self.assertIn("OLD BODY", first)
        self.assertNotIn("TRUTH REPORT", first)
        dialect_warmth_layer({**state, "quality_revision_count": 1})
        revision = router.prompts[-1]
        self.assertIn("FACT-CHECKED SCRIPT", revision)
        self.assertNotIn("OLD BODY", revision)
        for report in ("TRUTH REPORT", "FIDELITY REPORT", "CRITIQUE", "OLD CTA"):   # CTA revision kept
            self.assertIn(report, revision)


class ConfigTest(unittest.TestCase):
    def test_api_key_goes_to_keys_file_not_config(self):
        folder = tempfile.mkdtemp()
        cfg_path, keys_path = os.path.join(folder, "llm_variables.json"), os.path.join(folder, "llm_keys.env")
        env = "MB_TEST_PROXY_TOKEN"
        self.addCleanup(os.environ.pop, env, None)
        config = llm.load_llm_config(cfg_path)
        config["providers"]["gemini"] = dict(config["providers"]["gemini"], api_key="secret-token", api_key_env=env)
        llm.save_llm_config(config, cfg_path, keys_path)
        with open(cfg_path) as f:
            self.assertNotIn("secret-token", f.read())
        with open(keys_path) as f:
            self.assertIn(f"{env}=secret-token", f.read())
        with open(os.path.join(folder, ".gitignore")) as f:
            self.assertIn("llm_keys.env", f.read().splitlines())
        self.assertEqual(llm.provider_key(llm.load_llm_config(cfg_path)["providers"]["gemini"]), "secret-token")


class ProxyTokenTest(unittest.TestCase):
    def setUp(self):
        from medical_brain.llm import config
        self.config = config
        config._proxy_token_cache.clear()
        self.addCleanup(config._proxy_token_cache.clear)
        home = tempfile.mkdtemp()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = home
        self.addCleanup(os.environ.__setitem__, "HOME", old_home or "")
        self.home = home
        os.environ["MB_TEST_TOKEN"] = "your_api_key_here"
        self.addCleanup(os.environ.pop, "MB_TEST_TOKEN", None)
        self.cfg = {"type": "canvas_proxy", "api_key_env": "MB_TEST_TOKEN"}

    def test_placeholder_is_not_a_key_and_proxy_token_is_found(self):
        self.assertIsNone(llm.provider_key(self.cfg))
        folder = os.path.join(self.home, "Desktop", "gemini-canvas-proxy-main", "native_host")
        os.makedirs(folder)
        with open(os.path.join(folder, ".proxy_token"), "w") as f:
            f.write("real-token\n")
        self.config._proxy_token_cache.clear()
        self.assertEqual(llm.provider_key(self.cfg), "real-token")
        os.environ["MB_TEST_TOKEN"] = "env-token"
        self.assertEqual(llm.provider_key(self.cfg), "env-token")

    def test_run_stops_before_starting_when_main_model_is_down(self):
        router = llm.LLMRouter({**llm.load_llm_config(), "main_model": "gemini:x", "backup_model": "gemini:x",
                                "hybrid_routes": {}}, provider="main")
        import medical_brain.llm.router as r
        old = r.test_model
        r.test_model = lambda spec, providers: (False, "token missing")
        self.addCleanup(setattr, r, "test_model", old)
        with self.assertRaises(llm.ProviderUnavailable):
            router.select()


class ExportTest(unittest.TestCase):
    def test_whiteboard_rules(self):
        bad = ('heart | IMAGE PROMPT: heart line art, black background, no text | DRAW-ON PROMPT: dark background '
               '| LABELS IN PREMIERE: "قلب رياضي" at 9:47 | Dur: 7s')
        good = ('heart | IMAGE PROMPT: plain white whiteboard, black marker line art | DRAW-ON PROMPT: a hand '
                'with a black marker draws, white background | LABELS IN PREMIERE: "قلب رياضي" at "قلب رياضي بيدق" | Dur: 7s')
        self.assertEqual(len(whiteboard_problems(bad)), 2)
        self.assertEqual(whiteboard_problems(good), [])

    def test_shorts_heading_not_doubled(self):
        self.assertEqual(with_heading("REELS & SHORTS SCRIPTS", "---\n\n## REELS & SHORTS SCRIPTS\n\nx").count("SCRIPTS"), 1)
        self.assertTrue(with_heading("REELS & SHORTS SCRIPTS", "x").startswith("## REELS"))

    def test_ab_test_set_in_workbook(self):
        package = SCRIPT + "\n" + STORYBOARD
        md = ew.build_editing_workbook(package, {"recommended_title": "عنوان", "packaging_ab_test_set": "1. a\n2. b"})
        self.assertIn("A/B TEST SET", md)
        self.assertIn("**Recommended title:** عنوان", md)


class JsonAnswersTest(unittest.TestCase):
    """Reading JSON works the same whichever model answered."""

    def test_parse_handles_every_models_habits(self):
        from medical_brain.utils.llm_json import parse_llm_json
        self.assertEqual(parse_llm_json('{"a": "he flagged this as a "lost number" here", "b": 1}')["a"],
                         'he flagged this as a "lost number" here')                     # stray quotes
        self.assertEqual(parse_llm_json('Sure! ```json\n{"a": 1}\n``` hope it helps')["a"], 1)
        self.assertEqual(parse_llm_json('Result: {"a": {"b": 2}} done')["a"]["b"], 2)
        self.assertEqual(parse_llm_json('{"a": True, "b": [1, 2,],}'), {"a": True, "b": [1, 2]})
        self.assertEqual(parse_llm_json('{"a": "line one\nline two"}')["a"], "line one\nline two")
        with self.assertRaises(ValueError):
            parse_llm_json("no json here")

    def test_pick_finds_nested_and_renamed_fields(self):
        from medical_brain.utils.llm_json import pick
        data = {"scores": {"dialect_score": "9/10", "medical_pass": "yes"}}
        self.assertEqual(pick(data, "dialect_authenticity_score", "dialect_score", kind=int), 9)
        self.assertIs(pick(data, "medical_accuracy_pass", "medical_pass", kind=bool), True)
        self.assertIsNone(pick(data, "warmth_score", kind=int))

    def test_one_repair_call_only_when_needed(self):
        router = use_router(self, Canned('{"grade": "PASS", "score": 9}'))
        data, _ = llm.call_llm_json("s", "u", required=["grade"])
        self.assertEqual((data["grade"], len(router.prompts)), ("PASS", 1))
        answers = iter(['{"grade": "PASS", "note": "a "quoted" word" "broken', '{"grade": "PASS"}'])
        router = use_router(self, Canned())
        router.call = lambda s, u, *a, **k: (router.prompts.append(u), next(answers))[1]
        data, _ = llm.call_llm_json("s", "u", required=["grade"])
        self.assertEqual(len(router.prompts), 2)
        self.assertIn("PREVIOUS ANSWER", router.prompts[1])
        self.assertEqual(data["grade"], "PASS")


class QualityGateTest(unittest.TestCase):
    JUDGES = {"fidelity_score": 9, "medical_accuracy_pass": True, "truth_score": 9, "truth_pass": True,
              "refined_script": "VERIFIED SCRIPT", "quality_revision_count": 2, "max_quality_revision_count": 4}

    def test_missing_scores_are_not_zero_and_verified_script_moves_on(self):
        use_router(self, Canned(json.dumps({"critique_grade": "A", "critique_report": "all gates passed",
                                            "revised_script": "UNCHECKED REWRITE"})))
        out = self_critique(dict(self.JUDGES, dialect_score=9, warmth_score=9))
        self.assertEqual(out["quality_grade"], "PASS")
        self.assertEqual(out["fidelity_score"], 9)
        self.assertEqual(out["refined_script"], "VERIFIED SCRIPT")

    def test_last_round_keeps_the_best_script(self):
        use_router(self, Canned(json.dumps({"critique_grade": "A", "dialect_authenticity_score": 9, "warmth_score": 9,
                                            "fidelity_score": 9, "medical_accuracy_pass": True})))
        state = dict(self.JUDGES)
        state.update(self_critique(state))                       # round 3 passes and becomes the best
        self.assertEqual(state["best_script"], "VERIFIED SCRIPT")
        state.update(refined_script="WORSE SCRIPT", fidelity_score=7, medical_accuracy_pass=False,
                     quality_grade="C")                          # round 4: the rewrite got worse
        use_router(self, Canned("not json at all"))
        out = self_critique(state)
        self.assertEqual(out["refined_script"], "VERIFIED SCRIPT")
        self.assertEqual(out["quality_grade"], "PASS")
        self.assertTrue(out["medical_accuracy_pass"])

    def test_overview_warns_when_the_gate_never_passed(self):
        failed = {"quality_revision_count": 4, "quality_grade": "C", "fidelity_score": 7,
                  "medical_accuracy_pass": False, "truth_score": 9, "truth_pass": True}
        self.assertIn("did not pass after 4 rounds", ew.quality_warning(failed))
        self.assertEqual(ew.quality_warning(dict(failed, quality_grade="PASS", medical_accuracy_pass=True)), "")
        self.assertIn("did not pass", ew.build_editing_workbook(SCRIPT + "\n" + STORYBOARD, failed))


class BrollEngagementTest(unittest.TestCase):
    HEAD = ("| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Script Context | Type | Main Cue | AI Generation Prompt "
            "| Composition Notes | Dur |\n|---|---|---|---|---|---|---|---|---|---|\n")

    def test_flags_static_clips_text_in_images_and_gaps(self):
        from medical_brain.nodes.production import broll_engagement_problems
        table = self.HEAD + (
            '| 1 | 0:40 | a | b | c | 🎬 Video | watch | Close-up of a wrist, the watch displays "45 BPM". '
            'Avoid: AI artifacts. | center | 4s |\n'
            '| 2 | 3:00 | a | b | c | 🖼️ Image | paper | A lab paper on a desk. | center | 4s |\n')
        problems = " ".join(broll_engagement_problems(table, "", 10))
        for expected in ("no camera move", "on-screen text", "no Ken Burns", "Only 2 B-rolls",
                         "first 20 seconds", "between 0:40 and 3:00"):
            self.assertIn(expected, problems)

    def test_engaging_prompts_pass(self):
        from medical_brain.nodes.production import broll_engagement_problems
        rows = "".join(f'| {i} | {i // 2}:{(i % 2) * 30:02d} | a | b | c | 🎬 Video | x | A man in a Cairo street '
                       f'checks his wrist, slow push-in, teal grade. Avoid: no text, no "letters". | left | 4s |\n'
                       for i in range(0, 7))
        self.assertEqual(broll_engagement_problems(self.HEAD + rows, "", 3), [])


class BrollPathTest(unittest.TestCase):
    """The approved B-roll table reaches the document and the export files whole."""
    APPROVED = ("## AI B-ROLL GENERATION PROMPTS\n\n### B-Roll Prompt Table\n"
                "| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |\n|---|---|---|---|---|---|\n"
                + "".join(f"| {i} | 0:{i:02d} | تعالوا ناخد مثال حي | أو الـ (AF) | 🎬 Video | **Action**: clip number {i} "
                          f"of a man checking his watch, slow push-in |\n" for i in range(1, 7))
                + "\n### B-Roll Density Summary\n| Act | Total | 🖼️ Images | 🎬 Videos |\n|---|---|---|---|\n| Hook | 6 | 0 | 6 |\n")

    def test_condensed_part2_still_gets_the_full_table(self):
        part2 = (STORYBOARD + "\n### 🖼️ AI B-ROLL GENERATION PROMPTS\n| # | Prompt |\n|---|---|\n| 1 | only one row |\n"
                 "\n### INTEGRATION DATA\nx\n")
        router = use_router(self, Canned())
        router.call = lambda system, user, *a, **k: "## SCRIPT PACKAGE\n" + SCRIPT if "Compile Part 1" in user else part2
        out = final_script_package({"refined_script": SCRIPT, "broll_prompts": self.APPROVED})["final_package"]
        section = ew.find_section(out, "AI B-ROLL GENERATION PROMPTS")
        self.assertEqual(section.count("clip number"), 6)
        self.assertNotIn("only one row", out)
        self.assertIn("| Hook | 6 | 0 | 6 |", section)          # the density table isn't treated as cues

    def test_export_reads_the_approved_table_as_plain_text(self):
        from medical_brain.exports.prompt_files import export_broll_prompt_files
        folder = tempfile.mkdtemp()
        _, videos = export_broll_prompt_files("", fallback_broll=self.APPROVED, output_dir=folder)
        self.assertEqual(len(videos), 6)
        self.assertNotIn("**", videos[0])

    def test_broll_revision_patches_the_previous_prompts(self):
        from medical_brain.nodes.production import broll_prompt_generator
        router = use_router(self, Canned("table"))
        broll_prompt_generator({"broll_prompts": "OLD TABLE", "production_critique_output": "FIX ROW 3"})
        self.assertNotIn("OLD TABLE", router.prompts[-1])
        self.assertIn("past ~40 seconds", router.prompts[-1])
        broll_prompt_generator({"broll_prompts": "OLD TABLE", "production_critique_output": "FIX ROW 3",
                                "production_revision_count": 1})
        self.assertIn("OLD TABLE", router.prompts[-1])
        self.assertIn("FIX ROW 3", router.prompts[-1])

    def test_production_warning_lists_what_is_left(self):
        failed = {"production_revision_count": 2, "production_grade": "NEEDS_REVISION",
                  "broll_prompts": BrollEngagementTest.HEAD + '| 1 | 0:05 | a | b | c | 🎬 Video | x | A wrist, '
                  'the watch displays "45 BPM", slow push-in | left | 4s |\n', "target_duration": "1"}
        warning = ew.production_warning(failed)
        self.assertIn("did not pass after 2 rounds", warning)
        self.assertIn("45 BPM", warning)
        self.assertEqual(ew.production_warning(dict(failed, production_grade="PASS")), "")


if __name__ == "__main__":
    unittest.main()
