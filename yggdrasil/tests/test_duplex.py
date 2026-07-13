"""Full-duplex conversation (v1.4) — the pure logic: echo detection and cancellable speech.

The mic-monitoring loop itself needs real audio hardware (RC install QA covers it); these
tests pin the two pieces that can be verified deterministically: (1) the assistant never
answers its own reflected voice, and (2) playback actually stops when barged in.
"""
from __future__ import annotations

import threading
import time

import pytest

from yggdrasil.voice.loop import sounds_like_echo, strip_wake_name


# ---- echo guard ----

@pytest.mark.parametrize("heard, spoken", [
    # verbatim and partial reflections of the assistant's own reply
    ("the weather in oslo is twelve degrees", "The weather in Oslo is twelve degrees and cloudy."),
    ("twelve degrees and cloudy", "The weather in Oslo is twelve degrees and cloudy."),
    ("weather in oslo is twelve", "The weather in Oslo is twelve degrees and cloudy."),
    # STT mangles a word or two of the echo — still ≥80% overlap
    ("the weather in oslo his twelve degrees and cloudy",
     "The weather in Oslo is twelve degrees and cloudy."),
])
def test_reflected_speech_is_echo(heard, spoken):
    assert sounds_like_echo(heard, spoken)


@pytest.mark.parametrize("heard, spoken", [
    ("stop", "The weather in Oslo is twelve degrees and cloudy."),
    ("no not that one", "Opening the documents folder."),
    ("actually install kdenlive instead", "Done — OBS Studio is installed."),
    ("what about tomorrow", "The weather in Oslo is twelve degrees and cloudy."),
    ("jarvis open the browser", "Here are the files in your workspace."),
])
def test_real_interruptions_are_not_echo(heard, spoken):
    assert not sounds_like_echo(heard, spoken)


def test_empty_never_echo():
    assert not sounds_like_echo("", "anything")
    assert not sounds_like_echo("anything", "")


# ---- cancellable playback ----

class _FakeProc:
    """Pretends to be a 2s pw-play; records whether it was terminated."""

    def __init__(self):
        self.terminated = False
        self._t0 = time.time()

    def poll(self):
        return 0 if self.terminated or time.time() - self._t0 > 2.0 else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def _speaker(tmp_path, monkeypatch, procs):
    from yggdrasil.voice import tts

    voice = tmp_path / "voice.onnx"
    voice.write_bytes(b"fake")
    monkeypatch.setattr(tts.Speaker, "synthesize",
                        lambda self, text, out: open(out, "wb").close() or str(out))
    monkeypatch.setattr(tts.subprocess, "Popen",
                        lambda *a, **kw: procs.append(_FakeProc()) or procs[-1])
    return tts.Speaker(str(voice))


def test_bargein_stops_playback(tmp_path, monkeypatch):
    procs = []
    sp = _speaker(tmp_path, monkeypatch, procs)
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    t0 = time.time()
    completed = sp.say_cancellable("a long reply the user talks over", cancel)
    assert completed is False
    assert procs and procs[0].terminated
    assert time.time() - t0 < 1.0  # stopped promptly, not after the full 2s "playback"


def test_uninterrupted_playback_completes(tmp_path, monkeypatch):
    procs = []
    sp = _speaker(tmp_path, monkeypatch, procs)
    procs_done = sp.say_cancellable("short reply", threading.Event())
    assert procs_done is True
    assert procs and not procs[0].terminated


def test_wake_name_still_strips_inside_conversation():
    assert strip_wake_name("Jarvis", "Jarvis, open the browser") == "open the browser"
    assert strip_wake_name("Jarvis", "what about tomorrow") is None
