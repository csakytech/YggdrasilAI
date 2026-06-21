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
    _OPEN = re.compile(r"open\s+(?:the\s+)?(?:folder\s+|file\s+)?(.+)", re.I)
    _LIST = re.compile(
        r"(?:list|show|what(?:'s| is) in)\s+(?:the\s+)?(?:contents of\s+)?(.+)", re.I
    )

    async def plan(self, goal: str) -> list[Task]:
        g = goal.strip().rstrip(".")
        for pat, action in (
            (self._CREATE, "file.create_folder"),
            (self._DELETE, "file.delete"),
            (self._OPEN, "file.open"),
            (self._LIST, "file.list"),
        ):
            m = pat.search(g)
            if m:
                name = m.group(1).strip().strip("\"'")
                return [Task(action=action, agent="file", params={"path": name})]
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
            "You are Yggdrasil's task planner. Output JSON only: an ordered list of steps using "
            "ONLY the allowed actions. 'path' is a simple name inside the user's workspace, never "
            "an absolute path. If the request is NOT a file operation you can perform, return an "
            "empty steps list — do NOT invent folders. Examples:\n"
            'create a folder called reports -> {"steps":[{"action":"file.create_folder","path":"reports"}]}\n'
            'open reports -> {"steps":[{"action":"file.open","path":"reports"}]}\n'
            'what is in reports -> {"steps":[{"action":"file.list","path":"reports"}]}\n'
            'dance a jig -> {"steps":[]}\n'
            'what is the weather -> {"steps":[]}'
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


# Words that refer back to the last thing acted on ("open it", "delete that").
_PRONOUNS = {"it", "that", "this", "them", "the folder", "the file"}
_PRONOUN_GOAL = re.compile(
    r"^(open|list|show|delete|remove)\s+(it|that|this|them|the folder|the file)\s*$", re.I
)


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
        self._last_path: str | None = None  # for resolving "it" / "that"

    async def handle(self, goal: str) -> str:
        goal = self._rewrite_pronouns(goal)
        tasks = await self.planner.plan(goal)
        if not tasks:
            return "I'm not sure how to do that yet. I can create, list, open, or delete folders."
        replies = []
        for task in tasks:
            self._resolve_pronoun(task)
            result = await self._dispatch(task)
            if result.status is Status.OK and task.params.get("path"):
                self._last_path = task.params["path"]
            replies.append(self._render(task, result))
        return " ".join(replies)

    def _rewrite_pronouns(self, goal: str) -> str:
        """Resolve 'open it' / 'delete that' to the last folder BEFORE planning — a small
        model usually returns an empty plan for a bare pronoun."""
        if self._last_path:
            m = _PRONOUN_GOAL.match(goal.strip().rstrip("."))
            if m:
                return f"{m.group(1)} {self._last_path}"
        return goal

    def _resolve_pronoun(self, task: Task) -> None:
        if not task.action.startswith("file."):
            return
        path = (task.params.get("path") or "").strip()
        if (not path or path.lower() in _PRONOUNS) and self._last_path:
            task.params["path"] = self._last_path

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
    def _render(task: Task, result: Result) -> str:
        verb = task.action.split(".")[-1]
        data = result.data if isinstance(result.data, dict) else {}
        name = data.get("name") or task.params.get("path") or "it"
        if result.status is Status.OK:
            if data.get("missing"):
                return f"I couldn't find {name}."
            if verb == "list":
                items = data.get("items", [])
                if not items:
                    return f"{name} is empty."
                return f"{name} contains: " + ", ".join(items) + "."
            if verb == "open":
                if data.get("no_display"):
                    return "I can only open a window when you're signed in at the desktop."
                return f"Opened {name}."
            if verb == "delete":
                return f"Deleted {name}."
            if verb == "create_folder":
                return f"Created {name}."
            return "Done."
        if result.status is Status.DENIED:
            return f"Cancelled. {result.error}." if result.error else "Cancelled."
        if result.status is Status.TIMEOUT:
            return "That took too long."
        if "sandbox" in (result.error or "").lower():
            return "I can only work inside my workspace, so I can't reach that path."
        return "Sorry, I couldn't do that one."
