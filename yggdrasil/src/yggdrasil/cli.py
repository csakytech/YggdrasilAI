"""Text-mode entrypoint — runs the whole Phase-0 spine today.

    python -m yggdrasil

Type a goal. Safe actions run immediately; dangerous ones (delete) print an authorization
code you must type back as `Authorize <code>`. With no model configured it uses the
heuristic planner; set YGGDRASIL_MODEL=qwen3:8b (with Ollama running) to use the LLM planner.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .agents.file_agent import FileAgent
from .core.bus import LocalBus
from .core.orchestrator import HeuristicPlanner, LLMPlanner, Orchestrator, Planner
from .core.permissions import AuthChallenge, DefaultPolicy, PermissionManager, UserChannel

BANNER = r"""
  Yggdrasil OS - Phase 0 spine (text mode)
  Try:  create a folder called Crypto Research
        delete Crypto Research        (requires an authorization code)
  'exit' or Ctrl-C to quit.
"""


class ConsoleChannel(UserChannel):
    async def present_challenge(self, challenge: AuthChallenge) -> None:
        print(f"\n  [AUTH REQUIRED] {challenge.summary}")
        print(f"  To approve, type:  Authorize {challenge.code}\n")


async def console_auth_resolver(challenge: AuthChallenge) -> str:
    # Accept "Authorize <code>" or a bare "<code>"; return the last token.
    line = await asyncio.to_thread(input, "  > ")
    parts = line.strip().split()
    return parts[-1] if parts else ""


def build_planner(file_agent: FileAgent) -> Planner:
    model = os.environ.get("YGGDRASIL_MODEL")
    if model:
        from .core.llm import OllamaProvider

        allowed = [f"file.{verb}" for verb in file_agent.capabilities]
        return LLMPlanner(OllamaProvider(model), allowed_actions=allowed)
    return HeuristicPlanner()


def build_speaker():
    """Optional voice output: set YGGDRASIL_VOICE_MODEL to a Piper .onnx file to hear replies."""
    model = os.environ.get("YGGDRASIL_VOICE_MODEL")
    if not model:
        return None
    from .voice.tts import Speaker

    return Speaker(model)


async def main_async() -> None:
    sandbox = Path(os.environ.get("YGGDRASIL_SANDBOX", Path.home() / "YggdrasilSandbox"))
    bus = LocalBus()
    perms = PermissionManager(DefaultPolicy(), ConsoleChannel())
    file_agent = FileAgent(bus, perms, sandbox_root=sandbox)
    await file_agent.start()
    orch = Orchestrator(bus, perms, build_planner(file_agent), console_auth_resolver)
    speaker = build_speaker()

    print(BANNER)
    print(f"  Sandbox: {file_agent.sandbox_root}\n")
    if speaker:
        await asyncio.to_thread(speaker.say, "Yggdrasil online.")
    while True:
        try:
            goal = await asyncio.to_thread(input, "you > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if goal.strip().lower() in {"exit", "quit"}:
            break
        if not goal.strip():
            continue
        reply = await orch.handle(goal)
        print(f"jarvis > {reply}\n")
        if speaker:
            await asyncio.to_thread(speaker.say, reply)
    await bus.close()


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
