"""B-roll media generation (medical_brain/media): manifest, gflow wrapper and runner, run
against a fake gflow executable (tests/fakes/fake_gflow.py) — never Google Flow."""
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

from medical_brain.media import manifest as mf
from medical_brain.media import runner
from medical_brain.media.gflow import Gflow

FAKE = os.path.join(os.path.dirname(__file__), "fakes", "fake_gflow.py")

BROLL_TABLE = """## AI B-ROLL GENERATION PROMPTS
| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Type | AI Generation Prompt |
|---|---|---|---|---|---|
| 1 | 0:04 | بتاخد حبتين مسكن | مع فنجان قهوة | 🎬 Video | Macro shot of two white tablets beside a cup of dark coffee, slow push-in, warm light |
| 2 | 1:15 | دي صورة الرنين | من غير مشاكل | 🖼️ Image | Still photograph of brain MRI films on an illuminated lightbox in a dim reading room |
"""
OVERLAY = """#### Drawing Animation 1: The extinguisher
- **On screen (from the spoken cues)**: 3:48 – 4:02 (14s total)
* **Whiteboard Image Prompt (Google Flow)**: Plain white whiteboard, black marker line art of a trash can and an extinguisher, no text
* **Draw-on Video Prompt (Google Flow)**: A hand with a black marker draws a trash can and an extinguisher, static camera, no text appears, exactly 11 seconds
"""


def fake_settings(**extra):
    s = dict(runner.DEFAULTS, gflow_bin=FAKE, pause_between_videos=[0, 0], pause_between_images=[0, 0])
    s.update(extra)
    return s


class FakeGflowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.fake_dir = os.path.join(self.dir, "fake")
        os.makedirs(self.fake_dir)
        os.chmod(FAKE, os.stat(FAKE).st_mode | stat.S_IXUSR)
        self.env = mock.patch.dict(os.environ, {"FAKE_GFLOW_DIR": self.fake_dir, "FAKE_GFLOW_SCENARIO": "{}"})
        self.env.start()
        self.broll_dir = os.path.join(self.dir, "session", "4_broll")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def scenario(self, **values):
        os.environ["FAKE_GFLOW_SCENARIO"] = json.dumps(values)

    def calls(self):
        path = os.path.join(self.fake_dir, "calls.jsonl")
        return [json.loads(l) for l in open(path)] if os.path.exists(path) else []

    def generate(self, manifest, **kw):
        kw.setdefault("settings", fake_settings())
        kw.setdefault("yes", True)
        with mock.patch("builtins.print"):
            return runner.generate(manifest, self.broll_dir, sleep=lambda s: None, **kw)


def sample_manifest():
    return mf.validate(mf.build_manifest("", BROLL_TABLE, OVERLAY, "migraine"))


class ManifestTest(unittest.TestCase):
    def test_built_from_the_package_with_numbers_times_and_kinds(self):
        items = sample_manifest()["items"]
        self.assertEqual([(i["id"], i["kind"]) for i in items],
                         [("B01", "broll_video"), ("B02", "broll_image"), ("WB1", "whiteboard")])
        self.assertEqual(items[0]["time"], "0:04")
        self.assertEqual(items[0]["start_cue"], "بتاخد حبتين مسكن")
        self.assertEqual(items[2]["time"], "3:48")
        self.assertIn("draws a trash can", items[2]["drawon_prompt"])

    def test_a_folder_of_prompt_files_is_a_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "04_broll_videos.txt"), "w").write("video one prompt\n\nvideo two prompt\n")
            open(os.path.join(d, "05_whiteboard_images.txt"), "w").write("board drawing\n")
            open(os.path.join(d, "06_whiteboard_videos.txt"), "w").write("hand draws it\n")
            items = mf.read_manifest(d)["items"]
        self.assertEqual([(i["id"], i["kind"]) for i in items],
                         [("B01", "broll_video"), ("B02", "broll_video"), ("WB1", "whiteboard")])
        self.assertEqual(items[2]["drawon_prompt"], "hand draws it")

    def test_a_broken_manifest_is_refused_with_its_reason(self):
        with self.assertRaisesRegex(ValueError, "drawon_prompt"):
            mf.validate({"items": [{"id": "WB1", "kind": "whiteboard", "prompt": "x"}]})
        with self.assertRaisesRegex(ValueError, "twice"):
            mf.validate({"items": [{"id": "B1", "kind": "broll_video", "prompt": "a"},
                                   {"id": "B1", "kind": "broll_image", "prompt": "b"}]})


class PromptTest(unittest.TestCase):
    def test_a_drawon_length_flow_cannot_make_is_corrected(self):
        self.assertEqual(runner.sanitize_prompt("hand draws, no text, exactly 11 seconds", 8),
                         "hand draws, no text, 8 seconds")
        self.assertEqual(runner.sanitize_prompt("no text, 6 seconds duration.", 8), "no text, 8 seconds.")
        self.assertEqual(runner.sanitize_prompt("static camera, 8s", 8), "static camera, 8 seconds")
        self.assertEqual(runner.sanitize_prompt("line\n\n two", None), "line two")

    def test_timecodes_become_file_name_labels(self):
        self.assertEqual(runner.timecode_label("0:49"), "0m49s")
        self.assertEqual(runner.timecode_label("1:02:05"), "1h02m05s")
        self.assertEqual(runner.timecode_label(""), "")


class RunTest(FakeGflowCase):
    def test_everything_is_generated_named_and_upscaled(self):
        result = self.generate(sample_manifest())
        self.assertEqual(result["failures"], [])
        calls = self.calls()
        t2v = [c for c in calls if c[:2] == ["video", "t2v"]]
        i2v = [c for c in calls if c[:2] == ["video", "i2v"]]
        self.assertEqual(len(t2v), 4)                                    # x4 = four x1 calls
        self.assertTrue(all(c[c.index("--count") + 1] == "1" for c in t2v + i2v))
        self.assertEqual(len(i2v), 4)
        for c in i2v:                                                    # M4: blank → chosen drawing (2K)
            self.assertIn("_blank_for_v1", c[c.index("--initial-frame") + 1])
            self.assertTrue(c[c.index("--end-frame") + 1].endswith("WB1_3m48s_v1_2k.jpg"))
            self.assertTrue(c[2].endswith("8 seconds"))                   # "exactly 11 seconds" corrected
        self.assertEqual(len([c for c in calls if c[:2] == ["image", "upscale"]]), 8)
        self.assertEqual(len([c for c in calls if c[:2] == ["video", "upscale"]]), 8)
        videos = sorted(os.listdir(os.path.join(self.broll_dir, "videos")))
        self.assertIn("B01_0m04s_v4_1080p.mp4", videos)
        images = sorted(os.listdir(os.path.join(self.broll_dir, "images")))
        self.assertIn("B02_1m15s_v1_2k.jpg", images)
        self.assertTrue(os.path.exists(os.path.join(self.broll_dir, "contact_sheet.html")))
        report = open(os.path.join(self.broll_dir, "00_START_HERE.md")).read()
        self.assertIn("| WB1 | 3:48 | whiteboard |", report)

    def test_a_second_run_generates_nothing_again(self):
        self.generate(sample_manifest())
        before = len(self.calls())
        self.generate(sample_manifest())
        new = self.calls()[before:]
        self.assertEqual([c for c in new if c[0] in ("image", "video", "project")], [])

    def test_a_lost_reply_is_recovered_from_the_catalog_not_paid_again(self):
        self.scenario(lose_video=[2])
        manifest = {"items": [i for i in sample_manifest()["items"] if i["id"] == "B01"]}
        result = self.generate(manifest)
        calls = self.calls()
        self.assertEqual(result["failures"], [])
        self.assertEqual(len([c for c in calls if c[:2] == ["video", "t2v"]]), 4)   # not resubmitted
        self.assertEqual(len([c for c in calls if c[:2] == ["data", "download"]]), 1)
        self.assertEqual(len(os.listdir(os.path.join(self.broll_dir, "videos", "_originals"))), 4)

    def test_a_security_block_stops_the_run_and_keeps_what_was_made(self):
        self.scenario(video_rc={"2": 10})
        result = self.generate(sample_manifest())
        self.assertIn("security layer", result["stopped"])
        state = json.load(open(os.path.join(self.broll_dir, "logs", "broll_state.json")))
        self.assertEqual(len(state["items"]["B02"]["images"]), 4)       # images came first and stay
        self.assertTrue(os.path.exists(os.path.join(self.broll_dir, "00_START_HERE.md")))
        before = len([c for c in self.calls() if c[:2] == ["video", "i2v"]])
        self.scenario()
        self.generate(sample_manifest())                                  # continues, no re-pay
        self.assertEqual(len([c for c in self.calls() if c[:2] == ["video", "i2v"]]) - before, 3)

    def test_a_rejected_prompt_skips_only_that_item(self):
        self.scenario(policy_word="MRI")
        result = self.generate(sample_manifest())
        self.assertEqual([i for i, _ in result["failures"]], ["B02"])
        self.assertEqual(len(os.listdir(os.path.join(self.broll_dir, "videos", "_originals"))), 4)

    def test_a_failed_1080p_export_keeps_the_720p_clip(self):
        self.scenario(video_upscale_rc=7)
        manifest = {"items": [i for i in sample_manifest()["items"] if i["id"] == "B01"]}
        self.generate(manifest)
        self.assertIn("B01_0m04s_v1.mp4", os.listdir(os.path.join(self.broll_dir, "videos")))
        self.assertIn("1080p export failed", open(os.path.join(self.broll_dir, "00_START_HERE.md")).read())

    def test_a_new_whiteboard_pick_makes_new_drawon_clips(self):
        manifest = {"items": [i for i in sample_manifest()["items"] if i["id"] == "WB1"]}
        self.generate(manifest)
        self.generate(manifest, picks={"WB1": "3"})
        i2v = [c for c in self.calls() if c[:2] == ["video", "i2v"]]
        self.assertEqual(len(i2v), 8)
        self.assertTrue(i2v[-1][i2v[-1].index("--end-frame") + 1].endswith("_v3_2k.jpg"))

    def test_a_dry_run_and_an_unconfirmed_run_spend_nothing(self):
        result = self.generate(sample_manifest(), dry_run=True)
        self.assertEqual(result["plan"]["paid_clips"], 8)
        self.assertEqual(self.calls(), [])
        with self.assertRaises(runner.BrollNotReady):
            self.generate(sample_manifest(), yes=False, interactive=False)
        self.assertEqual([c for c in self.calls() if c[0] in ("image", "video")], [])


if __name__ == "__main__":
    unittest.main()
