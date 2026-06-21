"""Voice loop — Phase 1: always-listening "Jarvis".

Pipeline:  wake word (openWakeWord) -> record until silence (energy endpointing)
           -> STT (faster-whisper, CPU) -> orchestrator -> TTS (Piper).
After a reply it stays in a short conversation window so you can give a follow-up without
repeating the wake word. Dangerous actions are spoken back as an authorization challenge and
the code is captured by voice ("Authorize seven one zero ...").

Run on FusionOS (mic + speakers connected):
    XDG_RUNTIME_DIR=/run/user/$(id -u) \
    YGGDRASIL_MODEL=qwen3:8b \
    YGGDRASIL_VOICE_MODEL=~/yggdrasil-voices/en_US-lessac-medium.onnx \
    ~/yggdrasil-venv/bin/python -m yggdrasil.voice.loop

Tunables (env): YGGDRASIL_WAKEWORD (hey_jarvis), YGGDRASIL_WAKE_THRESHOLD (0.5),
YGGDRASIL_VAD_ENERGY (300, raise if it triggers on noise / lower if it misses speech).
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path

SR = 16000
BLOCK = 1280  # 80 ms frames — openWakeWord's expected input size
DEF_WAKE_THRESHOLD = 0.5
DEF_VAD_ENERGY = 300.0  # int16 RMS; tune per microphone
ENDPOINT_SILENCE_S = 0.8
MAX_UTTERANCE_S = 15.0
NO_SPEECH_GIVEUP_S = 4.0
CONVERSATION_WINDOW_S = 10.0


def _wakeword_path(name: str) -> str:
    import openwakeword as ow

    for p in ow.get_pretrained_model_paths():
        if name in os.path.basename(p):
            return p
    raise RuntimeError(f"wake word '{name}' not found in openWakeWord resources")


class VoiceAssistant:
    """Owns the mic stream and the wake/endpoint logic. `on_text(text)` returns
    (reply, conversation_over)."""

    def __init__(self, on_text, speaker, recognizer) -> None:
        import numpy as np
        import sounddevice as sd
        from openwakeword.model import Model

        self.np, self.sd = np, sd
        self.on_text = on_text
        self.speaker = speaker
        self.recognizer = recognizer
        self.wake_threshold = float(os.environ.get("YGGDRASIL_WAKE_THRESHOLD", DEF_WAKE_THRESHOLD))
        self.vad_energy = float(os.environ.get("YGGDRASIL_VAD_ENERGY", DEF_VAD_ENERGY))
        wakeword = os.environ.get("YGGDRASIL_WAKEWORD", "hey_jarvis")
        self.wake = Model(wakeword_model_paths=[_wakeword_path(wakeword)])
        self.wake_key = next(iter(self.wake.models.keys()))
        self._stream = None

    def _read(self):
        block, _ = self._stream.read(BLOCK)
        return block[:, 0] if getattr(block, "ndim", 1) == 2 else block.reshape(-1)

    def _rms(self, frame) -> float:
        f = frame.astype(self.np.float32)
        return float(self.np.sqrt(self.np.mean(f * f))) if f.size else 0.0

    def _record_utterance(self, prefill=None):
        np = self.np
        frames = [prefill] if prefill is not None else []
        voiced, silence, start, step = False, 0.0, time.time(), BLOCK / SR
        while True:
            frame = self._read()
            frames.append(frame)
            if self._rms(frame) >= self.vad_energy:
                voiced, silence = True, 0.0
            elif voiced:
                silence += step
            if voiced and silence >= ENDPOINT_SILENCE_S:
                break
            elapsed = time.time() - start
            if elapsed > MAX_UTTERANCE_S:
                break
            if not voiced and elapsed > NO_SPEECH_GIVEUP_S:
                return None
        return np.concatenate(frames) if frames else None

    def capture_text(self, prefill=None) -> str:
        audio = self._record_utterance(prefill)
        if audio is None or audio.size < int(0.2 * SR):
            return ""
        f32 = audio.astype(self.np.float32) / 32768.0
        return self.recognizer.transcribe_array(f32).text.strip()

    def run(self) -> None:
        with self.sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=BLOCK) as stream:
            self._stream = stream
            self.speaker.say("Yggdrasil online.")
            print("Listening for the wake word… say it, then your request. Ctrl-C to quit.")
            conversation_until = 0.0
            while True:
                frame = self._read()
                if time.time() >= conversation_until:  # SLEEPING: watch for wake word
                    score = float(self.wake.predict(frame)[self.wake_key])
                    if score < self.wake_threshold:
                        continue
                    print(f"[wake {score:.2f}]")
                text = self.capture_text(prefill=frame)  # LISTENING
                self.wake.reset()
                if not text:
                    conversation_until = 0.0
                    continue
                print(f"you (voice) > {text}")
                reply, over = self.on_text(text)
                print(f"jarvis > {reply}")
                self.speaker.say(reply)
                conversation_until = 0.0 if over else time.time() + CONVERSATION_WINDOW_S


_DIGITS = re.compile(r"\d")
_NUMWORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
             "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _extract_code(text: str) -> str:
    digits = "".join(_DIGITS.findall(text))
    if digits:
        return digits
    return "".join(_NUMWORDS.get(w, "") for w in re.findall(r"[a-z]+", text.lower()))


def _ends_conversation(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in ("goodbye", "good bye", "that's all", "thats all",
                                "stop listening", "never mind", "nothing else"))


def main() -> None:
    voice_model = os.environ.get("YGGDRASIL_VOICE_MODEL")
    if not voice_model:
        print("Set YGGDRASIL_VOICE_MODEL to a Piper .onnx voice file.", file=sys.stderr)
        sys.exit(2)
    model = os.environ.get("YGGDRASIL_MODEL")
    sandbox = Path(os.environ.get("YGGDRASIL_SANDBOX", Path.home() / "YggdrasilSandbox"))

    from ..agents.file_agent import FileAgent
    from ..core.bus import LocalBus
    from ..core.orchestrator import HeuristicPlanner, LLMPlanner, Orchestrator
    from ..core.permissions import AuthChallenge, DefaultPolicy, PermissionManager, UserChannel
    from .stt import Recognizer
    from .tts import Speaker

    speaker = Speaker(voice_model)
    recognizer = Recognizer()  # CPU base.en
    holder: dict = {}

    class VoiceChannel(UserChannel):
        async def present_challenge(self, ch: AuthChallenge) -> None:
            spoken = " ".join(ch.code)  # "7 1 0 6 2 8" reads more clearly than "710628"
            speaker.say(f"{ch.summary} requires authorization. Say, authorize {spoken}.")

    async def voice_auth_resolver(ch: AuthChallenge) -> str:
        assistant = holder.get("a")
        if assistant is None:
            return ""
        heard = assistant.capture_text()
        code = _extract_code(heard)
        print(f"[auth heard {heard!r} -> {code!r}]")
        return code

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bus = LocalBus()
    perms = PermissionManager(DefaultPolicy(), VoiceChannel())
    file_agent = FileAgent(bus, perms, sandbox_root=sandbox)
    loop.run_until_complete(file_agent.start())

    if model:
        from ..core.llm import OllamaProvider

        allowed = [f"file.{verb}" for verb in file_agent.capabilities]
        planner = LLMPlanner(OllamaProvider(model), allowed_actions=allowed)
    else:
        planner = HeuristicPlanner()
    orch = Orchestrator(bus, perms, planner, voice_auth_resolver)

    def on_text(text: str):
        reply = loop.run_until_complete(orch.handle(text))
        return reply, _ends_conversation(text)

    assistant = VoiceAssistant(on_text, speaker, recognizer)
    holder["a"] = assistant
    print(f"Sandbox: {file_agent.sandbox_root}")
    try:
        assistant.run()
    except KeyboardInterrupt:
        print()
    finally:
        loop.run_until_complete(bus.close())
        loop.close()


if __name__ == "__main__":
    main()
