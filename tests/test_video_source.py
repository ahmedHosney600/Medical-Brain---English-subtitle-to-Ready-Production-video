"""The editable video source (research/video_source.md): writing it with a hash,
reading the creator's edits, flagging uncited bullets, and continue_research passing
the edited text to run_video."""
import json
import os
import tempfile
import unittest
from unittest import mock

from medical_brain import cli
from medical_brain.research import dossier as rd
from medical_brain.research.state import new_state


def guide_state(**over):
    """A small research state whose patient guide produces cited bullet lines."""
    st = {
        "topic": "Cramps",
        "sources": {"S1": {"title": "Cochrane review", "journal": "cochranelibrary.com",
                           "year": 2024, "level": 2, "url": "u1"}},
        "facts": [{"id": "F1", "section": "A", "kind": "advice", "claim": "Stretch the calf.",
                   "quote": "Stretch", "source_ids": ["S1"], "level": 2, "status": "verified",
                   "strength": "proven"}],
        "outline": [{"id": "A", "title": "Why", "questions": []}],
        "patient_guide": {
            "promise": "You will know what to do.",
            "action_card": {"do_now": [{"text": "Stretch the calf.", "facts": ["F1"],
                                        "strength": "proven"}],
                            "daily": [], "works": [], "doesnt_work_or_risky": [],
                            "see_doctor_if": [], "emergency_if": []},
            "sections": [], "special_groups": [], "myths": [],
            "complementary": {"options": [], "not_proven": []},
            "key_numbers": [], "uncertain": [],
        },
        "guide_report": {},
        "rejected": [],
    }
    st.update(over)
    return st


class WriteReadVideoSourceTest(unittest.TestCase):
    def test_write_writes_file_and_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            state = guide_state()
            path = rd.write_video_source(state, folder)
            self.assertEqual(path, os.path.join(folder, rd.VIDEO_SOURCE_FILE))
            with open(path, encoding="utf-8") as f:
                written = f.read()
            self.assertIn("Stretch the calf. [S1]", written)
            import hashlib
            self.assertEqual(state["video_source_sha256"],
                             hashlib.sha256(written.encode("utf-8")).hexdigest())

    def test_read_returns_generated_text_while_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            state = guide_state()
            rd.write_video_source(state, folder)
            text, edited = rd.read_video_source(state, folder)
            self.assertFalse(edited)
            self.assertEqual(text, rd.video_source(state))

    def test_read_returns_the_file_text_when_edited(self):
        with tempfile.TemporaryDirectory() as folder:
            state = guide_state()
            rd.write_video_source(state, folder)
            with open(os.path.join(folder, rd.VIDEO_SOURCE_FILE), "w", encoding="utf-8") as f:
                f.write("MY OWN SOURCE\n- new claim\n")
            text, edited = rd.read_video_source(state, folder)
            self.assertTrue(edited)
            self.assertEqual(text, "MY OWN SOURCE\n- new claim\n")

    def test_empty_edit_falls_back_to_generated_text(self):
        with tempfile.TemporaryDirectory() as folder:
            state = guide_state()
            rd.write_video_source(state, folder)
            with open(os.path.join(folder, rd.VIDEO_SOURCE_FILE), "w", encoding="utf-8") as f:
                f.write("   \n\n")
            text, edited = rd.read_video_source(state, folder)
            self.assertFalse(edited)
            self.assertEqual(text, rd.video_source(state))

    def test_missing_file_falls_back_to_generated_text(self):
        with tempfile.TemporaryDirectory() as folder:
            state = guide_state()
            text, edited = rd.read_video_source(state, folder)
            self.assertFalse(edited)
            self.assertEqual(text, rd.video_source(state))


class UncitedLinesTest(unittest.TestCase):
    def test_flags_only_uncited_bullets_outside_the_sources_block(self):
        text = "\n".join([
            "- cited claim [S1]",
            "- new claim",
            "- (none)",
            "- (none has good evidence for this topic yet)",
            "SOURCES:",
            "- a bullet inside the sources block",
        ])
        self.assertEqual(rd.uncited_lines(text), ["- new claim"])

    def test_generated_source_has_no_uncited_lines(self):
        self.assertEqual(rd.uncited_lines(rd.video_source(guide_state())), [])

    def test_myth_lines_are_ignored(self):
        # Generated MYTH lines have no citation of their own; the TRUTH line below carries it.
        text = "\n".join([
            "- MYTH: cracking knuckles causes arthritis.",
            "  TRUTH: it does not [S1]",
            "- new claim",
        ])
        self.assertEqual(rd.uncited_lines(text), ["- new claim"])


class ContinueResearchVideoSourceTest(unittest.TestCase):
    def _session(self, tmp, state, review_text=None, video_source=None):
        session = os.path.join(tmp, "20260101_000000")
        folder = os.path.join(session, "research")
        os.makedirs(folder)
        state.setdefault("workflow", "research-video")
        with open(os.path.join(folder, "research_state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f)
        if review_text is not None:
            with open(os.path.join(folder, "review.md"), "w", encoding="utf-8") as f:
                f.write(review_text)
        if video_source is not None:
            with open(os.path.join(folder, rd.VIDEO_SOURCE_FILE), "w", encoding="utf-8") as f:
                f.write(video_source)
        return session, folder

    def test_edited_video_source_is_passed_to_run_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            edited = "MY EDITED SOURCE\n- a claim with no citation\n"
            session, folder = self._session(tmp, guide_state(), review_text="APPROVED: yes\n",
                                            video_source=edited)
            captured = {}

            def fake_run_video(router, original_script, **kw):
                captured["router"] = router
                captured["script"] = original_script
                captured["kw"] = kw

            with mock.patch.object(cli, "run_video", fake_run_video), \
                    mock.patch.object(cli, "PROJECT_ROOT", tmp), \
                    mock.patch("builtins.print") as printed:
                cli.continue_research(lambda: "ROUTER", session)

            self.assertEqual(captured["script"], edited)
            self.assertEqual(captured["router"], "ROUTER")
            self.assertEqual(captured["kw"]["output_dir"], session)
            text = " ".join(str(c) for c in printed.call_args_list)
            self.assertIn("Using your edited video_source.md", text)
            self.assertIn("line(s) have no source id", text)

    def test_missing_video_source_is_written_for_older_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = new_state("Cramps")
            session, folder = self._session(tmp, state)          # no review.md → not approved
            with mock.patch.object(cli, "PROJECT_ROOT", tmp), mock.patch("builtins.print") as printed:
                cli.continue_research(lambda: self.fail("no model needed"), session)

            source_path = os.path.join(folder, rd.VIDEO_SOURCE_FILE)
            self.assertTrue(os.path.exists(source_path))
            with open(os.path.join(folder, "research_state.json"), encoding="utf-8") as f:
                self.assertIn("video_source_sha256", json.load(f))
            text = " ".join(str(c) for c in printed.call_args_list)
            self.assertIn("Wrote", text)
            self.assertIn("no requests", text)
            self.assertIn("video_source.md", text)

    def test_only_newly_added_uncited_lines_are_warned_about(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = guide_state()
            state["patient_guide"]["myths"] = [
                {"myth": "Cracking knuckles causes arthritis.",
                 "truth": "It does not.", "facts": ["F1"]}]
            generated = rd.video_source(state)
            self.assertIn("- MYTH: Cracking knuckles causes arthritis.", generated)
            edited = generated.replace(
                "- MYTH: Cracking knuckles causes arthritis.",
                "- MYTH: Cracking knuckles causes arthritis.\n- a brand new uncited claim")
            session, _ = self._session(tmp, state, review_text="APPROVED: yes\n",
                                       video_source=edited)
            import contextlib
            import io
            buf = io.StringIO()
            with mock.patch.object(cli, "run_video", lambda *a, **k: None), \
                    mock.patch.object(cli, "PROJECT_ROOT", tmp), \
                    contextlib.redirect_stdout(buf):
                cli.continue_research(lambda: "ROUTER", session)
            out = buf.getvalue()
            self.assertIn("1 line(s) have no source id", out)
            self.assertIn("a brand new uncited claim", out)
            self.assertNotIn("MYTH: Cracking knuckles", out)


if __name__ == "__main__":
    unittest.main()
