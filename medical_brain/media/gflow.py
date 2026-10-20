"""A thin wrapper around the gflow-cli command line (github.com/ffroliva/gflow-cli).

gflow drives the user's own Google Flow account through a real Chrome window. It is called
as a subprocess (its own uv tool environment, so its Playwright pin never meets this
project's), always with --json, and every call's stdout/stderr is kept in logs/gflow/.

Exit codes are what matter (docs/USAGE.md § Exit codes in gflow-cli):
  STOP  — retrying makes it worse or cannot help: auth, quota, WAF block, credits, no Flow
          access, UI drift… the whole run stops and says what to do.
  SKIP  — this one item cannot be made as asked (content policy, unsupported form).
  RETRY — transient (network, timeout, unparsable reply, Flow error page): try again once.
"""
import glob
import json
import os
import re
import subprocess
import time

STOP_CODES = {3: "the Flow session was rejected — run: gflow auth login --browser chrome",
              4: "Flow quota / rate limit reached — wait (images: daily quota per model) and run again",
              8: "the gflow profile has no saved login — run: gflow auth login --browser chrome",
              10: "Flow's security layer blocked this profile — stop for 30–60 min before trying again",
              11: "gflow configuration error (see the log)",
              12: "the sign-in window was not completed — run: gflow auth login --browser chrome",
              14: "Google rejected the automation browser — run: gflow auth login --browser chrome",
              21: "Flow model quota reached — wait and run again",
              23: "Flow's page changed and gflow could not find a control — update gflow: gflow update",
              25: "Flow's agent-mode page is active for this account — open Flow once in Chrome and switch to the classic editor",
              28: "the classic Flow editor could not be reached — run again later",
              37: "not enough Flow credits for this model — top up or wait for the monthly allowance",
              38: "Google's account chooser needs you — run: gflow auth login --browser chrome",
              39: "this Google account has no Flow access"}
SKIP_CODES = {5: "Flow's content policy rejected the prompt", 27: "Flow refused the uploaded frame",
              32: "a reference image could not be attached", 36: "this request form is not supported on your Flow host"}
RETRY_CODES = {1, 6, 7, 9, 26, 31}


class GflowStop(Exception):
    """The whole generation run must stop (see STOP_CODES)."""

    def __init__(self, rc: int, detail: str = ""):
        self.rc = rc
        super().__init__(f"gflow exit {rc}: {STOP_CODES.get(rc, 'stopped')}" + (f" ({detail})" if detail else ""))


class GflowFailed(Exception):
    """One request failed (after its retry); the run goes on with the next item."""

    def __init__(self, rc: int, detail: str = ""):
        self.rc = rc
        self.detail = detail
        reason = SKIP_CODES.get(rc, "failed")
        super().__init__(f"gflow exit {rc}: {reason}" + (f" — {detail}" if detail else ""))


def _problem_detail(data, stderr: str) -> str:
    if isinstance(data, dict):
        for key in ("detail", "title", "error_message"):
            if data.get(key):
                return str(data[key])[:300]
    found = re.findall(r'"detail": "([^"]{0,300})', stderr or "")
    return found[-1] if found else ""


class Gflow:
    def __init__(self, binary: str = "gflow", logs_dir: str = "", profile: str = "", sleep=time.sleep):
        self.binary = binary
        self.sleep = sleep
        self.logs_dir = logs_dir
        self.profile = profile
        self.calls = 0
        if logs_dir:
            os.makedirs(logs_dir, exist_ok=True)

    # ------------------------------------------------------------------ plumbing
    def run(self, args: list, tag: str, timeout: int = 1800) -> tuple:
        """(exit code, parsed JSON or {}). Never raises for a non-zero exit."""
        if self.profile and args and args[0] in ("image", "video", "project", "data", "auth", "credits"):
            args = [*args, "--profile", self.profile]
        env = dict(os.environ, GFLOW_CLI_UPDATE_CHECK="0")
        self.calls += 1
        try:
            proc = subprocess.run([self.binary, *args], capture_output=True, text=True, env=env, timeout=timeout)
            rc, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            rc, out, err = 9, exc.stdout or "", (exc.stderr or "") + f"\n(timed out after {timeout}s)"
            out = out.decode() if isinstance(out, bytes) else out
            err = err.decode() if isinstance(err, bytes) else err
        except FileNotFoundError:
            raise GflowStop(127, f"{self.binary} not found — install: uv tool install gflow-cli")
        if self.logs_dir:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            base = os.path.join(self.logs_dir, f"{stamp}_{self.calls:03d}_{tag}")
            with open(base + ".json", "w", encoding="utf-8") as f:
                json.dump({"args": [a if len(a) < 400 else a[:400] + "…" for a in args], "rc": rc,
                           "stdout": out[-20000:]}, f, ensure_ascii=False, indent=1)
            with open(base + ".stderr.log", "w", encoding="utf-8") as f:
                f.write(err[-200000:])
        try:
            data = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            data = {}
        if rc and not isinstance(data, dict):
            data = {}
        if rc:
            data = dict(data, _detail=_problem_detail(data, err))
        return rc, data

    def call(self, args: list, tag: str, retries: int = 1, retry_pause: float = 20.0) -> dict:
        """Runs a request; raises GflowStop / GflowFailed, retrying transient codes `retries` times."""
        for attempt in range(retries + 1):
            rc, data = self.run(args, tag)
            if rc == 0:
                return data
            if rc in STOP_CODES:
                raise GflowStop(rc, data.get("_detail", ""))
            if rc in RETRY_CODES and attempt < retries:
                self.sleep(retry_pause)
                continue
            raise GflowFailed(rc, data.get("_detail", ""))
        raise GflowFailed(-1)

    # ------------------------------------------------------------------ checks
    def version(self) -> str:
        try:
            proc = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=60,
                                  env=dict(os.environ, GFLOW_CLI_UPDATE_CHECK="0"))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def logged_in(self) -> bool:
        rc, _ = self.run(["auth", "status"], "auth_status", timeout=300)
        return rc == 0

    # ------------------------------------------------------------------ projects
    def create_project(self, name: str) -> str:
        data = self.call(["project", "create", "--name", name[:90], "--json"], "project_create")
        project = data.get("project_id") or ""
        if not project:
            raise GflowFailed(7, "project create returned no project id")
        return project

    # ------------------------------------------------------------------ images
    def t2i(self, prompt: str, *, model: str, aspect: str, count: int, project: str, out_dir: str,
            tag: str) -> list:
        """[(media id, local path)] for every image made."""
        data = self.call(["image", "t2i", prompt, "--model", model, "--aspect", aspect, "-n", str(count),
                          "--project", project, "--out", out_dir, "--json"], tag)
        return [(i["media_name"], i["local_path"]) for i in data.get("images", []) if i.get("local_path")]

    def i2i(self, prompt: str, ref: str, *, model: str, aspect: str, project: str, out_dir: str, tag: str) -> list:
        data = self.call(["image", "i2i", prompt, "--ref", ref, "--model", model, "--aspect", aspect, "-n", "1",
                          "--project", project, "--out", out_dir, "--json"], tag)
        return [(i["media_name"], i["local_path"]) for i in data.get("images", []) if i.get("local_path")]

    def upscale_image(self, media: str, *, scale: str, project: str, out_dir: str, tag: str) -> str:
        self.call(["image", "upscale", media, "--scale", scale, "--project", project, "--out", out_dir], tag,
                  retries=2, retry_pause=10)
        return _find_output(out_dir, media, scale)

    # ------------------------------------------------------------------ videos
    def video(self, mode: str, prompt: str, *, model: str, duration: str, aspect: str, resolution: str,
              project: str, out_dir: str, tag: str, initial_frame: str = "", end_frame: str = "") -> tuple:
        """(media id, local path) of ONE clip. gflow saves a single file per video call, so a
        "x4" request is four calls (see runner)."""
        args = ["video", mode, prompt, "--model", model, "--aspect", aspect, "--count", "1",
                "--duration", str(duration), "--project", project, "--out-dir", out_dir, "--json"]
        if resolution:
            args += ["--resolution", resolution]
        if initial_frame:
            args += ["--initial-frame", initial_frame]
        if end_frame:
            args += ["--end-frame", end_frame]
        rc, data = self.run(args, tag)
        if rc == 0 and data.get("local_path"):
            return data.get("media_id") or "", data["local_path"]
        if rc in STOP_CODES:
            raise GflowStop(rc, data.get("_detail", ""))
        # A paid request is never blindly re-sent: when Flow made the clip and only the
        # download failed, it is fetched from the catalog for free.
        if rc in SKIP_CODES:
            raise GflowFailed(rc, data.get("_detail", ""))
        return "", ""

    def recent_videos(self, project: str, since_iso: str) -> list:
        """Catalogued videos of `project` created at/after `since_iso` (newest first)."""
        rc, rows = self._data_list("videos")
        if rc:
            return []
        return [r for r in rows if r.get("project_id") == project and (r.get("created_at") or "") >= since_iso]

    def _data_list(self, what: str) -> tuple:
        env = dict(os.environ, GFLOW_CLI_UPDATE_CHECK="0")
        args = [self.binary, "data", "list", what, "--json", "--limit", "50"]
        if self.profile:
            args += ["--profile", self.profile]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 1, []
        rows = []
        for line in proc.stdout.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return proc.returncode, rows

    def download_video(self, media: str, out_dir: str, tag: str) -> str:
        data = self.call(["data", "download", media, "--out", out_dir, "--json"], tag, retries=2, retry_pause=10)
        path = data.get("path") or ""
        return os.path.abspath(path) if path else _find_output(out_dir, media, "")

    def upscale_video(self, media: str, *, scale: str, project: str, out_dir: str, tag: str) -> str:
        self.call(["video", "upscale", media, "--scale", scale, "--project", project, "--out", out_dir], tag,
                  retries=1, retry_pause=15)
        return _find_output(out_dir, media, scale)


def _find_output(out_dir: str, media: str, scale: str) -> str:
    """gflow nests outputs (<out>/images/<date>/<id>_2k.jpg); find the file by media id."""
    pattern = f"{media}_{scale}.*" if scale else f"{media}*"
    found = sorted(glob.glob(os.path.join(out_dir, "**", pattern), recursive=True), key=os.path.getmtime)
    return found[-1] if found else ""
