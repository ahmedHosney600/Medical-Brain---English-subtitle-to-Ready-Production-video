"""LLMRouter: picks the model for each step (main / single / hybrid) and falls back on failure."""
import os
from typing import Optional

from .backends import make_backend
from .checks import test_model
from .config import MODE_ALIASES, MODES, parse_spec
from .errors import AnswerCutOff, ProviderUnavailable, _short
from .progress import _node_state, current_node


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
        self.down = {}          # provider -> reason, for the rest of the run
        self.events = []
        self.calls = {}
        self.node_models = {}
        self._backends = {}

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

    def _backend(self, spec: str):
        if spec not in self._backends:
            self._backends[spec] = make_backend(spec, self.config["providers"])
        return self._backends[spec]

    def _log(self, msg: str):
        print(f"🔀 [LLM] {msg}")
        self.events.append(msg)

    def select(self, ask=None):
        """Quick check of every model this run will use. Never blocks: a model
        that fails here is simply skipped and its steps run on the main model."""
        self._log(f"Mode: {self.mode} · {self.describe()}")
        for spec in self.models_in_use():
            provider = parse_spec(spec)[0]
            if provider in self.down:
                continue
            ok, detail = test_model(spec, self.config["providers"])
            if ok:
                self._log(f"✅ {spec}")
            elif spec == self.main:
                then = f"steps will run on the backup model {self.backup}" if self.backup else \
                    "the run will stop at the first step if it stays down (set a backup model in setup_models.py)"
                self._log(f"⚠️ main model {spec} did not answer ({detail}). Each step will still try it first; if it fails, {then}.")
            else:
                self.down[provider] = detail
                self._log(f"⚠️ {spec} unavailable ({detail}). Its steps will run on {self.main}.")

    def _count(self, node, spec):
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
            target = spec if parse_spec(spec)[0] not in self.down else self.main
            print(f"▶ {node} … ({target})", flush=True)
        if spec != self.main:
            provider = parse_spec(spec)[0]
            if provider not in self.down:
                try:
                    result = self._backend(spec).call(system_prompt, user_prompt, temperature, max_tokens)
                    self._count(node, spec)
                    return result
                except ProviderUnavailable as e:
                    self.down[provider] = _short(e)
                    self._log(f"⚠️ {node}: {spec} unavailable ({_short(e)}). "
                              f"This and later {provider} steps run on {self.main}.")
                except Exception as e:
                    if isinstance(e, AnswerCutOff):
                        cut_text = e.text
                    self._log(f"⚠️ {node}: {spec} failed ({_short(e)}). Running this step on {self.main}.")
        try:
            result = self._backend(self.main).call(system_prompt, user_prompt, temperature, max_tokens)
            self._count(node, self.main)
            return result
        except Exception as e:
            if isinstance(e, AnswerCutOff) and not cut_text:
                cut_text = e.text
            if not self.backup or parse_spec(self.backup)[0] in self.down:
                if cut_text:
                    self._log(f"⚠️ {node}: no model gave a complete answer; using the cut-off one.")
                    return cut_text
                raise
            self._log(f"⚠️ {node}: main model {self.main} failed ({_short(e)}). Running this step on the backup {self.backup}.")
        try:
            result = self._backend(self.backup).call(system_prompt, user_prompt, temperature, max_tokens)
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
        return {
            "mode": self.mode,
            "setup": self.describe(),
            "calls": dict(self.calls),
            "node_models": dict(self.node_models),
            "unavailable": dict(self.down),
            "events": list(self.events),
        }
