"""Model roles — one LLM per JOB, so models are interchangeable, upgradeable, and can
run side by side.

Agents never name a model; they ask for a *role* and the ``ModelManager`` resolves it:

    planner  — fast intent routing. In the hot path of EVERY utterance, so it stays
               pinned resident in VRAM (keep_alive=-1). Swapping it is gated by a
               self-test because the routing prompts are tuned to its behavior.
    reasoner — the never-dead-end backbone + conversation. Point it at a bigger model
               when VRAM allows.
    coder    — CLI-synthesis and programming agents. Specialist coder models are
               provably better here; loaded on demand, idles out of VRAM after use.
    writer   — documents, briefings, summaries.

Bindings live in ``~/.config/yggdrasil/models.json`` (user-owned, like config.json):
``{"roles": {"coder": "qwen2.5-coder:7b"}}``. An unbound role falls back to the default
model (YGGDRASIL_MODEL). ``get(role)`` returns a ``RoleProvider`` — a live proxy that
re-resolves the binding on every call, so "use X for coding" takes effect instantly,
no restart, and every agent holding the provider picks it up.

Ollama already serves several models concurrently (loading/unloading on demand); this
layer only decides WHICH model answers WHICH job. On small GPUs everything simply
collapses onto the one default model — same agents, same code, different hardware tier.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .llm import LLMProvider, LLMResponse, OllamaProvider

ROLES: dict[str, str] = {
    "planner": "routes your commands (fast, always loaded)",
    "reasoner": "reasoning, conversation, and working out hard requests",
    "coder": "writing programs and terminal commands",
    "writer": "writing documents and briefings",
    "vision": "looking at the screen and describing what's on it",
}

# Spoken names for roles -> canonical role. The orchestrator + ModelAgent share this.
ROLE_ALIASES: dict[str, str] = {
    "planner": "planner", "planning": "planner", "routing": "planner",
    "reasoner": "reasoner", "reasoning": "reasoner", "thinking": "reasoner",
    "conversation": "reasoner", "chat": "reasoner", "research": "reasoner",
    "coder": "coder", "coding": "coder", "code": "coder", "programming": "coder",
    "development": "coder", "python": "coder",
    "writer": "writer", "writing": "writer", "documents": "writer", "docs": "writer",
    "briefings": "writer",
    "vision": "vision", "sight": "vision", "seeing": "vision", "eyes": "vision",
    "screen": "vision", "visual": "vision",
}

# Known-good specialist suggestions by VRAM floor (MiB), per role. Mirrors the spirit of
# llm.MODEL_TIERS. Tags are [VERIFY] at release time — the local-model landscape moves fast.
SUGGESTED: dict[str, list[tuple[int, str]]] = {
    "coder": [
        (24000, "qwen2.5-coder:32b"),
        (12000, "qwen2.5-coder:14b"),
        (6000, "qwen2.5-coder:7b"),
        (0, "qwen2.5-coder:3b"),
    ],
    # vision needs a MULTIMODAL model (the general text default can't see). qwen2.5-vl is the
    # strong small VLM; llava is the lighter fallback. Tags [VERIFY] at release time.
    "vision": [
        (12000, "qwen2.5vl:7b"),
        (6000, "qwen2.5vl:3b"),
        (0, "llava:7b"),
    ],
}
# The role whose model must be MULTIMODAL. suggest("vision") never collapses onto the text
# default — a text model would "describe the screen" by hallucinating.
VISION_DEFAULT = "qwen2.5vl:3b"


def _cfg_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "yggdrasil" / "models.json"


class RoleProvider(LLMProvider):
    """A live proxy: resolves role -> model at CALL time, so rebinding a role takes
    effect immediately for every agent that holds this provider."""

    def __init__(self, manager: "ModelManager", role: str) -> None:
        self.manager = manager
        self.role = role

    @property
    def model(self) -> str:  # so existing code that reads provider.model keeps working
        return self.manager.resolved(self.role)

    async def generate(self, *, system, prompt, schema=None, temperature=0.2) -> LLMResponse:
        return await self.manager._provider_for(self.role).generate(
            system=system, prompt=prompt, schema=schema, temperature=temperature)

    async def describe_image(self, *, system, prompt, image_b64, temperature=0.2) -> LLMResponse:
        return await self.manager._provider_for(self.role).describe_image(
            system=system, prompt=prompt, image_b64=image_b64, temperature=temperature)


class ModelManager:
    """Resolves roles to models, talks to Ollama about what's installed/loaded, and
    downloads new models with spoken-progress tracking."""

    def __init__(self, default_model: str, host: str = "http://127.0.0.1:11434") -> None:
        self.default_model = default_model
        self.host = host.rstrip("/")
        self._providers: dict[str, OllamaProvider] = {}  # by model name (shared across roles)
        self._pulls: dict[str, dict] = {}  # model -> {"pct": float, "done": bool, "error": str|None}
        self._lock = threading.Lock()

    # --- config -----------------------------------------------------------------
    @staticmethod
    def _raw() -> dict:
        try:
            d = json.loads(_cfg_path().read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _save(cfg: dict) -> None:
        p = _cfg_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def bindings(self) -> dict[str, Optional[str]]:
        """role -> explicitly bound model (None = using the default)."""
        roles = self._raw().get("roles") or {}
        return {r: roles.get(r) for r in ROLES}

    def resolved(self, role: str) -> str:
        """The model that currently answers this role. Vision is special: it MUST be a
        multimodal model, so it never falls back to the (text-only) default — an unbound
        vision role resolves to the vision default instead."""
        bound = (self._raw().get("roles") or {}).get(role)
        if bound:
            return bound
        if role == "vision":
            return VISION_DEFAULT
        return self.default_model

    def bind(self, role: str, model: str) -> None:
        cfg = self._raw()
        cfg.setdefault("roles", {})[role] = model
        self._save(cfg)

    def unbind(self, role: str) -> None:
        cfg = self._raw()
        (cfg.get("roles") or {}).pop(role, None)
        self._save(cfg)

    # --- providers ----------------------------------------------------------------
    def get(self, role: str) -> RoleProvider:
        """The provider agents hold. Live: rebinding the role redirects it instantly."""
        return RoleProvider(self, role)

    def _provider_for(self, role: str) -> OllamaProvider:
        model = self.resolved(role)
        p = self._providers.get(model)
        if p is None:
            # Pin the planner's model resident (it answers every utterance — a cold load
            # would add seconds of latency); specialists idle out of VRAM after 10 minutes.
            keep = -1 if model == self.resolved("planner") else "10m"
            p = OllamaProvider(model, host=self.host)
            p.keep_alive = keep
            self._providers[model] = p
        return p

    # --- Ollama inventory -----------------------------------------------------------
    async def installed(self) -> list[dict]:
        """Locally available models: [{"name", "size_gb"}...] (GET /api/tags)."""
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.host}/api/tags")
            r.raise_for_status()
            out = []
            for m in r.json().get("models", []):
                out.append({"name": m.get("name", ""),
                            "size_gb": round((m.get("size") or 0) / 1e9, 1)})
            return out

    async def loaded(self) -> list[str]:
        """Models resident in memory right now (GET /api/ps)."""
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.host}/api/ps")
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]

    # --- downloads --------------------------------------------------------------------
    def pull_status(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._pulls.items()}

    def start_pull(self, model: str, on_done=None) -> None:
        """Download a model in the background; progress readable via pull_status().
        ``on_done(model, error)`` fires from the worker thread when it finishes."""
        with self._lock:
            if model in self._pulls and not self._pulls[model].get("done"):
                return  # already downloading
            self._pulls[model] = {"pct": 0.0, "done": False, "error": None}

        def worker() -> None:
            import httpx

            error = None
            try:
                with httpx.Client(timeout=None) as client:
                    with client.stream("POST", f"{self.host}/api/pull",
                                       json={"model": model, "stream": True}) as r:
                        r.raise_for_status()
                        for line in r.iter_lines():
                            if not line:
                                continue
                            try:
                                st = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if st.get("error"):
                                error = st["error"]
                                break
                            total, done = st.get("total"), st.get("completed")
                            if total:
                                with self._lock:
                                    self._pulls[model]["pct"] = 100.0 * (done or 0) / total
            except Exception as e:  # noqa: BLE001
                error = str(e)
            with self._lock:
                self._pulls[model]["done"] = True
                self._pulls[model]["error"] = error
                if not error:
                    self._pulls[model]["pct"] = 100.0
            if on_done:
                try:
                    on_done(model, error)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True, name=f"pull-{model}").start()

    # --- hardware + suggestions ---------------------------------------------------------
    @staticmethod
    def vram_mib() -> int:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip().splitlines()[0])
        except Exception:
            return 0

    def suggest(self, role: str) -> Optional[str]:
        """A known-good specialist for this role on this machine's VRAM, or None if the
        general default model is already the right answer."""
        table = SUGGESTED.get(role)
        if not table:
            return None
        vram = self.vram_mib()
        for floor, tag in table:
            if vram >= floor:
                return tag
        return table[-1][1]

    # --- planner self-test ----------------------------------------------------------------
    async def self_test(self, model: str) -> tuple[bool, str]:
        """Can this model do schema-constrained routing? The planner's whole job is emitting
        valid action JSON — a model that flunks these probes would silently break every
        command, so a planner rebind is refused unless this passes."""
        probes = [
            ("what time is it", "system.time"),
            ("open the calculator", "apps.launch"),
            ("list my files", "file.list"),
        ]
        schema = {
            "type": "object",
            "properties": {"steps": {"type": "array", "items": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "argument": {"type": "string"}},
                "required": ["action", "argument"]}}},
            "required": ["steps"],
        }
        system = (
            "You route user requests to actions. Reply ONLY with JSON matching the schema. "
            "Actions: system.time (current time), apps.launch (open an application), "
            "file.list (list files). Use the single best action; argument may be empty.")
        provider = OllamaProvider(model, host=self.host)
        passed = 0
        for goal, expect in probes:
            try:
                resp = await provider.generate(system=system, prompt=goal, schema=schema)
            except Exception as e:  # noqa: BLE001
                return False, f"the model didn't respond: {e}"
            steps = (resp.parsed or {}).get("steps") or []
            if steps and steps[0].get("action") == expect:
                passed += 1
        if passed == len(probes):
            return True, "passed"
        return False, f"only routed {passed} of {len(probes)} test commands correctly"
