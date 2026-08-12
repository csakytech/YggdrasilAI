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
        "find": Capability("find", False, "Search HuggingFace for a model by name and offer to download it"),
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
        if verb == "find":
            return await self._find(arg)
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
        # NAME them in the spoken reply too. The names used to live only in the "list" card, which
        # the voice path can't render — so by voice you heard "you have two models installed" and
        # never which two. Speech is the only channel a voice user has; the answer has to be IN it.
        names = ", ".join(m["name"] for m in models)
        n = len(models)
        return {"speech": f"You have {n} language model{'s' if n != 1 else ''} installed: {names}.",
                "list": lines}

    async def _status(self):
        parts = []
        default = self.models.default_model
        bound = self.models.bindings()
        for role, desc in ROLES.items():
            m = bound.get(role)
            parts.append(f"{role} ({desc}): {m or default + ' — the default'}")
        # Say the assignments, don't just gesture at them — by voice "Here's which model handles
        # which job" with the answer in the list card is no answer at all (same bug as _list).
        speech = "Here's which model handles which job. " + "; ".join(parts) + "."
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
        # A HuggingFace model can't be guessed from a name — many repos share similar names, and
        # the owner is case-sensitive. If they mention HuggingFace without a full path, ASK for it
        # rather than fabricating a bare tag that 404s (which is exactly what "downloaded nothing
        # and didn't tell me why" was). A pasted/typed full path is honoured by _as_tag above.
        if re.search(r"hugging\s*face|hf\.co", spoken, re.I) and \
                not re.search(r"(?:hf\.co|huggingface\.co)/\S+", spoken, re.I):
            return {"speech": "For a Hugging Face model I need its full address — something like "
                              "hf.co/owner/model-name. Say or type that and I'll download it. "
                              "(In a terminal it's: ollama pull hf.co/owner/model.)"}
        target = self._as_tag(spoken)
        if not target:
            return {"speech": f"I couldn't work out a model name from “{spoken}”. Give me the exact "
                              "tag, like qwen2.5-coder:7b, or a full hf.co/owner/model address."}
        if await self._match(spoken):
            return {"speech": f"{spoken} looks like it's already installed."}
        self._staged = {"model": target, "role": None}
        return {"speech": f"That's a multi-gigabyte download of {target}. Go ahead? Say yes or no.",
                "await_confirm": True, "agent": self.domain}

    async def _find(self, spoken: str):
        """Resolve a spoken model NAME to a real HuggingFace repo and offer to download it — the
        piece that turns "download the dolphin cyber model" from "open a web page / describe it"
        into an actual install, without the user typing hf.co/Owner/Repo:Quant by hand."""
        if not spoken:
            return {"speech": "Which model should I look for?"}
        import asyncio

        from ..core.models import search_hf_gguf
        try:
            results = await asyncio.to_thread(search_hf_gguf, spoken)
        except Exception:
            results = []
        if not results:
            return {"speech": f"I couldn't find a downloadable version of “{spoken}” on "
                              "HuggingFace. It may not have a GGUF build, or the name might be "
                              "slightly different — try the exact model name."}
        top = results[0]
        tag = f"hf.co/{top['repo']}:{top['best']}"
        if await self._match(tag):
            return {"speech": f"You already have {top['repo']} installed."}
        self._staged = {"model": tag, "role": None}
        extra = (f" I found {len(results)} matches; this is the closest." if len(results) > 1 else "")
        return {"speech": f"I found {top['repo']} on HuggingFace.{extra} Shall I download its "
                          f"{top['best']} version? It's a multi-gigabyte download — say yes or no.",
                "await_confirm": True, "agent": self.domain,
                "list": [f"{r['repo']}  ({', '.join(r['quants'][:4])})" for r in results]}

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
        """The Ollama pull target if the user gave one, else a guess from known spoken names, else
        None. A full HuggingFace path is preserved VERBATIM — repo owners are case-sensitive and
        contain slashes, so it must not be lowercased or space-stripped like a bare tag. (The old
        code did both, so even a correctly typed hf.co/Owner/Repo path was mangled to nothing.)"""
        raw = spoken.strip()
        m = re.search(r"(?:hf\.co|huggingface\.co)/\S+", raw, re.I)
        if m:
            return re.sub(r"(?i)^huggingface\.co/", "hf.co/", m.group(0))
        low = raw.lower()
        known = {
            "qwencoder": "qwen2.5-coder:7b", "qwen coder": "qwen2.5-coder:7b",
            "deepseekcoder": "deepseek-coder-v2:16b", "codellama": "codellama:7b",
            "llama": "llama3.2:3b", "mistral": "mistral:7b", "gemma": "gemma3:4b",
        }
        if low in known or low.replace(" ", "") in known:
            return known.get(low) or known[low.replace(" ", "")]
        # A real Ollama tag has NO spaces (qwen2.5-coder:7b). Mashing the spaces out of a spoken
        # DESCRIPTION ("Dolphin3 Cyber 8B") fabricates a tag that 404s silently — the exact bug
        # here. So only accept a space-free, tag-shaped literal; a spaced phrase we don't know
        # resolves to None, and the caller asks for the exact tag or an hf.co path.
        if " " not in low and re.fullmatch(r"[a-z0-9._\-]+(:[a-z0-9._\-]+)?", low) \
                and any(c.isdigit() or c in ":.-" for c in low):
            return low
        return None
