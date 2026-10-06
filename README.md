# Medical-Brain---English-subtitle-to-Ready-Production-video

## Install

```bash
python3 -m pip install -r requirements.txt
# Homebrew Python may refuse ("externally-managed-environment"); then:
python3 -m pip install --user --break-system-packages -r requirements.txt
```
`setup_models.py` also checks this and offers to install anything missing.
`anthropic` is needed for Claude by API key and for OpenCode Go's Qwen and MiniMax models.

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
   - a **backup model** (optional): runs a step when the main model fails;
   - a model for each **hybrid judge** step.
4. **Test and save.** It test-calls every chosen model (✅/❌), lets you re-pick the failed ones, and saves to `llm_variables.json`.

`python3 setup_models.py --check` only shows the status.

Models are written `provider:model`:

| Provider | Example | Needs |
|---|---|---|
| `gemini` | `gemini:gemini-3-flash-preview` | the Gemini Canvas proxy on `localhost:8765` (Canvas tab open) |
| `claude` | `claude:claude-opus-5-5` | Claude Code installed and logged in (subscription). Or set `"transport": "anthropic_api"` and `ANTHROPIC_API_KEY` |
| `deepseek` | `deepseek:deepseek-v4-pro` | `DEEPSEEK_API_KEY` (api.deepseek.com) |
| `opencode` | `opencode:glm-5.3` | `OPENCODE_API_KEY` (OpenCode Go, opencode.ai/zen/go/v1) |

**OpenCode Go uses three different APIs** ([docs](https://opencode.ai/docs/go/)). The workflow picks the right one per model:

| API | Models |
|---|---|
| OpenAI `/chat/completions` | GLM, Kimi, LongCat, DeepSeek, MiMo, Hy, Space Bunny |
| OpenAI `/responses` | Grok, GPT Luna, Muse Spark |
| Anthropic-compatible `/messages` | MiniMax, Qwen |

`setup_models.py` shows the API next to each model that doesn't use chat, e.g. `opencode:qwen3.8-max (messages)`.
- To send a new model to another API, add `"endpoints": {"<model-id>": "chat|responses|messages"}` under `providers.opencode`.
- If `/models` can't be read, the documented model list is shown instead, and the key is checked with a real call.

**Answer length for API models.** Reasoning models (Qwen, DeepSeek, Kimi…) think before answering, and that thinking counts against each step's `max_tokens`. Those limits were tuned for Gemini, so for API providers:
- every step gets at least `min_max_tokens` (16,000; a ceiling, not a target);
- an answer that still stops at the limit is re-sent once with double the limit, up to `max_output_cap` (32,000);
- a model that refuses a limit that high is retried with 8,192.

Both numbers can be changed per provider in `llm_variables.json`.

**Progress and slow models.** Each step prints `▶ step … (model)` when it starts. For API models, a live line shows `⏳ model · 1m 40s · thinking…` / `writing ~1,850 tokens`. If one call takes longer than `step_timeout_seconds` (default 600 = 10 min), it's stopped and the step runs on the backup model. `setup_models.py` shows how long each model takes to answer. Slow models are better as judges (~5 calls each) than as the main writer (~20 calls).

Any other OpenAI-compatible service can be added under `providers` with `"type": "openai"`, a `base_url` and an `api_key_env`.

### Gemini Canvas proxy: special handling (this provider only)
The proxy ([pranrichh/gemini-canvas-proxy](https://github.com/pranrichh/gemini-canvas-proxy)) has limits a normal API doesn't. The workflow handles them for `gemini` only:

| Proxy limit | What the workflow does | Setting |
|---|---|---|
| Each request gets 60 s, then error 504 (the Canvas tab is slow or closed) | Retries the step, then uses the backup model | `timeout_retries` (2), `retry_wait_seconds` (10) |
| Gemini's hidden thinking counts against `max_tokens` and can cut answers or JSON short | Raises `max_tokens` to at least 16,000 | `min_max_tokens` |
| The proxy supports JSON mode | Switches it on for steps that ask for JSON only (the auditors) | `json_mode` |
| Safety blocks come back as error 502 | Not retried, since the same prompt gets the same answer. Goes to the backup model | |
| `/v1/models` also lists image models | Hidden in `setup_models.py` | |
| The token lives in `<proxy folder>/native_host/.proxy_token` | `setup_models.py` asks for the proxy folder and reads it | `token_file` |

DeepSeek, OpenCode Go, Claude and any other API are called normally, with none of these limits: long time-outs, the client's standard retries, and the step's own settings.

**Backup model (optional, recommended).** If the main model still fails after its retries, that step runs on `backup_model`, e.g. `deepseek:deepseek-v4-flash`. The next step tries the main model again.

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
  - If the main model fails too, the backup model runs that step. Only if that also fails (or there's no backup) does the run stop.
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
