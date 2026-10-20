"""Prompt files for the Google Flow automator: B-roll images/videos and whiteboard drawings."""
import os
import re

from . import workbook as ew
from .workbook import broll_rows, plain_prompt


def broll_prompt_items(text: str, fallback_broll: str = "") -> list:
    """The B-roll prompts the prompt files and the media manifest are made from:
    [{num, time, start, end, kind ('image'/'video'), prompt}] in table order. Whiteboard
    rows and empty prompts are left out (drawings have their own files)."""
    # Sources, best first: the approved table from the production loop, the
    # document's B-roll section, then the storyboard's B-ROLL rows.
    rows = (broll_rows(ew.realign_broll_table(fallback_broll)[0])
            or broll_rows(ew.realign_broll_table(ew.find_section(text, "AI B-ROLL GENERATION PROMPTS"))[0]))
    if not rows:
        for r in ew.parse_storyboard(text):
            if "B-ROLL" in r["layer"].upper():
                parts = [p.strip() for p in re.split(r"(?<!\\)\|", r["detail"]) if p.strip()]
                kind = "image" if parts and ("🖼" in parts[0] or "IMAGE" in parts[0].upper()) else "video"
                prompt = max((p for p in parts if not re.match(r"(?i)dur\b", p)), key=len, default="")
                rows.append({"num": "", "time": r.get("time", ""), "start": "", "end": "", "kind": kind,
                             "prompt": prompt})
    items = []
    for r in rows:
        prompt = plain_prompt(r["prompt"])
        if r["kind"] == "drawing" or len(prompt) <= 30:
            continue
        items.append({"num": r.get("num", ""), "time": r.get("time", ""), "start": r.get("start", ""),
                      "end": r.get("end", ""), "kind": "image" if r["kind"] == "image" else "video",
                      "prompt": prompt})
    return items


def export_broll_prompt_files(text: str, fallback_broll: str = "", output_dir: str = "") -> tuple:
    """
    Extract AI B-roll image and video prompts and save them into two dedicated text files:
    - broll_images.txt
    - broll_videos.txt

    Format per user specification:
    text prompt 1(one line or multiline)

    text prompt 2(one line or multiline)
    """
    if not output_dir:
        return [], []

    items = broll_prompt_items(text, fallback_broll)
    images = [i["prompt"] for i in items if i["kind"] == "image"]
    videos = [i["prompt"] for i in items if i["kind"] == "video"]

    img_path = os.path.join(output_dir, "broll_images.txt")
    vid_path = os.path.join(output_dir, "broll_videos.txt")

    if images:
        with open(img_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(images).strip() + "\n")
        print(f"🖼️ B-Roll Image prompts saved to: {img_path} ({len(images)} prompts)")

    if videos:
        with open(vid_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(videos).strip() + "\n")
        print(f"🎬 B-Roll Video prompts saved to: {vid_path} ({len(videos)} prompts)")

    return images, videos


def _drawings_from_overlay_guide(guide: str) -> list:
    """(image prompt, draw-on prompt) pairs from the overlay designer's drawing specs."""
    field = r"\**\s*%s\s*\**[^:\n]*:\s*\**\s*(.+)"
    images = re.findall(field % r"whiteboard image prompt", guide or "", re.IGNORECASE)
    videos = re.findall(field % r"draw-on video prompt", guide or "", re.IGNORECASE)
    return [(plain_prompt(i), plain_prompt(v)) for i, v in zip(images, videos)]


def whiteboard_prompt_pairs(text: str, overlay_guide: str = "") -> list:
    """(image prompt, draw-on prompt) per drawing, in order: the storyboard's DRAWING rows,
    else the overlay guide's drawing specs."""
    pairs = []
    for r in ew.parse_storyboard(text):
        if "DRAWING" not in r["layer"]:
            continue
        img = re.search(r"IMAGE PROMPT:\s*(.*?)(?=\s*\|\s*(?:DRAW-ON PROMPT|LABELS IN PREMIERE|Dur)\b|$)", r["detail"], re.IGNORECASE | re.DOTALL)
        vid = re.search(r"DRAW-ON PROMPT:\s*(.*?)(?=\s*\|\s*(?:LABELS IN PREMIERE|Dur)\b|$)", r["detail"], re.IGNORECASE | re.DOTALL)
        if img and vid:
            pairs.append((img.group(1).strip(" []`\""), vid.group(1).strip(" []`\"")))
    # No DRAWING rows in the storyboard (e.g. it came back short): use the
    # approved drawing specs from the overlay guide instead.
    return pairs or _drawings_from_overlay_guide(overlay_guide)


def export_whiteboard_prompt_files(text: str, output_dir: str = "", overlay_guide: str = "") -> tuple:
    """
    Saves the whiteboard (DRAWING ANIM) prompts for the Google Flow automator:
    - whiteboard_images.txt : the finished whiteboard drawing per drawing (generate first)
    - whiteboard_videos.txt : draw-on prompt per drawing, in the same order: it runs from a
                              blank board to the matching image (its END frame)
    Same format as the B-roll files: prompts separated by a blank line.
    """
    if not output_dir:
        return [], []
    pairs = whiteboard_prompt_pairs(text, overlay_guide)
    images = [i for i, _ in pairs]
    videos = [v for _, v in pairs]
    for name, prompts, label in (("whiteboard_images.txt", images, "image"), ("whiteboard_videos.txt", videos, "draw-on video")):
        if prompts:
            path = os.path.join(output_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(prompts).strip() + "\n")
            print(f"✏️ Whiteboard {label} prompts saved to: {path} ({len(prompts)} prompts)")
    return images, videos
