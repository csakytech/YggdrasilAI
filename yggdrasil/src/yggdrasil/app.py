"""Shared assembly of the Yggdrasil agent stack — used by the text CLI and the voice loop so
both get the same agents, memory, planner, and conversational ability."""
from __future__ import annotations

import os
from pathlib import Path

from .agents.app_agent import AppsAgent
from .agents.command_agent import CommandAgent
from .agents.document_agent import DocumentsAgent
from .agents.explain_agent import ExplainAgent
from .agents.file_agent import FileAgent
from .agents.focus_agent import FocusAgent
from .agents.market_agent import MarketAgent
from .agents.memory_agent import MemoryAgent
from .agents.research_agent import ResearchAgent
from .agents.scheduler_agent import SchedulerAgent
from .agents.security_agent import SecurityAgent
from .agents.system_agent import SystemAgent
from .agents.task_agent import TaskAgent
from .agents.update_agent import UpdateAgent
from .agents.writer_agent import WriterAgent
from .core import config
from .core.activity import Activity
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
    name = config.get_name()

    bus = LocalBus()
    perms = PermissionManager(DefaultPolicy(), channel)

    model = os.environ.get("YGGDRASIL_MODEL")
    llm = None
    if model:
        from .core.llm import OllamaProvider

        llm = OllamaProvider(model)

    # Register the Core agents (these are our first dogfooded "modules"). On-disk module
    # loading + profiles plug in here later — see docs/MODULES.md.
    registry = Registry()
    file_agent = FileAgent(bus, perms, sandbox_root=sandbox)
    store = MemoryStore()
    registry.register(file_agent)
    registry.register(MemoryAgent(bus, perms, store))
    registry.register(SystemAgent(bus, perms))
    registry.register(AppsAgent(bus, perms, llm, sandbox))
    registry.register(SecurityAgent(bus, perms, llm))
    registry.register(CommandAgent(bus, perms))
    registry.register(TaskAgent(bus, perms, llm, sandbox))
    registry.register(FocusAgent(bus, perms))
    registry.register(DocumentsAgent(bus, perms))
    registry.register(ExplainAgent(bus, perms, llm))
    registry.register(WriterAgent(bus, perms))
    registry.register(ResearchAgent(bus, perms, llm))
    registry.register(SchedulerAgent(bus, perms, llm))
    market = MarketAgent(bus, perms, llm)
    registry.register(market)
    registry.register(UpdateAgent(bus, perms, llm))

    # Installed marketplace agents. In-process for now (trusted/verified only — the sandbox lands
    # before untrusted packets). Core domains are reserved so a packet can't hijack 'file'/'system'/…
    from .core import modules
    reserved = {a.domain for a in registry.agents}
    for agent in modules.load_installed(bus, perms, llm, reserved_domains=reserved):
        registry.register(agent)

    await registry.start_all()

    if llm:
        planner = LLMPlanner(
            llm,
            allowed_actions=registry.allowed_actions(),
            examples=registry.planner_examples(),
        )
    else:
        planner = HeuristicPlanner()

    # When the Market agent installs/removes, hot-load the change into the live registry + planner so
    # the new agent is usable immediately (LLMPlanner reads allowed_actions/examples on every plan()).
    async def _reload_modules():
        have = {a.domain for a in registry.agents}
        for ag in modules.load_installed(bus, perms, llm, reserved_domains=reserved):
            if ag.domain not in have:
                registry.register(ag)
                await ag.start()
        if hasattr(planner, "allowed_actions"):
            planner.allowed_actions = registry.allowed_actions()
            planner.examples = registry.planner_examples()
    market.on_change = _reload_modules

    orch = Orchestrator(bus, perms, planner, auth_resolver, memory=store, llm=llm,
                        assistant_name=name, activity=Activity())
    return bus, orch, file_agent, store, name
