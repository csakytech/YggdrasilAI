"""Memory Agent: stores, recalls, and forgets things the user tells Yggdrasil to remember.

All operations are safe (no authorization challenge). The planner routes "remember …" /
"my name is …" here; recall is usually answered conversationally from injected context, but
the explicit capability exists too.
"""
from __future__ import annotations

from typing import Any

from ..core.memory import MemoryStore
from ..core.permissions import Capability
from .base import BaseAgent


class MemoryAgent(BaseAgent):
    domain = "memory"
    capabilities = {
        "remember": Capability("remember", dangerous=False, description="Remember a fact about the user"),
        "forget": Capability("forget", dangerous=False, description="Forget remembered facts matching text"),
        "recall": Capability("recall", dangerous=False, description="List what is remembered"),
    }

    def __init__(self, bus, perms, store: MemoryStore) -> None:
        super().__init__(bus, perms)
        self.store = store

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        text = (params.get("text") or params.get("argument") or "").strip()
        if verb == "remember":
            return {"remembered": self.store.remember(text)}
        if verb == "forget":
            return {"forgot": self.store.forget(text)}
        if verb == "recall":
            return {"facts": self.store.recall()}
        raise ValueError(f"unhandled verb '{verb}'")
