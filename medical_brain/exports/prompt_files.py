"""Prompt files for the Google Flow automator: B-roll images/videos and whiteboard drawings."""
import os
import re

from . import workbook as ew
from .workbook import broll_rows, plain_prompt


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

    images, videos = [], []

    # Sources, best first: the approved table from the production loop, the
    # document's B-roll section, then the storyboard's B-ROLL rows.
    rows = broll_rows(fallback_broll) or broll_rows(ew.find_section(text, "AI B-ROLL GENERATION PROMPTS"))
    if not rows:
        for r in ew.parse_storyboard(text):
            if "B-ROLL" in r["layer"].upper():
                parts = [p.strip() for p in re.split(r"(?<!\\)\|", r["detail"]) if p.strip()]
                kind = "image" if parts and ("🖼" in parts[0] or "IMAGE" in parts[0].upper()) else "video"
                prompt = max((p for p in parts if not re.match(r"(?i)dur\b", p)), key=len, default="")
                rows.append({"kind": kind, "prompt": prompt})
    for r in rows:
        prompt = plain_prompt(r["prompt"])
        if r["kind"] == "drawing" or len(prompt) <= 30:
            continue                      # whiteboard rows have their own files
        (images if r["kind"] == "image" else videos).append(prompt)

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


def export_whiteboard_prompt_files(text: str, output_dir: str = "", overlay_guide: str = "") -> tuple:
    """
    Saves the whiteboard (DRAWING ANIM) prompts for the Google Flow automator:
    - whiteboard_images.txt : still whiteboard image per drawing (generate first)
    - whiteboard_videos.txt : draw-on image-to-video prompt per drawing, in the
                              same order, to run with the matching image as start frame
    Same format as the B-roll files: prompts separated by a blank line.
    """
    if not output_dir:
        return [], []
    images, videos = [], []
    for r in ew.parse_storyboard(text):
        if "DRAWING" not in r["layer"]:
            continue
        img = re.search(r"IMAGE PROMPT:\s*(.*?)(?=\s*\|\s*(?:DRAW-ON PROMPT|LABELS IN PREMIERE|Dur)\b|$)", r["detail"], re.IGNORECASE | re.DOTALL)
        vid = re.search(r"DRAW-ON PROMPT:\s*(.*?)(?=\s*\|\s*(?:LABELS IN PREMIERE|Dur)\b|$)", r["detail"], re.IGNORECASE | re.DOTALL)
        if img and vid:
            images.append(img.group(1).strip(" []`\""))
            videos.append(vid.group(1).strip(" []`\""))
    if not images:
        # No DRAWING rows in the storyboard (e.g. it came back short): use the
        # approved drawing specs from the overlay guide instead.
        for img, vid in _drawings_from_overlay_guide(overlay_guide):
            images.append(img)
            videos.append(vid)
    for name, prompts, label in (("whiteboard_images.txt", images, "image"), ("whiteboard_videos.txt", videos, "draw-on video")):
        if prompts:
            path = os.path.join(output_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(prompts).strip() + "\n")
            print(f"✏️ Whiteboard {label} prompts saved to: {path} ({len(prompts)} prompts)")
    return images, videos
