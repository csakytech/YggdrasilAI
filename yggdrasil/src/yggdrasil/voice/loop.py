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

from ..core import config

SR = 16000
BLOCK = 1280  # 80 ms frames — openWakeWord's expected input size
DEF_WAKE_THRESHOLD = 0.5
DEF_VAD_ENERGY = 300.0  # int16 RMS; tune per microphone
ENDPOINT_SILENCE_S = 0.8
MAX_UTTERANCE_S = 15.0
NO_SPEECH_GIVEUP_S = 4.0
CONVERSATION_WINDOW_S = 10.0


def _wake_display() -> None:
    """Light the screen back up when spoken to. Speaking to the assistant IS user activity,
    but GNOME only counts mouse/keyboard — so the display blanks mid-conversation and stays
    dark while commands run. On X11 (every NVIDIA install): DPMS the panel on AND inject a
    harmless Shift press so the idle timer resets like a real mouse-wiggle (DPMS alone
    re-blanks moments later; GNOME's SimulateUserActivity D-Bus method no longer exists —
    verified live). Wayland: best-effort shield deactivate. Fire-and-forget: never raises."""
    import subprocess

    def fire(cmd: list[str]) -> None:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        fire(["xset", "s", "reset"])
        fire(["xset", "dpms", "force", "on"])
        fire(["xdotool", "key", "--clearmodifiers", "shift"])
    elif os.environ.get("WAYLAND_DISPLAY"):
        fire(["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
              "--object-path", "/org/gnome/ScreenSaver",
              "--method", "org.gnome.ScreenSaver.SetActive", "false"])


def _wakeword_path(name: str) -> str:
    import openwakeword as ow

    for p in ow.get_pretrained_model_paths():
        if name in os.path.basename(p):
            return p
    raise RuntimeError(f"wake word '{name}' not found in openWakeWord resources")


def strip_wake_name(name: str, text: str) -> str | None:
    """If ``text`` opens with the wake name (optionally after a 'hey'/'ok'), return the rest of the
    request; otherwise None. Lets you say just the name — "Athena, open my doc" — or the name alone
    (returns "")."""
    rx = re.compile(r"^\s*(?:hey|hi|ok|okay|yo)?[\s,]*" + re.escape(name) + r"[\s,.:;!?-]*", re.I)
    m = rx.match(text)
    return text[m.end():].strip() if m else None


_WORDS = re.compile(r"[a-z0-9']+")


def sounds_like_echo(heard: str, spoken: str) -> bool:
    """True when what the mic 'heard' during/after playback is really the assistant's OWN
    voice bleeding from the speakers (no acoustic echo cancellation, or an imperfect one).
    Two signals: the heard words are a near-subset of what was being spoken, or the two texts
    are highly similar overall. Deliberately conservative — a false positive here swallows a
    real user interruption, which is worse than an occasional self-reply glitch."""
    import difflib

    h = _WORDS.findall((heard or "").lower())
    s = _WORDS.findall((spoken or "").lower())
    if not h or not s:
        return False
    hs, ss = set(h), set(s)
    if len(hs - ss) <= max(0, len(hs) // 5):  # ≥80% of heard words appear in the spoken text
        return True
    return difflib.SequenceMatcher(None, " ".join(h), " ".join(s)).ratio() > 0.75


class VoiceAssistant:
    """Owns the mic stream and the wake/endpoint logic. `on_text(text)` returns
    (reply, conversation_over)."""

    def __init__(self, on_text, speaker, recognizer, greeting="Yggdrasil online.") -> None:
        import numpy as np
        import sounddevice as sd

        self.np, self.sd = np, sd
        self.on_text = on_text
        self.speaker = speaker
        self.recognizer = recognizer
        self.greeting = greeting
        self.wake_threshold = float(os.environ.get("YGGDRASIL_WAKE_THRESHOLD", DEF_WAKE_THRESHOLD))
        self.vad_energy = float(os.environ.get("YGGDRASIL_VAD_ENERGY", DEF_VAD_ENERGY))
        self.wake_mode = config.get_wake_mode()  # "name" (say the name) | "model" (openWakeWord)
        # Full duplex: keep listening WHILE speaking, so the user can interrupt ("barge in")
        # and follow-ups flow like a real conversation. Settings toggle (config) with the
        # YGGDRASIL_DUPLEX env as an override; =0 restores strict turn-taking.
        self.duplex = config.get_duplex()
        self.barge_mult = float(os.environ.get("YGGDRASIL_BARGE_MULT", "2.0"))
        self.wake = self.wake_key = None
        if self.wake_mode == "model":
            from openwakeword.model import Model

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

    def _speak_with_bargein(self, text: str):
        """Speak the reply while WATCHING THE MIC (full duplex). Returns None if the reply
        played out normally; on barge-in, stops the voice mid-sentence and returns the mic
        frames of the user's opening words (prefill for capture_text).

        Echo strategy: the speakers bleed into the mic, so a fixed threshold can't tell the
        user from the assistant. A rolling MEDIAN of the mic level during THIS playback is the
        echo envelope for this room/volume (median resists brief user speech); a real
        interruption is ~320ms of sustained level well above both that envelope and the normal
        VAD floor. A PipeWire echo-cancelled source (dev/ISO config) makes this trivially
        reliable; the envelope keeps it workable without one."""
        import threading

        if not self.duplex:
            self.speaker.say(text)
            return None
        np = self.np
        cancel = threading.Event()
        done = threading.Event()

        def _play():
            try:
                self.speaker.say_cancellable(text, cancel)
            finally:
                done.set()

        t = threading.Thread(target=_play, daemon=True)
        t.start()
        levels: list[float] = []  # rolling window of playback-echo loudness
        consec, barge = 0, []
        while not done.is_set():
            try:
                frame = self._read()
            except Exception:
                break  # mic hiccup — let the reply finish; outer loops recover the stream
            r = self._rms(frame)
            levels.append(r)
            del levels[:-25]  # keep ~2s
            envelope = sorted(levels)[len(levels) // 2]
            threshold = max(self.vad_energy * self.barge_mult, envelope * 2.5)
            if r >= threshold:
                consec += 1
                barge.append(frame)
                if consec >= 4:  # ~320ms of sustained speech over the echo — a real barge-in
                    cancel.set()
                    break
            else:
                consec, barge = 0, []
        t.join(timeout=3)
        if cancel.is_set() and barge:
            print("[barge-in]", file=sys.stderr, flush=True)
            return np.concatenate(barge)
        return None

    def _converse(self, command: str, addressed: bool) -> float:
        """One conversational exchange, full duplex: answer, and if the user interrupts or
        follows up, keep the thread going — no wake word needed inside the conversation.
        Returns the new conversation_until deadline."""
        while True:
            print(f"you (voice) > {command}{'' if addressed else '  [follow-up]'}", flush=True)
            reply, over = self.on_text(command, addressed)
            print(f"jarvis > {reply}", flush=True)
            prefill = self._speak_with_bargein(reply)
            if prefill is None:
                return 0.0 if over else time.time() + CONVERSATION_WINDOW_S
            heard = self.capture_text(prefill=prefill)
            if not heard or sounds_like_echo(heard, reply):
                # our own voice reflected (or noise) — the reply was cut, but resuming a
                # half-spoken sentence is worse than moving on
                return 0.0 if over else time.time() + CONVERSATION_WINDOW_S
            named = self._strip_name(heard)
            addressed = named is not None
            command = named if named else heard
            if not command:  # bare name mid-conversation — "yes?" and listen fresh
                self.speaker.say("Yes?")
                command = self.capture_text()
                if not command:
                    return time.time() + CONVERSATION_WINDOW_S
                addressed = True

    def run(self) -> None:
        self.speaker.say(self.greeting)
        if self.wake_mode == "model":
            print("Listening for the wake word… say it, then your request. Ctrl-C to quit.", flush=True)
        else:
            print(f'Listening — say "{config.get_name()}" to wake me, then your request. '
                  "Ctrl-C to quit.", flush=True)
        while True:  # OUTER: (re)open the mic stream — recover if it ever breaks
            try:
                with self.sd.InputStream(samplerate=SR, channels=1, dtype="int16",
                                         blocksize=BLOCK) as stream:
                    self._stream = stream
                    print("[mic] stream open", file=sys.stderr, flush=True)
                    self._listen_model() if self.wake_mode == "model" else self._listen_name()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"[voice] mic stream reset ({e!r}); reopening in 1s…",
                      file=sys.stderr, flush=True)
                time.sleep(1)

    def _strip_name(self, text: str) -> str | None:
        return strip_wake_name(config.get_name(), text)  # name read live so a rename takes effect now

    def _listen_name(self) -> None:
        """Name wake mode: transcribe each spoken utterance and act on it only if it opens with the
        assistant's name. Say "Athena, open my doc" in one breath, or just "Athena" then your request."""
        conversation_until = 0.0
        while True:
            try:
                in_convo = time.time() < conversation_until
                text = self.capture_text()  # waits for speech, transcribes; "" if none in ~4s
                if not text:
                    continue
                # Detect the name EVEN inside the conversation window: saying it is the user's
                # "new topic" signal (addressed=True); a nameless follow-up continues the current
                # topic (addressed=False).
                named = self._strip_name(text)  # None = no name; "" = only the name; else remainder
                if named is not None:
                    addressed = True
                    _wake_display()  # spoken to by name -> screen on, like a mouse-wiggle
                    if not named:  # only the name was spoken
                        self.speaker.say("Yes?")
                        command = self.capture_text()
                        if not command:
                            conversation_until = 0.0
                            continue
                    else:
                        command = named
                elif in_convo:
                    addressed = False
                    command = text  # follow-up within the conversation window — same topic
                    _wake_display()
                else:
                    continue  # speech not addressed to us — ignore
                conversation_until = self._converse(command, addressed)
            except KeyboardInterrupt:
                raise
            except self.sd.PortAudioError:
                raise  # bubble up so run() reopens the mic stream
            except Exception as e:
                print(f"[voice] recovered from: {e!r}", file=sys.stderr, flush=True)
                conversation_until = 0.0
                time.sleep(0.2)

    def _listen_model(self) -> None:
        conversation_until = 0.0
        frames, peak, last_beat = 0, 0.0, time.time()
        while True:
            frame = self._read()  # OUTSIDE try: a dead stream bubbles up to run() to reopen
            frames += 1
            # One bad utterance (STT/agent/TTS error) must never stop us — recover and continue.
            try:
                asleep = time.time() >= conversation_until  # woke by wake word = addressed
                if asleep:  # SLEEPING: watch for wake word
                    score = float(self.wake.predict(frame)[self.wake_key])
                    peak = max(peak, score)
                    if time.time() - last_beat >= 10.0:  # heartbeat: proves audio is flowing
                        print(f"[listening] {frames} frames, peak wake {peak:.2f}",
                              file=sys.stderr, flush=True)
                        last_beat, peak = time.time(), 0.0
                    if score < self.wake_threshold:
                        continue
                    print(f"[wake {score:.2f}]", flush=True)
                _wake_display()  # woken by voice -> screen on, like a mouse-wiggle
                text = self.capture_text(prefill=frame)  # LISTENING
                self.wake.reset()
                if not text:
                    conversation_until = 0.0
                    continue
                conversation_until = self._converse(text, asleep)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"[voice] recovered from: {e!r}", file=sys.stderr, flush=True)
                conversation_until = 0.0
                try:
                    self.wake.reset()
                except Exception:
                    pass
                time.sleep(0.2)


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
    from ..core import voices

    voice_model = voices.active_path()  # config choice -> YGGDRASIL_VOICE_MODEL -> any installed
    if not voice_model:
        print("No voice installed — set YGGDRASIL_VOICE_MODEL to a Piper .onnx voice file.",
              file=sys.stderr)
        sys.exit(2)
    from ..app import build_orchestrator
    from ..core.permissions import AuthChallenge, UserChannel
    from .stt import Recognizer
    from .tts import Speaker

    speaker = Speaker(voice_model, voice_source=voices.active_path)
    recognizer = Recognizer()
    holder: dict = {}

    class VoiceChannel(UserChannel):
        async def present_challenge(self, ch: AuthChallenge) -> None:
            spoken = " ".join(ch.code)  # "4 8 2 9 1 7" reads more clearly than "482917"
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
    bus, orch, file_agent, _store, name = loop.run_until_complete(
        build_orchestrator(VoiceChannel(), voice_auth_resolver)
    )

    def on_text(text: str, addressed: bool = True):
        reply = loop.run_until_complete(orch.handle(text, addressed))
        return reply, _ends_conversation(text)

    assistant = VoiceAssistant(on_text, speaker, recognizer, greeting=f"{name} online.")
    holder["a"] = assistant
    print(f"Sandbox: {file_agent.sandbox_root}")

    # Background scheduler: fire due reminders + briefings even while idle. It speaks on its own
    # thread (Speaker.say is locked) and runs briefings via its own research agent + event loop.
    from ..core.scheduler import Runner, shared_schedule

    _model = os.environ.get("YGGDRASIL_MODEL")
    _research = None
    if _model:
        from ..agents.research_agent import ResearchAgent
        from ..core.bus import LocalBus
        from ..core.llm import OllamaProvider
        from ..core.permissions import DefaultPolicy, PermissionManager
        _research = ResearchAgent(LocalBus(), PermissionManager(DefaultPolicy(), VoiceChannel()),
                                  OllamaProvider(_model))

    def _briefing(query: str) -> str:
        if _research is None:
            return "Briefings need a language model."
        return asyncio.run(_research._lookup(query))

    runner = Runner(shared_schedule(), speak=speaker.say, briefing=_briefing)
    runner.start()
    print(f"[scheduler] {len(shared_schedule().list())} job(s) loaded")
    try:
        assistant.run()
    except KeyboardInterrupt:
        print()
    finally:
        runner.stop()
        loop.run_until_complete(bus.close())
        loop.close()


if __name__ == "__main__":
    main()
