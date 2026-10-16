"""The command line. Three workflows share the model setup and the video pipeline:

  python3 main.py <script.txt> [video_analysis.txt]        English subtitle → video package
  python3 main.py --topic "…" [--workflow research]         topic → verified research dossier
  python3 main.py --topic "…" --workflow research-video     topic → research → video package
  python3 main.py --continue [session]                      apply review.md / approve research
  python3 main.py --resume [session]                        continue a stopped run
  python3 main.py                                           menu
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
    while True:
        choice = input("Choose 1-5 [1]: ").strip() or "1"
        if choice in ("1", "2", "3", "4", "5"):
            return {"1": "video", "2": "research-video", "3": "research", "4": "continue",
                    "5": "resume"}[choice]
        print("Please type 1, 2, 3, 4 or 5.")


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
    """The session folder a checkpoint lives in ('<s>/research/research_checkpoint.json'
    or '<s>/checkpoint.json')."""
    base = os.path.basename(path)
    if base == "research_checkpoint.json":
        return os.path.dirname(os.path.dirname(path))
    if base == "checkpoint.json":
        return os.path.dirname(path)
    return ""


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
        session_id = os.path.basename(output_dir.rstrip("/"))
        os.makedirs(output_dir, exist_ok=True)
        set_cache_dir(os.path.join(output_dir, "llm_cache"))
        if research is None:
            research_path = os.path.join(output_dir, "research", "research_state.json")
            if os.path.exists(research_path):
                with open(research_path, encoding="utf-8") as f:
                    research = json.load(f)
        print(f"Starting video workflow... Session ID: {session_id}")
        print(f"Logs: {output_dir}")
        if entry != END:
            state = run_graph(build_app(entry=entry), state, output_dir, router, resume_meta=resume_meta)
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
            session_id = os.path.basename(output_dir.rstrip("/"))
            os.makedirs(output_dir, exist_ok=True)
        else:
            session_id, output_dir = _new_session_dir()
        set_cache_dir(os.path.join(output_dir, "llm_cache"))
        print(f"Starting video workflow... Session ID: {session_id}")
        print(f"Logs: {output_dir}")
        state = run_graph(build_app(), initial_state, output_dir, router)

    final_script_path = os.path.join(output_dir, "final_script.md")

    if research:
        # The sources the finished script actually uses go into the document
        # (appendix + YouTube description); the full list stays in the research folder.
        _apply_script_sources(state, research, output_dir)

    for warning in (ew.quality_warning(state), ew.production_warning(state), ew.package_warning(state)):
        if warning:
            print(f"\n{warning}")

    provider_report = router.report()
    with open(os.path.join(output_dir, "llm_provider.json"), "w", encoding="utf-8") as pf:
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
        print(f"Checkpoint with all intermediate outputs saved to: {os.path.join(output_dir, 'checkpoint.json')}")
        _mark_checkpoint_done(output_dir, "workflow")
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

    shorts_path = os.path.join(output_dir, "shorts_package.md")
    if shorts_section:
        with open(shorts_path, "w", encoding="utf-8") as sf:
            sf.write(shorts_section.strip())
        print(f"Dedicated Reels & Shorts package saved to: {shorts_path}")

    export_broll_prompt_files(text=longform_deliverable + shorts_section,
                              fallback_broll=state.get("broll_prompts", ""), output_dir=output_dir)
    export_whiteboard_prompt_files(longform_deliverable, output_dir=output_dir,
                                   overlay_guide=state.get("text_animation_overlay", ""))
    _convert(final_script_path, "final script")
    if shorts_section and os.path.exists(shorts_path):
        _convert(shorts_path, "shorts package")
    _mark_checkpoint_done(output_dir, "workflow")


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
        if c and os.path.exists(os.path.join(c, "research", "research_state.json")):
            return os.path.abspath(c)
    found = sorted(glob.glob(os.path.join(PROJECT_ROOT, "output", "*", "research", "research_state.json")))
    if not arg and found:
        return os.path.dirname(os.path.dirname(found[-1]))
    return ""


def _prepare_research(state: dict, session_dir: str) -> str:
    """The session's research folder, with its HTTP and model-answer caches in place."""
    from .research.sources import google, http
    folder = os.path.join(session_dir, "research")
    os.makedirs(folder, exist_ok=True)
    http.set_cache_dir(os.path.join(folder, "cache"))
    set_cache_dir(os.path.join(folder, "llm_cache"))
    google.reset_run(max_queries=state.get("settings", {}).get("max_google_queries", 40))
    return folder


def _finish_research(session_dir: str, state: dict) -> dict:
    """Everything after the research graph: dossier + patient guide + review.md + exports."""
    from .research import dossier as rd
    folder = os.path.join(session_dir, "research")
    state["review"] = {}
    # Write the editable source before rd.save so its sha256 is in the saved state.
    video_source_path = rd.write_video_source(state, folder)
    paths = rd.save(state, folder)
    review_path = os.path.join(folder, "review.md")
    has_guide = bool(state.get("patient_guide"))
    if os.path.exists(review_path):           # keep the previous round's review for the record
        os.replace(review_path, os.path.join(folder, f"review_round{state.get('review_round', 0)}.md"))
    rd.write_review_file(review_path, state["topic"], os.path.relpath(session_dir, PROJECT_ROOT),
                         has_guide=has_guide)
    facts = rd.verified_facts(state)
    guide_md = paths.get("guide_md")
    if has_guide and guide_md:
        print(f"\n📄 Patient guide (what the video will be built from): {guide_md}")
        _convert(guide_md, "patient guide")
    print(f"📄 Evidence Library (dossier): {paths['md']}  ({len(facts)} verified facts, "
          f"{len(rd.used_sources(state))} sources, {len(state.get('rejected', []))} left out)")
    _convert(paths["md"], "research dossier")
    print(f"📝 Video source (edit it to change what the script is written from): {video_source_path}")
    print(f"✍️  Review it, then edit {review_path}")
    print(f"   → python3 main.py --continue {os.path.relpath(session_dir, PROJECT_ROOT)}")
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
    folder = os.path.join(session_dir, "research")
    with open(os.path.join(folder, "research_state.json"), encoding="utf-8") as f:
        state = json.load(f)
    # Older sessions have no editable video source: write one (and record its hash in
    # the saved state) so the creator can change what the script is built from.
    video_source_path = os.path.join(folder, rd.VIDEO_SOURCE_FILE)
    if not os.path.exists(video_source_path):
        rd.write_video_source(state, folder)
        with open(os.path.join(folder, "research_state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"📝 Wrote {os.path.relpath(video_source_path, PROJECT_ROOT)} — edit it if you want "
              "to change what the video is built from.")
    review_path = os.path.join(folder, "review.md")
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
        source, edited = rd.read_video_source(state, folder)
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
    base = os.path.basename(path)
    if base == "research_checkpoint.json":
        return "research"
    if base == "checkpoint.json":
        return "video"
    return ""


def _read_checkpoint_meta(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return (json.load(f) or {}).get("_resume") or {}


def _checkpoint_for_session(session_dir: str) -> str:
    """A session folder's checkpoint: the newest one that is not "done" (a research-video
    session has both), else research, else video. '' when the folder has none."""
    found = []
    for rel in (os.path.join("research", "research_checkpoint.json"), "checkpoint.json"):
        path = os.path.join(session_dir, rel)
        if not os.path.exists(path):
            continue
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
    for pattern in ("output/*/research/research_checkpoint.json", "output/*/checkpoint.json"):
        for path in glob.glob(os.path.join(PROJECT_ROOT, pattern)):
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

    if workflow and workflow not in WORKFLOWS:
        stop_run(ValueError(f"--workflow must be one of: {', '.join(WORKFLOWS)}"))
    if continuing:
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
                path = os.path.join(session_dir, "research", "research_state.json")
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                saved["workflow"] = "research-video"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(saved, f, ensure_ascii=False, indent=2)
        if cont == "" and _interactive() and not workflow:
            sessions = sorted(glob.glob(os.path.join(PROJECT_ROOT, "output", "*", "research", "research_state.json")))[-5:]
            if sessions:
                print("\nRecent research sessions:")
                for i, p in enumerate(reversed(sessions), 1):
                    with open(p, encoding="utf-8") as f:
                        print(f"  {i}) {os.path.basename(os.path.dirname(os.path.dirname(p)))} — {json.load(f).get('topic')}")
                pick = input("Choose [1]: ").strip() or "1"
                if pick.isdigit() and 1 <= int(pick) <= len(sessions):
                    cont = os.path.dirname(os.path.dirname(list(reversed(sessions))[int(pick) - 1]))
        continue_research(make_router, cont or "")
        return

    if chosen == "resume":
        resume_run(make_router, resume or "")
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
