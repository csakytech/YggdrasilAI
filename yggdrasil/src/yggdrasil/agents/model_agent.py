"""Model Agent — see, swap, and download the LLMs behind each job, by voice.

"What models do I have?" → the installed list. "What model do you use for coding?" → the
role bindings. "Use qwen coder for coding" → rebinds the role (downloading the model first
if needed, with spoken consent — models are gigabytes). "Reset the coding model" → back to
the default. The planner role is special: a rebind runs a routing self-test first and is
refused if the candidate can't do schema-constrained routing (a bad planner would silently
break every command).
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess

from ..core import resolve as resolver
from ..core.models import ROLE_ALIASES, ROLES, ModelManager
from ..core.permissions import Capability
from .base import BaseAgent


def _simplify(name: str) -> str:
    """qwen2.5-coder:7b -> 'qwen coder'-comparable form (letters only, spoken-friendly)."""
    return re.sub(r"[^a-z ]+", " ", name.lower().split(":")[0]).strip()


def _notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        try:
            subprocess.Popen(["notify-send", "-a", "ThorOS", title, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


class ModelAgent(BaseAgent):
    domain = "model"
    module_id = "core.model"
    planner_examples = [
        'what models do I have -> {"steps":[{"action":"model.list","argument":""}]}',
        'what model do you use for coding -> {"steps":[{"action":"model.status","argument":""}]}',
        'use qwen coder for coding -> {"steps":[{"action":"model.bind","argument":"qwen coder","role":"coder"}]}',
        'download the qwen coder model -> {"steps":[{"action":"model.pull","argument":"qwen coder"}]}',
        'reset the coding model -> {"steps":[{"action":"model.reset","argument":"coder"}]}',
        "how's the model download going -> {\"steps\":[{\"action\":\"model.status\",\"argument\":\"\"}]}",
    ]
    capabilities = {
        "list": Capability("list", False, "List the language models installed on this machine"),
        "status": Capability("status", False, "Which model handles which job, and download progress"),
        "bind": Capability("bind", False, "Choose which model handles a job (coding, writing, …)"),
        "pull": Capability("pull", False, "Download a language model from the Ollama library"),
        "reset": Capability("reset", False, "Point a job back at the default model"),
        "confirm": Capability("confirm", False, "Confirm the staged model download"),
        "cancel": Capability("cancel", False, "Cancel the staged model download"),
    }

    def __init__(self, bus, perms, models: ModelManager | None = None) -> None:
        super().__init__(bus, perms)
        self.models = models
        self._staged: dict | None = None  # {"model": ..., "role": ...|None} awaiting yes/no

    async def _execute(self, verb, params):
        if self.models is None:
            return {"speech": "Model management needs the language-model system, which isn't running."}
        arg = (params.get("argument") or "").strip()
        role = (params.get("role") or "").strip().lower()
        if verb == "list":
            return await self._list()
        if verb == "status":
            return await self._status()
        if verb == "bind":
            return await self._bind(arg, role)
        if verb == "pull":
            return await self._pull(arg)
        if verb == "reset":
            return self._reset(arg or role)
        if verb == "confirm":
            return self._confirm()
        if verb == "cancel":
            self._staged = None
            return {"speech": "Okay, cancelled."}
        raise ValueError(f"unhandled verb '{verb}'")

    # --- capabilities -------------------------------------------------------------
    async def _list(self):
        try:
            models = await self.models.installed()
        except Exception:
            return {"speech": "I couldn't reach the model server — Ollama may still be starting up."}
        if not models:
            return {"speech": "No language models are installed yet."}
        in_use = {self.models.resolved(r) for r in ROLES}
        lines = [f"{m['name']} ({m['size_gb']} gigabytes)" + (" — in use" if m["name"] in in_use else "")
                 for m in models]
        return {"speech": f"You have {len(models)} language "
                          f"model{'s' if len(models) != 1 else ''} installed.",
                "list": lines}

    async def _status(self):
        parts = []
        default = self.models.default_model
        bound = self.models.bindings()
        for role, desc in ROLES.items():
            m = bound.get(role)
            parts.append(f"{role} ({desc}): {m or default + ' — the default'}")
        speech = "Here's which model handles which job."
        pulls = self.models.pull_status()
        active = {m: st for m, st in pulls.items() if not st.get("done")}
        for m, st in active.items():
            speech = f"Downloading {m} — {st['pct']:.0f} percent done."
        for m, st in pulls.items():
            if st.get("done") and st.get("error"):
                parts.append(f"download of {m} FAILED: {st['error']}")
        return {"speech": speech, "list": parts}

    async def _bind(self, spoken: str, role: str):
        role = ROLE_ALIASES.get(role or "", role)
        if role not in ROLES:
            return {"speech": "Which job is that model for — coding, writing, reasoning, or planning?"}
        if not spoken:
            return {"speech": f"Which model should handle {role}?"}
        model = await self._match(spoken)
        if model:  # installed already -> bind now (planner gets the self-test)
            return await self._apply_bind(model, role)
        # Not installed. Suggest the known-good specialist for this machine if the spoken
        # name is vague, else take the name literally as an Ollama tag.
        target = self._as_tag(spoken) or self.models.suggest(role)
        if not target:
            return {"speech": f"I don't have a model called {spoken}, and I don't have a "
                              f"suggestion for {role} — say the exact model name to download."}
        self._staged = {"model": target, "role": role}
        return {"speech": f"{target} isn't downloaded yet — it's a multi-gigabyte download. "
                          f"Shall I download it and use it for {role}? Say yes or no.",
                "await_confirm": True, "agent": self.domain}

    async def _pull(self, spoken: str):
        if not spoken:
            return {"speech": "Which model should I download?"}
        target = self._as_tag(spoken)
        if await self._match(spoken):
            return {"speech": f"{spoken} looks like it's already installed."}
        self._staged = {"model": target, "role": None}
        return {"speech": f"That's a multi-gigabyte download of {target}. Go ahead? Say yes or no.",
                "await_confirm": True, "agent": self.domain}

    def _confirm(self):
        if not self._staged:
            return {"speech": "There's nothing staged to download."}
        model, role = self._staged["model"], self._staged.get("role")
        self._staged = None
        mgr = self.models

        def on_done(m, error):  # worker thread — no event loop here
            if error:
                _notify("Model download failed", f"{m}: {error}")
                return
            if role == "planner":
                ok, msg = asyncio.run(mgr.self_test(m))
                if not ok:
                    _notify("Model NOT activated", f"{m} downloaded but failed the routing "
                                                   f"self-test ({msg}). Keeping the current planner.")
                    return
            if role:
                mgr.bind(role, m)
                _notify("Model ready", f"{m} is now handling {role}.")
            else:
                _notify("Model ready", f"{m} is downloaded and ready.")

        mgr.start_pull(model, on_done=on_done)
        what = f" and switch {role} to it" if role else ""
        return {"speech": f"Downloading {model} now{what}. It's large, so it'll take a while — "
                          "I'll pop up a notification when it's ready, or ask me how the "
                          "download is going."}

    async def _apply_bind(self, model: str, role: str):
        if role == "planner":
            ok, msg = await self.models.self_test(model)
            if not ok:
                return {"speech": f"I tested {model} as the planner and it {msg} — "
                                  "so I'm keeping the current one. It could still work "
                                  "for coding or writing."}
        self.models.bind(role, model)
        return {"speech": f"Done — {model} now handles {role}."}

    def _reset(self, spoken: str):
        role = ROLE_ALIASES.get((spoken or "").strip().lower())
        if role is None:
            for w in re.findall(r"[a-z]+", (spoken or "").lower()):
                if w in ROLE_ALIASES:
                    role = ROLE_ALIASES[w]
                    break
        if role is None:
            return {"speech": "Which job should go back to the default — coding, writing, "
                              "reasoning, or planning?"}
        self.models.unbind(role)
        return {"speech": f"Okay — {role} is back on the default model, "
                          f"{self.models.default_model}."}

    # --- helpers -----------------------------------------------------------------------
    async def _match(self, spoken: str) -> str | None:
        """Resolve a spoken name ('qwen coder') against the installed models."""
        try:
            names = [m["name"] for m in await self.models.installed()]
        except Exception:
            return None
        got, confident, _ = resolver.resolve(spoken, names, names)
        if got and confident:
            return got
        # spoken-friendly retry: compare letters-only forms ("qwen coder" ~ qwen2.5-coder:7b)
        simple = {_simplify(n): n for n in names}
        got, confident, _ = resolver.resolve(_simplify(spoken), list(simple), list(simple))
        return simple.get(got) if got and confident else None

    @staticmethod
    def _as_tag(spoken: str) -> str | None:
        """A literal Ollama tag if the user spoke one ('qwen2.5-coder:7b'), else a guess
        from known spoken names, else None."""
        s = spoken.strip().lower().replace(" ", "")
        if re.fullmatch(r"[a-z0-9._\-]+(:[a-z0-9._\-]+)?", s) and any(c.isdigit() or c in ":.-" for c in s):
            return s
        known = {
            "qwencoder": "qwen2.5-coder:7b", "qwen coder": "qwen2.5-coder:7b",
            "deepseekcoder": "deepseek-coder-v2:16b", "codellama": "codellama:7b",
            "llama": "llama3.2:3b", "mistral": "mistral:7b", "gemma": "gemma3:4b",
        }
        return known.get(s) or known.get(spoken.strip().lower())
