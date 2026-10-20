#!/usr/bin/env python3
"""A stand-in for the gflow-cli executable (tests never reach Google Flow).

Mirrors the real CLI's file layout and JSON: t2i/i2i write <out>/<uuid>_<k>.jpg, video writes
<out-dir>/<uuid>.mp4, upscales nest under <out>/images|videos/<date>/<id>_<scale>.<ext>.

Env:  FAKE_GFLOW_DIR       folder for calls.jsonl, counters.json, catalog.jsonl
      FAKE_GFLOW_SCENARIO  JSON: {"lose_video": [2]}      video call #2 exits 7, clip lands in the catalog
                                 {"video_rc": {"3": 10}}   video call #3 exits with that code
                                 {"policy_word": "FORBIDDEN"} image prompts containing it exit 5
                                 {"video_upscale_rc": 7}   every video upscale exits 7
                                 {"batch_lose": [2]}       extra clip #2 of a --count N call is not downloaded
"""
import json
import os
import sys
import uuid

base = os.environ["FAKE_GFLOW_DIR"]
scenario = json.loads(os.environ.get("FAKE_GFLOW_SCENARIO") or "{}")
args = sys.argv[1:]
with open(os.path.join(base, "calls.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\n")


def opt(name, default=""):
    return args[args.index(name) + 1] if name in args else default


def bump(key):
    path = os.path.join(base, "counters.json")
    counters = json.load(open(path)) if os.path.exists(path) else {}
    counters[key] = counters.get(key, 0) + 1
    json.dump(counters, open(path, "w"))
    return counters[key]


def touch(path, data=b"x" * 2000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def catalog_add(media, project):
    with open(os.path.join(base, "catalog.jsonl"), "a") as f:
        f.write(json.dumps({"media_id": media, "project_id": project, "status": "pending",
                            "created_at": "2999-01-01T00:00:00+00:00", "local_path": None}) + "\n")


if args[:1] == ["--version"]:
    print("gflow, version 0.86.0")
    sys.exit(0)
cmd = args[:2]
if cmd == ["auth", "status"]:
    sys.exit(0)
if cmd == ["project", "create"]:
    print(json.dumps({"status": "ok", "project_id": "proj-1"}))
    sys.exit(0)
if cmd in (["image", "t2i"], ["image", "i2i"]):
    if scenario.get("policy_word") and scenario["policy_word"] in args[2]:
        print(json.dumps({"title": "content policy", "detail": "prompt rejected"}))
        sys.exit(5)
    out = opt("--out")
    images = []
    for k in range(1, int(opt("-n", "1")) + 1):
        media = str(uuid.uuid4())
        images.append({"media_name": media, "local_path": touch(os.path.join(out, f"{media}_{k}.jpg"))})
    print(json.dumps({"images": images}))
    sys.exit(0)
if cmd == ["image", "upscale"]:
    touch(os.path.join(opt("--out"), "images", "2026-10-20", f"{args[2]}_{opt('--scale')}.jpg"))
    print("Saved")
    sys.exit(0)
if cmd[0] == "video" and cmd[1] in ("t2v", "i2v", "r2v"):
    n = bump("video")
    media = str(uuid.uuid4())
    rc = int(scenario.get("video_rc", {}).get(str(n), 0))
    if rc:
        print(json.dumps({"title": "error", "detail": f"scenario rc {rc}"}))
        sys.exit(rc)
    if n in scenario.get("lose_video", []):
        catalog_add(media, opt("--project"))
        print(json.dumps({"title": "wire format", "detail": "no signed media URL"}))
        sys.exit(7)
    path = touch(os.path.join(opt("--out-dir"), f"{media}.mp4"))
    extras = []
    for k in range(1, int(opt("--count", "1"))):          # the batch-clips-patched gflow
        other = str(uuid.uuid4())
        lost = k in scenario.get("batch_lose", [])
        extras.append({"media_id": other,
                       "local_path": None if lost else touch(os.path.join(opt("--out-dir"), f"{other}.mp4"))})
    print(json.dumps({"status": "ok", "media_id": media, "local_path": path, "succeeded": True,
                      "extra_clips": extras}))
    sys.exit(0)
if cmd == ["video", "upscale"]:
    if scenario.get("video_upscale_rc"):
        sys.exit(int(scenario["video_upscale_rc"]))
    touch(os.path.join(opt("--out"), "videos", "2026-10-20", f"{args[2]}_{opt('--scale')}.mp4"))
    print("Saved")
    sys.exit(0)
if cmd == ["data", "list"]:
    path = os.path.join(base, "catalog.jsonl")
    if os.path.exists(path):
        print(open(path).read(), end="")
    sys.exit(0)
if cmd == ["data", "download"]:
    path = touch(os.path.join(opt("--out"), f"{args[2]}.mp4"))
    print(json.dumps({"media_id": args[2], "path": path, "bytes": 2000}))
    sys.exit(0)
print(json.dumps({"title": "unknown command"}))
sys.exit(2)
