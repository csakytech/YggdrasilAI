"""In-process message bus for Phase 1.

The Bus is deliberately an interface so the transport can be swapped without touching
agents. Phase 1 uses ``LocalBus`` (asyncio, single process). The multi-agent-team phase
adds a ``NatsBus`` implementing the same contract. See docs/ARCHITECTURE.md (ADR-0003).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


class Status(str, Enum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
    TIMEOUT = "timeout"
    AWAITING_AUTH = "awaiting_auth"


@dataclass(slots=True)
class Task:
    """A unit of work the orchestrator sends to an agent domain."""

    action: str  # e.g. "file.create_folder"
    params: dict[str, Any] = field(default_factory=dict)
    agent: Optional[str] = None  # target domain, e.g. "file"
    task_id: str = field(default_factory=new_id)
    correlation_id: str = field(default_factory=new_id)  # groups one plan
    origin: str = "orchestrator"
    deadline_s: float = 30.0
    created_at: float = field(default_factory=now)
    auth_token: Optional[str] = None  # set after a passed authorization challenge


@dataclass(slots=True)
class Result:
    task_id: str
    status: Status
    data: Any = None
    error: Optional[str] = None
    agent: Optional[str] = None
    challenge: Any = None  # AuthChallenge when status == AWAITING_AUTH
    finished_at: float = field(default_factory=now)


Handler = Callable[[Task], Awaitable[Result]]


class Bus(ABC):
    """Transport-agnostic contract. Same interface for LocalBus and (later) NatsBus."""

    @abstractmethod
    async def subscribe(self, domain: str, handler: Handler) -> None: ...

    @abstractmethod
    async def request(
        self, domain: str, task: Task, timeout_s: Optional[float] = None
    ) -> Result: ...

    @abstractmethod
    async def close(self) -> None: ...


class LocalBus(Bus):
    """Single-process asyncio bus: one handler per domain (Phase 1).

    Failures are isolated — a handler that raises or times out becomes an ERROR/TIMEOUT
    ``Result`` rather than propagating and taking down the orchestrator.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    async def subscribe(self, domain: str, handler: Handler) -> None:
        self._handlers[domain] = handler

    async def request(
        self, domain: str, task: Task, timeout_s: Optional[float] = None
    ) -> Result:
        handler = self._handlers.get(domain)
        if handler is None:
            return Result(task.task_id, Status.ERROR, error=f"no agent for domain '{domain}'")
        timeout = timeout_s if timeout_s is not None else task.deadline_s
        try:
            return await asyncio.wait_for(handler(task), timeout=timeout)
        except asyncio.TimeoutError:
            return Result(task.task_id, Status.TIMEOUT, agent=domain, error="deadline exceeded")
        except Exception as e:  # isolation: never propagate
            return Result(task.task_id, Status.ERROR, agent=domain, error=repr(e))

    async def close(self) -> None:
        self._handlers.clear()
