"""Orchestrator: goal -> plan -> dispatch -> authorize -> results.

Two planners ship: ``HeuristicPlanner`` (no model, covers the File Agent verbs so the spine
runs today) and ``LLMPlanner`` (schema-constrained, used once Ollama is available). The
dispatch path transparently handles the authorization-code challenge for dangerous actions.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from .bus import Bus, Result, Status, Task
from .llm import LLMProvider
from .permissions import AuthChallenge, PermissionManager

# Supplied by the CLI (stdin) or, later, the voice loop (speech): given a challenge,
# return the code the user provides.
AuthResolver = Callable[[AuthChallenge], Awaitable[str]]


class Planner(ABC):
    @abstractmethod
    async def plan(self, goal: str) -> list[Task]: ...


class HeuristicPlanner(Planner):
    """Phase-0 placeholder: pattern-matches the two File Agent verbs so the spine runs
    with no model. Replaced by ``LLMPlanner`` once Ollama is available."""

    _CREATE = re.compile(
        r"(?:create|make|new)\s+(?:a\s+)?folder\s+(?:called\s+|named\s+)?(.+)", re.I
    )
    _DELETE = re.compile(r"(?:delete|remove)\s+(?:the\s+)?(?:file\s+|folder\s+)?(.+)", re.I)

    async def plan(self, goal: str) -> list[Task]:
        g = goal.strip().rstrip(".")
        m = self._CREATE.search(g)
        if m:
            name = m.group(1).strip().strip("\"'")
            return [Task(action="file.create_folder", agent="file", params={"path": name})]
        m = self._DELETE.search(g)
        if m:
            name = m.group(1).strip().strip("\"'")
            return [Task(action="file.delete", agent="file", params={"path": name})]
        return []


PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["action"],
            },
        }
    },
    "required": ["steps"],
}


class LLMPlanner(Planner):
    """Schema-constrained planner. ``action`` is restricted to the available tools (enum),
    so the model cannot invent tools or emit malformed JSON."""

    def __init__(self, llm: LLMProvider, allowed_actions: list[str]) -> None:
        self.llm = llm
        self.allowed_actions = allowed_actions

    async def plan(self, goal: str) -> list[Task]:
        import copy

        schema = copy.deepcopy(PLAN_SCHEMA)
        schema["properties"]["steps"]["items"]["properties"]["action"] = {
            "type": "string",
            "enum": self.allowed_actions,
        }
        system = (
            "You are Yggdrasil's task planner. Convert the user's goal into a short ordered "
            "list of concrete steps using ONLY the allowed actions. Output JSON only."
        )
        resp = await self.llm.generate(system=system, prompt=goal, schema=schema)
        steps = (resp.parsed or {}).get("steps", [])
        tasks: list[Task] = []
        for s in steps:
            action = s.get("action", "")
            domain = action.split(".", 1)[0] if "." in action else action
            params = {k: v for k, v in s.items() if k != "action"}
            tasks.append(Task(action=action, agent=domain, params=params))
        return tasks


class Orchestrator:
    def __init__(
        self,
        bus: Bus,
        perms: PermissionManager,
        planner: Planner,
        auth_resolver: AuthResolver,
    ) -> None:
        self.bus = bus
        self.perms = perms
        self.planner = planner
        self.auth_resolver = auth_resolver

    async def handle(self, goal: str) -> str:
        tasks = await self.planner.plan(goal)
        if not tasks:
            return (
                "I couldn't turn that into an action yet. "
                "(Phase 0 understands 'create a folder called X' and 'delete X'.)"
            )
        replies = [self._render(await self._dispatch(t)) for t in tasks]
        return " ".join(replies)

    async def _dispatch(self, task: Task) -> Result:
        result = await self.bus.request(task.agent, task)
        if result.status is Status.AWAITING_AUTH and result.challenge is not None:
            code = await self.auth_resolver(result.challenge)
            token = self.perms.verify(result.challenge.challenge_id, code)
            if token is None:
                return Result(
                    task.task_id,
                    Status.DENIED,
                    agent=task.agent,
                    error="authorization failed or timed out",
                )
            task.auth_token = token
            result = await self.bus.request(task.agent, task)
        return result

    @staticmethod
    def _render(result: Result) -> str:
        if result.status is Status.OK:
            return "Done."
        if result.status is Status.DENIED:
            return f"Cancelled ({result.error})."
        if result.status is Status.TIMEOUT:
            return "That timed out."
        return f"Something went wrong: {result.error}"
