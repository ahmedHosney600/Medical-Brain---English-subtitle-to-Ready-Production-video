"""A B-roll table row that lost a column shifts every cell: the model wrote a 12-column
header but 11-cell data rows, dropping "Main Cue", so the long generation prompt sat under
"Main Cue" and "Composition Notes" landed in the prompt cell. realign_broll_table puts the
missing empty cell back; broll_rows reads such a saved table defensively."""
import unittest

from medical_brain.exports import workbook as ew
from medical_brain.nodes import production


HEADER = ("| # | Timestamp | ▶️ START CUE | ⏹️ END CUE | Script Context | Type | Main Cue | "
          "AI Generation Prompt | Composition Notes | Linked to Transition # | Linked to Overlay # | "
          "Duration (s) |")
SEP = "|" + "---|" * 12

PROMPT = ("Close-up of a young man waking up suddenly in bed at night, gripping his calf in pain, "
          "sweat on his forehead, moonlight through the window, cinematic photorealistic medical "
          "stock footage. Avoid: plastic skin, AI artifacts, no text.")
NOTES = "Focal point right; leave lower-third open for chapter title, slow push-in camera move"
# The model dropped Main Cue: 11 cells instead of 12.
SHIFTED_ROW = (f"| 1 | 0:07 | تخيل إنك نايم | من الألم | Hook — sudden night cramp | 🎬 Video | "
               f"{PROMPT} | {NOTES} | After T#1 | After I#1 | 4s |")
SHIFTED = "\n".join([HEADER, SEP, SHIFTED_ROW]) + "\n"

NO_MOVE_NOTES = "Focal point right; leave the lower-third open for the chapter title"
SHIFTED_NO_MOVE_ROW = (f"| 1 | 0:07 | تخيل إنك نايم | من الألم | Hook — sudden night cramp | "
                       f"🎬 Video | {PROMPT} | {NO_MOVE_NOTES} | After T#1 | After I#1 | 4s |")
SHIFTED_NO_MOVE = "\n".join([HEADER, SEP, SHIFTED_NO_MOVE_ROW]) + "\n"

WELL_FORMED_ROW = (f"| 1 | 0:07 | تخيل إنك نايم | من الألم | Hook — sudden night cramp | 🎬 Video | | "
                   f"{PROMPT} | {NOTES} | After T#1 | After I#1 | 4s |")
WELL_FORMED = "\n".join([HEADER, SEP, WELL_FORMED_ROW]) + "\n"


def _data_cells(table: str) -> list:
    row = next(l for l in table.splitlines() if l.startswith("| 1 "))
    return ew._cells(row)


class RealignBrollTableTest(unittest.TestCase):
    def test_shifted_row_is_realigned(self):
        table, fixed = ew.realign_broll_table(SHIFTED)
        self.assertEqual(fixed, 1)
        cells = _data_cells(table)
        self.assertEqual(len(cells), 12)
        self.assertEqual(cells[6], "")                 # Main Cue gets the inserted empty cell
        self.assertEqual(cells[7], PROMPT)             # AI Generation Prompt
        self.assertEqual(cells[8], NOTES)              # Composition Notes
        rows = ew.broll_rows(table)
        self.assertEqual(rows[0]["prompt"], PROMPT)
        self.assertEqual(rows[0]["kind"], "video")

    def test_well_formed_table_is_returned_unchanged(self):
        out, fixed = ew.realign_broll_table(WELL_FORMED)
        self.assertEqual(fixed, 0)
        self.assertEqual(out, WELL_FORMED)

    def test_escaped_pipe_inside_the_prompt_survives(self):
        prompt = ("A wide shot of a clinic corridor \\| with a nurse walking past, soft daylight, "
                  "photorealistic cinematic medical stock footage, shallow depth of field, "
                  "avoid text, logos and watermarks.")
        row = (f"| 1 | 0:07 | تخيل إنك نايم | من الألم | Hook | 🎬 Video | "
               f"{prompt} | {NO_MOVE_NOTES} | After T#1 | After I#1 | 4s |")
        table = "\n".join([HEADER, SEP, row]) + "\n"
        out, fixed = ew.realign_broll_table(table)
        self.assertEqual(fixed, 1)
        self.assertEqual(len(_data_cells(out)), 12)
        self.assertIn("\\|", out)
        self.assertEqual(ew.broll_rows(out)[0]["prompt"], prompt)

    def test_auto_repair_edits_the_prompt_cell_not_the_notes(self):
        aligned, fixed = ew.realign_broll_table(SHIFTED_NO_MOVE)
        self.assertEqual(fixed, 1)
        repaired, moves = production.auto_repair_camera_moves(aligned)
        self.assertEqual(moves, 1)
        cells = _data_cells(repaired)
        self.assertIn("slow push-in camera move", cells[7])   # generation prompt
        self.assertEqual(cells[8], NO_MOVE_NOTES)             # notes untouched

    def test_broll_rows_on_the_unrealigned_table_still_gives_the_prompt(self):
        rows = ew.broll_rows(SHIFTED)
        self.assertEqual(rows[0]["prompt"], PROMPT)
        self.assertEqual(rows[0]["kind"], "video")

    def test_kind_is_detected_from_any_cell_when_type_is_missing(self):
        # A row whose Type cell is empty: the 🎬 marker in another cell still names it video.
        row = (f"| 1 | 0:07 | a b | c d | 🎬 Video hook | | "
               f"{PROMPT} | {NOTES} | After T#1 | After I#1 | 4s |")
        table = "\n".join([HEADER, SEP, row]) + "\n"
        rows = ew.broll_rows(table)
        self.assertEqual(rows[0]["kind"], "video")
        self.assertEqual(rows[0]["prompt"], PROMPT)


if __name__ == "__main__":
    unittest.main()
