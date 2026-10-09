"""Real Google Chrome over the DevTools Protocol (CDP), for the pages that refuse
plain HTTP clients: mayoclinic.org (Akamai), hopkinsmedicine.org (Cloudflare) and
Google result pages all block non-browser requests *and* headless Chrome, but load
fine in a normal headful window parked off-screen. No stealth or anti-detection
tricks — just the user's own Chrome.

`websockets` is the only dependency (its sync client); every failure is a
RuntimeError so the research collector can fall back to the plain HTTP sources.
"""
import atexit
import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request

from websockets.sync.client import connect

from ...exports.convert import find_chrome_executable

DEFAULT_PROFILE = os.path.expanduser("~/.cache/medical_brain/chrome-profile")


class Blocked(Exception):
    """A page we loaded but a bot-shield stopped: .kind is 'captcha' or 'blocked'."""

    def __init__(self, kind: str, url: str = ""):
        self.kind = kind
        self.url = url
        super().__init__(f"{kind}: {url or '?'}")


def page_status(title: str, text: str, url: str) -> str:
    """Classify a loaded page as 'ok', 'captcha' (Google bot check) or 'blocked' (WAF)."""
    low_text = (text or "").lower()
    low_title = (title or "").lower()
    low_url = (url or "").lower()
    if "google." in low_url and ("/sorry/" in low_url or "unusual traffic" in low_text or "not a robot" in low_text):
        return "captcha"
    markers = ("access denied", "attention required", "just a moment",
               "sorry, you have been blocked", "you don't have permission to access")
    if any(marker in low_title for marker in markers):
        return "blocked"
    # Body markers only count on a small page: bot-shields are tiny, whereas a long
    # medical article may innocently contain phrases like "just a moment".
    if len(low_text) < 3000 and any(marker in low_text for marker in markers):
        return "blocked"
    return "ok"


def parse_devtools_port(content: str):
    """First line of a DevToolsActivePort file is the port Chrome chose (or None)."""
    for line in (content or "").splitlines():
        line = line.strip()
        if line:
            try:
                return int(line)
            except ValueError:
                return None
    return None


class _CDP:
    """One DevTools websocket with id-matched request/response (events are skipped)."""

    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self._lock = threading.Lock()   # websockets' sync client is not thread-safe

    def call(self, method: str, params: dict = None, timeout: float = 25.0) -> dict:
        deadline = time.monotonic() + timeout
        with self._lock:
            self._id += 1
            mid = self._id
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                raw = self.ws.recv(timeout=max(0.1, deadline - time.monotonic()))
                if raw is None:
                    raise RuntimeError("Chrome closed the DevTools connection")
                msg = json.loads(raw)
                if msg.get("id") != mid:
                    continue            # a protocol event while we wait for the reply
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})

    def wait_event(self, method: str, timeout: float) -> bool:
        """Consume messages until `method` arrives; False on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            raw = self.ws.recv(timeout=remaining)
            if raw is None:
                return False
            if json.loads(raw).get("method") == method:
                return True

    def close(self) -> None:
        try:
            self.ws.close(timeout=2)
        except Exception:
            pass


_SNAPSHOT = ("({url: location.href, title: document.title, "
             "text: document.body ? document.body.innerText : '', "
             "html: document.documentElement ? document.documentElement.outerHTML : ''})")


class Browser:
    """A single persistent Chrome, shared by threads; each open() is its own tab."""

    def __init__(self, profile_dir: str = None, offscreen: bool = True, max_tabs: int = 3):
        self.profile_dir = profile_dir or DEFAULT_PROFILE
        self.offscreen = offscreen
        self.port = None
        self._proc = None
        self._browser = None
        self._targets = set()
        self._shown = set()                          # CAPTCHA tabs kept open for the user
        self._state_lock = threading.RLock()         # reentrant: start() closes on failure
        self._tabs_lock = threading.Lock()           # guards _targets
        self._sem = threading.Semaphore(max_tabs)    # at most `max_tabs` tabs at once

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        with self._state_lock:
            if self._proc is not None:
                return
            chrome = find_chrome_executable()
            if not chrome:
                raise RuntimeError("Google Chrome was not found; install it or add chrome to PATH")
            os.makedirs(self.profile_dir, exist_ok=True)
            port_file = os.path.join(self.profile_dir, "DevToolsActivePort")
            self._remove(port_file)                  # a stale file would make us read an old port

            # Never use --remote-debugging-port=0. In that mode Chrome advertises its port
            # over a pipe/DevToolsActivePort file, and that launch mode makes Google answer
            # *every* search with the "unusual traffic" captcha (reproduced 100%); passing a
            # concrete loopback port instead gets normal result pages (same profile/flags
            # otherwise). We pick a free port ourselves and pass it explicitly.
            port = self._free_port()
            args = [chrome, f"--remote-debugging-port={port}", f"--user-data-dir={self.profile_dir}",
                    "--no-first-run", "--no-default-browser-check", "--window-size=1200,900"]
            args.append("--window-position=-2400,-2400" if self.offscreen else "--window-position=80,80")
            args.append("about:blank")               # headful: headless is blocked by all three sites
            stderr_path = os.path.join(self.profile_dir, ".chrome-stderr.log")
            with open(stderr_path, "wb") as err:
                self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=err,
                                              start_new_session=True)

            actual = self._wait_for_port(port, port_file, timeout=15)
            if not actual:
                self.close()
                raise RuntimeError(self._startup_error())

            self.port = actual
            ws_url = self._connect_browser_ws(actual)
            self._browser = _CDP(connect(ws_url, max_size=None, proxy=None))

    @staticmethod
    def _free_port() -> int:
        """A free loopback port for Chrome's DevTools endpoint (we never pass 0)."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @staticmethod
    def _read_port_file(port_file: str):
        try:
            with open(port_file, encoding="utf-8") as f:
                return parse_devtools_port(f.read())
        except OSError:
            return None

    def _port_ready(self, port: int) -> bool:
        try:
            self._http(port, "/json/version")
            return True
        except Exception:
            return False

    def _wait_for_port(self, port: int, port_file: str, timeout: float):
        """Wait for the port we chose to answer; fall back to the port Chrome wrote to disk."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                return None
            if self._port_ready(port):
                return port
            written = self._read_port_file(port_file)          # confirmation / fallback
            if written and written != port and self._port_ready(written):
                return written
            time.sleep(0.2)
        written = self._read_port_file(port_file)
        return written if written and self._port_ready(written) else None

    def _connect_browser_ws(self, port: int) -> str:
        """Wait for the HTTP endpoint, then return the browser-level websocket URL."""
        deadline = time.monotonic() + 10
        last = None
        while time.monotonic() < deadline:
            try:
                data = json.loads(self._http(port, "/json/version"))
                ws_url = data.get("webSocketDebuggerUrl")
                if ws_url:
                    return ws_url
            except Exception as e:
                last = e
            time.sleep(0.2)
        raise RuntimeError(f"Chrome started on port {port} but its DevTools endpoint refused connections: {last}")

    def _startup_error(self) -> str:
        msg = "Chrome did not expose a DevTools port within 15s."
        if os.path.lexists(os.path.join(self.profile_dir, "SingletonLock")):
            msg += (f" The profile {self.profile_dir} is locked by an already-running Chrome instance "
                    "launched earlier; close it or delete its SingletonLock.")
        tail = self._stderr_tail()
        if tail:
            msg += f" Chrome stderr: {tail}"
        return msg

    def _stderr_tail(self) -> str:
        try:
            with open(os.path.join(self.profile_dir, ".chrome-stderr.log"), "rb") as f:
                return f.read()[-400:].decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def close(self) -> None:
        with self._state_lock:
            proc, self._proc = self._proc, None
            browser, self._browser = self._browser, None
            self.port = None
        with self._tabs_lock:
            self._shown.clear()
        if browser:
            browser.close()
        if proc:
            self._terminate(proc)
        self._remove(os.path.join(self.profile_dir, "DevToolsActivePort"))

    @staticmethod
    def _terminate(proc) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)   # Chrome forks renderer helpers
            else:
                proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            except Exception:
                pass

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    # -- pages ---------------------------------------------------------------
    def open(self, url: str, wait: float = 6.0) -> dict:
        """Load `url` in a fresh tab and return its final url/title/html/text/status."""
        if self._proc is None:
            raise RuntimeError("Browser.start() must be called before open()")
        with self._sem:                                       # cap concurrent tabs
            target_id = None
            page = None
            try:
                target_id = self._browser.call("Target.createTarget", {"url": "about:blank"})["targetId"]
                with self._tabs_lock:
                    self._targets.add(target_id)
                page = _CDP(connect(self._page_ws(target_id), max_size=None, proxy=None))
                page.call("Page.enable")
                page.call("Page.navigate", {"url": url})
                page.wait_event("Page.loadEventFired", timeout=25)   # best-effort, then settle
                if wait:
                    time.sleep(wait)
                value = page.call("Runtime.evaluate",
                                  {"expression": _SNAPSHOT, "returnByValue": True})["result"].get("value") or {}
                title = value.get("title") or ""
                text = value.get("text") or ""
                final_url = value.get("url") or url
                return {"url": final_url, "title": title, "html": value.get("html") or "",
                        "text": text, "status": page_status(title, text, final_url)}
            finally:
                if page:
                    page.close()
                if target_id:
                    with self._tabs_lock:
                        self._targets.discard(target_id)
                    try:
                        self._browser.call("Target.closeTarget", {"targetId": target_id})
                    except Exception:
                        pass

    def _page_ws(self, target_id: str) -> str:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for item in json.loads(self._http(self.port, "/json/list")):
                if item.get("id") == target_id and item.get("webSocketDebuggerUrl"):
                    return item["webSocketDebuggerUrl"]
            time.sleep(0.1)
        raise RuntimeError(f"no DevTools websocket for tab {target_id}")

    # -- window position (lets the user answer a Google CAPTCHA) -------------
    def show(self, url: str) -> None:
        """Open `url` in a tab kept open and bring the window on-screen so the user can solve
        a CAPTCHA. Never raises."""
        try:
            if self._browser is None:
                return
            target_id = self._browser.call("Target.createTarget", {"url": url})["targetId"]
            with self._tabs_lock:
                self._shown.add(target_id)
            try:
                self._browser.call("Target.activateTarget", {"targetId": target_id})
            except Exception:
                pass
            self._move_window(target_id, 80, 80)
        except Exception:
            pass

    def hide(self) -> None:
        """Close the CAPTCHA tabs and park the window off-screen again. Never raises."""
        try:
            with self._tabs_lock:
                shown = list(self._shown)
                self._shown.clear()
            for target_id in shown:
                try:
                    self._browser.call("Target.closeTarget", {"targetId": target_id})
                except Exception:
                    pass
            if self._browser is None:
                return
            # The initial about:blank tab stays, so use it to find the window again.
            for item in json.loads(self._http(self.port, "/json/list")):
                if item.get("type") == "page" and item.get("id"):
                    self._move_window(item["id"], -2400, -2400)
                    break
        except Exception:
            pass

    def _move_window(self, target_id: str, left: int, top: int) -> None:
        try:
            window = self._browser.call("Browser.getWindowForTarget", {"targetId": target_id})["windowId"]
            self._browser.call("Browser.setWindowBounds", {
                "windowId": window, "bounds": {"left": left, "top": top, "windowState": "normal"}})
        except Exception:
            pass

    @staticmethod
    def _http(port: int, path: str) -> str:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.read().decode("utf-8", "replace")


# -- shared instance ---------------------------------------------------------
_shared = None
_shared_lock = threading.Lock()


def get_browser() -> Browser:
    """One lazily started Browser for the whole process, closed at exit."""
    global _shared
    with _shared_lock:
        if _shared is None:
            browser = Browser()
            browser.start()
            atexit.register(browser.close)
            _shared = browser
        return _shared


def available() -> bool:
    """True when a real Chrome is installed (the research can then use it)."""
    return find_chrome_executable() is not None
