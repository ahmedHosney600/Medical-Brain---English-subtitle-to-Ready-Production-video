"""The numbered session layout (1_research/ · 2_review/ · 3_video/), older sessions,
migration, and starting the workflow from a chosen point (--from)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from medical_brain import cli
from medical_brain import session_layout as layout


def touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def checkpoint(path, state, completed, status="done"):
    doc = dict(state)
    doc["_resume"] = {"label": "workflow", "graph": "video", "completed": completed,
                      "last_node": completed[-1] if completed else None, "status": status}
    touch(path, json.dumps(doc))


class LayoutPathsTest(unittest.TestCase):
    def test_new_session_uses_numbered_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = os.path.join(tmp, "20260101_000000")
            os.makedirs(s)
            self.assertFalse(layout.is_legacy(s))
            self.assertEqual(layout.research_logs(s), os.path.join(s, "1_research", "logs"))
            self.assertEqual(layout.review_file(s), os.path.join(s, "2_review", "01_review.md"))
            self.assertEqual(layout.video_source_file(s), os.path.join(s, "2_review", "02_video_source.md"))
            self.assertEqual(layout.video_logs(s), os.path.join(s, "3_video", "logs"))
            self.assertEqual(layout.final_script_md(s), os.path.join(s, "3_video", "01_final_script.md"))

    def test_older_session_keeps_its_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = os.path.join(tmp, "old")
            touch(os.path.join(s, "research", "research_state.json"), "{}")
            touch(os.path.join(s, "checkpoint.json"), "{}")
            self.assertTrue(layout.is_legacy(s))
            self.assertEqual(layout.research_logs(s), os.path.join(s, "research"))
            self.assertEqual(layout.review_file(s), os.path.join(s, "research", "review.md"))
            self.assertEqual(layout.video_logs(s), s)
            self.assertEqual(layout.final_script_md(s), os.path.join(s, "final_script.md"))

    def test_session_of_checkpoint_both_layouts(self):
        self.assertEqual(layout.session_of_checkpoint("/o/s/1_research/logs/research_checkpoint.json"), "/o/s")
        self.assertEqual(layout.session_of_checkpoint("/o/s/3_video/logs/checkpoint.json"), "/o/s")
        self.assertEqual(layout.session_of_checkpoint("/o/s/research/research_checkpoint.json"), "/o/s")
        self.assertEqual(layout.session_of_checkpoint("/o/s/checkpoint.json"), "/o/s")
        self.assertEqual(layout.checkpoint_kind("/o/s/3_video/logs/checkpoint.json"), "video")

    def test_cli_finds_checkpoints_in_the_new_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = os.path.join(tmp, "output", "20260101_000000")
            checkpoint(os.path.join(s, "3_video", "logs", "checkpoint.json"), {}, ["a"], status="stopped")
            with mock.patch.object(cli, "PROJECT_ROOT", tmp):
                self.assertEqual(cli._checkpoint_for_session(s), os.path.join(s, "3_video", "logs", "checkpoint.json"))
                self.assertTrue(cli._latest_stopped_checkpoint().endswith(os.path.join("3_video", "logs", "checkpoint.json")))
                self.assertEqual(cli._resolve_resume("20260101_000000")[0], os.path.abspath(s))


class ArchiveAndStartHereTest(unittest.TestCase):
    def test_fresh_archive_moves_everything_continue_keeps_the_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "3_video")
            touch(os.path.join(video, "01_final_script.md"))
            touch(os.path.join(video, "logs", "checkpoint.json"), "{}")
            touch(os.path.join(video, "logs", "llm_cache", "a.json"), "{}")
            kept = layout.archive_stage(video, keep_checkpoint=True)
            self.assertTrue(os.path.exists(os.path.join(kept, "01_final_script.md")))
            self.assertTrue(os.path.exists(os.path.join(video, "logs", "checkpoint.json")))   # still there
            self.assertFalse(os.path.exists(os.path.join(video, "logs", "llm_cache")))       # fresh answers
            self.assertFalse(os.path.exists(os.path.join(video, "01_final_script.md")))
            fresh = layout.archive_stage(video)
            self.assertTrue(os.path.exists(os.path.join(fresh, "logs", "checkpoint.json")))
            self.assertEqual(os.listdir(video), [layout.PREVIOUS_DIR])

    def test_start_here_lists_files_in_reading_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("01_final_script.pdf", "01_final_script.md", "02_shorts_package.pdf",
                         "03_broll_images.txt", "06_whiteboard_videos.txt"):
                touch(os.path.join(tmp, "3_video", name))
            os.makedirs(os.path.join(tmp, "3_video", "logs"))
            path = layout.write_start_here(tmp, layout.VIDEO_DIR, "output/x")
            text = open(path, encoding="utf-8").read()
            order = [text.index(n) for n in ("01_final_script", "02_shorts_package", "03_broll_images",
                                             "06_whiteboard_videos", "`logs/`")]
            self.assertEqual(order, sorted(order))
            self.assertIn("--from production output/x", text)
            self.assertNotIn("05_whiteboard_images", text)          # only files that exist

    def test_prompt_files_are_numbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in layout.PROMPT_FILES:
                touch(os.path.join(tmp, "3_video", name))
            renamed = layout.number_prompt_files(tmp)
            self.assertEqual(sorted(renamed), sorted(layout.PROMPT_FILES.values()))


class MigrateTest(unittest.TestCase):
    def test_older_session_moves_into_numbered_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = os.path.join(tmp, "old")
            for name in ("patient_guide.md", "patient_guide.pdf", "dossier.md", "dossier.docx", "dossier.json",
                         "research_state.json", "research_checkpoint.json", "review.md", "video_source.md"):
                touch(os.path.join(s, "research", name))
            for name in ("final_script.md", "final_script.pdf", "shorts_package.md", "broll_images.txt",
                         "whiteboard_videos.txt", "checkpoint.json", "session_log.jsonl", "llm_provider.json"):
                touch(os.path.join(s, name))
            touch(os.path.join(s, "llm_cache", "a.json"))
            moves = layout.migrate(s)
            self.assertTrue(moves)
            for rel in ("1_research/01_patient_guide.pdf", "1_research/02_evidence_library.docx",
                        "1_research/logs/dossier.md", "1_research/logs/research_state.json",
                        "2_review/01_review.md", "2_review/02_video_source.md",
                        "3_video/01_final_script.pdf", "3_video/02_shorts_package.md",
                        "3_video/03_broll_images.txt", "3_video/06_whiteboard_videos.txt",
                        "3_video/logs/checkpoint.json", "3_video/logs/llm_cache/a.json",
                        "1_research/00_START_HERE.md", "3_video/00_START_HERE.md"):
                self.assertTrue(os.path.exists(os.path.join(s, rel)), rel)
            self.assertFalse(os.path.exists(os.path.join(s, "research")))
            self.assertFalse(layout.is_legacy(s))
            self.assertEqual(layout.migrate(s), [])

    def test_older_final_patient_guide_becomes_the_video_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = os.path.join(tmp, "old")
            for name in ("patient_guide.md", "research_state.json", "final_patient_guide.md"):
                touch(os.path.join(s, "research", name))
            layout.migrate(s)
            self.assertTrue(os.path.exists(os.path.join(s, "2_review", "02_video_source.md")))
            self.assertFalse(os.path.exists(os.path.join(s, "1_research", "logs", "final_patient_guide.md")))


class StartFromTest(unittest.TestCase):
    STATE = {"refined_script": "script", "packaging_ab_test_set": "titles", "broll_prompts": "broll",
             "text_animation_overlay": "overlays", "transition_design": "map", "final_package": "pkg",
             "production_revision_count": 3, "shorts_revision_count": 1, "production_grade": "PASS",
             "shorts_scripts": "s", "original_script": "the subtitle"}
    COMPLETED = ["self_critique", "packaging_honesty_ctr_auditor", "text_animation_overlay_designer",
                 "broll_prompt_generator", "transition_designer", "production_quality_critique",
                 "final_script_package", "shorts_quality_gate"]

    def _session(self, tmp):
        s = os.path.join(tmp, "output", "20260101_000000")
        checkpoint(os.path.join(s, "3_video", "logs", "checkpoint.json"), self.STATE, self.COMPLETED)
        touch(os.path.join(s, "3_video", "01_final_script.md"), "old package")
        return s

    def _run(self, tmp, *args):
        calls = []
        with mock.patch.object(cli, "PROJECT_ROOT", tmp), \
                mock.patch.object(cli, "run_video", lambda *a, **k: calls.append((a, k))), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.start_from(lambda: "router", *args)
        return calls

    def test_rewound_clears_the_stage_and_later_ones(self):
        state, meta = cli._rewound(self.STATE, {"completed": self.COMPLETED}, "production")
        self.assertEqual(state["broll_prompts"], "")
        self.assertEqual(state["final_package"], "")
        self.assertEqual(state["shorts_scripts"], "")
        self.assertEqual(state["production_revision_count"], 0)
        self.assertEqual(state["packaging_ab_test_set"], "titles")           # earlier stages kept
        self.assertEqual(meta["completed"], ["self_critique", "packaging_honesty_ctr_auditor"])

    def test_from_production_reruns_from_the_overlay_step_and_keeps_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._session(tmp)
            calls = self._run(tmp, "production", s)
            (args, kwargs), = calls
            self.assertEqual(kwargs["entry"], "text_animation_overlay_designer")
            self.assertEqual(kwargs["resume_state"]["production_revision_count"], 0)
            archives = os.listdir(os.path.join(s, "3_video", layout.PREVIOUS_DIR))
            self.assertEqual(len(archives), 1)
            self.assertTrue(os.path.exists(os.path.join(s, "3_video", layout.PREVIOUS_DIR, archives[0],
                                                        "01_final_script.md")))
            self.assertTrue(os.path.exists(os.path.join(s, "3_video", "logs", "checkpoint.json")))

    def test_from_script_with_an_external_review_replaces_the_video_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._session(tmp)
            touch(os.path.join(s, "1_research", "logs", "research_state.json"), json.dumps({"topic": "t"}))
            touch(os.path.join(s, "2_review", "02_video_source.md"), "generated source")
            mine = os.path.join(tmp, "my_review.md")
            touch(mine, "my final review " + " ".join(f"[S{i}]" for i in range(12)))
            (args, kwargs), = self._run(tmp, "script", s, mine)
            self.assertTrue(args[1].startswith("my final review"))
            self.assertEqual(kwargs["research"], {"topic": "t"})
            self.assertTrue(kwargs["extra_state"]["research_dossier"])
            self.assertTrue(open(os.path.join(s, "2_review", "02_video_source.md"), encoding="utf-8")
                            .read().startswith("my final review"))
            self.assertEqual(len(os.listdir(os.path.join(s, "2_review", layout.PREVIOUS_DIR))), 1)

    def test_from_script_without_research_reuses_the_subtitle(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._session(tmp)
            (args, kwargs), = self._run(tmp, "script", s)
            self.assertEqual(args[1], "the subtitle")
            self.assertIsNone(kwargs["research"])
            self.assertFalse(os.path.exists(os.path.join(s, "2_review")))   # only 3_video for subtitle runs

    def test_from_on_an_older_session_moves_it_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = os.path.join(tmp, "output", "old")
            checkpoint(os.path.join(s, "checkpoint.json"), self.STATE, self.COMPLETED)
            touch(os.path.join(s, "final_script.md"))
            (args, kwargs), = self._run(tmp, "final", s)
            self.assertEqual(kwargs["entry"], "final_script_package")
            self.assertTrue(os.path.exists(os.path.join(s, "3_video", "logs", "checkpoint.json")))

    def test_unknown_point_and_unfinished_stage_stop_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._session(tmp)
            with self.assertRaises(SystemExit):
                self._run(tmp, "lunch", s)
            checkpoint(os.path.join(s, "3_video", "logs", "checkpoint.json"), {"refined_script": "x"}, ["self_critique"])
            with self.assertRaises(SystemExit):
                self._run(tmp, "production", s)


if __name__ == "__main__":
    unittest.main()


class ScriptSourceTest(unittest.TestCase):
    def test_missing_video_source_is_written_from_the_research(self):
        from medical_brain.research import dossier as rd
        with tempfile.TemporaryDirectory() as tmp:
            s = os.path.join(tmp, "output", "r")
            touch(os.path.join(s, "1_research", "logs", "research_state.json"), json.dumps({"topic": "t"}))
            with mock.patch.object(cli, "PROJECT_ROOT", tmp), \
                    mock.patch.object(rd, "video_source", lambda state: "GENERATED SOURCE"):
                text, research, label = cli._script_source(s, "")
            self.assertEqual(text, "GENERATED SOURCE")
            self.assertTrue(os.path.exists(os.path.join(s, "2_review", "02_video_source.md")))
