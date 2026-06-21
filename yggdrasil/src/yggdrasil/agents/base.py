"""BaseAgent: declares capabilities, enforces the permission gate, returns results.

Every agent subclasses this. The permission check happens BEFORE any side effect, so an
agent physically cannot act on a dangerous capability without an authorization token.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..core.bus import Bus, Result, Status, Task
from ..core.permissions import Capability, PermissionManager


class BaseAgent(ABC):
    domain: str
    capabilities: dict[str, Capability]

    def __init__(self, bus: Bus, perms: PermissionManager) -> None:
        self.bus = bus
        self.perms = perms

    async def start(self) -> None:
        await self.bus.subscribe(self.domain, self.handle)

    async def handle(self, task: Task) -> Result:
        verb = task.action.split(".", 1)[-1]
        cap = self.capabilities.get(verb)
        if cap is None:
            return Result(
                task.task_id, Status.DENIED, agent=self.domain,
                error=f"unknown capability '{verb}'",
            )

        decision = await self.perms.check(task, cap, self.domain)
        if decision.status is Status.DENIED:
            return Result(task.task_id, Status.DENIED, agent=self.domain, error=decision.reason)
        if decision.status is Status.AWAITING_AUTH:
            return Result(
                task.task_id, Status.AWAITING_AUTH, agent=self.domain,
                challenge=decision.challenge,
            )

        try:
            data = await self._execute(verb, task.params)
            return Result(task.task_id, Status.OK, data=data, agent=self.domain)
        except Exception as e:
            return Result(task.task_id, Status.ERROR, agent=self.domain, error=repr(e))

    @abstractmethod
    async def _execute(self, verb: str, params: dict[str, Any]) -> Any: ...
