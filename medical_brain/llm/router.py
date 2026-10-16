"""LLMRouter: picks the model for each step (main / single / hybrid) and falls back on failure."""
import hashlib
import json
import os
import tempfile
import threading
from typing import Optional

from .backends import make_backend
from .checks import leak_check, test_model
from .config import MODE_ALIASES, MODES, parse_spec
from .errors import AnswerCutOff, ProviderUnavailable, StepFailed, _short
from .progress import _node_state, current_node


def _cache_key(spec: str, system_prompt, user_prompt, temperature, max_tokens) -> str:
    """One key per exact request: the model, the two prompts and the sampling settings."""
    payload = json.dumps([spec, system_prompt, user_prompt, temperature, max_tokens],
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLMRouter:
    def __init__(self, config: dict, provider: Optional[str] = None):
        self.config = config
        mode = (provider or os.environ.get("LLM_PROVIDER") or config["provider"]).lower()
        mode = MODE_ALIASES.get(mode, mode)
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}' (use main, single or hybrid)")
        self.mode = mode
        self.main = config["main_model"]
        self.backup = config.get("backup_model") or ""
        if self.backup == self.main:
            self.backup = ""
        self.routes = dict(config["hybrid_routes"])
        self.down = {}          # provider -> reason (auth/billing/quota), for the rest of the run
        self.down_specs = {}    # provider:model -> reason (e.g. unknown model), for the rest of the run
        self.events = []
        self.calls = {}
        self.node_models = {}
        self._backends = {}
        self._semaphores = {}           # provider -> BoundedSemaphore (lazy, only if max_concurrency set)
        self._lock = threading.Lock()   # guards the book-keeping dicts and lazy backends
        self.cache_dir = None           # optional: a folder of one JSON file per answered request
        self.cache_hits = 0             # answers served from that folder instead of the model
        self._last_key = threading.local()  # cache entry behind this thread's last answer

    def set_cache_dir(self, path: Optional[str]) -> None:
        """Remember every successful model answer under `path` (one JSON file per request),
        so a resumed run reuses the answers it already paid for. None turns the cache off."""
        self.cache_dir = path or None
        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)

    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, key + ".json")

    def _cache_get(self, key: str) -> Optional[str]:
        try:
            with open(self._cache_path(key), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        answer = data.get("answer") if isinstance(data, dict) else data
        if isinstance(answer, str) and answer.strip():
            with self._lock:                # a hit still lands in report()
                self.cache_hits += 1
            return answer
        return None

    def forget_last(self) -> None:
        """Drop the cached answer this thread just received — called when it turned out to
        be unusable (e.g. broken JSON), so a retry asks the model again instead of being
        served the same broken answer."""
        key = getattr(self._last_key, "value", "")
        if key and self.cache_dir:
            try:
                os.remove(self._cache_path(key))
            except OSError:
                pass

    def _cache_put(self, key: str, answer: str) -> None:
        """Write to a temp file then os.replace: parallel calls never see a half-written file."""
        if not self.cache_dir:
            return
        tmp = ""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)   # also when cache_dir was set directly
            fd, tmp = tempfile.mkstemp(dir=self.cache_dir, prefix=key + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"answer": answer}, f, ensure_ascii=False)
            os.replace(tmp, self._cache_path(key))
        except OSError:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def model_for(self, node: Optional[str]) -> str:
        if self.mode == "single":
            return self.config["single_model"]
        if self.mode == "hybrid":
            return self.routes.get(node, self.main)
        return self.main

    def models_in_use(self) -> list:
        if self.mode == "single":
            return [self.config["single_model"], self.main] + ([self.backup] if self.backup else [])
        extra = [self.backup] if self.backup else []
        if self.mode == "hybrid":
            return [self.main] + sorted(set(self.routes.values()) - {self.main}) + extra
        return [self.main] + extra

    def describe(self) -> str:
        if self.mode == "main":
            return f"all nodes on {self.main}"
        if self.mode == "single":
            return f"all nodes on {self.config['single_model']} (falls back to {self.main})"
        routed = ", ".join(f"{n} → {m}" for n, m in self.routes.items())
        backup = f"; backup {self.backup}" if self.backup else ""
        return f"{self.main} writes; {routed}{backup}"

    def _is_down(self, spec: str) -> bool:
        with self._lock:
            return spec in self.down_specs or parse_spec(spec)[0] in self.down

    def _ask(self, spec: str, system_prompt, user_prompt, temperature, max_tokens) -> str:
        cache_key = _cache_key(spec, system_prompt, user_prompt, temperature, max_tokens) if self.cache_dir else ""
        self._last_key.value = cache_key
        if cache_key:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached
        backend = self._backend(spec)
        # Cap how many calls to one provider run at once (0/absent = unlimited). The
        # wait happens before backend.call(), so a queued call's step time-out only
        # starts once it actually runs. Nothing is printed per call, even when waiting.
        sem = self._semaphore(parse_spec(spec)[0])
        if sem is None:
            result = backend.call(system_prompt, user_prompt, temperature, max_tokens)
        else:
            with sem:
                result = backend.call(system_prompt, user_prompt, temperature, max_tokens)
        if not (result or "").strip():
            raise StepFailed(f"{spec} returned an empty answer")
        if cache_key:                       # only a complete, non-empty answer is remembered
            self._cache_put(cache_key, result)
        return result

    MAIN_RETRY_WAITS = (10, 30)     # seconds before the 2nd and 3rd try of the main model

    def _ask_retrying(self, node, spec, system_prompt, user_prompt, temperature, max_tokens) -> str:
        """The main model gets a couple more tries for transient failures (an empty
        answer, a time-out, a server error) before the step falls back or stops: free
        web proxies fail now and then, and one blip shouldn't end a 30-minute run.
        A missing key / unknown model (ProviderUnavailable) or a cut-off answer is not retried."""
        import time
        for wait in (*self.MAIN_RETRY_WAITS, None):
            try:
                return self._ask(spec, system_prompt, user_prompt, temperature, max_tokens)
            except (ProviderUnavailable, AnswerCutOff):
                raise
            except Exception as e:
                if wait is None:
                    raise
                self._log(f"⏳ {node}: {spec} failed ({_short(e)}); trying again in {wait}s.")
                time.sleep(wait)

    def _semaphore(self, provider: str):
        """The provider's concurrency semaphore, or None when it is unlimited.
        Created lazily under the lock so parallel first-calls share one."""
        cfg = (self.config.get("providers") or {}).get(provider) or {}
        try:
            limit = int(cfg.get("max_concurrency") or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit < 1:
            return None
        with self._lock:
            sem = self._semaphores.get(provider)
            if sem is None:
                sem = self._semaphores[provider] = threading.BoundedSemaphore(limit)
            return sem

    def _backend(self, spec: str):
        with self._lock:               # two steps may need the same backend at once
            backend = self._backends.get(spec)
            if backend is None:
                backend = self._backends[spec] = make_backend(spec, self.config["providers"])
            return backend

    def _log(self, msg: str):
        with self._lock:               # keep whole event lines from interleaving
            print(f"🔀 [LLM] {msg}")
            self.events.append(msg)

    def _leak_check(self, spec: str) -> tuple:
        """(ok, detail) for a spec whose provider asked for the memory/leak check: run it
        after the ping. A provider that remembers earlier requests is taken out of the
        run, exactly like a model that failed its ping (the run falls back)."""
        provider, model = parse_spec(spec)
        cfg = (self.config.get("providers") or {}).get(provider) or {}
        if not cfg.get("leak_check"):
            return True, ""
        leaked, message = leak_check(provider, cfg, model)
        if leaked is True:
            self._log(f"❌ {spec}: remembers earlier requests — not used this run")
            return False, message
        if leaked is None:
            self._log(f"⚠️ {spec}: {message}")
        return True, ""

    def select(self, ask=None):
        """Quick check of every model this run will use. Never blocks: a model
        that fails here is simply skipped and its steps run on the main model."""
        self._log(f"Mode: {self.mode} · {self.describe()}")
        for spec in self.models_in_use():
            if self._is_down(spec):
                continue
            ok, detail = test_model(spec, self.config["providers"])
            if ok:
                # The ping answered; a provider that must start a fresh conversation
                # is checked for memory before it is trusted with the whole run.
                ok, detail = self._leak_check(spec)
            if ok:
                self._log(f"✅ {spec}")
            elif spec == self.main:
                if not self.backup and self.mode != "single":
                    # Every step needs the main model sooner or later: stop now, not at step 1.
                    raise ProviderUnavailable(f"main model {spec} did not answer: {detail}")
                then = f"steps will run on the backup model {self.backup}" if self.backup else "they stop the run"
                self._log(f"⚠️ main model {spec} did not answer ({detail}). Each step will still try it first; "
                          f"if it fails, {then}.")
            else:
                # Only this model is taken out: another model of the same provider
                # (another judge, or the backup) may still work.
                with self._lock:
                    self.down_specs[spec] = detail
                self._log(f"⚠️ {spec} unavailable ({detail}). Its steps will run on {self.main}.")

    def _count(self, node, spec):
        with self._lock:               # read-modify-write from parallel calls
            self.calls[spec] = self.calls.get(spec, 0) + 1
            if node:
                self.node_models[node] = spec

    def call(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
        node = current_node.get()
        spec = self.model_for(node)
        cut_text = None   # best cut-off answer, used only if every model fails
        state = _node_state.get()
        if node and state is not None and not state["announced"]:
            state["announced"] = True
            target = spec if not self._is_down(spec) else self.main
            print(f"▶ {node} … ({target})", flush=True)
        if spec != self.main:
            provider = parse_spec(spec)[0]
            if not self._is_down(spec):
                try:
                    result = self._ask(spec, system_prompt, user_prompt, temperature, max_tokens)
                    self._count(node, spec)
                    return result
                except ProviderUnavailable as e:
                    # When the routed model shares the main model's provider (e.g. two agy
                    # models), only that model is benched: quotas there are per model, and
                    # benching the provider would take the main model down with it.
                    with self._lock:
                        if provider == parse_spec(self.main)[0]:
                            self.down_specs[spec] = _short(e)
                        else:
                            self.down[provider] = _short(e)
                    self._log(f"⚠️ {node}: {spec} unavailable ({_short(e)}). "
                              f"This and later steps routed to it run on {self.main}.")
                except Exception as e:
                    if isinstance(e, AnswerCutOff):
                        cut_text = e.text
                    self._log(f"⚠️ {node}: {spec} failed ({_short(e)}). Running this step on {self.main}.")
        try:
            result = self._ask_retrying(node, self.main, system_prompt, user_prompt, temperature, max_tokens)
            self._count(node, self.main)
            return result
        except Exception as e:
            if isinstance(e, AnswerCutOff) and not cut_text:
                cut_text = e.text
            if isinstance(e, ProviderUnavailable):
                with self._lock:
                    self.down[parse_spec(self.main)[0]] = _short(e)
            if not self.backup or self._is_down(self.backup):
                if cut_text:
                    self._log(f"⚠️ {node}: no model gave a complete answer; using the cut-off one.")
                    return cut_text
                raise
            self._log(f"⚠️ {node}: main model {self.main} failed ({_short(e)}). Running this step on the backup {self.backup}.")
        try:
            result = self._ask(self.backup, system_prompt, user_prompt, temperature, max_tokens)
        except Exception as e:
            if isinstance(e, AnswerCutOff):
                cut_text = cut_text or e.text
            if cut_text:
                self._log(f"⚠️ {node}: backup failed too ({_short(e)}); using the cut-off answer.")
                return cut_text
            raise
        self._count(node, self.backup)
        return result

    def model_for_node(self, node: str) -> Optional[str]:
        return self.node_models.get(node)

    def report(self) -> dict:
        with self._lock:               # a consistent snapshot even if a call is still landing
            return {
                "mode": self.mode,
                "setup": self.describe(),
                "calls": dict(self.calls),
                "cache_hits": self.cache_hits,
                "node_models": dict(self.node_models),
                "unavailable": {**self.down, **self.down_specs},
                "events": list(self.events),
            }
