"""The command line. Three workflows share the model setup and the video pipeline:

  python3 main.py <script.txt> [video_analysis.txt]        English subtitle → video package
  python3 main.py --topic "…" [--workflow research]         topic → verified research dossier
  python3 main.py --topic "…" --workflow research-video     topic → research → video package
  python3 main.py --continue [session]                      apply review.md / approve research
  python3 main.py --resume [session]                        continue a stopped run
  python3 main.py --from <point> <session> [--source FILE]  start from a point in a session, on to the end
      points: research, review, script, packaging, production, final, shorts, export
  python3 main.py --migrate [session|all]                   move older sessions to the numbered layout
  python3 main.py --broll [session]                         generate the B-roll media (Google Flow) into 4_broll/
  python3 main.py --broll-file FILE [--into session]        same, from your own manifest / prompt files
      --dry-run (plan only) · --yes (no credit question) · --only images,videos,whiteboards
      --items B03,WB1 · --wb-pick WB1=3 · --video-count/--image-count/--wb-video-count/--wb-image-count N
      on a workflow run: --generate-broll
  python3 main.py                                           menu
A session folder output/<id>/ holds 1_research/ · 2_review/ · 3_video/ · 4_broll/ (each made when its
stage runs; files numbered in reading order, 00_START_HERE.md first). See
docs/Guides/Sessions_and_Workflow_Guide.md.
Options: --provider main|single|hybrid (skips the model menu) · --minutes N (target video length for this run)."""
import datetime
import glob
import json
import os
import re
import sys
import time

from dotenv import load_dotenv
from langgraph.graph import END

from .exports import workbook as ew
from .exports.prompt_files import export_broll_prompt_files, export_whiteboard_prompt_files
from .graph import build_app
from .llm import LLMRouter, ProviderUnavailable, load_llm_config, set_cache_dir, set_router
from . import session_layout as layout
from .paths import PROJECT_ROOT
from .state import apply_defaults

WORKFLOWS = {"video": "English subtitle → video", "research-video": "Topic → research → video",
             "research": "Topic → research only"}


def with_heading(heading: str, content: str) -> str:
    """'## heading' + content, unless the model's text already opens with its own heading."""
    body = re.sub(r"^(\s*-{3,}\s*\n)+", "", content.lstrip())
    if re.match(r"#{1,3}\s", body):
        return body.strip()
    return f"## {heading}\n\n{content}"


_DOSSIER_CITATION_RE = re.compile(r"\[S\d+\]")


def looks_like_dossier(text: str) -> bool:
    """True when a source file is a research dossier rather than a plain subtitle: it
    carries many [S#] citations (the research step's format). Such a file must take the
    dossier-only truth checks (overclaims, every claim traced to a source)."""
    return len(_DOSSIER_CITATION_RE.findall(text or "")) >= 10


def stop_run(error, checkpoint_path: str = ""):
    """Ends the run with the fix, not a traceback. A saved checkpoint can be resumed."""
    kind = "" if isinstance(error, (ProviderUnavailable, ValueError)) else f"{type(error).__name__}: "
    print(f"\n❌ Stopped: {kind}{error}")
    if checkpoint_path:
        print(f"   Steps finished so far are saved in: {checkpoint_path}")
        session = _session_of_checkpoint(checkpoint_path)
        if session:
            print(f"   → resume with: python3 main.py --resume {os.path.relpath(session, PROJECT_ROOT)}")
    print("   Check your models with: python3 setup_models.py")
    sys.exit(1)


def _take_flag(name: str, has_value: bool = True):
    """Removes --name [value] from sys.argv and returns the value (True for a bare flag)."""
    if name not in sys.argv:
        return None
    i = sys.argv.index(name)
    if not has_value:
        del sys.argv[i]
        return True
    value = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--") else ""
    del sys.argv[i:i + (2 if value else 1)]
    return value


def _interactive() -> bool:
    return sys.stdin.isatty()


def choose_workflow() -> str:
    print("\nWhat do you want to make?")
    print("  1) English subtitle script → video production package")
    print("  2) Topic → research (verified facts from trusted sources) → video package")
    print("  3) Topic → research only (review the dossier first)")
    print("  4) Continue a research session (apply your review.md, or approve it)")
    print("  5) Resume a stopped run")
    print("  6) Start from a chosen point in a session (research, review, script, packaging, production…)")
    print("  7) Generate the B-roll media (images, videos, whiteboards) for a session with Google Flow")
    while True:
        choice = input("Choose 1-7 [1]: ").strip() or "1"
        if choice in ("1", "2", "3", "4", "5", "6", "7"):
            return {"1": "video", "2": "research-video", "3": "research", "4": "continue",
                    "5": "resume", "6": "start-from", "7": "broll"}[choice]
        print("Please type a number from 1 to 7.")


def choose_start_point(session_arg: str = "") -> tuple:
    """Asks for the session and the point to start from. Returns (point, session)."""
    if not _interactive():
        stop_run(ValueError("--from needs a point: " + ", ".join(START_POINTS)))
    if not session_arg:
        recent = sorted({_session_of_checkpoint(p) for _, pattern in layout.CHECKPOINT_PATTERNS
                         for p in glob.glob(os.path.join(PROJECT_ROOT, "output", pattern))}, key=os.path.getmtime)[-6:]
        if recent:
            print("\nRecent sessions:")
            for i, session in enumerate(reversed(recent), 1):
                folders = [d for d in (layout.RESEARCH_DIR, layout.REVIEW_DIR, layout.VIDEO_DIR)
                           if os.path.isdir(os.path.join(session, d))]
                print(f"  {i}) {os.path.basename(session)}  {' '.join(folders) or '(older layout)'}")
            pick = input("Session [1]: ").strip() or "1"
            session_arg = list(reversed(recent))[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(recent) else pick
    points = list(START_POINTS)
    print("\nStart from:")
    for i, point in enumerate(points, 1):
        print(f"  {i}) {point:<11} {START_POINTS[point]}")
    pick = input(f"Choose 1-{len(points)}: ").strip()
    point = points[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(points) else pick
    return point, session_arg


def choose_mode(llm_config: dict) -> str:
    print("\nWhich model setup for this run?  (change the models with: python3 setup_models.py)")
    print(f"  1) Main model for the whole workflow ({llm_config['main_model']})")
    print(f"  2) One strong model for the whole workflow ({llm_config['single_model']})")
    print(f"  3) Hybrid (recommended): {llm_config['main_model']} writes, judges check:")
    for node, model in llm_config["hybrid_routes"].items():
        print(f"       {node} → {model}")
    print("     Any step whose model fails runs on the main model instead.")
    if llm_config.get("backup_model"):
        print(f"  Backup if the main model fails: {llm_config['backup_model']}")
    while True:
        choice = input("Choose 1-3 [3]: ").strip() or "3"
        if choice in ("1", "2", "3"):
            return {"1": "main", "2": "single", "3": "hybrid"}[choice]
        print("Please type 1, 2 or 3.")


def start_router(llm_config: dict, provider):
    try:
        router = LLMRouter(llm_config, provider=provider)
    except ValueError as e:      # unknown --provider / LLM_PROVIDER value
        stop_run(e)
    set_router(router)
    # Quick check of every model this run uses: a missing main model stops here.
    try:
        router.select()
    except ProviderUnavailable as e:
        stop_run(e)
    return router


def _checkpoint_path(out_dir: str, label: str = "workflow") -> str:
    return os.path.join(out_dir, "checkpoint.json" if label == "workflow" else f"{label}_checkpoint.json")


def _write_checkpoint(path: str, state: dict, label: str, completed: list, status: str, error: str = "") -> None:
    """The full run state plus a small "_resume" record, so a stopped run can continue."""
    doc = dict(state)
    doc["_resume"] = {
        "label": label,
        "graph": "research" if label == "research" else "video",
        "last_node": completed[-1] if completed else None,
        "completed": list(completed),
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "error": error,
    }
    with open(path, "w", encoding="utf-8") as cp:
        json.dump(doc, cp, ensure_ascii=False, indent=2)


def load_checkpoint(path: str) -> tuple:
    """(state, _resume metadata). The state is exactly what a graph expects: _resume is gone."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    meta = doc.pop("_resume", None) or {}
    return doc, meta


def _mark_checkpoint_done(out_dir: str, label: str = "workflow") -> None:
    """Flip a checkpoint to status "done" once the post-run work is finished too."""
    path = _checkpoint_path(out_dir, label)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return
    meta = doc.get("_resume") or {}
    meta["status"] = "done"
    meta["error"] = ""
    meta["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    doc["_resume"] = meta
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"  [warning] Could not update checkpoint: {e}")


def _session_of_checkpoint(path: str) -> str:
    """The session folder a checkpoint lives in (new layout: <s>/1_research/logs/ or
    <s>/3_video/logs/; older sessions: <s>/research/ or <s>/)."""
    return layout.session_of_checkpoint(path)


def run_graph(app, initial_state: dict, out_dir: str, router, label: str = "workflow",
              resume_meta: dict = None) -> dict:
    """Streams a graph step by step, with a log line, a session log and a checkpoint
    after every step. Any failure stops cleanly with the checkpoint saved.

    `resume_meta` is a previous checkpoint's "_resume": the steps it lists are kept in
    the new checkpoint's history (they were not re-run). The status stays "running"
    until the caller marks the whole run done after its post-run work."""
    log_path = os.path.join(out_dir, "session_log.jsonl" if label == "workflow" else f"{label}_log.jsonl")
    checkpoint_path = _checkpoint_path(out_dir, label)
    state = dict(initial_state)
    completed = [n for n in (resume_meta or {}).get("completed", []) if n]
    with open(log_path, "a", encoding="utf-8") as log_file:
        step_start = time.time()
        stream = app.stream(initial_state)
        while True:
            try:
                output = next(stream)
            except StopIteration:
                break
            except Exception as e:
                log_file.flush()
                try:
                    _write_checkpoint(checkpoint_path, state, label, completed, "stopped",
                                      f"{type(e).__name__}: {e}"[:300])
                except Exception as werr:
                    print(f"  [warning] Could not save checkpoint: {werr}")
                stop_run(e, checkpoint_path)
            for key, value in output.items():
                state.update(value or {})
                elapsed = time.time() - step_start
                step_start = time.time()
                model = router.model_for_node(key)
                print(f"✅ Node '{key}' completed. ({elapsed:.0f}s" + (f", {model})" if model else ")"))
                completed.append(key)      # loops may revisit a node, so record every run
                log_file.write(json.dumps({"timestamp": datetime.datetime.now().isoformat(), "node": key,
                                           "state_updates": value}, ensure_ascii=False) + "\n")
                log_file.flush()
                try:
                    _write_checkpoint(checkpoint_path, state, label, completed, "running")
                except Exception as e:
                    print(f"  [warning] Could not save checkpoint: {e}")
    return state


def _new_session_dir() -> tuple:
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(PROJECT_ROOT, "output", session_id)
    os.makedirs(out, exist_ok=True)
    return session_id, out


def _convert(md_path: str, label: str) -> None:
    try:
        from .exports.convert import convert_markdown_file
        print(f"\n🚀 Exporting {label} to Responsive HTML, PDF, and DOCX...")
        res = convert_markdown_file(md_path)
        print(f"  📄 HTML export saved to: {res[0]}")
        if res[1]:
            print(f"  📑 PDF export saved to:  {res[1]}")
        if len(res) > 2 and res[2]:
            print(f"  📝 DOCX export saved to: {res[2]}")
    except Exception as conv_err:
        print(f"  ⚠️ Automatic HTML/PDF/DOCX conversion warning: {conv_err}")


# --------------------------------------------------------------------------
# Video workflow
# --------------------------------------------------------------------------
def _apply_script_sources(state: dict, research: dict, output_dir: str) -> None:
    """Layer 3: the sources the FINAL script actually uses go into the document (appendix
    + YouTube description), plus the claims no verified fact supports (for the doctor).
    Built after the script is written, so no tags are needed inside the script. If the
    mapping fails entirely, fall back to the old full reference list with one warning."""
    from .research import dossier as rd
    from .research import script_sources as ss

    package = state.get("final_package", "")
    error = ""
    try:
        sentences = ss.split_script(ew.production_script(package))
        mapping = ss.map_sources(sentences, research)
        guide = ss.build_sources_guide(sentences, mapping, research)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        sentences, mapping = [], {"links": {}, "unsupported": []}
        guide = {"entries": [], "unsupported": [], "stats": {}}

    if sentences and (mapping.get("links") or mapping.get("unsupported")):
        document = package.rstrip() + "\n\n---\n\n" + ss.render_markdown(guide)
        state["final_package"] = ew.insert_description_sources(document, ss.description_block(guide))
        with open(os.path.join(output_dir, "sources_map.json"), "w", encoding="utf-8") as f:
            json.dump({"sentences": sentences, "mapping": mapping, "guide": guide},
                      f, ensure_ascii=False, indent=2)
        stats = guide.get("stats") or {}
        print(f"📚 Sources guide: {stats.get('sources', 0)} sources for {stats.get('linked', 0)} "
              f"medical sentences; {stats.get('unsupported', 0)} claims to check")
    else:
        print("  ⚠️ Sources guide could not map this script"
              + (f" ({error})" if error else "") + "; using the full reference list.")
        state["final_package"] = ew.add_research_sources(
            package, rd.reference_lines(research), rd.key_sources(research))


def run_video(router, original_script: str, video_analysis: str = "", output_dir: str = "",
              extra_state: dict = None, research: dict = None,
              resume_state: dict = None, entry=None, resume_meta: dict = None) -> None:
    if resume_state is not None:
        # Resume: the checkpoint already holds the whole state; only the steps after
        # `entry` run. research is loaded from the session so the sources guide still works.
        state = dict(resume_state)
        if not output_dir:
            stop_run(ValueError("resuming a video run needs its session folder"))
        session_dir = output_dir
        session_id = os.path.basename(session_dir.rstrip("/"))
        logs_dir = layout.video_logs(session_dir)
        os.makedirs(logs_dir, exist_ok=True)
        set_cache_dir(os.path.join(logs_dir, "llm_cache"))
        if research is None:
            research_path = layout.research_state_file(session_dir)
            if os.path.exists(research_path):
                with open(research_path, encoding="utf-8") as f:
                    research = json.load(f)
        print(f"Starting video workflow... Session ID: {session_id}")
        print(f"Logs: {logs_dir}")
        if entry != END:
            state = run_graph(build_app(entry=entry), state, logs_dir, router, resume_meta=resume_meta)
    else:
        input_file = os.path.join(PROJECT_ROOT, "input_fields.json")
        if not os.path.exists(input_file):
            stop_run(FileNotFoundError("input_fields.json is missing (the channel settings)"))
        with open(input_file, "r", encoding="utf-8") as f:
            initial_state = json.load(f)
        initial_state["original_script"] = original_script
        if video_analysis:
            initial_state["video_analysis"] = video_analysis
        initial_state.update(extra_state or {})
        # Long-form hint: 0 (the default) lets the strategy planner choose the duration;
        # `--minutes N` on the command line overrides input_fields.json for this run.
        initial_state.setdefault("target_minutes", 0)
        if os.environ.get("MB_TARGET_MINUTES"):
            initial_state["target_minutes"] = int(float(os.environ["MB_TARGET_MINUTES"]))
        apply_defaults(initial_state)

        if output_dir:
            session_dir = output_dir
            session_id = os.path.basename(session_dir.rstrip("/"))
            os.makedirs(session_dir, exist_ok=True)
        else:
            session_id, session_dir = _new_session_dir()
        logs_dir = layout.video_logs(session_dir)
        os.makedirs(logs_dir, exist_ok=True)
        set_cache_dir(os.path.join(logs_dir, "llm_cache"))
        print(f"Starting video workflow... Session ID: {session_id}")
        print(f"Logs: {logs_dir}")
        state = run_graph(build_app(), initial_state, logs_dir, router)

    video_folder = layout.video_dir(session_dir)
    final_script_path = layout.final_script_md(session_dir)

    if research:
        # The sources the finished script actually uses go into the document
        # (appendix + YouTube description); the full list stays in the research folder.
        _apply_script_sources(state, research, logs_dir)

    for warning in (ew.quality_warning(state), ew.production_warning(state), ew.package_warning(state)):
        if warning:
            print(f"\n{warning}")

    provider_report = router.report()
    with open(os.path.join(logs_dir, "llm_provider.json"), "w", encoding="utf-8") as pf:
        json.dump(provider_report, pf, ensure_ascii=False, indent=2)
    print(f"\n🔀 LLM calls this run ({provider_report['mode']}): {provider_report['calls']}")

    longform_deliverable = state.get("final_package", "")
    shorts_scripts_content = state.get("shorts_scripts", "")
    shorts_captions_content = state.get("shorts_captions", "")
    shorts_moments_content = state.get("shorts_moments", "")
    shorts_section = ""
    if shorts_scripts_content:
        shorts_section += "\n\n---\n\n# 📱 REELS & SHORTS PACKAGE\n\n"
        if shorts_moments_content:
            shorts_section += with_heading("IDENTIFIED MOMENTS", shorts_moments_content) + "\n\n---\n\n"
        shorts_section += with_heading("REELS & SHORTS SCRIPTS", shorts_scripts_content) + "\n\n---\n\n"
        if shorts_captions_content:
            shorts_section += with_heading("TIMED CAPTIONS & ON-SCREEN OVERLAYS", shorts_captions_content)

    if not longform_deliverable:
        print("\nWorkflow finished, but no final package was generated.")
        print(f"Checkpoint with all intermediate outputs saved to: {os.path.join(logs_dir, 'checkpoint.json')}")
        _mark_checkpoint_done(logs_dir, "workflow")
        return
    # Reorder into the editing workflow (pure Python, nothing is retyped).
    try:
        longform_document = ew.build_editing_workbook(longform_deliverable, state)
    except Exception as wb_err:
        print(f"  ⚠️ Could not reorder the package into editing steps ({wb_err}); saving it as generated.")
        longform_document = longform_deliverable
    with open(final_script_path, "w", encoding="utf-8") as f:
        f.write(longform_document + shorts_section)
    print(f"\nWorkflow finished. Final complete package saved to: {final_script_path}")

    shorts_path = layout.shorts_md(session_dir)
    if shorts_section:
        with open(shorts_path, "w", encoding="utf-8") as sf:
            sf.write(shorts_section.strip())
        print(f"Dedicated Reels & Shorts package saved to: {shorts_path}")

    export_broll_prompt_files(text=longform_deliverable + shorts_section,
                              fallback_broll=state.get("broll_prompts", ""), output_dir=video_folder)
    export_whiteboard_prompt_files(longform_deliverable, output_dir=video_folder,
                                   overlay_guide=state.get("text_animation_overlay", ""))
    layout.number_prompt_files(session_dir)
    try:
        from .media.manifest import build_manifest, write_manifest
        write_manifest(video_folder, build_manifest(longform_deliverable, state.get("broll_prompts", ""),
                                                    state.get("text_animation_overlay", ""), _topic_of(state)))
    except Exception as manifest_err:
        print(f"  ⚠️ Could not write the B-roll media manifest ({manifest_err}); the prompt files are unaffected.")
    _convert(final_script_path, "final script")
    if shorts_section and os.path.exists(shorts_path):
        _convert(shorts_path, "shorts package")
    _mark_checkpoint_done(logs_dir, "workflow")
    if layout.write_start_here(session_dir, layout.VIDEO_DIR, os.path.relpath(session_dir, PROJECT_ROOT)):
        print(f"📂 Open {os.path.join(video_folder, layout.START_HERE)} — the files in reading order.")
    if os.environ.get("MB_GENERATE_BROLL"):
        # The video package is done; a B-roll problem is reported, not turned into a failed run.
        run_broll(session_dir, yes=bool(os.environ.get("MB_BROLL_YES")), exit_on_error=False)


# --------------------------------------------------------------------------
# B-roll media (Google Flow via gflow-cli)
# --------------------------------------------------------------------------
def _session_manifest(session_dir: str) -> dict:
    """The session's 07_broll_manifest.json, built (and saved) from its checkpoint when an
    older run did not write one."""
    from .media.manifest import MANIFEST_NAME, build_manifest, read_manifest, write_manifest
    path = os.path.join(layout.video_dir(session_dir), MANIFEST_NAME)
    if os.path.exists(path):
        return read_manifest(path)
    checkpoint = layout.video_checkpoint_file(session_dir)
    if not os.path.exists(checkpoint):
        raise ValueError(f"{os.path.relpath(session_dir, PROJECT_ROOT)} has no finished video package")
    state, _meta = load_checkpoint(checkpoint)
    manifest = build_manifest(state.get("final_package", ""), state.get("broll_prompts", ""),
                              state.get("text_animation_overlay", ""), _topic_of(state))
    if not manifest["items"]:
        raise ValueError("the video package has no B-roll or whiteboard prompts")
    write_manifest(layout.video_dir(session_dir), manifest)
    return read_manifest(path)


def run_broll(session_arg: str = "", manual_file: str = "", into: str = "", yes: bool = False,
              dry_run: bool = False, only: str = "", items: str = "", wb_pick: str = "",
              counts: dict = None, exit_on_error: bool = True) -> None:
    """`--broll [session]` / `--broll-file FILE [--into session]`: generate the B-roll media."""
    from .media.gflow import GflowStop
    from .media.manifest import read_manifest
    from .media.runner import BrollNotReady, generate, load_settings
    try:
        if manual_file:
            manifest = read_manifest(manual_file)
            session_dir = _resolve_session(into) if into else _new_session_dir()[1]
            if into and not session_dir:
                raise ValueError(f"session not found: {into}")
        else:
            session_dir = session_arg if os.path.isdir(session_arg or "") else _resolve_session(session_arg or "")
            if not session_dir:
                latest = _latest_any_checkpoint()
                session_dir = _session_of_checkpoint(latest) if latest and session_arg in ("", None) else ""
            if not session_dir:
                raise ValueError(f"session not found: {session_arg}")
            manifest = _session_manifest(session_dir)
        kinds = {k.strip() for k in (only or "").split(",") if k.strip()}
        if kinds - {"images", "videos", "whiteboards"}:
            raise ValueError("--only takes images, videos and/or whiteboards")
        picks = dict(p.split("=", 1) for p in (wb_pick or "").split(",") if "=" in p)
        broll_dir = layout.broll_dir(session_dir)
        if manual_file:
            os.makedirs(os.path.join(broll_dir, layout.LOGS_DIR), exist_ok=True)
            with open(os.path.join(broll_dir, layout.LOGS_DIR, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
        for key, value in (counts or {}).items():
            if value not in (None, "", True) and (not str(value).isdigit() or not 1 <= int(value) <= 4):
                raise ValueError(f"--{key.replace('_', '-')} takes a number from 1 to 4")
        settings = load_settings({k: int(v) for k, v in (counts or {}).items() if str(v).isdigit()})
        result = generate(manifest, broll_dir, settings=settings, only=kinds or None,
                          ids={i.strip() for i in (items or "").split(",") if i.strip()} or None,
                          picks=picks, dry_run=dry_run, yes=yes, interactive=_interactive(),
                          session_label=os.path.relpath(session_dir, PROJECT_ROOT))
    except (ValueError, OSError, BrollNotReady, GflowStop) as exc:
        print(f"\n❌ B-roll generation: {exc}")
        if exit_on_error:
            sys.exit(1)
        return
    if result["failures"]:
        print(f"⚠️ {len(result['failures'])} items could not be made: "
              + ", ".join(f"{i} ({why[:60]})" for i, why in result["failures"]))
    if result["stopped"] and result["stopped"] != "declined" and exit_on_error:
        sys.exit(1)


# --------------------------------------------------------------------------
# Research workflow
# --------------------------------------------------------------------------
def _research_settings() -> dict:
    """Research limits from input_fields.json (optional keys), e.g. max_verify_rounds."""
    try:
        with open(os.path.join(PROJECT_ROOT, "input_fields.json"), encoding="utf-8") as f:
            fields = json.load(f)
    except Exception:
        return {}
    from .research.state import DEFAULT_SETTINGS
    if fields.get("research_trusted_domains"):
        os.environ["RESEARCH_TRUSTED_DOMAINS"] = ",".join(fields["research_trusted_domains"])
    return {k: fields[k] for k in DEFAULT_SETTINGS if k in fields}


def _find_session(arg: str) -> str:
    """A session folder from 'output/<id>', '<id>' or '' (the latest research session)."""
    candidates = [arg, os.path.join(PROJECT_ROOT, arg), os.path.join(PROJECT_ROOT, "output", arg)] if arg else []
    for c in candidates:
        if c and os.path.exists(layout.research_state_file(c)):
            return os.path.abspath(c)
    found = sorted(_research_sessions(), key=os.path.getmtime)
    if not arg and found:
        return found[-1]
    return ""


def _research_sessions() -> list:
    """Every session folder that has a saved research state (either layout)."""
    sessions = set()
    for rel in (os.path.join(layout.RESEARCH_DIR, layout.LOGS_DIR), "research"):
        for path in glob.glob(os.path.join(PROJECT_ROOT, "output", "*", rel, "research_state.json")):
            session = os.path.dirname(os.path.dirname(path)) if rel == "research" else \
                os.path.dirname(os.path.dirname(os.path.dirname(path)))
            if os.path.exists(layout.research_state_file(session)):
                sessions.add(session)
    return sorted(sessions)


def _prepare_research(state: dict, session_dir: str) -> str:
    """The session's research logs folder (1_research/logs/, or research/ in an older
    session), with its HTTP and model-answer caches in place."""
    from .research.sources import google, http
    folder = layout.research_logs(session_dir)
    os.makedirs(folder, exist_ok=True)
    http.set_cache_dir(os.path.join(folder, "cache"))
    set_cache_dir(os.path.join(folder, "llm_cache"))
    google.reset_run(max_queries=state.get("settings", {}).get("max_google_queries", 40))
    return folder


def _finish_research(session_dir: str, state: dict) -> dict:
    """Everything after the research graph: dossier + patient guide + review file + the
    editable video source + exports. New layout: 1_research/ (numbered, reader files),
    2_review/ (01_review.md, 02_video_source.md); older sessions keep research/."""
    from .research import dossier as rd
    folder = layout.research_logs(session_dir)
    legacy = layout.is_legacy(session_dir)
    rel = os.path.relpath(session_dir, PROJECT_ROOT)
    state["review"] = {}
    # Write the editable source before rd.save so its sha256 is in the saved state.
    video_source_path = rd.write_video_source(state, layout.review_dir(session_dir),
                                              layout.video_source_name(session_dir))
    paths = rd.save(state, folder)
    review_path = layout.review_file(session_dir)
    has_guide = bool(state.get("patient_guide"))
    if os.path.exists(review_path):           # keep the previous round's review for the record
        os.replace(review_path, os.path.join(folder, f"review_round{state.get('review_round', 0)}.md"))
    rd.write_review_file(review_path, state["topic"], rel, has_guide=has_guide)
    facts = rd.verified_facts(state)
    guide_md, dossier_md = paths.get("guide_md"), paths["md"]
    if not legacy:
        # Reader copies, numbered in reading order; the machine files stay in logs/.
        import shutil
        research_dir = layout.research_dir(session_dir)
        if has_guide and guide_md and os.path.exists(guide_md):
            guide_md = shutil.copyfile(guide_md, os.path.join(research_dir, layout.PATIENT_GUIDE + ".md"))
        dossier_md = shutil.copyfile(dossier_md, os.path.join(research_dir, layout.EVIDENCE_LIBRARY + ".md"))
    if has_guide and guide_md:
        print(f"\n📄 Patient guide (what the video will be built from): {guide_md}")
        _convert(guide_md, "patient guide")
    print(f"📄 Evidence Library (dossier): {dossier_md}  ({len(facts)} verified facts, "
          f"{len(rd.used_sources(state))} sources, {len(state.get('rejected', []))} left out)")
    _convert(dossier_md, "research dossier")
    layout.write_start_here(session_dir, layout.RESEARCH_DIR, rel)
    layout.write_start_here(session_dir, layout.REVIEW_DIR, rel)
    print(f"📝 Video source (edit it to change what the script is written from): {video_source_path}")
    print(f"✍️  Review it, then edit {review_path}")
    print(f"   → python3 main.py --continue {rel}")
    print("     (APPROVED: yes to finish" + (" and build the video)" if state.get("workflow") == "research-video" else ")"))
    return state


def run_research(router, state: dict, session_dir: str) -> dict:
    from .research.graph import build_research_app
    from .research.sources import enabled
    folder = _prepare_research(state, session_dir)
    print(f"\n🔬 Researching: {state['topic']}  (sources: {', '.join(enabled())})")
    state = run_graph(build_research_app(), state, folder, router, label="research")
    _finish_research(session_dir, state)
    _mark_checkpoint_done(folder, "research")
    return state


def resume_research(router, state: dict, session_dir: str, entry, resume_meta: dict) -> dict:
    """Continue a stopped research run from `entry`, then do the normal post-run work."""
    from .research.graph import build_research_app
    from .research.sources import enabled
    folder = _prepare_research(state, session_dir)
    print(f"\n🔬 Resuming research: {state.get('topic', '')}  (sources: {', '.join(enabled())})")
    if entry != END:
        state = run_graph(build_research_app(entry=entry), state, folder, router,
                          label="research", resume_meta=resume_meta)
    _finish_research(session_dir, state)
    _mark_checkpoint_done(folder, "research")
    return state


def start_research(router, topic: str, workflow: str, audience: str = "", notes: str = "") -> None:
    from .research.state import new_state
    session_id, session_dir = _new_session_dir()
    state = new_state(topic, audience, notes, _research_settings())
    state["workflow"] = workflow
    state["session"] = session_id
    run_research(router, state, session_dir)


def continue_research(router_factory, arg: str) -> None:
    from .research import dossier as rd
    session_dir = _find_session(arg)
    if not session_dir:
        stop_run(ValueError(f"no research session found for '{arg or 'latest'}' (expected output/<id>/research/)"))
    state_path = layout.research_state_file(session_dir)
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    review_folder, source_name = layout.review_dir(session_dir), layout.video_source_name(session_dir)
    # Older sessions have no editable video source: write one (and record its hash in
    # the saved state) so the creator can change what the script is built from.
    video_source_path = layout.video_source_file(session_dir)
    if not os.path.exists(video_source_path):
        rd.write_video_source(state, review_folder, source_name)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"📝 Wrote {os.path.relpath(video_source_path, PROJECT_ROOT)} — edit it if you want "
              "to change what the video is built from.")
    review_path = layout.review_file(session_dir)
    review_text = ""
    if os.path.exists(review_path):
        with open(review_path, encoding="utf-8") as f:
            review_text = f.read()
    review = rd.parse_review(review_text)
    print(f"\n📂 Research session: {os.path.relpath(session_dir, PROJECT_ROOT)} — {state.get('topic')}")
    if review["approved"]:
        make_video = state.get("workflow") == "research-video"
        if not make_video and _interactive():
            make_video = (input("Research approved. Build the video from it now? [y/N]: ").strip().lower() == "y")
        if not make_video:
            print("✅ Research approved. Build the video any time with: python3 main.py --continue "
                  f"{os.path.relpath(session_dir, PROJECT_ROOT)} --workflow research-video")
            return
        router = router_factory()
        source, edited = rd.read_video_source(state, review_folder, source_name)
        if edited:
            print("📝 Using your edited video_source.md")
            # Only warn about uncited lines the creator ADDED: generated sources
            # legitimately contain uncited lines (e.g. "- MYTH: ...", whose citation
            # is on the next TRUTH line) that shouldn't be flagged.
            generated = {l.strip() for l in rd.video_source(state).splitlines()}
            uncited = [l for l in rd.uncited_lines(source) if l not in generated]
            if uncited:
                print(f"⚠️  {len(uncited)} line(s) have no source id [S#] — the truth check will "
                      "treat them as unverified and may drop them:")
                for line in uncited[:5]:
                    print(f"   {line[:120]}")
        _archive_previous_video(session_dir)
        run_video(router, source, output_dir=session_dir, research=state, extra_state={
            "source_format": "Research dossier (verified facts with citations)", "research_dossier": True})
        return
    if not rd.has_requests(review):
        print(f"review.md has no requests and isn't approved yet. Edit {review_path} "
              "(write your changes, or APPROVED: yes) and run --continue again. "
              f"You can also edit {os.path.relpath(video_source_path, PROJECT_ROOT)} to change "
              "what the video is built from before approving.")
        return
    router = router_factory()
    state["review"] = review
    state["settings"] = {**state.get("settings", {}), **_research_settings()}
    run_research(router, state, session_dir)


# --------------------------------------------------------------------------
# Resume a stopped run
# --------------------------------------------------------------------------
def _checkpoint_kind(path: str) -> str:
    return layout.checkpoint_kind(path)


def _read_checkpoint_meta(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return (json.load(f) or {}).get("_resume") or {}


def _checkpoint_for_session(session_dir: str) -> str:
    """A session folder's checkpoint: the newest one that is not "done" (a research-video
    session has both), else research, else video. '' when the folder has none."""
    found = []
    for _, path in layout.session_checkpoints(session_dir):
        try:
            status = _read_checkpoint_meta(path).get("status")
        except (OSError, ValueError):
            status = None
        found.append((path, status))
    if not found:
        return ""
    stopped = [p for p, status in found if status != "done"]
    return max(stopped or [p for p, _ in found], key=os.path.getmtime)


def _latest_stopped_checkpoint() -> str:
    """The most recently written checkpoint whose status is not "done"."""
    best = None
    for _, pattern in layout.CHECKPOINT_PATTERNS:
        for path in glob.glob(os.path.join(PROJECT_ROOT, "output", pattern)):
            try:
                meta = _read_checkpoint_meta(path)
            except (OSError, ValueError):
                continue
            if not meta or meta.get("status") == "done":
                continue
            mtime = os.path.getmtime(path)
            if best is None or mtime > best[0]:
                best = (mtime, path)
    return best[1] if best else ""


def _resolve_resume(arg: str) -> tuple:
    """(session_dir, checkpoint path) for 'output/<id>', '<id>', a path, or ""
    (the latest stopped run). Returns ('', '') when there is nothing to resume."""
    if arg:
        for cand in (arg, os.path.join(PROJECT_ROOT, arg), os.path.join(PROJECT_ROOT, "output", arg)):
            if cand and os.path.isdir(cand):
                path = _checkpoint_for_session(cand)
                if path:
                    return os.path.abspath(cand), path
        return "", ""
    path = _latest_stopped_checkpoint()
    return (_session_of_checkpoint(path), path) if path else ("", "")


def _topic_of(state: dict) -> str:
    if state.get("topic"):
        return str(state["topic"])
    text = (state.get("original_script") or "").strip()
    return text.splitlines()[0][:80] if text else "(untitled)"


def resume_video(router, state: dict, session_dir: str, entry, resume_meta: dict) -> None:
    run_video(router, "", output_dir=session_dir, resume_state=state, entry=entry, resume_meta=resume_meta)


def resume_run(router_factory, arg: str = "") -> None:
    """`python3 main.py --resume [session]`: continue the newest stopped research or
    video run from the step it was on, then do the normal post-run work for that graph."""
    session_dir, path = _resolve_resume(arg or "")
    if not path:
        where = f"'{arg}'" if arg else "the output folder"
        stop_run(ValueError(f"no stopped run to resume in {where} (nothing with status != done)"))
    kind = _checkpoint_kind(path)
    state, meta = load_checkpoint(path)
    completed = [n for n in (meta.get("completed") or []) if n]
    if kind == "research":
        from .research.graph import next_after
    else:
        from .graph import next_after
    entry = next_after(meta.get("last_node"), state)
    label = meta.get("label") or kind
    step = "the final step" if entry == END else entry
    print(f"▶ Resuming {label} for '{_topic_of(state)}' at step {step} "
          f"({len(completed)} steps already done)")
    router = router_factory()
    if kind == "research":
        resume_research(router, state, session_dir, entry, meta)
    else:
        resume_video(router, state, session_dir, entry, meta)


# --------------------------------------------------------------------------
# Start from a chosen point in an existing session
# --------------------------------------------------------------------------
# point → (first step of the video graph, the state each re-run stage starts without)
_STAGE_RESET = {
    "packaging": ["packaging_brainstorm_output", "title_options", "thumbnail_concepts", "packaging_critique_output",
                  "recommended_title", "packaging_ab_test_set", "thumbnail_face_rules", "packaging_grade"],
    "production": ["transition_design", "text_animation_overlay", "broll_prompts", "production_critique_output",
                   "production_grade"],
    "final": ["final_package"],
    "shorts": ["shorts_moments", "shorts_scripts", "shorts_captions", "shorts_quality_output",
               "shorts_quality_grade"],
}
_STAGE_ORDER = ["packaging", "production", "final", "shorts"]
_STAGE_ENTRY = {"packaging": "packaging_creative_director", "production": "text_animation_overlay_designer",
                "final": "final_script_package", "shorts": "shorts_moment_identifier"}
_STAGE_COUNTERS = {"packaging": "packaging_revision_count", "production": "production_revision_count",
                   "shorts": "shorts_revision_count"}
_STAGE_NEEDS = {"packaging": ("refined_script", "the script"),
                "production": ("packaging_ab_test_set", "titles & thumbnails"),
                "final": ("broll_prompts", "production"),
                "shorts": ("final_package", "the final package"),
                "export": ("final_package", "the final package")}
START_POINTS = {
    "research": "Run the research again for this session's topic (earlier research moves to _previous_runs/)",
    "review": "Apply 2_review/01_review.md: a new research round, or the video when APPROVED: yes",
    "script": "Run the script workflow from 2_review/02_video_source.md (or --source FILE)",
    "packaging": "Redo titles & thumbnails, then production, final package and shorts",
    "production": "Redo overlays, B-roll and transitions, then the final package and shorts",
    "final": "Rebuild the final package and the shorts",
    "shorts": "Redo only the Reels & Shorts",
    "export": "Only write the files again (final script, shorts, prompt files, PDF/DOCX)",
}


def _archive_previous_video(session_dir: str, keep_checkpoint: bool = False) -> None:
    """Moves an earlier video package of this session to 3_video/_previous_runs/<time>/."""
    if layout.is_legacy(session_dir):
        return
    archived = layout.archive_stage(layout.video_dir(session_dir), keep_checkpoint=keep_checkpoint)
    if archived:
        print(f"🗂  Earlier video files kept in: {os.path.relpath(archived, PROJECT_ROOT)}")


def _rewound(state: dict, meta: dict, point: str) -> tuple:
    """(state, meta) for re-running the video graph from `point`: the outputs of that stage
    and every later one are cleared, their revision counters reset (a re-run starts fresh,
    not in revision mode), and the step history is cut back to before the stage."""
    state = dict(state)
    for stage in _STAGE_ORDER[_STAGE_ORDER.index(point):]:
        for key in _STAGE_RESET[stage]:
            state[key] = ""
        if stage in _STAGE_COUNTERS:
            state[_STAGE_COUNTERS[stage]] = 0
        if stage == "production":
            state["production_chapter_outputs"] = {}
            state["broll_by_chapter"] = {}
    completed = [n for n in (meta.get("completed") or []) if n]
    entry = _STAGE_ENTRY[point]
    if entry in completed:
        completed = completed[:completed.index(entry)]
    meta = {**meta, "completed": completed, "last_node": completed[-1] if completed else None,
            "status": "running"}
    return state, meta


def _resolve_session(arg: str) -> str:
    for cand in ([arg, os.path.join(PROJECT_ROOT, arg), os.path.join(PROJECT_ROOT, "output", arg)] if arg else []):
        if cand and os.path.isdir(cand):
            return os.path.abspath(cand)
    return ""


def _script_source(session_dir: str, source_file: str) -> tuple:
    """(source text, research state or None, label) for `--from script`."""
    from .research import dossier as rd
    research = None
    if os.path.exists(layout.research_state_file(session_dir)):
        with open(layout.research_state_file(session_dir), encoding="utf-8") as f:
            research = json.load(f)
    if source_file:
        if not os.path.exists(source_file):
            stop_run(ValueError(f"--source file not found: {source_file}"))
        with open(source_file, encoding="utf-8") as f:
            text = f.read()
        if research is not None:
            # The external final review becomes this session's 2_review/02_video_source.md
            # (the earlier one is kept), so the folder always shows what the script used.
            target = layout.video_source_file(session_dir)
            if os.path.exists(target):
                keep = os.path.join(layout.review_dir(session_dir), layout.PREVIOUS_DIR)
                os.makedirs(keep, exist_ok=True)
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                os.replace(target, os.path.join(keep, f"{stamp}_{os.path.basename(target)}"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(text)
            layout.write_start_here(session_dir, layout.REVIEW_DIR, os.path.relpath(session_dir, PROJECT_ROOT))
        return text, research, f"your file {source_file}"
    if research is not None:
        if not os.path.exists(layout.video_source_file(session_dir)):
            # An older session never wrote one: write it so 2_review/ shows what the script used.
            rd.write_video_source(research, layout.review_dir(session_dir), layout.video_source_name(session_dir))
            with open(layout.research_state_file(session_dir), "w", encoding="utf-8") as f:
                json.dump(research, f, ensure_ascii=False, indent=2)
            layout.write_start_here(session_dir, layout.REVIEW_DIR, os.path.relpath(session_dir, PROJECT_ROOT))
        text, edited = rd.read_video_source(research, layout.review_dir(session_dir), layout.video_source_name(session_dir))
        return text, research, ("your edited " if edited else "") + os.path.relpath(
            layout.video_source_file(session_dir), PROJECT_ROOT)
    checkpoint = layout.video_checkpoint_file(session_dir)
    if os.path.exists(checkpoint):
        previous, _ = load_checkpoint(checkpoint)
        if previous.get("original_script"):
            return previous["original_script"], None, "the same source script as the last run"
    stop_run(ValueError("no script source in this session: give one with --source FILE"))


def start_from(router_factory, point: str, arg: str = "", source_file: str = "") -> None:
    """`python3 main.py --from <point> <session> [--source FILE]`: run the workflow from a
    chosen point inside an existing session (same session id), then on to the end."""
    if point not in START_POINTS:
        stop_run(ValueError(f"--from must be one of: {', '.join(START_POINTS)}"))
    session_dir = _resolve_session(arg) if arg else (_session_of_checkpoint(_latest_any_checkpoint()) or "")
    if not session_dir:
        stop_run(ValueError(f"no session found for '{arg or 'latest'}' — give output/<id>"))
    rel = os.path.relpath(session_dir, PROJECT_ROOT)
    if layout.is_legacy(session_dir):
        migrate_session(session_dir)

    if point == "review":
        continue_research(router_factory, session_dir)
        return

    if point == "research":
        from .research.state import new_state
        if not os.path.exists(layout.research_state_file(session_dir)):
            stop_run(ValueError(f"{rel} has no research to redo (start one with --topic)"))
        with open(layout.research_state_file(session_dir), encoding="utf-8") as f:
            old = json.load(f)
        archived = layout.archive_stage(layout.research_dir(session_dir))
        if archived:
            print(f"🗂  Earlier research kept in: {os.path.relpath(archived, PROJECT_ROOT)}")
        state = new_state(old.get("topic", ""), old.get("audience", ""), old.get("notes", ""), _research_settings())
        state["workflow"] = old.get("workflow", "research")
        state["session"] = os.path.basename(session_dir)
        print(f"▶ Researching again in {rel}: {state['topic']}")
        run_research(router_factory(), state, session_dir)
        return

    if point == "script":
        text, research, label = _script_source(session_dir, source_file)
        extra = {}
        if research is not None or looks_like_dossier(text):
            extra = {"source_format": "Research dossier (verified facts with citations)", "research_dossier": True}
        print(f"▶ Script workflow in {rel} from {label}")
        router = router_factory()
        _archive_previous_video(session_dir)
        run_video(router, text, output_dir=session_dir, research=research, extra_state=extra)
        return

    checkpoint = layout.video_checkpoint_file(session_dir)
    if not os.path.exists(checkpoint):
        stop_run(ValueError(f"{rel} has no video run yet — start with --from script"))
    state, meta = load_checkpoint(checkpoint)
    need, what = _STAGE_NEEDS[point]
    if not state.get(need):
        stop_run(ValueError(f"{rel} has not finished {what} yet: use --resume {rel}, or an earlier --from point"))
    if point == "export":
        print(f"▶ Writing the files of {rel} again")
        router = router_factory()
        _archive_previous_video(session_dir, keep_checkpoint=True)
        run_video(router, "", output_dir=session_dir, resume_state=state, entry=END, resume_meta=meta)
        return
    state, meta = _rewound(state, meta, point)
    print(f"▶ Re-running {rel} from {point} ({_STAGE_ENTRY[point]}), then on to the end")
    router = router_factory()
    _archive_previous_video(session_dir, keep_checkpoint=True)
    run_video(router, "", output_dir=session_dir, resume_state=state, entry=_STAGE_ENTRY[point], resume_meta=meta)


def migrate_session(session_dir: str) -> None:
    """Moves an older session into the numbered layout and says what moved."""
    moves = layout.migrate(session_dir)
    for stage in (layout.RESEARCH_DIR, layout.REVIEW_DIR, layout.VIDEO_DIR):
        layout.write_start_here(session_dir, stage, os.path.relpath(session_dir, PROJECT_ROOT))
    if moves:
        print(f"📦 {os.path.relpath(session_dir, PROJECT_ROOT)} moved to the numbered layout "
              f"({len(moves)} items): " + " · ".join(
                  f"{stage}/" for stage in (layout.RESEARCH_DIR, layout.REVIEW_DIR, layout.VIDEO_DIR)
                  if os.path.isdir(os.path.join(session_dir, stage))))


def migrate_sessions(arg: str) -> None:
    """`python3 main.py --migrate [session|all]`: older sessions → numbered layout."""
    if arg in ("", "all", True):
        sessions = sorted(d for d in glob.glob(os.path.join(PROJECT_ROOT, "output", "*")) if os.path.isdir(d))
    else:
        session = _resolve_session(arg)
        if not session:
            stop_run(ValueError(f"no session found for '{arg}'"))
        sessions = [session]
    moved = 0
    for session in sessions:
        if layout.is_legacy(session):
            migrate_session(session)
            moved += 1
    print(f"✅ {moved} session(s) moved; {len(sessions) - moved} already in the numbered layout or empty.")


def _latest_any_checkpoint() -> str:
    """The most recently written checkpoint of any session (done or not)."""
    paths = [p for _, pattern in layout.CHECKPOINT_PATTERNS
             for p in glob.glob(os.path.join(PROJECT_ROOT, "output", pattern))]
    return max(paths, key=os.path.getmtime) if paths else ""


# --------------------------------------------------------------------------
def main():
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    llm_config = load_llm_config()

    # Optional: --provider main|single|hybrid skips the model menu (old names: gemini, claude)
    cli_provider = _take_flag("--provider")
    if cli_provider == "":
        stop_run(ValueError("--provider needs a value: main, single or hybrid"))
    workflow = _take_flag("--workflow")
    minutes = _take_flag("--minutes")
    if minutes and minutes is not True:
        os.environ["MB_TARGET_MINUTES"] = str(minutes)
    topic = _take_flag("--topic")
    audience = _take_flag("--audience") or ""
    cont = _take_flag("--continue")
    continuing = cont is not None
    resume = _take_flag("--resume")
    resuming = resume is not None
    start_point = _take_flag("--from")
    source_file = _take_flag("--source") or ""
    broll = _take_flag("--broll")
    broll_file = _take_flag("--broll-file")
    broll_into = _take_flag("--into") or ""
    broll_opts = {"yes": bool(_take_flag("--yes", has_value=False)),
                  "dry_run": bool(_take_flag("--dry-run", has_value=False)),
                  "only": _take_flag("--only") or "", "items": _take_flag("--items") or "",
                  "wb_pick": _take_flag("--wb-pick") or "",
                  "counts": {"video_count": _take_flag("--video-count"), "image_count": _take_flag("--image-count"),
                             "whiteboard_video_count": _take_flag("--wb-video-count"),
                             "whiteboard_image_count": _take_flag("--wb-image-count")}}
    if _take_flag("--generate-broll", has_value=False):
        os.environ["MB_GENERATE_BROLL"] = "1"
        if broll_opts["yes"]:
            os.environ["MB_BROLL_YES"] = "1"
    if broll_file is not None:
        if not broll_file or broll_file is True:
            stop_run(ValueError("--broll-file needs a file or folder"))
        run_broll(manual_file=broll_file, into=broll_into, **broll_opts)
        return
    if broll is not None:
        run_broll(session_arg=broll if broll not in ("", True) else (sys.argv[1] if len(sys.argv) >= 2 else ""),
                  **broll_opts)
        return
    migrate = _take_flag("--migrate")
    if migrate is not None:
        migrate_sessions(sys.argv[1] if migrate in ("", True) and len(sys.argv) >= 2 else (migrate or ""))
        return

    if workflow and workflow not in WORKFLOWS:
        stop_run(ValueError(f"--workflow must be one of: {', '.join(WORKFLOWS)}"))
    if start_point is not None:
        chosen = "from"
    elif continuing:
        chosen = "continue"
    elif resuming:
        chosen = "resume"
    elif workflow:
        chosen = workflow
    elif topic:
        chosen = "research"
    elif len(sys.argv) >= 2:
        chosen = "video"
    elif _interactive():
        chosen = choose_workflow()
    else:
        print(__doc__)
        sys.exit(1)

    def make_router():
        provider = cli_provider
        if not provider and not os.environ.get("LLM_PROVIDER") and _interactive():
            provider = choose_mode(llm_config)
        return start_router(llm_config, provider)

    if chosen == "continue":
        if workflow == "research-video":
            # --continue <s> --workflow research-video: build the video even if the session was research-only
            session_dir = _find_session(cont or "")
            if session_dir:
                path = layout.research_state_file(session_dir)
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                saved["workflow"] = "research-video"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(saved, f, ensure_ascii=False, indent=2)
        if cont == "" and _interactive() and not workflow:
            sessions = _research_sessions()[-5:]
            if sessions:
                print("\nRecent research sessions:")
                for i, session in enumerate(reversed(sessions), 1):
                    with open(layout.research_state_file(session), encoding="utf-8") as f:
                        print(f"  {i}) {os.path.basename(session)} — {json.load(f).get('topic')}")
                pick = input("Choose [1]: ").strip() or "1"
                if pick.isdigit() and 1 <= int(pick) <= len(sessions):
                    cont = list(reversed(sessions))[int(pick) - 1]
        continue_research(make_router, cont or "")
        return

    if chosen == "resume":
        resume_run(make_router, resume or "")
        return

    if chosen == "broll":
        session_arg = input("\nSession (Enter = the newest): ").strip()
        mode = input("Dry run first (show the plan, generate nothing)? [Y/n]: ").strip().lower()
        run_broll(session_arg=session_arg, dry_run=mode not in ("n", "no"))
        return

    if chosen in ("from", "start-from"):
        session_arg = sys.argv[1] if len(sys.argv) >= 2 else ""
        if chosen == "start-from" or not start_point or start_point is True:
            start_point, session_arg = choose_start_point(session_arg)
        start_from(make_router, start_point, session_arg, source_file)
        return

    if chosen in ("research", "research-video"):
        if not topic:
            if not _interactive():
                stop_run(ValueError('--topic "…" is needed for the research workflow'))
            topic = input("\nTopic (title of the video subject, any language): ").strip()
            if not topic:
                stop_run(ValueError("no topic given"))
            notes = input("Anything the research must include or focus on? (Enter to skip): ").strip()
        else:
            notes = ""
        start_research(make_router(), topic, chosen, audience, notes)
        return

    # Video from an English subtitle script
    if len(sys.argv) < 2:
        if not _interactive():
            print(__doc__)
            sys.exit(1)
        sys.argv.append(input("\nPath to the English subtitle/script file: ").strip().strip('"').strip("'"))
    script_file = sys.argv[1]
    if not os.path.exists(script_file):
        stop_run(ValueError(f"Script file not found: {script_file}"))
    with open(script_file, "r", encoding="utf-8") as f:
        original_script = f.read()
    video_analysis = ""
    if len(sys.argv) >= 3:
        if not os.path.exists(sys.argv[2]):
            stop_run(ValueError(f"Video analysis file not found: {sys.argv[2]}"))
        with open(sys.argv[2], "r", encoding="utf-8") as f:
            video_analysis = f.read()
        print(f"📋 Video analysis loaded from: {sys.argv[2]}")
    extra_state = {}
    if looks_like_dossier(original_script):
        print("📚 Source has [S#] citations — treating it as a research dossier")
        extra_state = {"source_format": "Research dossier (verified facts with citations)",
                       "research_dossier": True}
    run_video(make_router(), original_script, video_analysis, extra_state=extra_state)


if __name__ == "__main__":
    main()
