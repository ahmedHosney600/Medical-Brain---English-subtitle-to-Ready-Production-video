"""Runs `gflow …` with the 1080p video-export fix applied in memory (gflow-cli 0.86).

Run with gflow-cli's OWN interpreter (the generator does this), e.g.
    ~/.local/share/uv/tools/gflow-cli/bin/python gflow_upscale_shim.py video upscale <id> --scale 1080p …

The bug: while Flow renders the export, gflow's response listener aborts the whole export
(exit 7, "Failed to read a video export response … No data found for resource") when
Chrome cannot hand over the body of ANY unrelated batchexecute reply. Here an unreadable
reply is only logged; a parsed refusal of the export RPC still fails, and success is still
decided by the captured video blob (checked for real MP4 bytes at the requested size).
Nothing on disk is modified. This file must not import the Medical Brain project.
"""
import sys

from gflow_cli.api.transports import migrated_video_upscale as upscale


def _tolerant_listener(failed):
    async def on_response(response):
        if "batchexecute" not in response.url:
            return
        try:
            text = await response.text()
        except Exception as exc:  # noqa: BLE001 — an evicted unrelated body is not a refusal
            upscale.log.warning("migrated_video_upscale.unreadable_reply_ignored", error=str(exc))
            return
        for err in upscale.rpc_errors(text):
            if err.rpcid in upscale._EXPORT_RPCIDS:
                upscale.settle_exception(failed, upscale.WireFormatError(
                    detail=f"Video export RPC {err.rpcid} refused: code={err.code} reasons={err.reasons}",
                    route="video_upscale"))
    return on_response


if not hasattr(upscale, "_export_refusal_listener"):
    sys.exit("gflow-cli changed: the 1080p export fix does not apply to this version")
upscale._export_refusal_listener = _tolerant_listener

from gflow_cli.cli import main  # noqa: E402

sys.argv = ["gflow", *sys.argv[1:]]
main()
