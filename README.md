# Medical-Brain---English-subtitle-to-Ready-Production-video

## Choosing the model: Claude Opus 5.5 or Gemini Canvas

The pipeline runs on one of two models, chosen at the start of each run:

| Path | When it is used | Needs |
|---|---|---|
| **Claude Opus 5.5** | Your Claude subscription is active (or you set an API key) | Claude Code installed and logged in (`claude` command), or `ANTHROPIC_API_KEY` |
| **Gemini Canvas** | Claude is unavailable, or you choose it | The Gemini Canvas proxy running on `localhost:8765` with the Canvas tab open |

```bash
python3 main.py script.txt                      # auto: Claude if it answers, otherwise Gemini Canvas
python3 main.py script.txt --provider claude    # Claude only (stops if it is unavailable)
python3 main.py script.txt --provider gemini    # Gemini Canvas only
```

You can also set the default with `"provider"` in `llm_variables.json` or the `LLM_PROVIDER` environment variable.
See `llm_variables.example.json` for all settings:

- `claude.transport`: `claude_code` uses your Claude subscription through the `claude` CLI; `anthropic_api` uses a pay-per-token API key.
- `claude.effort`: how hard Opus 5.5 thinks (`low`, `medium`, `high`, `xhigh`, `max`). Default `high`.
- `fallback_to_gemini`: if Claude hits a usage limit in the middle of a run, the remaining steps switch to Gemini Canvas so the run still finishes. Set to `false` to stop instead.

Your existing `llm_variables.json` (flat `model` / `api_key` / `base_url`) still works and is read as the Gemini settings.
Each run writes `output/<session>/llm_provider.json` showing which model handled how many steps and any switch that happened.

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
