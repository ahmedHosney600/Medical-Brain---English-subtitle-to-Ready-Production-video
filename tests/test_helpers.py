"""Pure helpers: JSON fences, thumbnail face check, workbook cue/chapter checks."""
import json
import unittest

from medical_brain.exports import workbook as ew
from medical_brain.nodes.packaging import check_thumbnail_faces
from medical_brain.utils.llm_json import strip_json_fence


class JsonFenceTest(unittest.TestCase):
    def test_fenced_and_plain(self):
        self.assertEqual(json.loads(strip_json_fence('```json\n{"a": 1}\n```').strip()), {"a": 1})
        self.assertEqual(json.loads(strip_json_fence('```\n{"a": 2}\n```').strip()), {"a": 2})
        self.assertEqual(strip_json_fence('{"a": 3}'), '{"a": 3}')


class FaceRulesTest(unittest.TestCase):
    def test_extreme_face_is_flagged(self):
        self.assertTrue(check_thumbnail_faces("- **Presenter Expression**: shocked, mouth open, wide eyes"))

    def test_natural_face_and_negatives_pass(self):
        text = ("- **Presenter Expression**: calm, slight confident smile\n"
                "- prompt: ... Negative prompt: exaggerated facial expression, shocked face, open mouth\n"
                "- prompt: subtle focused look, not shocked")
        self.assertEqual(check_thumbnail_faces(text), [])


class WorkbookTest(unittest.TestCase):
    SCRIPT = "### 🎬 PRODUCTION SCRIPT\nفاكرين المشهد اللي وجع قلوبنا كلنا؟ لاعب كورة في عز شبابه.\n"

    def test_cue_found_and_normalized(self):
        norm = ew.normalize_ar(ew.spoken_text(self.SCRIPT))
        self.assertIsNone(ew.cue_problem("فاكرين المشهد اللي وجع", norm))
        self.assertIsNone(ew.cue_problem("فاكرين المشهد اللى وجع", norm))      # ى/ي spelling
        self.assertIn("placeholder", ew.cue_problem("(بداية الفيديو)", norm))
        self.assertIn("not found", ew.cue_problem("كلام مش موجود في النص", norm))

    def test_chapter_titles(self):
        bad = [{"num": "1", "title": "المقدمة والهوك", "duration_s": 60},
               {"num": "2", "title": "ليه رسم القلب ممكن يطلع سليم؟", "duration_s": 60}]
        problems = ew.check_chapter_titles(bad)
        self.assertTrue(any("at least 3" in p for p in problems))
        self.assertTrue(any("production label" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
