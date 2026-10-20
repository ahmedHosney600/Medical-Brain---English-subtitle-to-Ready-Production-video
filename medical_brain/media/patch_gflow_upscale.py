"""One-line fix for gflow-cli 0.86.0 `video upscale` on flow.google.com (exit 7,
"Failed to read a video export response: ... No data found for resource").

The export listener aborts the whole export when Chrome cannot hand over the body of ANY
unrelated batchexecute reply. This makes it only log that case; a real refusal of the
export RPC still fails, and success is still decided by the captured video blob.

Undo:  uv tool install --reinstall gflow-cli   (or restore the .orig backup next to it)
"""
import glob
import os
import shutil

paths = glob.glob(os.path.expanduser(
    "~/.local/share/uv/tools/gflow-cli/lib/python3*/site-packages/gflow_cli/api/transports/migrated_video_upscale.py"))
if not paths:
    raise SystemExit("gflow-cli install not found")
path = paths[0]
src = open(path).read()
old = '''            log.warning("migrated_video_upscale.parse_error", error=str(exc))
            settle_exception(
                failed,
                WireFormatError(
                    detail=f"Failed to read a video export response: {exc}",
                    route="video_upscale",
                ),
            )
'''
new = '''            # Local patch: an unreadable unrelated reply must not abort the export.
            log.warning("migrated_video_upscale.parse_error", error=str(exc))
'''
if new in src:
    raise SystemExit("already patched")
if old not in src:
    raise SystemExit("code differs from 0.86.0 — not patched")
if not os.path.exists(path + ".orig"):
    shutil.copy2(path, path + ".orig")
open(path, "w").write(src.replace(old, new))
print("patched", path)
