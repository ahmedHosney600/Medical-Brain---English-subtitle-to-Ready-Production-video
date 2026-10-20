# Sessions & Workflow Guide

How a video is made from start to finish, where every file goes, which file to open first, and
how to start — or restart — any part of the work inside the same session.

---

## 1. The big picture

Making one video has three stages. Each stage has its own folder inside one **session**:

```
 ┌─────────────────┐      ┌──────────────────────┐      ┌──────────────────────────┐
 │  1. RESEARCH    │ ───► │  2. REVIEW (final)   │ ───► │  3. SCRIPT WORKFLOW      │
 │  facts, sources │      │  you check & approve │      │  script, titles, B-roll, │
 │  patient guide  │      │  the input text      │      │  production, shorts      │
 └─────────────────┘      └──────────────────────┘      └──────────────────────────┘
     1_research/               2_review/                       3_video/
```

- A **session** is one folder: `output/<session id>/`, for example `output/20261018_155421/`.
  The id is the date and time the session started.
- Each stage folder is **created only when that stage runs**:
  - research → review → video: all three folders.
  - video from an English subtitle/script (no research): **only `3_video/`**.
- Inside every stage folder, the files you read are **numbered in reading order**. Always open
  `00_START_HERE.md` first: it lists what is in the folder, top to bottom, and the commands for
  the next step.
- Machine files (state, checkpoints, logs, caches) live in a `logs/` folder, so the files you
  read are never buried among them.
- Nothing is overwritten: when you re-run a stage, the earlier files move to
  `_previous_runs/<date_time>/` inside that stage's folder.

---

## 2. The folder map

```
output/20261018_155421/
│
├── 1_research/                         ← made when research runs
│   ├── 00_START_HERE.md                ← open first
│   ├── 01_patient_guide.pdf  .docx .html .md   ← what the video will be built from
│   ├── 02_evidence_library.pdf .docx .html .md ← every verified fact, its source [S#], references
│   ├── logs/                           ← research_state.json, research_checkpoint.json,
│   │                                      research_log.jsonl, dossier.json, caches,
│   │                                      review_round1.md, review_round2.md … (earlier reviews)
│   └── _previous_runs/                 ← earlier research, if you ran it again
│
├── 2_review/                           ← made when research finishes
│   ├── 00_START_HERE.md
│   ├── 01_review.md                    ← YOUR review: requests, or APPROVED: yes
│   ├── 02_video_source.md              ← the EXACT text the script workflow is written from
│   └── _previous_runs/                 ← earlier video sources you replaced
│
└── 3_video/                            ← made when the script workflow runs
    ├── 00_START_HERE.md
    ├── 01_final_script.pdf .docx .html .md  ← the whole package, in editing order
    ├── 02_shorts_package.pdf .docx .html .md← Reels & Shorts
    ├── 03_broll_images.txt             ← B-roll image prompts (Google Flow)
    ├── 04_broll_videos.txt             ← B-roll video prompts
    ├── 05_whiteboard_images.txt        ← whiteboard start frames (generate first)
    ├── 06_whiteboard_videos.txt        ← whiteboard draw-on videos (blank board → the matching drawing)
    ├── 07_broll_manifest.json          ← every B-roll item with number, time, cue (input to --broll)
    ├── logs/                           ← checkpoint.json (every step's output), session_log.jsonl,
    │                                      llm_provider.json (which model ran each step),
    │                                      sources_map.json, llm_cache/
    └── _previous_runs/                 ← earlier video packages of this session

└── 4_broll/                            ← made only when you generate the B-roll media (optional)
    ├── 00_START_HERE.md · contact_sheet.html
    ├── images/  videos/  whiteboards/  ← named <id>_<time>_v<n>_2k / _1080p
    └── logs/                           ← broll_state.json (what exists), gflow/ (every call)
```

### What to open, in order

| Folder | Open | Why |
|---|---|---|
| `1_research/` | `01_patient_guide.pdf` | The content of the future video, in plain language. |
| | `02_evidence_library.pdf` | When you want to check a fact and its source. |
| `2_review/` | `01_review.md` | Write your changes or approve. |
| | `02_video_source.md` | The exact text the script will be written from — edit it freely. |
| `3_video/` | `01_final_script.pdf` (or `.docx`) | Titles & thumbnails → script → production tables → storyboard. |
| | `02_shorts_package.pdf` | Shorts and Reels. |
| | `03`–`06` `.txt` | Paste into Google Flow (images first, then videos). |

---

## 3. The three ways to start

| You have | Command | Folders made |
|---|---|---|
| An English subtitle / script file | `python3 main.py path/to/subtitle.txt` | `3_video/` |
| A topic (research, then you review) | `python3 main.py --topic "high blood pressure at home"` | `1_research/`, `2_review/` (then `3_video/` when approved) |
| A topic, straight on to the video after approval | `python3 main.py --topic "…" --workflow research-video` | all three |
| Nothing — show me a menu | `python3 main.py` | — |

Useful options on any run: `--provider main|single|hybrid` (skip the model menu),
`--minutes N` (a target length; normally leave it out — the length follows the content).

---

## 4. Scenario walkthroughs

### A. Research → review → video (the normal path)

1. **Research.**
   ```
   python3 main.py --topic "Insomnia: what really helps"
   ```
   When it finishes it prints the session, e.g. `output/20261020_091500`.
2. **Read** `1_research/01_patient_guide.pdf`.
3. **Review** — open `2_review/01_review.md` and either:
   - write what to ADD / CHANGE / REMOVE, then run
     ```
     python3 main.py --continue output/20261020_091500
     ```
     A new research round researches only what you asked for. The new patient guide replaces
     the old one in `1_research/`; your earlier review is kept as
     `1_research/logs/review_round1.md`. Repeat as often as you like; **or**
   - write `APPROVED: yes` and run the same `--continue` command → the script workflow starts
     from `2_review/02_video_source.md` and writes `3_video/`.
4. **Open** `3_video/00_START_HERE.md`, then `01_final_script.pdf`.

### B. I changed the review myself, outside the workflow

You can edit the text the video is built from in two ways — both stay in the same session:

- **Edit it in place:** open `2_review/02_video_source.md`, change anything, save, then
  ```
  python3 main.py --from script output/20261020_091500
  ```
  The workflow sees that the file was edited and uses your version.
- **Use a file you prepared elsewhere** (Word export pasted to .md, notes from a colleague…):
  ```
  python3 main.py --from script output/20261020_091500 --source ~/Desktop/my_final_review.md
  ```
  Your file becomes `2_review/02_video_source.md` (the earlier one is kept in
  `2_review/_previous_runs/`), so the folder always shows exactly what the script used.

Tip: keep the `[S#]` source ids on medical lines you add. Lines without one are treated as
unverified by the truth check and may be dropped.

### C. Video from an English subtitle (no research)

```
python3 main.py path/to/subtitle.txt
```
Only `3_video/` is made. Later you can re-run any part of it with `--from` (section 5) — for
example `--from script` runs the whole script workflow again from the same subtitle.

### D. Re-run only one part of a finished video

```
python3 main.py --from packaging  output/20261020_091500   # new titles & thumbnails (+ everything after)
python3 main.py --from production output/20261020_091500   # new overlays, B-roll, transitions (+ after)
python3 main.py --from final      output/20261020_091500   # rebuild the final package and shorts
python3 main.py --from shorts     output/20261020_091500   # only the Reels & Shorts
python3 main.py --from export     output/20261020_091500   # only write the files again (nothing new generated)
```
The script, and every stage before the one you chose, are kept exactly as they were. The
earlier `3_video/` files move to `3_video/_previous_runs/<date_time>/`.

### E. A run stopped (quota, crash, Ctrl+C, closed laptop)

```
python3 main.py --resume output/20261020_091500
python3 main.py --resume                        # the newest stopped session
```
The run continues at the step that was interrupted, with everything already finished kept.
Answers a model had already given inside that step are reused from the cache (no new cost).
`--resume` works for research and for video runs, in both the new and the older layout.

### F. Sessions made before this layout

Older sessions keep working as they are — `--resume` and `--continue` find their files in
`research/` and the session root. To move them into the numbered layout:

```
python3 main.py --migrate output/20261018_155421   # one session
python3 main.py --migrate all                      # every session in output/
```
Nothing is deleted: files are moved and renamed (`final_script.pdf` → `3_video/01_final_script.pdf`,
`research/patient_guide.pdf` → `1_research/01_patient_guide.pdf`, `checkpoint.json` →
`3_video/logs/checkpoint.json`, …). `--from` migrates an older session automatically before it
starts.

---

## 5. Start points (`--from`) in detail

```
python3 main.py --from <point> <session> [--source FILE]
```
`<session>` can be `output/20261018_155421`, `20261018_155421`, or a full path. Without a session,
`--from` uses the most recent one. In the menu (`python3 main.py`) choose **6) Start from a chosen
point in a session**: it lists your recent sessions and the points.

| Point | What runs | What is kept | Needs |
|---|---|---|---|
| `research` | The full research again for the session's topic, then the review files | — (earlier research moves to `1_research/_previous_runs/`) | a research session |
| `review` | Applies `2_review/01_review.md` (same as `--continue`): a new research round, or the video when `APPROVED: yes` | research | a research session |
| `script` | The whole script workflow, from `2_review/02_video_source.md`, `--source FILE`, or (subtitle sessions) the same subtitle as before | research & review | a source |
| `packaging` | Titles & thumbnails → production → final package → shorts | the script | the script finished |
| `production` | Overlays → B-roll → transitions → production review (up to 3 rounds) → final package → shorts | script, titles & thumbnails | packaging finished |
| `final` | Final package → shorts | script, packaging, production | production finished |
| `shorts` | Reels & Shorts only | everything else | final package finished |
| `export` | Writes the files again (PDF/DOCX/HTML, prompt files). No model writes anything new. | everything | final package finished |

How a re-run starts **fresh**:
- the outputs of the chosen stage and every later stage are cleared,
- their revision counters are reset (the stage starts as a first draft, not a revision of the
  old one),
- the model-answer cache moves into the archive, so the models are asked again (with `--resume`
  the cache is kept, because there the goal is to not pay twice).

`--from` vs `--resume`:
- `--resume` = "continue exactly where it stopped".
- `--from <point>` = "go back to this point and redo it, then continue to the end".

---

## 6. All commands at a glance

| Command | What it does |
|---|---|
| `python3 main.py` | Menu: 1) subtitle → video · 2) topic → research → video · 3) research only · 4) continue a research session · 5) resume a stopped run · 6) start from a chosen point · 7) generate B-roll media |
| `python3 main.py FILE [video_analysis.txt]` | Subtitle/script → video package (`3_video/`) |
| `python3 main.py --topic "…"` | Research only (`1_research/`, `2_review/`) |
| `python3 main.py --topic "…" --workflow research-video` | Research, then the video after approval |
| `python3 main.py --continue [SESSION]` | Apply `2_review/01_review.md` (new round, or the video when approved) |
| `python3 main.py --continue SESSION --workflow research-video` | Build the video for a research-only session |
| `python3 main.py --resume [SESSION]` | Continue a stopped run |
| `python3 main.py --from POINT SESSION [--source FILE]` | Start from a point (section 5) |
| `python3 main.py --migrate SESSION\|all` | Move older sessions into the numbered layout |
| `python3 main.py --broll SESSION [--dry-run] [--yes]` | Generate the B-roll media with Google Flow into `4_broll/` — see [Broll_Generation_Guide.md](Broll_Generation_Guide.md) |
| `python3 main.py --broll-file FILE [--into SESSION]` | The same from your own manifest / prompt files |
| `python3 setup_models.py` | Choose which models run which steps |

---

## 7. Where things are recorded

| Question | File |
|---|---|
| Which model ran each step, any fallbacks? | `3_video/logs/llm_provider.json` |
| The full output of every step (script versions, critiques, scores)? | `3_video/logs/checkpoint.json` |
| The order steps ran in, with timestamps? | `3_video/logs/session_log.jsonl` |
| Which source supports each medical sentence of the script? | `3_video/logs/sources_map.json` (research videos) and the sources appendix at the end of `01_final_script` |
| Earlier review rounds? | `1_research/logs/review_round*.md` |
| An older version of the package? | `3_video/_previous_runs/<date_time>/` |

---

## 8. Troubleshooting

| Message | Meaning / fix |
|---|---|
| `has not finished … yet: use --resume …` | You asked to start from a stage whose earlier stage never finished. Resume the run, or choose an earlier point. |
| `has no video run yet — start with --from script` | The session has research but no video: use `--from script` (or `--continue` with `APPROVED: yes`). |
| `no script source in this session: give one with --source FILE` | A session with no research and no earlier video: pass the file with `--source`. |
| `no stopped run to resume` | Every run in that session finished. To redo a part, use `--from`. |
| A run stopped with a model or quota error | Fix the model (`python3 setup_models.py`) or wait for the quota, then `--resume` the session. |
| I can't find a file I had before | Look in that stage's `_previous_runs/` — nothing is deleted. |
