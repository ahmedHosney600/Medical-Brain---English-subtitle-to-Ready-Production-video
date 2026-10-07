"""Shared HTTP for the research sources: a polite User-Agent, time-outs, a couple of
retries and an on-disk cache (re-running a research round doesn't re-download).
Every failure is returned as None — a dead source never stops the research."""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

USER_AGENT = "MedicalBrainResearch/1.0 (educational medical video research)"
_cache_dir: Optional[str] = None
_last_call: dict = {}          # host -> time of last request (rate limiting)
log: list = []                 # (source, url, problem) for the run report


def set_cache_dir(path: Optional[str]) -> None:
    global _cache_dir
    _cache_dir = path
    if path:
        os.makedirs(path, exist_ok=True)


def _cache_path(key: str) -> Optional[str]:
    if not _cache_dir:
        return None
    return os.path.join(_cache_dir, hashlib.sha256(key.encode("utf-8")).hexdigest()[:32] + ".txt")


def _throttle(host: str, min_gap: float) -> None:
    wait = _last_call.get(host, 0) + min_gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None, data: Optional[dict] = None,
        timeout: float = 25, retries: int = 2, min_gap: float = 0.4, source: str = "") -> Optional[str]:
    """GET (or POST JSON when `data` is given) → response text, or None on failure."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    body = json.dumps(data).encode("utf-8") if data is not None else None
    key = url + ("\n" + body.decode("utf-8") if body else "")
    cached = _cache_path(key)
    if cached and os.path.exists(cached):
        with open(cached, encoding="utf-8") as f:
            return f.read()
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
    host = urllib.parse.urlparse(url).netloc
    problem = ""
    for attempt in range(retries + 1):
        _throttle(host, min_gap)
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if body is not None else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if cached:
                with open(cached, "w", encoding="utf-8") as f:
                    f.write(text)
            return text
        except urllib.error.HTTPError as e:
            problem = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:          # time-out, DNS, proxy, …
            problem = f"{type(e).__name__}: {e}"
        time.sleep(1.5 * (attempt + 1))
    log.append((source or host, url[:160], problem))
    return None


def get_json(url: str, **kw):
    text = get(url, **kw)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        log.append((kw.get("source") or url, url[:160], "not JSON"))
        return None
