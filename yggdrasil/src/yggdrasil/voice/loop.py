"""Voice loop — Phase 1 (STUB / design skeleton).

This file documents the intended wake -> listen -> transcribe -> act -> speak state machine
and the chosen components, but is NOT wired into the runtime yet. The text CLI is the working
entrypoint for Phase 0. Implement this on the Debian box where the audio + GPU stack is real;
imports are deferred so the rest of the package runs without the voice extras installed.

Chosen stack (all Apache/MIT, safe to ship in the ISO):
    wake word : openWakeWord   (custom "Jarvis" model trainable offline; light, always-on)
    VAD       : Silero VAD      (endpointing — knows when an utterance/conversation ends)
    STT       : faster-whisper  (large-v3-turbo on GPU; whisper.cpp base.en CPU fallback)
    TTS       : Piper           (default; Kokoro-82M optional HQ voice on GPU)

State machine:
    SLEEPING ──wake word──► LISTENING ──endpoint──► (STT → orchestrator → TTS)
        ▲                                                   │
        └──── conversation timeout / "goodbye" ◄── CONVERSATION_ACTIVE ◄┘
    In CONVERSATION_ACTIVE the wake word is skipped: the mic reopens (after TTS finishes,
    to avoid self-trigger) and listens via VAD for a follow-up until a short timeout.
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Awaitable, Callable

# orchestrator.handle(text) -> (reply_text, conversation_over)
Handler = Callable[[str], Awaitable[tuple[str, bool]]]


class VoiceState(Enum):
    SLEEPING = auto()
    LISTENING = auto()
    CONVERSATION_ACTIVE = auto()


class VoiceLoop:
    """Skeleton only. See module docstring for the implementation plan."""

    def __init__(self, handle: Handler, wake_word: str = "hey_jarvis") -> None:
        self.handle = handle
        self.wake_word = wake_word
        self.state = VoiceState.SLEEPING

    async def run(self) -> None:  # pragma: no cover - not implemented yet
        raise NotImplementedError(
            "Voice loop is a Phase-1 stub. Use the text CLI (`python -m yggdrasil`) for now. "
            "Install voice extras on the Debian box: pip install 'yggdrasil[voice]'."
        )
