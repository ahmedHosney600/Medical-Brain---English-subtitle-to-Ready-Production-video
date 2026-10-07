"""The command line. Three workflows share the model setup and the video pipeline:

  python3 main.py <script.txt> [video_analysis.txt]        English subtitle → video package
  python3 main.py --topic "…" [--workflow research]         topic → verified research dossier
  python3 main.py --topic "…" --workflow research-video     topic → research → video package
  python3 main.py --continue [session]                      apply review.md / approve research
  python3 main.py                                           menu
Options: --provider main|single|hybrid (skips the model menu)."""
import datetime
import glob
import json
import os
import re
import sys
import time

from dotenv import load_dotenv

from .exports import workbook as ew
from .exports.prompt_files import export_broll_prompt_files, export_whiteboard_prompt_files
from .graph import build_app
from .llm import LLMRouter, ProviderUnavailable, load_llm_config, set_router
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


def stop_run(error, checkpoint_path: str = ""):
    """Ends the run with the fix, not a traceback."""
    kind = "" if isinstance(error, (ProviderUnavailable, ValueError)) else f"{type(error).__name__}: "
    print(f"\n❌ Stopped: {kind}{error}")
    if checkpoint_path:
        print(f"   Steps finished so far are saved in: {checkpoint_path}")
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
    while True:
        choice = input("Choose 1-4 [1]: ").strip() or "1"
        if choice in ("1", "2", "3", "4"):
            return {"1": "video", "2": "research-video", "3": "research", "4": "continue"}[choice]
        print("Please type 1, 2, 3 or 4.")


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


def run_graph(app, initial_state: dict, out_dir: str, router, label: str = "workflow") -> dict:
    """Streams a graph step by step, with a log line, a session log and a checkpoint
    after every step. Any failure stops cleanly with the checkpoint saved."""
    log_path = os.path.join(out_dir, "session_log.jsonl" if label == "workflow" else f"{label}_log.jsonl")
    checkpoint_path = os.path.join(out_dir, "checkpoint.json" if label == "workflow" else f"{label}_checkpoint.json")
    state = dict(initial_state)
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
                with open(checkpoint_path, "w", encoding="utf-8") as cf:
                    json.dump(state, cf, ensure_ascii=False, indent=2)
                stop_run(e, checkpoint_path)
            for key, value in output.items():
                state.update(value or {})
                elapsed = time.time() - step_start
                step_start = time.time()
                model = router.model_for_node(key)
                print(f"✅ Node '{key}' completed. ({elapsed:.0f}s" + (f", {model})" if model else ")"))
                log_file.write(json.dumps({"timestamp": datetime.datetime.now().isoformat(), "node": key,
                                           "state_updates": value}, ensure_ascii=False) + "\n")
                log_file.flush()
                try:
                    with open(checkpoint_path, "w", encoding="utf-8") as cp:
                        json.dump(state, cp, ensure_ascii=False, indent=2)
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
def run_video(router, original_script: str, video_analysis: str = "", output_dir: str = "",
              extra_state: dict = None, research: dict = None) -> None:
    input_file = os.path.join(PROJECT_ROOT, "input_fields.json")
    if not os.path.exists(input_file):
        stop_run(FileNotFoundError("input_fields.json is missing (the channel settings)"))
    with open(input_file, "r", encoding="utf-8") as f:
        initial_state = json.load(f)
    initial_state["original_script"] = original_script
    if video_analysis:
        initial_state["video_analysis"] = video_analysis
    initial_state.update(extra_state or {})
    apply_defaults(initial_state)

    if output_dir:
        session_id = os.path.basename(output_dir.rstrip("/"))
        os.makedirs(output_dir, exist_ok=True)
    else:
        session_id, output_dir = _new_session_dir()
    final_script_path = os.path.join(output_dir, "final_script.md")
    print(f"Starting video workflow... Session ID: {session_id}")
    print(f"Logs: {output_dir}")

    state = run_graph(build_app(), initial_state, output_dir, router)

    if research:
        # The verified sources go into the document (appendix + YouTube description).
        from .research import dossier as rd
        state["final_package"] = ew.add_research_sources(
            state.get("final_package", ""), rd.reference_lines(research), rd.key_sources(research))

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


def run_research(router, state: dict, session_dir: str) -> dict:
    from .research import dossier as rd
    from .research.graph import build_research_app
    from .research.sources import enabled, http
    folder = os.path.join(session_dir, "research")
    os.makedirs(folder, exist_ok=True)
    http.set_cache_dir(os.path.join(folder, "cache"))
    print(f"\n🔬 Researching: {state['topic']}  (sources: {', '.join(enabled())})")
    state = run_graph(build_research_app(), state, folder, router, label="research")
    state["review"] = {}
    paths = rd.save(state, folder)
    review_path = os.path.join(folder, "review.md")
    if os.path.exists(review_path):           # keep the previous round's review for the record
        os.replace(review_path, os.path.join(folder, f"review_round{state.get('review_round', 0)}.md"))
    rd.write_review_file(review_path, state["topic"], os.path.relpath(session_dir, PROJECT_ROOT))
    _convert(paths["md"], "research dossier")
    facts = rd.verified_facts(state)
    print(f"\n📄 Dossier: {paths['md']}  ({len(facts)} verified facts, {len(rd.used_sources(state))} sources, "
          f"{len(state.get('rejected', []))} left out)")
    print(f"✍️  Review it, then edit {review_path}")
    print(f"   → python3 main.py --continue {os.path.relpath(session_dir, PROJECT_ROOT)}")
    print("     (APPROVED: yes to finish" + (" and build the video)" if state.get("workflow") == "research-video" else ")"))
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
        run_video(router, rd.video_source(state), output_dir=session_dir, research=state, extra_state={
            "source_format": "Research dossier (verified facts with citations)", "research_dossier": True})
        return
    if not rd.has_requests(review):
        print(f"review.md has no requests and isn't approved yet. Edit {review_path} "
              "(write your changes, or APPROVED: yes) and run --continue again.")
        return
    router = router_factory()
    state["review"] = review
    state["settings"] = {**state.get("settings", {}), **_research_settings()}
    run_research(router, state, session_dir)


# --------------------------------------------------------------------------
def main():
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    llm_config = load_llm_config()

    # Optional: --provider main|single|hybrid skips the model menu (old names: gemini, claude)
    cli_provider = _take_flag("--provider")
    if cli_provider == "":
        stop_run(ValueError("--provider needs a value: main, single or hybrid"))
    workflow = _take_flag("--workflow")
    topic = _take_flag("--topic")
    audience = _take_flag("--audience") or ""
    cont = _take_flag("--continue")
    continuing = cont is not None

    if workflow and workflow not in WORKFLOWS:
        stop_run(ValueError(f"--workflow must be one of: {', '.join(WORKFLOWS)}"))
    if continuing:
        chosen = "continue"
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
    run_video(make_router(), original_script, video_analysis)


if __name__ == "__main__":
    main()
