"""Memory Agent: stores/recalls facts the user tells Yggdrasil to remember, AND recaps what
the user actually DID from the activity journal ("what was I working on yesterday?").

All operations are safe (no authorization challenge). The planner routes "remember …" /
"my name is …" here; recall is usually answered conversationally from injected context.
The recap reads the timestamped journal (core.journal) and summarizes a time window.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..core import journal
from ..core.memory import MemoryStore
from ..core.permissions import Capability
from .base import BaseAgent


class MemoryAgent(BaseAgent):
    domain = "memory"
    module_id = "core.memory"
    planner_examples = [
        'my name is Sam -> {"steps":[{"action":"memory.remember","argument":"The user\'s name is Sam"}]}',
        'remember that I like dark mode -> {"steps":[{"action":"memory.remember","argument":"The user likes dark mode"}]}',
        'what was I working on yesterday -> {"steps":[{"action":"memory.recap","argument":"yesterday"}]}',
        'what did I do this week -> {"steps":[{"action":"memory.recap","argument":"this week"}]}',
    ]
    capabilities = {
        "remember": Capability("remember", dangerous=False, description="Remember a fact about the user"),
        "forget": Capability("forget", dangerous=False, description="Forget remembered facts matching text"),
        "recall": Capability("recall", dangerous=False, description="List what is remembered"),
        "recap": Capability("recap", dangerous=False, description="Recap what you worked on in a time period"),
    }

    def __init__(self, bus, perms, store: MemoryStore, llm=None) -> None:
        super().__init__(bus, perms)
        self.store = store
        self.llm = llm  # reasoner — turns the raw journal into a natural spoken recap

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        text = (params.get("text") or params.get("argument") or "").strip()
        if verb == "remember":
            return {"remembered": self.store.remember(text)}
        if verb == "forget":
            return {"forgot": self.store.forget(text)}
        if verb == "recall":
            return {"facts": self.store.recall()}
        if verb == "recap":
            return await self._recap(text)
        raise ValueError(f"unhandled verb '{verb}'")

    async def _recap(self, text: str) -> dict:
        start, end, label = journal.window_for(text)
        entries = journal.between(start, end)
        if not entries:
            hint = (" You haven't asked me to do much yet — once you create files, write "
                    "documents, or build a project, I'll remember it here."
                    if label == "today" else "")
            return {"speech": f"I don't have anything logged for {label}.{hint}"}

        # De-duplicate consecutive identical summaries, keep order.
        lines, seen_last = [], None
        for e in entries:
            s = e.get("summary", "").strip()
            if s and s != seen_last:
                lines.append(s)
                seen_last = s

        if self.llm is not None:
            try:
                bullet = "\n".join(f"- {s}" for s in lines[-40:])
                r = await self.llm.generate(
                    system=("You are recapping what the user did, from their own activity log. "
                            "Write a warm, natural SPOKEN summary in 1-3 short sentences — group "
                            "related actions (a project and its files together), lead with the "
                            "most significant work, keep small stuff brief. Use 'you'. Do NOT "
                            "invent anything not in the log. Plain text, no lists."),
                    prompt=f"The user asked what they worked on {label}. Their activity log for "
                           f"that period (oldest first):\n{bullet}",
                    temperature=0.3)
                speech = (r.text or "").strip()
                if speech:
                    return {"speech": speech, "list": lines}
            except Exception:
                pass

        # Fallback without a model: a tidy spoken list.
        shown = lines[-8:]
        joined = "; ".join(shown)
        more = f", and {len(lines) - len(shown)} more" if len(lines) > len(shown) else ""
        return {"speech": f"Here's {label}: {joined}{more}.", "list": lines}
