"""Public SDK for ThorOS agent modules (the marketplace surface).

Community agents import from **here** — not from ``yggdrasil.core`` / ``yggdrasil.agents`` internals —
so we can change the internals without breaking installed agents. The manifest's ``thoros_api``
field pins which version of this surface an agent was built against.

    from yggdrasil.sdk import Agent, Capability

    class MyAgent(Agent):
        domain = "myapp"
        capabilities = {"do": Capability("do", dangerous=False, description="…")}
        async def _execute(self, verb, params):
            return {"speech": "Done."}

Stability promise: anything exported here is stable within a major ``API_VERSION``; internals are not.
"""
from __future__ import annotations

from .agents.base import BaseAgent as Agent
from .core.permissions import Capability

# Bump the major when the agent contract changes incompatibly; agents declare `thoros_api = ">=1.0"`.
API_VERSION = "1.0"

__all__ = ["Agent", "Capability", "API_VERSION"]
