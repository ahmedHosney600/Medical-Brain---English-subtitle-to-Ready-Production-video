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
