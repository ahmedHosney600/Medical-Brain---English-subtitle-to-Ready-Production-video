# B-roll Generation Guide (Google Flow)

The script workflow writes the B-roll prompts. This step **generates the media itself**,
images, videos and whiteboard draw-ons, in your own Google Flow account, and puts the files in
`4_broll/` next to the rest of the session, named by B-roll number and time.

It is **optional and separate**. Nothing runs, and no credits are spent, unless you ask.

---

## 1. One-time setup

```bash
uv tool install gflow-cli                                   # the Flow driver (its own environment)
uv tool run --from gflow-cli playwright install chromium    # its browser (~150 MB)
gflow auth login --browser chrome                           # sign in once, in a real Chrome window
python3 medical_brain/media/patch_gflow_upscale.py          # 1080p fix for gflow-cli 0.86 (see §6)
```

- **Account:** a Google account with Flow access (Google AI Pro/Ultra).
- **Unofficial tool:** gflow-cli drives Flow through a real Chrome window. It is not affiliated with
  Google, so treat automation as your own account's risk.
- **What costs what:**
  - **Videos** spend Flow credits.
  - **Images and upscales** use a daily image quota, not credits.

---

## 2. Run it

| You want | Command |
|---|---|
| See what it would make (nothing generated) | `python3 main.py --broll output/<session> --dry-run` |
| Generate everything for a session | `python3 main.py --broll output/<session>` |
| Run unattended (no credit question) | add `--yes` |
| Only some kinds | `--only images` · `--only videos` · `--only whiteboards` (comma-separate several) |
| Only some items | `--items B03,B07,WB1` |
| Generate during a full workflow run | add `--generate-broll` (with `--yes` for background runs) |
| From your own prompts | `python3 main.py --broll-file FILE [--into output/<session>]` |
| From the menu | `python3 main.py` → **7** |

**Before anything is spent**, every run prints its plan, for example *"34 items · 6 image prompts
(24 images) · 128 video clips"*, and asks for your OK.

**Stopping and resuming:** you can stop at any time. Running the same command again continues
where it stopped, and nothing that already exists is generated or paid for twice.

---

## 3. What it makes (defaults)

| Kind | How | Files |
|---|---|---|
| B-roll image | Nano Banana Pro, 16:9, **x4**, each upscaled to **2K** | `images/B12_4m05s_v1_2k.jpg` … `_v4_2k.jpg` |
| B-roll video | omni-flash, **8 s**, 16:9, **x4**, each upscaled to **1080p** | `videos/B07_0m49s_v1_1080p.mp4` … |
| Whiteboard | Finished drawing x4 (2K); then a draw-on video x4 from a **blank board → that drawing** | `whiteboards/WB1_3m48s_v1_2k.jpg` …, `whiteboards/WB1_3m48s_v1_1080p.mp4` … |

Every file Flow first returned is kept in `_originals/`.

**Why x4 video is four requests:** gflow keeps only one file from a single "x4" video request,
although Flow bills all four. Four x1 requests cost the same, and every clip is downloaded.

**How whiteboards are made (method "M4"):** this won a 10-way test on 2026-10-20.
- The draw-on video runs from a blank board (start frame) to the finished drawing (end frame).
- A realistic hand draws stroke by stroke and lands exactly on the drawing, so your Premiere labels line up.
- The blank board is made from the chosen drawing, with the same surface and light and nothing drawn, so the clip does not jump.
- The drawing used is v1 by default. To use another and make new draw-on clips: `--wb-pick WB1=3`.

**Known limit:** for about 1 s the flames (or other filled shapes) may fade in slightly ahead of the pen.
10 s clips make this worse, so the default stays at 8 s.

**Settings:** change the defaults in `input_fields.json` → `"broll_generation"`. That covers
models, counts, duration, upscale sizes, the pauses between requests, and `ask_before_spending`.

---

## 4. The files

```
output/<session>/
├── 3_video/07_broll_manifest.json   ← what to generate: id, kind, time, cue, prompt (written by the workflow)
└── 4_broll/
    ├── 00_START_HERE.md             ← table: id · time · cue · files · status
    ├── contact_sheet.html           ← every variant side by side (pick your favourites)
    ├── images/  videos/  whiteboards/   (+ _originals/ in each)
    └── logs/broll_state.json        ← what exists (media ids, files); logs/gflow/ every call's output
```

Older sessions without a manifest get one built from their checkpoint the first time you run `--broll`.

---

## 5. Your own prompts (`--broll-file`)

Give either:

- **A JSON manifest**, the same format as `07_broll_manifest.json`:
  ```json
  {"version": 1, "title": "my video", "items": [
    {"id": "B01", "kind": "broll_video", "time": "0:04", "prompt": "Macro shot of …"},
    {"id": "B02", "kind": "broll_image", "time": "1:15", "prompt": "Still photograph of …"},
    {"id": "WB1", "kind": "whiteboard", "time": "3:48",
     "prompt": "Plain white whiteboard, black marker line art of …, no text",
     "drawon_prompt": "A hand with a black marker draws …, static camera, no text appears"}
  ]}
  ```
  `kind` is `broll_image`, `broll_video` or `whiteboard`, and a whiteboard needs `drawon_prompt`.
  `id` may use letters, digits, `-` and `_`. `time` is optional and only used in file names.
- **A folder (or a single file) of prompt `.txt` files**, one prompt per blank-line block:
  - the kind is taken from the file name: `broll_videos`, `broll_images`, `whiteboard_images`, `whiteboard_videos`;
  - whiteboard images and videos pair up in order.

Without `--into`, a new session folder is made for the media.

---

## 6. When something goes wrong

| Message | What to do |
|---|---|
| `gflow-cli is not installed` | Run the setup in §1. |
| `no working Flow login` / exit 3, 8, 12, 14, 38 | `gflow auth login --browser chrome` |
| `security layer blocked this profile` (exit 10) | Stop for 30–60 minutes, then run the same command again. |
| `quota` (exit 4, 21) | Daily image quota or rate limit: wait, then run again. |
| `not enough Flow credits` (exit 37) | Top up or wait for the monthly allowance. |
| `Flow's page changed` (exit 23) | `gflow update`, then run again. |
| An item listed as "could not be made" (e.g. content policy) | Reword that prompt in the manifest and run with `--items <id>`. |
| `1080p export failed` | gflow-cli 0.86 aborts the 1080p export on some accounts. Run `python3 medical_brain/media/patch_gflow_upscale.py` (undo: `uv tool install --reinstall gflow-cli`), then run the same command again. Clips are then only upscaled, not regenerated. Until then the 720p clip is used. |

**Lost downloads:** when Flow made a clip but its download was lost, the clip is fetched from
gflow's catalog for free. A paid request is never blindly sent twice.

**Pacing:** requests are spaced out (30–90 s between videos) because Flow's security layer reacts to bursts.
