"""Text-to-speech via Piper, played through PipeWire.

Confirmed on FusionOS: `piper -m <voice>.onnx -f out.wav` produces a 22 kHz mono WAV, and
`pw-play out.wav` plays it through PipeWire (ALSA direct access is blocked because PipeWire
owns the device). Piper is CPU-only and faster than real time, so no GPU contention with the
LLM. Speech is treated as non-critical: if it fails, the assistant logs once and carries on.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _audio_env() -> dict:
    """Ensure PipeWire is reachable even when launched outside the desktop session
    (e.g. over SSH): XDG_RUNTIME_DIR must point at the user's runtime dir."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _resolve_piper(explicit: str | None) -> list[str]:
    """Locate Piper whether or not the venv is 'activated'. Running `venv/bin/python`
    directly does NOT put `venv/bin` on PATH, so the `piper` console script next to the
    interpreter must be found explicitly (this was a real bug)."""
    if explicit:
        return [explicit]
    sibling = Path(sys.executable).with_name("piper")
    if sibling.exists():
        return [str(sibling)]
    found = shutil.which("piper")
    if found:
        return [found]
    return [sys.executable, "-m", "piper"]  # last resort


class Speaker:
    """Synthesize speech with Piper and play it through PipeWire."""

    def __init__(self, voice_model, piper_bin=None, player="pw-play") -> None:
        self.voice = str(Path(voice_model).expanduser())
        if not Path(self.voice).is_file():
            raise FileNotFoundError(f"Piper voice model not found: {self.voice}")
        self.piper_cmd = _resolve_piper(piper_bin)
        self.player = player
        self._env = _audio_env()
        self._warned = False

    def synthesize(self, text: str, out_path) -> str:
        """Render text to a WAV file. Returns the path."""
        out = str(out_path)
        subprocess.run(
            [*self.piper_cmd, "-m", self.voice, "-f", out],
            input=text.encode(),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self._env,
        )
        return out

    def say(self, text: str) -> None:
        """Speak text aloud (blocking — the mic should be closed during playback so the
        assistant doesn't hear itself). Never raises: voice is an enhancement, not a
        dependency."""
        text = (text or "").strip()
        if not text:
            return
        wav = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                wav = tf.name
            self.synthesize(text, wav)
            subprocess.run(
                [self.player, wav],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._env,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            if not self._warned:
                print(f"[voice] TTS unavailable ({e}); continuing without speech.", file=sys.stderr)
                self._warned = True
        finally:
            if wav:
                try:
                    os.unlink(wav)
                except OSError:
                    pass
