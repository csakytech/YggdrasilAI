"""Text-mode entrypoint.

    python -m yggdrasil       (or the `yggdrasil` / `jarvis-text` commands)

Type a goal or a question. Safe actions run immediately; dangerous ones (delete) print an
authorization code to type back as `Authorize <code>`. Set YGGDRASIL_MODEL=qwen3:8b (with
Ollama running) for the LLM planner + conversation; YGGDRASIL_VOICE_MODEL=<piper.onnx> to
hear replies spoken.
"""
from __future__ import annotations

import asyncio
import os

from .core.permissions import AuthChallenge, UserChannel

BANNER = r"""
  Yggdrasil OS - text mode
  Try:  create a folder called Crypto Research
        remember that I trade on weekends
        what's my name?            (answered conversationally)
        delete Crypto Research     (asks for an authorization code)
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


def build_speaker():
    """Optional voice output: set YGGDRASIL_VOICE_MODEL to a Piper .onnx file to hear replies."""
    model = os.environ.get("YGGDRASIL_VOICE_MODEL")
    if not model:
        return None
    from .voice.tts import Speaker

    return Speaker(model)


async def main_async() -> None:
    from .app import build_orchestrator

    bus, orch, file_agent, _store, name = await build_orchestrator(
        ConsoleChannel(), console_auth_resolver
    )
    speaker = build_speaker()

    print(BANNER)
    print(f"  Sandbox: {file_agent.sandbox_root}\n")
    if speaker:
        await asyncio.to_thread(speaker.say, f"{name} online.")
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
