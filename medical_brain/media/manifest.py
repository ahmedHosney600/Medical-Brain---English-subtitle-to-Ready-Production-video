"""The B-roll media manifest: every image, video and whiteboard the session asks for, with
its B-roll number, time and cue, in one JSON file (3_video/07_broll_manifest.json).

The prompt .txt files are anonymous blank-line blocks; the manifest is what lets generated
files be named "B07_0m49s_v2" and land next to the right moment in the edit. A person can
also hand the generator their own file — the same JSON, or a folder of the four .txt files
— see read_manifest()."""
import json
import os
import re

from ..exports.prompt_files import broll_prompt_items, whiteboard_prompt_pairs

MANIFEST_NAME = "07_broll_manifest.json"
MANIFEST_VERSION = 1
KINDS = ("broll_image", "broll_video", "whiteboard")

_TIME_RANGE = re.compile(r"(\d{1,2}:\d{2}(?:\.\d+)?)\s*[–-]\s*(\d{1,2}:\d{2}(?:\.\d+)?)")
_DRAWING_HEAD = re.compile(r"(?im)^[^\n]*\bdrawing animation\s*#?\s*\d+[^\n]*$")


def _whiteboard_times(overlay_guide: str) -> list:
    """The on-screen time of each drawing spec ("Drawing Animation N … 3:48 – 4:02"), in order."""
    heads = list(_DRAWING_HEAD.finditer(overlay_guide or ""))
    times = []
    for n, head in enumerate(heads):
        end = heads[n + 1].start() if n + 1 < len(heads) else len(overlay_guide)
        block = overlay_guide[head.start():end]
        found = _TIME_RANGE.search(block)
        times.append(found.group(1) if found else "")
    return times


def build_manifest(final_package: str, broll_table: str = "", overlay_guide: str = "",
                   title: str = "") -> dict:
    """The manifest for a finished video package (the same prompts as the .txt files)."""
    items = []
    for n, row in enumerate(broll_prompt_items(final_package, broll_table), 1):
        number = row["num"] or str(n)
        items.append({"id": f"B{int(number):02d}" if number.isdigit() else f"B{n:02d}",
                      "kind": "broll_image" if row["kind"] == "image" else "broll_video",
                      "broll_number": number, "time": row["time"], "start_cue": row["start"],
                      "end_cue": row["end"], "prompt": row["prompt"]})
    times = _whiteboard_times(overlay_guide)
    for n, (image, drawon) in enumerate(whiteboard_prompt_pairs(final_package, overlay_guide), 1):
        items.append({"id": f"WB{n}", "kind": "whiteboard", "time": times[n - 1] if n <= len(times) else "",
                      "prompt": image, "drawon_prompt": drawon})
    return {"version": MANIFEST_VERSION, "title": title, "items": items}


def write_manifest(folder: str, manifest: dict) -> str:
    if not manifest.get("items"):
        return ""
    path = os.path.join(folder, MANIFEST_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"🗂️ B-roll media manifest saved to: {path} ({len(manifest['items'])} items)")
    return path


def _blocks(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return [b.strip() for b in re.split(r"\n\s*\n", f.read()) if b.strip()]


def _from_text_files(paths: list) -> dict:
    """Items from the prompt .txt files (numbered or not), kind taken from each file's name."""
    by_kind = {}
    for path in paths:
        name = os.path.basename(path).lower()
        if "whiteboard" in name:
            by_kind["wb_video" if "video" in name else "wb_image"] = _blocks(path)
        elif "image" in name:
            by_kind["broll_image"] = _blocks(path)
        elif "video" in name:
            by_kind["broll_video"] = _blocks(path)
    items, n = [], 0
    for kind in ("broll_video", "broll_image"):
        for prompt in by_kind.get(kind, []):
            n += 1
            items.append({"id": f"B{n:02d}", "kind": kind, "broll_number": str(n), "time": "", "prompt": prompt})
    images, videos = by_kind.get("wb_image", []), by_kind.get("wb_video", [])
    if len(images) != len(videos) and images and videos:
        raise ValueError(f"{len(images)} whiteboard image prompts but {len(videos)} draw-on prompts: "
                         "they must pair up, in the same order")
    for k, image in enumerate(images, 1):
        items.append({"id": f"WB{k}", "kind": "whiteboard", "time": "", "prompt": image,
                      "drawon_prompt": videos[k - 1] if videos else ""})
    return {"version": MANIFEST_VERSION, "title": "", "items": items}


def read_manifest(path: str) -> dict:
    """A manifest from a .json file, a single prompt .txt file, or a folder holding the
    prompt .txt files. Raises ValueError with a readable reason when it cannot be used."""
    if os.path.isdir(path):
        txts = sorted(os.path.join(path, n) for n in os.listdir(path)
                      if n.lower().endswith(".txt") and re.search(r"(?i)broll|b-roll|whiteboard", n))
        jsons = [os.path.join(path, n) for n in os.listdir(path) if n == MANIFEST_NAME]
        if jsons:
            return read_manifest(jsons[0])
        if not txts:
            raise ValueError(f"{path} has no {MANIFEST_NAME} and no B-roll/whiteboard .txt prompt files")
        manifest = _from_text_files(txts)
    elif path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
    elif path.lower().endswith(".txt"):
        manifest = _from_text_files([path])
    else:
        raise ValueError(f"{path}: give a .json manifest, a prompt .txt file, or a folder of them")
    return validate(manifest)


def validate(manifest: dict) -> dict:
    items = manifest.get("items") if isinstance(manifest, dict) else None
    if not items:
        raise ValueError("the manifest has no items")
    seen = set()
    for k, item in enumerate(items, 1):
        if item.get("kind") not in KINDS:
            raise ValueError(f"item {k}: kind must be one of {', '.join(KINDS)}")
        if not str(item.get("prompt", "")).strip():
            raise ValueError(f"item {k} ({item.get('id', '?')}): empty prompt")
        if item["kind"] == "whiteboard" and not str(item.get("drawon_prompt", "")).strip():
            raise ValueError(f"item {k} ({item.get('id', '?')}): a whiteboard needs a drawon_prompt")
        item.setdefault("id", f"{'WB' if item['kind'] == 'whiteboard' else 'B'}{k:02d}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", item["id"]):
            raise ValueError(f"item {k}: id {item['id']!r} may only use letters, digits, - and _")
        if item["id"] in seen:
            raise ValueError(f"item id {item['id']} appears twice")
        seen.add(item["id"])
    return manifest
