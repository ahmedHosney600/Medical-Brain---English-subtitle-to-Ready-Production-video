"""Fixes found by reviewing a real run (output/20261007_121509): cue repair,
quality-loop revisions, the critique/auditor verdict, keys out of git, small export bugs."""
import json
import os
import tempfile
import unittest

from medical_brain import llm
from medical_brain.cli import with_heading
from medical_brain.exports import workbook as ew
from medical_brain.nodes.final_package import whiteboard_problems
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
        for report in ("TRUTH REPORT", "FIDELITY REPORT", "CRITIQUE"):
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


if __name__ == "__main__":
    unittest.main()
