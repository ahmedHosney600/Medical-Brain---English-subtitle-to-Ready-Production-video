"""Post-install edits for gflow-cli 0.86.0 (the version Medical Brain is built and tested on).

    python3 medical_brain/media/gflow_patches/apply_gflow_patches.py            # apply
    python3 medical_brain/media/gflow_patches/apply_gflow_patches.py --check    # report only
    python3 medical_brain/media/gflow_patches/apply_gflow_patches.py --revert   # restore originals
    … --target DIR   patch a copy of the gflow_cli package instead of the installed one

Edits (each an exact text replacement; nothing is applied unless every one matches):

  batch-clips      `gflow video t2v|i2v|r2v --count N` on flow.google.com keeps ALL N clips.
                   0.86.0 reads only the first clip record in Flow's submit reply, waits for
                   that one, downloads that one — the other N-1 are billed and left in the
                   project. Now every record is tracked, each finished clip is downloaded next
                   to the first, and `--json` lists them as "extra_clips".
  export-listener  `gflow video upscale --scale 1080p` no longer aborts (exit 7, "Failed to
                   read a video export response … No data found for resource") when Chrome
                   evicts the body of an unrelated reply while Flow renders the export.

Refuses any other gflow-cli version (the edits are tied to 0.86.0's code). Originals are kept
next to each file as <name>.orig; running it twice is safe. After `gflow update` or a
reinstall, run it again (it will refuse if the new version's code differs).
"""
import argparse
import glob
import os
import shutil
import sys

VERSION = "0.86.0"
MARK = "# [medical-brain-patch]"

EDITS = [
    # ------------------------------------------------------------------ batch-clips
    ("batch-clips", "api/video.py",
     '''    workflow_id: str | None = None  # see VideoStarted.workflow_id (#898)


# Callback type: invoked by the transport the moment a media_id becomes known,''',
     '''    workflow_id: str | None = None  # see VideoStarted.workflow_id (#898)
    # [medical-brain-patch] batch-clips: the other clips of a --count N run, as
    # (media_id, local_path or "") pairs, in Flow's order.
    extra_clips: tuple[tuple[str, str], ...] = ()


# Callback type: invoked by the transport the moment a media_id becomes known,'''),

    ("batch-clips", "api/transports/batchexecute.py",
     '''def image_records(rpcid: str, payload: Any) -> list[ImageGenerationRecord]:''',
     '''MEDICAL_BRAIN_PATCHES = ("batch-clips", "export-listener")  # [medical-brain-patch]


def generation_records(rpcid: str, payload: Any) -> list[GenerationRecord]:
    """[medical-brain-patch] batch-clips: EVERY generation record in one frame, in order,
    unique by media id — a --count N submit reply lists one record per clip."""
    out: list[GenerationRecord] = []
    seen: set[str] = set()
    for node in _walk_lists(payload):
        if _is_record(node) and node[2] not in seen:
            seen.add(node[2])
            out.append(generation_record(rpcid, node, media_id=node[2]))
    return out


def image_records(rpcid: str, payload: Any) -> list[ImageGenerationRecord]:'''),

    ("batch-clips", "api/transports/migrated_composer.py",
     '''from gflow_cli.api.transports.batchexecute import (
    GenerationRecord,
    generation_record,
    image_records,''',
     '''from gflow_cli.api.transports.batchexecute import (
    GenerationRecord,
    generation_record,
    generation_records,  # [medical-brain-patch]
    image_records,'''),

    ("batch-clips", "api/transports/migrated_composer.py",
     '''        workflow: dict[str, str] = {}
''',
     '''        workflow: dict[str, str] = {}
        # [medical-brain-patch] batch-clips: every clip of the submit, by media id.
        batch: dict[str, GenerationRecord] = {}
        self.batch_records = []
'''),

    ("batch-clips", "api/transports/migrated_composer.py",
     '''                    workflow["id"] = rec.workflow_id
                    log.info(
                        "migrated.submit_observed",''',
     '''                    workflow["id"] = rec.workflow_id
                    try:  # [medical-brain-patch] batch-clips
                        for sibling in generation_records(rid, payload):
                            batch[sibling.media_id] = sibling
                    except WireFormatError:
                        batch.setdefault(rec.media_id, rec)
                    log.info(
                        "migrated.submit_observed",'''),

    ("batch-clips", "api/transports/migrated_composer.py",
     '''                elif rid in STATUS_RPCS and workflow:
                    try:
                        rec = generation_record(rid, payload, workflow_id=workflow["id"])''',
     '''                elif rid in STATUS_RPCS and workflow:
                    for clip_id in list(batch):  # [medical-brain-patch] batch-clips
                        try:
                            sibling = generation_record(rid, payload, media_id=clip_id)
                        except WireFormatError:
                            continue
                        known = batch[clip_id]
                        if not (known.is_done and known.video_url) or sibling.video_url:
                            batch[clip_id] = sibling
                    try:
                        rec = generation_record(rid, payload, workflow_id=workflow["id"])'''),

    ("batch-clips", "api/transports/migrated_composer.py",
     '''                bytes=final.size_bytes,
            )
            return final
        finally:''',
     '''                bytes=final.size_bytes,
            )
            # [medical-brain-patch] batch-clips: the other clips finish at about the same
            # time; give them until the poll deadline (at most 4 more minutes).
            self.batch_records = await self._await_batch(batch, final, deadline=deadline)
            return final
        finally:'''),

    ("batch-clips", "api/transports/migrated_composer.py",
     '''    async def _verify_submit_target(self, page: Page, submit: Locator) -> None:''',
     '''    @staticmethod
    async def _await_batch(
        batch: dict[str, GenerationRecord], final: GenerationRecord, *, deadline: float
    ) -> list[GenerationRecord]:
        """[medical-brain-patch] batch-clips: wait until every other clip of the submit is
        failed or done with its signed URL (a done-without-URL clip gets the usual grace),
        then return their latest records in Flow's order."""
        others = [m for m in batch if m != final.media_id]
        if not others:
            return []
        stop = min(deadline, time.monotonic() + 240.0)
        url_grace_until: float | None = None
        while time.monotonic() < stop:
            pending = [batch[m] for m in others
                       if not (batch[m].is_failed or (batch[m].is_done and batch[m].video_url))]
            if not pending:
                break
            if all(r.is_done for r in pending):
                url_grace_until = url_grace_until or time.monotonic() + RESULT_URL_GRACE_S
                if time.monotonic() >= url_grace_until:
                    break
            await asyncio.sleep(2.0)
        records = [batch[m] for m in others]
        log.info("migrated.batch_result", clips=len(records) + 1,
                 done=sum(1 for r in records if r.is_done and r.video_url))
        return records

    async def _verify_submit_target(self, page: Page, submit: Locator) -> None:'''),

    ("batch-clips", "api/transports/migrated_composer.py",
     '''    local_path: Path | None = None
    if download and record.is_done:
        local_path = await composer.download(page, record, out_dir)
    return VideoResult(
        status=status,
        local_path=Path(local_path) if local_path is not None else None,
        project_id=pid,
        flow_operation_id=record.workflow_id,
        workflow_id=record.workflow_id,
    )''',
     '''    local_path: Path | None = None
    if download and record.is_done:
        local_path = await composer.download(page, record, out_dir)
    # [medical-brain-patch] batch-clips: download the other clips of a --count N run.
    extra_clips: list[tuple[str, str]] = []
    for sibling in getattr(composer, "batch_records", None) or []:
        saved = ""
        if download and sibling.is_done:
            try:
                got = await composer.download(page, sibling, out_dir)
                saved = str(got) if got is not None else ""
            except Exception as exc:  # noqa: BLE001 - one clip must not lose the others
                log.warning("migrated.batch_download_failed", media_id=sibling.media_id,
                            error=str(exc))
        extra_clips.append((sibling.media_id, saved))
    return VideoResult(
        status=status,
        local_path=Path(local_path) if local_path is not None else None,
        project_id=pid,
        flow_operation_id=record.workflow_id,
        workflow_id=record.workflow_id,
        extra_clips=tuple(extra_clips),
    )'''),

    ("batch-clips", "json_output.py",
     '''        "failure_reasons": list(st.failure_reasons),
        "error_message": st.error_message,
        "request": {
            "model": request.model.value if request.model is not None else None,''',
     '''        "failure_reasons": list(st.failure_reasons),
        "error_message": st.error_message,
        # [medical-brain-patch] batch-clips: the other clips of a --count N run.
        "extra_clips": [{"media_id": m, "local_path": p or None}
                        for m, p in getattr(result, "extra_clips", ())],
        "request": {
            "model": request.model.value if request.model is not None else None,'''),

    # ------------------------------------------------------------------ export-listener
    ("export-listener", "api/transports/migrated_video_upscale.py",
     '''            log.warning("migrated_video_upscale.parse_error", error=str(exc))
            settle_exception(
                failed,
                WireFormatError(
                    detail=f"Failed to read a video export response: {exc}",
                    route="video_upscale",
                ),
            )
''',
     '''            # [medical-brain-patch] export-listener: Chrome evicts some unrelated
            # batchexecute bodies ("No data found for resource"); that is not a refusal.
            # A parsed refusal still fails the export; the captured blob decides success.
            log.warning("migrated_video_upscale.parse_error", error=str(exc))
'''),
]


def installed_package() -> tuple:
    """(gflow_cli package dir, version) of the uv-tool install."""
    roots = glob.glob(os.path.expanduser("~/.local/share/uv/tools/gflow-cli/lib/python3*/site-packages"))
    for root in roots:
        pkg = os.path.join(root, "gflow_cli")
        dist = glob.glob(os.path.join(root, "gflow_cli-*.dist-info"))
        if os.path.isdir(pkg) and dist:
            return pkg, os.path.basename(dist[0])[len("gflow_cli-"):-len(".dist-info")]
    return "", ""


def plan(pkg: str) -> tuple:
    """(edits to apply, edits already applied, problems)."""
    todo, done, problems = [], [], []
    for name, rel, old, new in EDITS:
        path = os.path.join(pkg, rel)
        if not os.path.exists(path):
            problems.append(f"{rel}: missing")
            continue
        text = open(path, encoding="utf-8").read()
        if new in text:
            done.append((name, rel))
        elif text.count(old) == 1:
            todo.append((name, rel, old, new))
        else:
            problems.append(f"{name} · {rel}: the expected 0.86.0 code was not found "
                            f"({text.count(old)} matches)")
    return todo, done, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--target", help="a gflow_cli package directory to patch instead of the installed one")
    args = ap.parse_args()
    if args.target:
        pkg, version = os.path.abspath(args.target), VERSION
    else:
        pkg, version = installed_package()
        if not pkg:
            print("gflow-cli is not installed as a uv tool — run: uv tool install gflow-cli==0.86.0")
            return 1
    if version != VERSION:
        print(f"gflow-cli {version} is installed; these edits are for {VERSION} only.\n"
              f"Install it with: uv tool install --force gflow-cli=={VERSION}")
        return 1
    files = sorted({rel for _, rel, _, _ in EDITS})
    if args.revert:
        restored = 0
        for rel in files:
            path = os.path.join(pkg, rel)
            if os.path.exists(path + ".orig"):
                shutil.copy2(path + ".orig", path)
                restored += 1
        print(f"restored {restored} original files in {pkg}")
        return 0
    todo, done, problems = plan(pkg)
    for name, rel in done:
        print(f"  ✓ already applied: {name} · {rel}")
    if problems:
        print("Nothing changed — these edits do not match this install:")
        for p in problems:
            print("  ✗", p)
        return 1
    if args.check:
        for name, rel, _, _ in todo:
            print(f"  · to apply: {name} · {rel}")
        print("all applied" if not todo else f"{len(todo)} edits to apply")
        return 0 if not todo else 2
    for rel in files:
        path = os.path.join(pkg, rel)
        if not os.path.exists(path + ".orig"):
            shutil.copy2(path, path + ".orig")
    for name, rel, old, new in todo:
        path = os.path.join(pkg, rel)
        text = open(path, encoding="utf-8").read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new, 1))
        print(f"  + applied: {name} · {rel}")
    for cache in glob.glob(os.path.join(pkg, "**", "__pycache__"), recursive=True):
        shutil.rmtree(cache, ignore_errors=True)
    print(f"gflow-cli {VERSION} patched ({len(todo)} new edits) — {pkg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
