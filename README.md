# Medical-Brain---English-subtitle-to-Ready-Production-video

## Choosing the models

### 1. Set up once (and whenever you change models): `python3 setup_models.py`
The script works in four steps:

1. **Status check.** It shows which providers are active:
   ```
     ✅ gemini    Gemini Canvas proxy: 1 models
     ✅ claude    Claude via claude_code: subscription answers
     ✅ deepseek  DeepSeek: 2 models · balance 4.20 USD
     ⚪ opencode  OpenCode Go: no API key (OPENCODE_API_KEY)
   ```
2. **Keys.** For a provider without a key, it asks you to paste one. Keys are saved in `llm_keys.env`, which is kept out of git.
3. **Model choice.** It lists every model your active providers offer, and you choose:
   - the **main model**: writes the script, and runs any step whose own model fails;
   - **one strong model**: for option 2;
   - a model for each **hybrid judge** step.
4. **Test and save.** It test-calls every chosen model (✅/❌), lets you re-pick the failed ones, and saves to `llm_variables.json`.

`python3 setup_models.py --check` only shows the status.

Models are written `provider:model`:

| Provider | Example | Needs |
|---|---|---|
| `gemini` | `gemini:gemini-3-flash-preview` | the Gemini Canvas proxy on `localhost:8765` (Canvas tab open) |
| `claude` | `claude:claude-opus-5-5` | Claude Code installed and logged in (subscription). Or set `"transport": "anthropic_api"` and `ANTHROPIC_API_KEY` |
| `deepseek` | `deepseek:deepseek-v4-pro` | `DEEPSEEK_API_KEY` (api.deepseek.com) |
| `opencode` | `opencode:glm-5.1` | `OPENCODE_API_KEY` (OpenCode Go, opencode.ai/zen/go/v1) |

Any other OpenAI-compatible service can be added under `providers` with `"type": "openai"`, a `base_url` and an `api_key_env`.

### 2. Every run: `python3 main.py script.txt` asks
```
  1) Main model for the whole workflow (gemini:gemini-3-flash-preview)
  2) One strong model for the whole workflow (claude:claude-opus-5-5)
  3) Hybrid (recommended): gemini:gemini-3-flash-preview writes, judges check:
       medical_truth_verifier → claude:claude-opus-5-5
       ...
Choose 1-3 [3]:
```
- **Every step tries its own model first.** If that model fails for any reason (usage limit, weekly limit, outage, bad key), the step is re-run on the main model and the run continues.
  - When the failure means the provider is down, its later steps go straight to the main model.
  - Only a failure of the main model itself stops the run.
- `--provider main|single|hybrid` skips the menu.
- The terminal shows each step's model and time.
- `output/<session>/llm_provider.json` records which model ran each step and any fallbacks.

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
