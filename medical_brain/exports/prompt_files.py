"""Prompt files for the Google Flow automator: B-roll images/videos and whiteboard drawings."""
import os
import re

from . import workbook as ew


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

    images = []
    videos = []

    # Strategy 1: the approved B-roll table from the production loop (complete)
    block = fallback_broll or ""

    # Strategy 2: the document's AI B-ROLL GENERATION PROMPTS section
    if not block:
        block = ew.find_section(text, "AI B-ROLL GENERATION PROMPTS")

    # Strategy 3: fallback to storyboard if still empty
    if not block:
        match_sb = re.search(r"###\s*🎬?\s*INTEGRATED PRODUCTION STORYBOARD.*?\n(.*?)(?=\n###|\Z)", text, re.DOTALL)
        if match_sb:
            block = match_sb.group(1)

    for line in block.splitlines():
        line_clean = line.strip()
        if (
            line_clean.startswith("|")
            and not line_clean.startswith("|---")
            and "DRAWING" not in line_clean.upper()  # whiteboard rows have their own files
            and not any(h in line_clean.upper() for h in ["FULL AI GENERATION PROMPT", "AI GENERATION PROMPT", "TIMECODE", "START CUE", "LAYER"])
        ):
            parts = [p.strip() for p in line_clean.split("|") if p.strip()]
            if not parts:
                continue
            longest = max(parts, key=len)
            if len(longest) > 30:
                cleaned_prompt = longest.replace('\\"', '"').strip('"\'`')
                cleaned_prompt = re.sub(r"^(?:🎬|🖼️)?\s*(?:Video|Image)\s*\|?\s*", "", cleaned_prompt, flags=re.IGNORECASE).strip()
                # Plain text for the generator: no markdown bold/italics or <br> tags.
                cleaned_prompt = re.sub(r"\*\*|__|<br\s*/?>", " ", cleaned_prompt)
                cleaned_prompt = re.sub(r"\s{2,}", " ", cleaned_prompt).replace(" :", ":").strip()
                line_lower = line_clean.lower()
                if "🖼" in line_clean or "image" in line_lower:
                    images.append(cleaned_prompt)
                elif "🎬" in line_clean or "video" in line_lower:
                    videos.append(cleaned_prompt)

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


def export_whiteboard_prompt_files(text: str, output_dir: str = "") -> tuple:
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
    for name, prompts, label in (("whiteboard_images.txt", images, "image"), ("whiteboard_videos.txt", videos, "draw-on video")):
        if prompts:
            path = os.path.join(output_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(prompts).strip() + "\n")
            print(f"✏️ Whiteboard {label} prompts saved to: {path} ({len(prompts)} prompts)")
    return images, videos
