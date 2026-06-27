"""Quick Notes — an example ThorOS agent (the marketplace starting template).

Copy this folder, rename things, and replace the four methods below with your own logic. Everything
an agent needs is shown here: declaring capabilities, routing examples, a DANGEROUS (gated) action,
using the local model, and storing data in your private sandbox directory.

The contract (enforced by ThorOS):
  • subclass ``Agent`` (= BaseAgent from the SDK)
  • set ``domain``, ``module_id``, ``capabilities``, ``planner_examples``
  • implement ``_execute(verb, params)`` and return ``{"speech": "..."}``  (what Jarvis says back)
ThorOS checks the capability + the user's permission BEFORE calling ``_execute`` — so by the time
your code runs, a dangerous action has already been authorized.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from yggdrasil.sdk import Agent, Capability  # the stable public SDK — never import yggdrasil.core.*


class NotesAgent(Agent):
    # MUST match the manifest: [routing].domain and [agent].id
    domain = "notes"
    module_id = "yourname.notes-example"

    # Few-shot routing — keep these identical to manifest [routing].planner_examples.
    planner_examples = [
        'make a note to buy milk -> {"steps":[{"action":"notes.add","argument":"buy milk"}]}',
        'read my notes -> {"steps":[{"action":"notes.list","argument":""}]}',
        'summarize my notes -> {"steps":[{"action":"notes.summarize","argument":""}]}',
        'clear my notes -> {"steps":[{"action":"notes.clear","argument":""}]}',
    ]

    # The verbs you expose. dangerous=True actions are gated behind the user's auth code.
    capabilities = {
        "add":       Capability("add", dangerous=False, description="Save a quick note"),
        "list":      Capability("list", dangerous=False, description="Read your notes back"),
        "summarize": Capability("summarize", dangerous=False, description="Summarize your notes"),
        "clear":     Capability("clear", dangerous=True, description="Delete ALL of your notes"),
    }

    def __init__(self, bus, perms, llm=None) -> None:
        # ThorOS injects the bus, the permission manager, and (optionally) the local LLM provider.
        super().__init__(bus, perms)
        self.llm = llm
        # Your PRIVATE sandbox dir — auto-granted, no permission needed. Keep your data here.
        data_dir = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) \
            / "yggdrasil" / "modules" / self.module_id
        data_dir.mkdir(parents=True, exist_ok=True)
        self._notes = data_dir / "notes.txt"

    # The single entry point. Dispatch the verb to your handler; return {"speech": ...}.
    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        arg = (params.get("argument") or "").strip()
        if verb == "add":
            return {"speech": self._add(arg)}
        if verb == "list":
            return {"speech": self._list()}
        if verb == "summarize":
            return {"speech": await self._summarize()}
        if verb == "clear":
            return {"speech": self._clear()}
        raise ValueError(f"unhandled verb '{verb}'")

    # ---- handlers (this is the part you replace for your own agent) ----
    def _add(self, text: str) -> str:
        if not text:
            return "What should I note down?"
        with self._notes.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
        return f"Noted: {text}."

    def _list(self) -> str:
        lines = self._read()
        if not lines:
            return "You don't have any notes yet."
        return f"You have {len(lines)} note{'s' if len(lines) != 1 else ''}: " + "; ".join(lines) + "."

    async def _summarize(self) -> str:
        lines = self._read()
        if not lines:
            return "There's nothing to summarize yet."
        if not self.llm:  # the model is optional — always degrade gracefully
            return self._list()
        # Use the LOCAL model the host injected. /no_think keeps the small model fast + direct.
        resp = await self.llm.generate(
            system="You are a concise assistant. Summarize these notes in one short spoken sentence. /no_think",
            prompt="\n".join(f"- {n}" for n in lines),
            temperature=0.2,
        )
        return resp.text.strip() or self._list()

    def _clear(self) -> str:
        # Reaching here means the user already authorized it (capability is dangerous=True).
        try:
            self._notes.unlink(missing_ok=True)
        except OSError:
            return "I couldn't clear your notes."
        return "Cleared all your notes."

    def _read(self) -> list[str]:
        try:
            return [ln.strip() for ln in self._notes.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return []
