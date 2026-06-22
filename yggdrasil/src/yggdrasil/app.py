"""Shared assembly of the Yggdrasil agent stack — used by the text CLI and the voice loop so
both get the same agents, memory, planner, and conversational ability."""
from __future__ import annotations

import os
from pathlib import Path

from .agents.file_agent import FileAgent
from .agents.memory_agent import MemoryAgent
from .core.bus import LocalBus
from .core.memory import MemoryStore
from .core.orchestrator import AuthResolver, HeuristicPlanner, LLMPlanner, Orchestrator
from .core.permissions import DefaultPolicy, PermissionManager, UserChannel
from .core.registry import Registry


async def build_orchestrator(channel: UserChannel, auth_resolver: AuthResolver):
    """Wire bus + permissions + agents + memory + planner into an Orchestrator.

    Returns (bus, orchestrator, file_agent, memory_store, assistant_name). With YGGDRASIL_MODEL
    set, uses the LLM planner + conversation; otherwise the no-model heuristic planner.
    """
    sandbox = Path(os.environ.get("YGGDRASIL_SANDBOX", Path.home() / "YggdrasilSandbox"))
    name = os.environ.get("YGGDRASIL_NAME", "Jarvis")

    bus = LocalBus()
    perms = PermissionManager(DefaultPolicy(), channel)

    # Register the Core agents (these are our first dogfooded "modules"). On-disk module
    # loading + profiles plug in here later — see docs/MODULES.md.
    registry = Registry()
    file_agent = FileAgent(bus, perms, sandbox_root=sandbox)
    store = MemoryStore()
    registry.register(file_agent)
    registry.register(MemoryAgent(bus, perms, store))
    await registry.start_all()

    model = os.environ.get("YGGDRASIL_MODEL")
    llm = None
    if model:
        from .core.llm import OllamaProvider

        llm = OllamaProvider(model)
        planner = LLMPlanner(
            llm,
            allowed_actions=registry.allowed_actions(),
            examples=registry.planner_examples(),
        )
    else:
        planner = HeuristicPlanner()

    orch = Orchestrator(bus, perms, planner, auth_resolver, memory=store, llm=llm, assistant_name=name)
    return bus, orch, file_agent, store, name
