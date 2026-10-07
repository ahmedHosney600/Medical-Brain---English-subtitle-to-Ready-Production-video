"""The command-line run: `python3 main.py <script.txt> [video_analysis.txt] [--provider main|single|hybrid]`.

Asks which model setup to use, runs the pipeline graph step by step (saving a log
and a checkpoint after each step), then writes the final documents and exports."""
import json
import os
import time

from dotenv import load_dotenv

from .exports import workbook as ew
from .exports.prompt_files import export_broll_prompt_files, export_whiteboard_prompt_files
from .graph import build_app
from .llm import LLMRouter, load_llm_config, set_router
from .paths import PROJECT_ROOT
from .state import apply_defaults


def main():
    # Load environment variables (e.g. OPENAI_API_KEY)
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    llm_config = load_llm_config()
    app = build_app()

    import sys
    import datetime

    # Optional: --provider main|single|hybrid skips the startup menu
    # (old names: gemini = main, claude = single)
    cli_provider = None
    if "--provider" in sys.argv:
        i = sys.argv.index("--provider")
        if i + 1 >= len(sys.argv):
            print("--provider needs a value: main, single or hybrid")
            sys.exit(1)
        cli_provider = sys.argv[i + 1]
        del sys.argv[i:i + 2]

    if len(sys.argv) < 2:
        print("Usage: python3 main.py <path_to_original_script> [path_to_video_analysis] [--provider main|single|hybrid]")
        print("Choose models first with: python3 setup_models.py")
        sys.exit(1)

    # Model setup for this run: menu when interactive, otherwise flag / env / config
    if not cli_provider and not os.environ.get("LLM_PROVIDER") and sys.stdin.isatty():
        print("\nWhich model setup for this run?  (change the models with: python3 setup_models.py)")
        print(f"  1) Main model for the whole workflow ({llm_config['main_model']})")
        print(f"  2) One strong model for the whole workflow ({llm_config['single_model']})")
        print(f"  3) Hybrid (recommended): {llm_config['main_model']} writes, judges check:")
        for _node, _model in llm_config["hybrid_routes"].items():
            print(f"       {_node} → {_model}")
        print("     Any step whose model fails runs on the main model instead.")
        if llm_config.get("backup_model"):
            print(f"  Backup if the main model fails: {llm_config['backup_model']}")
        while True:
            _choice = input("Choose 1-3 [3]: ").strip() or "3"
            if _choice in ("1", "2", "3"):
                break
            print("Please type 1, 2 or 3.")
        cli_provider = {"1": "main", "2": "single", "3": "hybrid"}[_choice]
    llm_router = LLMRouter(llm_config, provider=cli_provider)
    set_router(llm_router)

    script_file = sys.argv[1]
    if not os.path.exists(script_file):
        raise FileNotFoundError(f"Script file not found: {script_file}")

    with open(script_file, "r", encoding="utf-8") as f:
        original_script_content = f.read()

    input_file = os.path.join(PROJECT_ROOT, "input_fields.json")
    if not os.path.exists(input_file):
        raise FileNotFoundError("please enter input fields file")

    with open(input_file, "r", encoding="utf-8") as f:
        initial_state = json.load(f)

    initial_state["original_script"] = original_script_content

    # Load optional video analysis file (second CLI argument)
    if len(sys.argv) >= 3:
        analysis_file = sys.argv[2]
        if not os.path.exists(analysis_file):
            raise FileNotFoundError(f"Video analysis file not found: {analysis_file}")
        with open(analysis_file, "r", encoding="utf-8") as f:
            initial_state["video_analysis"] = f.read()
        print(f"📋 Video analysis loaded from: {analysis_file}")

    # Initialize unprovided fields to prevent KeyError/None issues in prompts
    apply_defaults(initial_state)

    # Set up session output directory
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(PROJECT_ROOT, "output", session_id)
    os.makedirs(output_dir, exist_ok=True)

    session_log_path = os.path.join(output_dir, "session_log.jsonl")
    final_script_path = os.path.join(output_dir, "final_script.md")
    checkpoint_path = os.path.join(output_dir, "checkpoint.json")

    # Quick check of every model this run uses (never blocks; failures fall back)
    llm_router.select()

    print(f"Starting workflow... Session ID: {session_id}")
    print(f"Logs: {output_dir}")
    print(f"Checkpoint: {checkpoint_path}")

    cumulative_state = dict(initial_state)  # track full state for checkpointing

    # Using app.stream to observe execution
    with open(session_log_path, "w", encoding="utf-8") as log_file:
        step_start = time.time()
        for output in app.stream(initial_state):
            for key, value in output.items():
                # Merge updates into cumulative state
                cumulative_state.update(value)

                elapsed = time.time() - step_start
                step_start = time.time()
                _model = llm_router.model_for_node(key)
                print(f"✅ Node '{key}' completed. ({elapsed:.0f}s" + (f", {_model})" if _model else ")"))

                # Write step details to session log
                log_entry = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "node": key,
                    "state_updates": value
                }
                log_file.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                log_file.flush()

                # Save full cumulative checkpoint after every node
                # If the run crashes, re-running can resume from this file
                try:
                    with open(checkpoint_path, "w", encoding="utf-8") as cp:
                        json.dump(cumulative_state, cp, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"  [warning] Could not save checkpoint: {e}")

    # Record which model(s) actually produced this run
    provider_report = llm_router.report()
    with open(os.path.join(output_dir, "llm_provider.json"), "w", encoding="utf-8") as pf:
        json.dump(provider_report, pf, ensure_ascii=False, indent=2)
    print(f"\n🔀 LLM calls this run ({provider_report['mode']}): {provider_report['calls']}")

    # Save the final deliverables
    longform_deliverable = cumulative_state.get("final_package", "")
    shorts_scripts_content = cumulative_state.get("shorts_scripts", "")
    shorts_captions_content = cumulative_state.get("shorts_captions", "")
    shorts_moments_content = cumulative_state.get("shorts_moments", "")

    shorts_section = ""
    if shorts_scripts_content:
        shorts_section += "\n\n---\n\n# 📱 REELS & SHORTS PACKAGE\n\n"
        if shorts_moments_content:
            shorts_section += "## IDENTIFIED MOMENTS\n\n" + shorts_moments_content + "\n\n---\n\n"
        shorts_section += "## REELS & SHORTS SCRIPTS\n\n" + shorts_scripts_content + "\n\n---\n\n"
        if shorts_captions_content:
            shorts_section += "## TIMED CAPTIONS & ON-SCREEN OVERLAYS\n\n" + shorts_captions_content

    if longform_deliverable:
        # Reorder into the editing workflow (pure Python, nothing is retyped).
        # If the package has an unexpected shape, keep it as generated.
        try:
            longform_document = ew.build_editing_workbook(longform_deliverable, cumulative_state)
        except Exception as wb_err:
            print(f"  ⚠️ Could not reorder the package into editing steps ({wb_err}); saving it as generated.")
            longform_document = longform_deliverable
        complete_output = longform_document + shorts_section
        with open(final_script_path, "w", encoding="utf-8") as f:
            f.write(complete_output)
        print(f"\nWorkflow finished. Final complete package saved to: {final_script_path}")

        # Also write standalone shorts package file for easy access
        if shorts_section:
            shorts_path = os.path.join(output_dir, "shorts_package.md")
            with open(shorts_path, "w", encoding="utf-8") as sf:
                sf.write(shorts_section.strip())
            print(f"Dedicated Reels & Shorts package saved to: {shorts_path}")

        # Automatically export dedicated B-Roll prompt files (Images & Videos)
        export_broll_prompt_files(
            text=longform_deliverable + shorts_section,
            fallback_broll=cumulative_state.get("broll_prompts", ""),
            output_dir=output_dir
        )
        export_whiteboard_prompt_files(longform_deliverable, output_dir=output_dir)

        # Automatically generate responsive HTML, PDF, and DOCX exports
        try:
            from .exports.convert import convert_markdown_file
            print("\n🚀 Exporting final script to Responsive HTML, PDF, and DOCX...")
            res = convert_markdown_file(final_script_path)
            html_out = res[0]
            pdf_out = res[1]
            docx_out = res[2] if len(res) > 2 else None
            print(f"  📄 HTML export saved to: {html_out}")
            if pdf_out:
                print(f"  📑 PDF export saved to:  {pdf_out}")
            if docx_out:
                print(f"  📝 DOCX export saved to: {docx_out}")

            # Also export shorts package if generated
            if shorts_section and os.path.exists(shorts_path):
                convert_markdown_file(shorts_path)
        except Exception as conv_err:
            print(f"  ⚠️ Automatic HTML/PDF/DOCX conversion warning: {conv_err}")
    else:
        print("\nWorkflow finished, but no final package was generated.")
        print(f"Checkpoint with all intermediate outputs saved to: {checkpoint_path}")


if __name__ == "__main__":
    main()
