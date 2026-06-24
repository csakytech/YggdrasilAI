"""Decision trace — a short rolling log of what the orchestrator just did and the context it used,
so the Explain agent can answer "why did you do that?".

It records the *observable* inputs to each decision (your words, the focused window, whether
memory was drawn on, the actions chosen, the outcome) — so the explanation is grounded in the
real routing, not an after-the-fact rationalization. Process-local (same process as the agents,
like core/focus.py), not persisted — it's about the current session's actions.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class Decision:
    goal: str
    active: tuple = ("", "")  # (window name, kind) when the command ran
    memory_used: bool = False
    route: str = "action"  # "action" | "conversation"
    steps: list = field(default_factory=list)  # [{"action","arg","status"}]
    outcome: str = ""
    ok: bool = True


_LOG: deque[Decision] = deque(maxlen=8)


def record(d: Decision) -> None:
    # Don't log meta "explain" turns — they'd shadow the real action the user is asking about.
    if d.steps and all(s.get("action", "").startswith("explain.") for s in d.steps):
        return
    _LOG.append(d)


def last() -> Decision | None:
    return _LOG[-1] if _LOG else None
