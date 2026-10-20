"""Generates a session's B-roll media through Google Flow (gflow-cli) into 4_broll/.

For every manifest item (see manifest.py):
  broll_image  Nano Banana Pro, 16:9, x4 → each upscaled to 2K
  broll_video  omni-flash, 8 s, 16:9 → x4 as four separate x1 calls (gflow saves one file per
               call; a "--count 4" call billed four clips and kept one) → each upscaled to 1080p
  whiteboard   the finished drawing x4 (Nano Banana Pro → 2K), one chosen; a blank board made
               from it (same surface and light, nothing drawn); then the draw-on video x4 from
               that blank board (start frame) to the chosen drawing (end frame). This "M4"
               method won a 10-way test on 2026-10-20: a realistic hand draws stroke by stroke
               and lands exactly on the drawing, so the Premiere labels line up.

Money and safety:
  - nothing is generated twice: logs/broll_state.json records every variant (media id, files)
    and a re-run continues where the last one stopped;
  - a paid video request is never blindly re-sent: when its reply is lost, the clip is looked
    up in gflow's catalog and downloaded for free;
  - paid submissions are spaced out (Flow's security layer reacts to bursts);
  - auth / quota / block / credits / UI-drift errors stop the whole run with what to do.
"""
import datetime
import json
import os
import random
import re
import shutil
import time

from .. import session_layout as layout
from ..paths import PROJECT_ROOT
from .gflow import Gflow, GflowFailed, GflowStop


class BrollNotReady(Exception):
    """Generation cannot start yet (gflow missing, no login, credits not confirmed)."""

DEFAULTS = {
    "gflow_bin": "gflow",
    "profile": "",
    "video_model": "omni-flash",
    "video_duration": 8,
    "video_aspect": "16:9",
    "video_resolution": "720p",
    "video_count": 4,
    "video_upscale": "1080p",
    "image_model": "nano-pro",
    "image_aspect": "16:9",
    "image_count": 4,
    "image_upscale": "2k",
    "whiteboard_image_count": 4,
    "whiteboard_video_count": 4,
    "whiteboard_pick": 1,
    "pause_between_videos": [30, 90],
    "pause_between_images": [8, 20],
    "ask_before_spending": True,
}

BLANK_BOARD_PROMPT = ("The exact same whiteboard, camera angle, lighting and white surface, but completely "
                      "empty: nothing drawn, no lines, no marks, no text. Clean blank whiteboard.")
UPSCALE_PATCH_HINT = ("1080p export failed — gflow-cli 0.86 aborts it on this account (a known bug). Apply the "
                      "one-line fix: python3 medical_brain/media/patch_gflow_upscale.py (see docs/Guides/Broll_Generation_Guide.md) "
                      "and run the same command again: finished clips are only upscaled, not regenerated.")


def load_settings(overrides: dict = None) -> dict:
    """DEFAULTS ← input_fields.json "broll_generation" ← overrides."""
    settings = dict(DEFAULTS)
    try:
        with open(os.path.join(PROJECT_ROOT, "input_fields.json"), encoding="utf-8") as f:
            settings.update(json.load(f).get("broll_generation") or {})
    except (OSError, ValueError):
        pass
    settings.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return settings


def sanitize_prompt(prompt: str, duration=None) -> str:
    """One line, ≤ 2000 chars (gflow's limit). A draw-on spec that says "exactly 11 seconds"
    (Flow clips are 4–10 s) is told the length it will really get."""
    p = re.sub(r"\s+", " ", prompt or "").strip()
    if duration:
        # Any stated clip length ("exactly 11 seconds", "6 seconds duration", "8s.") is the
        # clip Flow will really make.
        p = re.sub(r"(?i)\b(?:exactly\s+)?\d+(?:\.\d+)?\s*(?:seconds?|secs?)\b(?:\s+(?:duration|long))?",
                   f"{duration} seconds", p)
        p = re.sub(r"(?i)\b\d+(?:\.\d+)?s\b(?=[\s.,;]*$)", f"{duration} seconds", p)
    return p[:2000]


def timecode_label(time_text: str) -> str:
    """"0:49" → "0m49s", "1:02:05" → "1h02m05s", "" → ""."""
    parts = re.findall(r"\d+", (time_text or "").split(".")[0])
    if len(parts) == 2:
        return f"{int(parts[0])}m{int(parts[1]):02d}s"
    if len(parts) == 3:
        return f"{int(parts[0])}h{int(parts[1]):02d}m{int(parts[2]):02d}s"
    return ""


def _base_name(item: dict) -> str:
    tc = timecode_label(item.get("time", ""))
    return f"{item['id']}_{tc}" if tc else item["id"]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class Run:
    def __init__(self, manifest: dict, broll_dir: str, settings: dict, picks: dict = None,
                 gflow: Gflow = None, sleep=time.sleep):
        self.manifest = manifest
        self.dir = broll_dir
        self.s = settings
        self.picks = picks or {}
        self.logs = os.path.join(broll_dir, layout.LOGS_DIR)
        os.makedirs(self.logs, exist_ok=True)
        self.state_path = os.path.join(self.logs, "broll_state.json")
        self.state = self._load()
        self.gflow = gflow or Gflow(settings["gflow_bin"], os.path.join(self.logs, "gflow"), settings.get("profile", ""),
                                    sleep=sleep)
        self.sleep = sleep
        self.paid_submitted = 0
        self.last_image_call = 0.0

    # ------------------------------------------------------------------ state
    def _load(self) -> dict:
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        return {"project": "", "items": {}}

    def save(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def entry(self, item: dict) -> dict:
        return self.state["items"].setdefault(item["id"], {"kind": item["kind"], "images": [], "videos": [],
                                                            "errors": []})

    # ------------------------------------------------------------------ planning
    def selected(self, only: set = None, ids: set = None) -> list:
        kinds = {"images": {"broll_image"}, "videos": {"broll_video"}, "whiteboards": {"whiteboard"}}
        allowed = set().union(*(kinds[k] for k in only)) if only else set(kinds["images"] | kinds["videos"] | kinds["whiteboards"])
        return [i for i in self.manifest["items"] if i["kind"] in allowed and (not ids or i["id"] in ids)]

    def plan(self, items: list) -> dict:
        """What is still to make: image prompts, paid video clips."""
        out = {"image_calls": 0, "images": 0, "paid_clips": 0, "items": len(items)}
        for item in items:
            done = self.state["items"].get(item["id"], {})
            if item["kind"] in ("broll_image", "whiteboard") and not done.get("images"):
                count = self.s["image_count"] if item["kind"] == "broll_image" else self.s["whiteboard_image_count"]
                out["image_calls"] += 1
                out["images"] += int(count)
            if item["kind"] in ("broll_video", "whiteboard"):
                want = int(self.s["video_count"] if item["kind"] == "broll_video" else self.s["whiteboard_video_count"])
                have = len([v for v in done.get("videos", []) if v.get("original")])
                out["paid_clips"] += max(0, want - have)
        return out

    # ------------------------------------------------------------------ pacing
    def _pace_video(self) -> None:
        if self.paid_submitted:
            low, high = self.s["pause_between_videos"]
            self.sleep(random.uniform(low, high))
        self.paid_submitted += 1

    def _pace_image(self) -> None:
        if self.last_image_call:
            low, high = self.s["pause_between_images"]
            self.sleep(random.uniform(low, high))
        self.last_image_call = time.time()

    # ------------------------------------------------------------------ helpers
    def _folder(self, kind: str) -> str:
        sub = {"broll_image": "images", "broll_video": "videos", "whiteboard": "whiteboards"}[kind]
        path = os.path.join(self.dir, sub)
        os.makedirs(os.path.join(path, "_originals"), exist_ok=True)
        return path

    @staticmethod
    def _move(src: str, dst: str) -> str:
        if not src or not os.path.exists(src):
            return ""
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return dst

    def _images(self, item: dict, prompt: str, count: int) -> None:
        """t2i x count → originals renamed into _originals/, each upscaled into the folder."""
        entry = self.entry(item)
        folder, base = self._folder(item["kind"]), _base_name(item)
        if not entry["images"]:
            self._pace_image()
            made = self.gflow.t2i(prompt, model=self.s["image_model"], aspect=self.s["image_aspect"],
                                  count=int(count), project=self.state["project"],
                                  out_dir=os.path.join(self.logs, "downloads"), tag=f"{item['id']}_t2i")
            if not made:
                raise GflowFailed(7, "Flow returned no images")
            for n, (media, path) in enumerate(made, 1):
                ext = os.path.splitext(path)[1] or ".jpg"
                original = self._move(path, os.path.join(folder, "_originals", f"{base}_v{n}{ext}"))
                entry["images"].append({"n": n, "media": media, "original": original, "final": "", "upscaled": False})
            self.save()
        for variant in entry["images"]:
            if variant.get("upscaled") or not variant.get("media"):
                continue
            try:
                got = self.gflow.upscale_image(variant["media"], scale=self.s["image_upscale"],
                                               project=self.state["project"],
                                               out_dir=os.path.join(self.logs, "downloads"),
                                               tag=f"{item['id']}_v{variant['n']}_upscale")
            except GflowFailed as exc:
                got = ""
                variant["upscale_error"] = str(exc)
            ext = os.path.splitext(got or variant["original"])[1] or ".jpg"
            if got:
                variant["final"] = self._move(got, os.path.join(folder, f"{base}_v{variant['n']}_{self.s['image_upscale']}{ext}"))
                variant["upscaled"] = bool(variant["final"])
            if not variant.get("final"):
                variant["final"] = os.path.join(folder, f"{base}_v{variant['n']}{ext}")
                shutil.copy2(variant["original"], variant["final"])
            self.save()

    def _one_clip(self, item: dict, n: int, mode: str, prompt: str, **frames) -> dict:
        """One paid clip: submit (paced), recover a lost reply from the catalog, never re-send
        a request Flow may have billed."""
        folder, base = self._folder(item["kind"]), _base_name(item)
        downloads = os.path.join(self.logs, "downloads")
        for attempt in (1, 2):
            self._pace_video()
            since = _now_iso()
            known = {v.get("media") for e in self.state["items"].values() for v in e.get("videos", [])}
            media, path = self.gflow.video(mode, prompt, model=self.s["video_model"],
                                           duration=self.s["video_duration"], aspect=self.s["video_aspect"],
                                           resolution=self.s["video_resolution"], project=self.state["project"],
                                           out_dir=downloads, tag=f"{item['id']}_v{n}_{mode}", **frames)
            if not path:
                fresh = [r for r in self.gflow.recent_videos(self.state["project"], since)
                         if r.get("media_id") and r["media_id"] not in known]
                if fresh:                     # made and billed; only the download was lost
                    media = fresh[0]["media_id"]
                    path = self.gflow.download_video(media, downloads, f"{item['id']}_v{n}_recover")
            if path:
                original = self._move(path, os.path.join(folder, "_originals", f"{base}_v{n}.mp4"))
                return {"n": n, "media": media, "original": original, "final": "", "upscaled": False}
            if attempt == 1:
                print(f"    ↻ {item['id']} v{n}: Flow did not answer and made no clip — trying once more")
        raise GflowFailed(9, "no clip after two requests")

    def _clips(self, item: dict, mode: str, prompt: str, count: int, **frames) -> None:
        entry = self.entry(item)
        folder, base = self._folder(item["kind"]), _base_name(item)
        have = {v["n"] for v in entry["videos"] if v.get("original")}
        for n in range(1, int(count) + 1):
            if n in have:
                continue
            print(f"  🎬 {item['id']} clip {n}/{count}…")
            entry["videos"].append(self._one_clip(item, n, mode, prompt, **frames))
            entry["videos"].sort(key=lambda v: v["n"])
            self.save()
        for variant in entry["videos"]:
            if variant.get("upscaled") or variant.get("upscale_failed") or not variant.get("media"):
                continue
            target = os.path.join(folder, f"{base}_v{variant['n']}_{self.s['video_upscale']}.mp4")
            try:
                got = self.gflow.upscale_video(variant["media"], scale=self.s["video_upscale"],
                                               project=self.state["project"],
                                               out_dir=os.path.join(self.logs, "downloads"),
                                               tag=f"{item['id']}_v{variant['n']}_upscale")
            except GflowFailed as exc:
                got = ""
                variant["upscale_error"] = str(exc)
            if got:
                variant["final"] = self._move(got, target)
                variant["upscaled"] = True
            else:
                variant["upscale_failed"] = True
                variant["final"] = os.path.join(folder, f"{base}_v{variant['n']}.mp4")
                shutil.copy2(variant["original"], variant["final"])
            self.save()

    # ------------------------------------------------------------------ kinds
    def broll_image(self, item: dict) -> None:
        self._images(item, sanitize_prompt(item["prompt"]), self.s["image_count"])

    def broll_video(self, item: dict) -> None:
        self._clips(item, "t2v", sanitize_prompt(item["prompt"]), self.s["video_count"])

    def whiteboard(self, item: dict) -> None:
        entry = self.entry(item)
        self._images(item, sanitize_prompt(item["prompt"]), self.s["whiteboard_image_count"])
        pick = int(self.picks.get(item["id"]) or entry.get("pick") or self.s["whiteboard_pick"] or 1)
        pick = min(max(pick, 1), len(entry["images"]))
        if entry.get("pick") and entry["pick"] != pick:
            # A new choice: the earlier blank board and clips belong to the old drawing.
            entry.setdefault("superseded", []).append({"pick": entry["pick"], "blank": entry.get("blank"),
                                                       "videos": entry["videos"]})
            entry["videos"], entry["blank"] = [], ""
        entry["pick"] = pick
        chosen = next(v for v in entry["images"] if v["n"] == pick)
        folder, base = self._folder("whiteboard"), _base_name(item)
        if not entry.get("blank"):
            self._pace_image()
            made = self.gflow.i2i(BLANK_BOARD_PROMPT, chosen["original"], model=self.s["image_model"],
                                  aspect=self.s["image_aspect"], project=self.state["project"],
                                  out_dir=os.path.join(self.logs, "downloads"), tag=f"{item['id']}_blank")
            if not made:
                raise GflowFailed(7, "Flow returned no blank board")
            ext = os.path.splitext(made[0][1])[1] or ".jpg"
            entry["blank"] = self._move(made[0][1], os.path.join(folder, "_originals", f"{base}_blank_for_v{pick}{ext}"))
            self.save()
        end_frame = chosen.get("final") if chosen.get("upscaled") else chosen["original"]
        self._clips(item, "i2v", sanitize_prompt(item["drawon_prompt"], self.s["video_duration"]),
                    self.s["whiteboard_video_count"], initial_frame=entry["blank"], end_frame=end_frame)

    # ------------------------------------------------------------------ run
    def execute(self, items: list) -> list:
        """Generates `items` (images first — they cost no credits). Returns the failures."""
        if not self.state.get("project"):
            title = (self.manifest.get("title") or os.path.basename(os.path.dirname(self.dir)) or "B-roll")
            self.state["project"] = self.gflow.create_project(f"Medical Brain — {title}")
            self.save()
        order = {"broll_image": 0, "whiteboard": 1, "broll_video": 2}
        failures = []
        for item in sorted(items, key=lambda i: order[i["kind"]]):
            print(f"▶ {item['id']} ({item['kind'].replace('_', ' ')})")
            try:
                getattr(self, item["kind"])(item)
                self.entry(item)["errors"] = []
            except GflowFailed as exc:
                print(f"  ⚠️ {item['id']}: {exc} — moving on")
                self.entry(item)["errors"].append(str(exc))
                failures.append((item["id"], str(exc)))
            self.save()
        return failures


# ---------------------------------------------------------------------- report
def write_report(broll_dir: str, manifest: dict, state: dict, session_label: str = "") -> str:
    """4_broll/00_START_HERE.md and contact_sheet.html (every variant side by side)."""
    def rel(path):
        return os.path.relpath(path, broll_dir) if path else ""

    lines = ["# 4 · B-roll media (Google Flow)", "",
             f"Session: `{session_label}`" if session_label else "", "",
             "Open `contact_sheet.html` to compare the variants. Files are named `<id>_<time>_v<n>`; "
             "`_2k` / `_1080p` are the upscaled versions, `_originals/` keeps what Flow first returned.", "",
             "| ID | Time | Kind | Start cue | Files | Status |", "|---|---|---|---|---|---|"]
    cards, upscale_warn = [], False
    for item in manifest["items"]:
        entry = state.get("items", {}).get(item["id"], {})
        files = [v.get("final") for v in entry.get("images", []) if v.get("final")]
        clips = [v.get("final") for v in entry.get("videos", []) if v.get("final")]
        upscale_warn |= any(v.get("upscale_failed") for v in entry.get("videos", []))
        if entry.get("errors"):
            status = "⚠️ " + entry["errors"][-1][:80]
        elif item["kind"] == "broll_image":
            status = "✅" if files else "—"
        else:
            status = "✅" if clips else "—"
        shown = ", ".join(f"`{os.path.basename(p)}`" for p in (clips or files)[:4])
        if item["kind"] == "whiteboard" and entry.get("pick"):
            shown += f" (drawing v{entry['pick']})"
        lines.append(f"| {item['id']} | {item.get('time', '')} | {item['kind'].replace('_', ' ')} | "
                     f"{(item.get('start_cue') or '')[:40]} | {shown or '—'} | {status} |")
        media = "".join(f'<figure><img src="{rel(p)}" loading="lazy"><figcaption>{os.path.basename(p)}</figcaption></figure>'
                        for p in files if item["kind"] == "broll_image" or not clips)
        media += "".join(f'<figure><video src="{rel(p)}" controls muted preload="metadata"></video>'
                         f'<figcaption>{os.path.basename(p)}</figcaption></figure>' for p in clips)
        if item["kind"] == "whiteboard" and clips:
            media = "".join(f'<figure><img src="{rel(v.get("final"))}" loading="lazy"><figcaption>drawing v{v["n"]}'
                            f'{" ← used" if v["n"] == entry.get("pick") else ""}</figcaption></figure>'
                            for v in entry.get("images", []) if v.get("final")) + media
        cards.append(f'<section><h2>{item["id"]} <small>{item.get("time", "")} · {item["kind"].replace("_", " ")}</small></h2>'
                     f'<p>{(item.get("prompt") or "")[:400]}</p><div class="row">{media or "<em>not generated</em>"}</div></section>')
    if upscale_warn:
        lines += ["", f"> ⚠️ {UPSCALE_PATCH_HINT}"]
    lines += ["", "Re-run (continues where it stopped, never pays twice):", "",
              f"    python3 main.py --broll {session_label or '<session>'}", "",
              "Use another whiteboard drawing for the video: `--wb-pick WB1=3` (makes new draw-on clips).", ""]
    with open(os.path.join(broll_dir, layout.START_HERE), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    html = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>B-roll Contact Sheet</title><style>body{font:14px -apple-system,system-ui,sans-serif;margin:0;padding:16px;"
            "background:#f4f5f7;color:#1b1f24}section{background:#fff;border:1px solid #e2e5ea;border-radius:8px;padding:12px;margin:0 0 16px}"
            "h2{margin:0 0 4px;font-size:16px}small{color:#5b6470;font-weight:400}p{color:#5b6470;margin:0 0 8px}"
            ".row{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px}figure{margin:0}"
            "img,video{width:100%;aspect-ratio:16/9;object-fit:contain;background:#000;border-radius:4px}"
            "figcaption{font-size:12px;color:#5b6470}@media (prefers-color-scheme:dark){body{background:#121417;color:#e8eaed}"
            "section{background:#1b1e23;border-color:#2c3138}}</style></head><body>"
            f"<h1>B-roll contact sheet</h1>{''.join(cards)}</body></html>")
    with open(os.path.join(broll_dir, "contact_sheet.html"), "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.join(broll_dir, layout.START_HERE)


# ---------------------------------------------------------------------- entry point
def generate(manifest: dict, broll_dir: str, *, settings: dict = None, only: set = None, ids: set = None,
             picks: dict = None, dry_run: bool = False, yes: bool = False, interactive: bool = False,
             session_label: str = "", gflow: Gflow = None, sleep=time.sleep) -> dict:
    """Plans, confirms and generates. Returns {"plan", "failures", "stopped"}."""
    settings = settings or load_settings()
    os.makedirs(broll_dir, exist_ok=True)
    run = Run(manifest, broll_dir, settings, picks, gflow=gflow, sleep=sleep)
    items = run.selected(only, ids)
    plan = run.plan(items)
    print(f"\n🎞️ B-roll media: {plan['items']} items · {plan['image_calls']} image prompts "
          f"({plan['images']} images, daily image quota — no credits) · "
          f"{plan['paid_clips']} video clips (Flow credits, {settings['video_model']})")
    if dry_run or not items:
        return {"plan": plan, "failures": [], "stopped": ""}
    if not run.gflow.version():
        raise BrollNotReady("gflow-cli is not installed — run: uv tool install gflow-cli && "
                            "uv tool run --from gflow-cli playwright install chromium")
    if not run.gflow.logged_in():
        raise BrollNotReady("no working Flow login — run: gflow auth login --browser chrome")
    if plan["paid_clips"] and settings.get("ask_before_spending", True) and not yes:
        if not interactive:
            raise BrollNotReady(f"{plan['paid_clips']} paid video clips need your OK — add --yes to run unattended")
        answer = input(f"Spend Flow credits on {plan['paid_clips']} clips? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Nothing generated.")
            return {"plan": plan, "failures": [], "stopped": "declined"}
    stopped = ""
    try:
        failures = run.execute(items)
    except GflowStop as exc:
        stopped, failures = str(exc), []
        print(f"\n⛔ Stopped: {exc}\n   Everything made so far is kept; run the same command again to continue.")
    finally:
        run.save()
        report = write_report(broll_dir, manifest, run.state, session_label)
        print(f"📂 Open {report} (and contact_sheet.html next to it).")
    return {"plan": plan, "failures": failures, "stopped": stopped}
