"""Module registry — loads agents and assembles the planner from their declared metadata.

Today it registers the built-in Core agents; the same surface will load on-disk modules
(see docs/MODULES.md §9). The key idea: the planner is **data-driven** — its allowed actions
and few-shot examples come from whatever agents are registered, so a new agent becomes usable
just by being registered, with no orchestrator changes.
"""
from __future__ import annotations


class Registry:
    def __init__(self) -> None:
        self.agents: list = []

    def register(self, agent) -> None:
        self.agents.append(agent)

    async def start_all(self) -> None:
        for a in self.agents:
            await a.start()

    def allowed_actions(self) -> list[str]:
        actions: list[str] = []
        for a in self.agents:
            actions += [f"{a.domain}.{verb}" for verb in a.capabilities]
        return actions

    def planner_examples(self) -> list[str]:
        examples: list[str] = []
        for a in self.agents:
            examples += list(getattr(a, "planner_examples", []))
        return examples

    def describe(self) -> list[str]:
        """Capability listing for help / the install-consent screen."""
        lines: list[str] = []
        for a in self.agents:
            for verb, cap in a.capabilities.items():
                flag = " (needs authorization)" if cap.dangerous else ""
                lines.append(f"{a.domain}.{verb}: {cap.description}{flag}")
        return lines
