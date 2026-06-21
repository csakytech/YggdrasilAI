"""Text-to-speech via Piper, played through PipeWire.

Confirmed on FusionOS: `piper -m <voice>.onnx -f out.wav` produces a 22 kHz mono WAV, and
`pw-play out.wav` plays it through PipeWire (ALSA direct access is blocked because PipeWire
owns the device). Piper is CPU-only and faster than real time, so no GPU contention with the
LLM. See docs/ARCHITECTURE.md and ADR notes on the voice stack.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def _audio_env() -> dict:
    """Ensure PipeWire is reachable even when launched outside the desktop session
    (e.g. over SSH): XDG_RUNTIME_DIR must point at the user's runtime dir."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


class Speaker:
    """Synthesize speech with Piper and play it through PipeWire."""

    def __init__(
        self,
        voice_model: str | os.PathLike,
        piper_bin: str = "piper",
        player: str = "pw-play",
    ) -> None:
        self.voice = str(Path(voice_model).expanduser())
        if not Path(self.voice).is_file():
            raise FileNotFoundError(f"Piper voice model not found: {self.voice}")
        self.piper_bin = piper_bin
        self.player = player
        self._env = _audio_env()

    def synthesize(self, text: str, out_path: str | os.PathLike) -> str:
        """Render text to a WAV file. Returns the path."""
        out = str(out_path)
        subprocess.run(
            [self.piper_bin, "-m", self.voice, "-f", out],
            input=text.encode(),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self._env,
        )
        return out

    def say(self, text: str) -> None:
        """Speak text aloud (blocking — the mic should be closed during playback to avoid
        the assistant hearing itself)."""
        text = (text or "").strip()
        if not text:
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav = tf.name
        try:
            self.synthesize(text, wav)
            subprocess.run(
                [self.player, wav],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._env,
            )
        finally:
            try:
                os.unlink(wav)
            except OSError:
                pass
