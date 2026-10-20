"""Where a session's files live.

A session folder (output/<id>/) holds up to four numbered stage folders, each created
only when its stage runs, read top to bottom:

    1_research/   research files: patient guide, evidence library, logs/
    2_review/     the approved input to the script workflow: review.md, video_source.md
    3_video/      the script workflow's output: final script, shorts, prompt files, logs/
    4_broll/      generated B-roll media (Google Flow): images/, videos/, whiteboards/, logs/

A session started from an English subtitle (no research) only has 3_video/. Inside each
stage folder the files a person reads are numbered in reading order (00_START_HERE.md
first); machine files (state, checkpoints, logs, caches) sit in logs/.

Sessions made before this layout keep working: their research is in research/ and their
video files sit in the session root. `is_legacy` tells them apart and every helper here
returns the old path for them, so --resume and --continue still find everything."""
import datetime
import os
import shutil

RESEARCH_DIR = "1_research"
REVIEW_DIR = "2_review"
VIDEO_DIR = "3_video"
BROLL_DIR = "4_broll"
LOGS_DIR = "logs"
PREVIOUS_DIR = "_previous_runs"
START_HERE = "00_START_HERE.md"

# Reader-facing names, in reading order.
PATIENT_GUIDE = "01_patient_guide"          # + .md/.html/.pdf/.docx
EVIDENCE_LIBRARY = "02_evidence_library"
REVIEW_FILE = "01_review.md"
VIDEO_SOURCE_FILE = "02_video_source.md"
FINAL_SCRIPT = "01_final_script"
SHORTS_PACKAGE = "02_shorts_package"
PROMPT_FILES = {                            # name the exporter writes → numbered name
    "broll_images.txt": "03_broll_images.txt",
    "broll_videos.txt": "04_broll_videos.txt",
    "whiteboard_images.txt": "05_whiteboard_images.txt",
    "whiteboard_videos.txt": "06_whiteboard_videos.txt",
}

_LEGACY_MARKERS = ("research", "checkpoint.json", "final_script.md", "session_log.jsonl")


def is_legacy(session_dir: str) -> bool:
    """True for a session written in the old layout (research/ + video files in the root)."""
    if not session_dir or not os.path.isdir(session_dir):
        return False
    if any(os.path.isdir(os.path.join(session_dir, d)) for d in (RESEARCH_DIR, REVIEW_DIR, VIDEO_DIR)):
        return False
    return any(os.path.exists(os.path.join(session_dir, m)) for m in _LEGACY_MARKERS)


# --------------------------------------------------------------------------
# Research and review
# --------------------------------------------------------------------------
def research_dir(session_dir: str) -> str:
    """The folder a person opens for the research (1_research/, or research/ when legacy)."""
    return os.path.join(session_dir, "research" if is_legacy(session_dir) else RESEARCH_DIR)


def research_logs(session_dir: str) -> str:
    """Where the research machinery writes (state, checkpoint, log, caches)."""
    if is_legacy(session_dir):
        return os.path.join(session_dir, "research")
    return os.path.join(session_dir, RESEARCH_DIR, LOGS_DIR)


def review_dir(session_dir: str) -> str:
    return os.path.join(session_dir, "research" if is_legacy(session_dir) else REVIEW_DIR)


def review_file(session_dir: str) -> str:
    return os.path.join(review_dir(session_dir), "review.md" if is_legacy(session_dir) else REVIEW_FILE)


def video_source_name(session_dir: str) -> str:
    return "video_source.md" if is_legacy(session_dir) else VIDEO_SOURCE_FILE


def video_source_file(session_dir: str) -> str:
    return os.path.join(review_dir(session_dir), video_source_name(session_dir))


def research_state_file(session_dir: str) -> str:
    return os.path.join(research_logs(session_dir), "research_state.json")


def research_checkpoint_file(session_dir: str) -> str:
    return os.path.join(research_logs(session_dir), "research_checkpoint.json")


# --------------------------------------------------------------------------
# Video
# --------------------------------------------------------------------------
def video_dir(session_dir: str) -> str:
    """The folder a person opens for the video package (3_video/, or the root when legacy)."""
    return session_dir if is_legacy(session_dir) else os.path.join(session_dir, VIDEO_DIR)


def video_logs(session_dir: str) -> str:
    """Where the video workflow writes its checkpoint, session log and caches."""
    return session_dir if is_legacy(session_dir) else os.path.join(session_dir, VIDEO_DIR, LOGS_DIR)


def broll_dir(session_dir: str) -> str:
    """Generated B-roll media (made only when B-roll generation runs)."""
    return os.path.join(session_dir, BROLL_DIR)


def video_checkpoint_file(session_dir: str) -> str:
    return os.path.join(video_logs(session_dir), "checkpoint.json")


def final_script_md(session_dir: str) -> str:
    name = "final_script.md" if is_legacy(session_dir) else FINAL_SCRIPT + ".md"
    return os.path.join(video_dir(session_dir), name)


def shorts_md(session_dir: str) -> str:
    name = "shorts_package.md" if is_legacy(session_dir) else SHORTS_PACKAGE + ".md"
    return os.path.join(video_dir(session_dir), name)


def number_prompt_files(session_dir: str) -> list:
    """Renames the exporter's prompt files to their numbered names (new layout only)."""
    if is_legacy(session_dir):
        return []
    folder, renamed = video_dir(session_dir), []
    for old, new in PROMPT_FILES.items():
        src = os.path.join(folder, old)
        if os.path.exists(src):
            os.replace(src, os.path.join(folder, new))
            renamed.append(new)
    return renamed


# --------------------------------------------------------------------------
# Locating sessions from checkpoints
# --------------------------------------------------------------------------
CHECKPOINT_PATTERNS = (
    ("research", os.path.join("*", RESEARCH_DIR, LOGS_DIR, "research_checkpoint.json")),
    ("video", os.path.join("*", VIDEO_DIR, LOGS_DIR, "checkpoint.json")),
    ("research", os.path.join("*", "research", "research_checkpoint.json")),     # legacy
    ("video", os.path.join("*", "checkpoint.json")),                             # legacy
)


def session_checkpoints(session_dir: str) -> list:
    """[(kind, path)] of the checkpoints that exist in a session, research first."""
    found = []
    for kind, rel in (("research", os.path.join(RESEARCH_DIR, LOGS_DIR, "research_checkpoint.json")),
                      ("research", os.path.join("research", "research_checkpoint.json")),
                      ("video", os.path.join(VIDEO_DIR, LOGS_DIR, "checkpoint.json")),
                      ("video", "checkpoint.json")):
        path = os.path.join(session_dir, rel)
        if os.path.exists(path):
            found.append((kind, path))
    return found


def session_of_checkpoint(path: str) -> str:
    """The session folder a checkpoint belongs to ('' when the path is not a checkpoint)."""
    folder = os.path.dirname(os.path.abspath(path))
    base = os.path.basename(path)
    if base == "research_checkpoint.json":
        if os.path.basename(folder) == LOGS_DIR and os.path.basename(os.path.dirname(folder)) == RESEARCH_DIR:
            return os.path.dirname(os.path.dirname(folder))
        return os.path.dirname(folder)
    if base == "checkpoint.json":
        if os.path.basename(folder) == LOGS_DIR and os.path.basename(os.path.dirname(folder)) == VIDEO_DIR:
            return os.path.dirname(os.path.dirname(folder))
        return folder
    return ""


def checkpoint_kind(path: str) -> str:
    base = os.path.basename(path or "")
    return {"research_checkpoint.json": "research", "checkpoint.json": "video"}.get(base, "")


# --------------------------------------------------------------------------
# Keeping earlier output when a stage runs again
# --------------------------------------------------------------------------
def archive_stage(folder: str, keep_checkpoint: bool = False) -> str:
    """Moves a stage folder's files into <folder>/_previous_runs/<timestamp>/ so a re-run
    never overwrites what the creator already has. Returns the archive path ('' when the
    folder had nothing to keep).

    logs/ is moved with them for a fresh start (keep_checkpoint=False). A run continuing
    from a later step (keep_checkpoint=True) needs the checkpoint: logs/ is copied instead
    and only its model-answer cache moves, so the re-run asks the models again rather
    than replaying the earlier answers."""
    if not os.path.isdir(folder):
        return ""
    items = [n for n in os.listdir(folder) if n != PREVIOUS_DIR and not n.startswith(".")]
    if not items:
        return ""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(folder, PREVIOUS_DIR, stamp)
    os.makedirs(target, exist_ok=True)
    for name in items:
        src = os.path.join(folder, name)
        if name == LOGS_DIR and keep_checkpoint and os.path.isdir(src):
            shutil.copytree(src, os.path.join(target, name), ignore=shutil.ignore_patterns("llm_cache", "cache"))
            cache = os.path.join(src, "llm_cache")
            if os.path.isdir(cache):
                shutil.move(cache, os.path.join(target, name, "llm_cache"))
            continue
        shutil.move(src, os.path.join(target, name))
    return target


# --------------------------------------------------------------------------
# 00_START_HERE.md
# --------------------------------------------------------------------------
_DESCRIPTIONS = {
    RESEARCH_DIR: [
        (PATIENT_GUIDE, "The patient guide: what the video will be built from (read this first)."),
        (EVIDENCE_LIBRARY, "The evidence library: every verified fact with its source [S#] and the reference list."),
        (LOGS_DIR, "Machine files: research state, checkpoint, step log, caches, earlier review rounds."),
    ],
    REVIEW_DIR: [
        (REVIEW_FILE, "Your review. Write what to ADD / CHANGE / REMOVE (a new research round runs), "
                      "or APPROVED: yes when the research is right."),
        (VIDEO_SOURCE_FILE, "The exact text the script workflow is written from. Edit it freely, or replace it "
                            "with your own final review (or use --source)."),
    ],
    VIDEO_DIR: [
        (FINAL_SCRIPT, "The full video package in editing order: titles & thumbnails, script, production "
                       "tables, storyboard (open the .pdf or .docx)."),
        (SHORTS_PACKAGE, "Reels & Shorts: chosen moments, scripts and timed captions."),
        ("03_broll_images.txt", "B-roll still-image prompts for Google Flow (one per blank-line block)."),
        ("04_broll_videos.txt", "B-roll video prompts for Google Flow."),
        ("05_whiteboard_images.txt", "Whiteboard drawings, finished (generate these first)."),
        ("06_whiteboard_videos.txt", "Whiteboard draw-on videos: from a blank board (start frame) to the matching "
                                     "drawing (end frame)."),
        ("07_broll_manifest", "Every B-roll image, video and whiteboard with its number, time and cue — what "
                              "`python3 main.py --broll <session>` generates into 4_broll/."),
        (LOGS_DIR, "Machine files: checkpoint (every step's output), session log, model report, sources map, cache."),
    ],
}

_NEXT_STEPS = {
    RESEARCH_DIR: "Next: open 2_review/ and review the research.",
    REVIEW_DIR: ("Next: edit 01_review.md, then run\n\n    python3 main.py --continue {session}\n\n"
                 "When it says APPROVED: yes, the script workflow starts from 02_video_source.md. "
                 "To start it yourself (also after editing 02_video_source.md outside the workflow):\n\n"
                 "    python3 main.py --from script {session}\n    python3 main.py --from script {session} "
                 "--source path/to/your_final_review.md"),
    VIDEO_DIR: ("Re-run one part in this same session (earlier output moves to _previous_runs/):\n\n"
                "    python3 main.py --from packaging {session}    # new titles & thumbnails\n"
                "    python3 main.py --from production {session}   # new overlays, B-roll, transitions\n"
                "    python3 main.py --from final {session}        # rebuild the final package and shorts\n"
                "    python3 main.py --from export {session}       # only re-export the files\n\n"
                "A run that stopped continues with: python3 main.py --resume {session}"),
}

_TITLES = {RESEARCH_DIR: "Research", REVIEW_DIR: "Review — the input to the script workflow",
           VIDEO_DIR: "Video package"}


def write_start_here(session_dir: str, stage: str, session_label: str = "") -> str:
    """Writes <stage>/00_START_HERE.md: the files present, in reading order, with what each
    one is, and the commands for the next step. Returns its path ('' when legacy)."""
    if is_legacy(session_dir):
        return ""
    folder = os.path.join(session_dir, stage)
    if not os.path.isdir(folder):
        return ""
    present = sorted(n for n in os.listdir(folder) if n != START_HERE and not n.startswith("."))
    lines = [f"# {_TITLES.get(stage, stage)}", "",
             f"Session: `{session_label or os.path.basename(session_dir.rstrip(os.sep))}`", "",
             "Read the files from top to bottom:", ""]
    for stem, what in _DESCRIPTIONS.get(stage, []):
        files = [n for n in present if n == stem or os.path.splitext(n)[0] == stem]
        if not files:
            continue
        shown = ", ".join(f"`{n}`" for n in files) if stem != LOGS_DIR else f"`{LOGS_DIR}/`"
        lines.append(f"- {shown} — {what}")
    if PREVIOUS_DIR in present:
        lines.append(f"- `{PREVIOUS_DIR}/` — earlier runs of this stage, by date (nothing is overwritten).")
    lines += ["", _NEXT_STEPS.get(stage, "").format(session=session_label or session_dir), ""]
    path = os.path.join(folder, START_HERE)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# --------------------------------------------------------------------------
# Moving an older session to this layout
# --------------------------------------------------------------------------
_EXPORT_EXTS = (".md", ".html", ".pdf", ".docx")
_VIDEO_LOG_FILES = ("checkpoint.json", "session_log.jsonl", "llm_provider.json", "sources_map.json")


def migrate(session_dir: str) -> list:
    """Moves a session made in the older layout (research/ + video files in the root) into
    1_research/ · 2_review/ · 3_video/ with numbered reader files and logs/ folders.
    Nothing is deleted. Returns a list of "old → new" lines ([] when nothing to move)."""
    if not is_legacy(session_dir):
        return []
    moves = []

    def move(src, dst):
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            moves.append(f"{os.path.relpath(src, session_dir)} → {os.path.relpath(dst, session_dir)}")

    old_research = os.path.join(session_dir, "research")
    if os.path.isdir(old_research):
        new_research = os.path.join(session_dir, RESEARCH_DIR)
        logs = os.path.join(new_research, LOGS_DIR)
        review = os.path.join(session_dir, REVIEW_DIR)
        os.makedirs(logs, exist_ok=True)
        for stem, numbered in (("patient_guide", PATIENT_GUIDE), ("dossier", EVIDENCE_LIBRARY)):
            src_md = os.path.join(old_research, stem + ".md")
            if os.path.exists(src_md):          # the machine copy stays in logs/
                shutil.copyfile(src_md, os.path.join(logs, stem + ".md"))
            for ext in _EXPORT_EXTS:
                move(os.path.join(old_research, stem + ext), os.path.join(new_research, numbered + ext))
        move(os.path.join(old_research, "review.md"), os.path.join(review, REVIEW_FILE))
        move(os.path.join(old_research, "video_source.md"), os.path.join(review, VIDEO_SOURCE_FILE))
        if not os.path.exists(os.path.join(review, VIDEO_SOURCE_FILE)):   # its older name
            move(os.path.join(old_research, "final_patient_guide.md"), os.path.join(review, VIDEO_SOURCE_FILE))
        for name in sorted(os.listdir(old_research)):
            move(os.path.join(old_research, name), os.path.join(logs, name))
        try:
            os.rmdir(old_research)
        except OSError:
            pass
    video_files = [n for n in os.listdir(session_dir)
                   if os.path.isfile(os.path.join(session_dir, n)) or n == "llm_cache"]
    if video_files:
        video = os.path.join(session_dir, VIDEO_DIR)
        logs = os.path.join(video, LOGS_DIR)
        for stem, numbered in (("final_script", FINAL_SCRIPT), ("shorts_package", SHORTS_PACKAGE)):
            for ext in _EXPORT_EXTS:
                move(os.path.join(session_dir, stem + ext), os.path.join(video, numbered + ext))
        for old, new in PROMPT_FILES.items():
            move(os.path.join(session_dir, old), os.path.join(video, new))
        for name in _VIDEO_LOG_FILES + ("llm_cache",):
            move(os.path.join(session_dir, name), os.path.join(logs, name))
        for name in sorted(os.listdir(session_dir)):     # anything else a run left in the root
            path = os.path.join(session_dir, name)
            if os.path.isfile(path) and not name.startswith("."):
                move(path, os.path.join(logs, name))
    label = os.path.basename(session_dir.rstrip(os.sep))
    for stage in (RESEARCH_DIR, REVIEW_DIR, VIDEO_DIR):
        write_start_here(session_dir, stage, label)
    return moves
