# Medical-Brain---English-subtitle-to-Ready-Production-video

## Choosing the model: Gemini, Claude, or Hybrid

When you run `python3 main.py script.txt`, it asks:

```
Which model setup for this run?
  1) Gemini for the whole workflow (gemini-3-flash-preview, free)
  2) Claude for the whole workflow (claude-opus-5-5)
  3) Hybrid (recommended): Gemini writes, Claude checks:
       medical_truth_verifier → claude-opus-5-5
       fidelity_auditor → claude-opus-5-5
       self_critique → claude-sonnet-5-5
       packaging_honesty_ctr_auditor → claude-sonnet-5-5
       production_quality_critique → claude-sonnet-5-5
Choose 1-3 [3]:
```

Press Enter for Hybrid. Hybrid is recommended because the writing stays on free Gemini while a different model family judges it, so the judge isn't grading its own writing.
- Opus 5.5 handles the two checks with real medical stakes.
- Sonnet 5.5 handles the critiques.

| Option | Needs |
|---|---|
| 1 Gemini | the Gemini Canvas proxy on `localhost:8765` with the Canvas tab open |
| 2 Claude | Claude Code installed and logged in (`claude` command), or `ANTHROPIC_API_KEY` with `"transport": "anthropic_api"` |
| 3 Hybrid | both |

To skip the menu, use `--provider gemini|claude|hybrid` or the `LLM_PROVIDER` environment variable.
When there's no terminal to answer the menu, `"provider"` in `llm_variables.json` is used.

Settings (see `llm_variables.example.json`):
- `hybrid_routes`: which nodes run on which Claude model in Hybrid. Add or remove nodes here.
- `claude.transport`: `claude_code` uses your Claude subscription through the `claude` CLI; `anthropic_api` uses a pay-per-token API key.
- `claude.effort` / `claude.efforts`: how hard Claude thinks (`low` … `max`). The default is `high`.
- `fallback_to_gemini`: if Claude is unavailable at the start of a Hybrid run, or hits a usage limit mid-run, those steps run on Gemini so the run still finishes. In option 2 you are asked first.

The terminal shows the model and time for each node.
`output/<session>/llm_provider.json` records the mode, calls per model, and which model ran each node.

## The final document: ordered like the editing workflow

`output/<session>/final_script.md` (and its HTML/PDF/DOCX) follows the order you edit in:

1. **Overview**: script metadata
2. **Before filming**: teleprompter script, retention lines, CTA versions
3. **Editing steps**:

   | Step | What you get |
   |---|---|
   | 1–2 Raw cut & First Listen | section list with start/end sentences, "must survive the cut" checklist, and a reminder to start B-roll generation (step 2.4) |
   | 3 Speed, 4 Audio | nothing from the package |
   | 5 L/J cuts | the section boundaries |
   | 6 Captions | English terms to check in auto-captions |
   | 7 B-rolls | every B-roll with its prompt |
   | 8 Text overlays | popups, titles, warnings, quotes, kinetic text, lower-thirds |
   | 9 Zooms, transitions & SFX | |
   | 10 Icons & shapes, 11 Animations | optional |
   | 12 Color | nothing from the package |
   | 13 Whiteboard | drawings |

4. **Publishing**: titles, thumbnails, description, pinned comment, chapters
5. **Reference appendix**: the full storyboard and detail tables

Every item is located by its **sentence cue** (▶️ START / ⏹️ END). These must be exact spoken words from the script and unique in it; the pipeline checks this and retries.
A cue that still couldn't be matched is marked ⚠️. Times marked ~ are pre-filming estimates.

**YouTube chapters** are the VIDEO SECTIONS titles: viewer-facing titles that use your search keywords, never "المقدمة" / "الخاتمة".
The description's chapter list is rebuilt from that table automatically. After editing, put the playhead on each start sentence and update the times.

**Whiteboard drawings** are made in Google Flow, in three steps:

1. Generate the image from `whiteboard_images.txt`.
2. Use it as the start frame for the matching prompt in `whiteboard_videos.txt` (same order).
3. Add the labels in Premiere as text.

The images never contain text, because AI can't write Arabic or anatomy labels reliably.
