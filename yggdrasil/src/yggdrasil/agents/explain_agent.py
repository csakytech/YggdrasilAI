"""Explain Agent (Core module): answers "why did you do that?".

The orchestrator records a short trace of each command (core/trace.py): what you asked, the
context it used (the focused window, whether it drew on memory), what it did, and the result.
This agent turns the most recent real action into a plain-spoken explanation — grounded in the
actual routing inputs, not an invented rationalization. A deterministic reason is built first
(so it's always accurate), then the local LLM rephrases it naturally without adding facts.
"""
from __future__ import annotations

import re
from typing import Any

from ..core import trace
from ..core.permissions import Capability
from .base import BaseAgent

_THINK = re.compile(r"<think>.*?</think>", re.S)


class ExplainAgent(BaseAgent):
    domain = "explain"
    module_id = "core.explain"
    planner_examples = [
        'why did you do that -> {"steps":[{"action":"explain.why","argument":""}]}',
        'why -> {"steps":[{"action":"explain.why","argument":""}]}',
        'explain that -> {"steps":[{"action":"explain.why","argument":""}]}',
        'why did you open that -> {"steps":[{"action":"explain.why","argument":""}]}',
        'what were you thinking -> {"steps":[{"action":"explain.why","argument":""}]}',
        'how did you decide that -> {"steps":[{"action":"explain.why","argument":""}]}',
    ]
    capabilities = {"why": Capability("why", False, "Explain why I did what I just did")}

    def __init__(self, bus, perms, llm=None) -> None:
        super().__init__(bus, perms)
        self.llm = llm

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        d = trace.last()
        if d is None:
            return {"speech": "I haven't done anything yet to explain."}
        reason = self._reason(d)
        if self.llm:
            reason = await self._polish(reason)
        return {"speech": reason}

    # --- build a truthful explanation from the recorded inputs ---------------------------------

    @staticmethod
    def _active(d) -> tuple[str, str]:
        a = d.active
        if isinstance(a, (tuple, list)) and len(a) >= 2:
            return str(a[0]), str(a[1])
        return "", ""

    def _reason(self, d) -> str:
        _name, kind = self._active(d)
        if d.route == "conversation":
            why = "that's a question rather than an action, so I just answered"
            if d.memory_used:
                why += " using what I remember about you"
            return f"You asked “{d.goal}” — {why}."
        parts = [self._step_reason(s.get("action", ""), s.get("arg", ""), kind) for s in d.steps]
        return f"You asked “{d.goal}.” " + " ".join(p for p in parts if p)

    @staticmethod
    def _step_reason(action: str, arg: str, kind: str) -> str:
        domain, _, verb = action.partition(".")
        a = f"“{arg}”" if arg else "it"
        if domain == "focus":
            if verb == "enter":
                return f"Because a terminal was focused, I ran {a} inside it instead of as a separate command."
            if verb == "type":
                return f"Because that window was focused, I typed {a} into it."
            return f"I pressed {a} in the focused window."
        if domain == "documents":
            return {
                "open": f"so I searched your documents, found a match, and opened {a}.",
                "save": f"so I saved the document{(' as ' + arg) if arg else ''}.",
                "new": "so I opened a blank document.",
                "recent": "so I looked through your recent files.",
            }.get(verb, f"so I ran the {verb} document action.")
        if domain == "app":
            return {
                "launch": f"so I launched {a}.",
                "close": f"so I closed {a}.",
                "browse": f"so I opened {a} in the browser.",
                "search": f"so I searched the web for {a}.",
            }.get(verb, f"so I ran the {verb} app action.")
        if domain == "file":
            return f"so I ran the {verb} operation on {a} in your workspace."
        if domain == "command":
            return f"so I ran the system command {a} (after the safety check)."
        if domain == "memory":
            return f"so I saved {a} to memory."
        if domain == "system":
            return f"so I checked the system ({verb})."
        if domain == "security":
            return f"so I ran a security {verb}."
        return f"so I ran {action} with {a}."

    async def _polish(self, reason: str) -> str:
        try:
            system = ("Rephrase this explanation as one or two short, natural spoken sentences, first "
                      "person and friendly. Keep every fact; do NOT add anything new. No markdown. /no_think")
            resp = await self.llm.generate(system=system, prompt=reason, temperature=0.3)
            return _THINK.sub("", resp.text).strip() or reason
        except Exception:
            return reason
