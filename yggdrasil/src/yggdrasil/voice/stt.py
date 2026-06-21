"""Speech-to-text via faster-whisper.

Auto-selects GPU `large-v3-turbo` (int8_float16) when the CUDA libs are present — fast
(~0.6 s) and accurate — and transparently falls back to CPU `base.en` otherwise. Anti-
hallucination decode options (VAD filter + confidence thresholds) keep silence/quiet audio
from turning into invented text. Override with YGGDRASIL_STT_MODEL / YGGDRASIL_STT_DEVICE.
GPU needs nvidia-cublas-cu12 + nvidia-cudnn-cu12 on LD_LIBRARY_PATH (the `jarvis` launchers
set this). See docs/ARCHITECTURE.md voice-stack notes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Transcript:
    text: str
    language: str
    duration: float


class Recognizer:
    # Whisper invents text ("thanks for watching", motivational lines) on silence/quiet audio;
    # vad_filter drops non-speech BEFORE decoding, and the thresholds reject low-confidence junk.
    _DECODE_OPTS = dict(
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        temperature=0.0,
    )

    def __init__(self, model=None, device=None, compute_type=None) -> None:
        import numpy as np
        from faster_whisper import WhisperModel

        model = model or os.environ.get("YGGDRASIL_STT_MODEL")
        device = device or os.environ.get("YGGDRASIL_STT_DEVICE") or "auto"

        if device == "auto":
            # Prefer GPU large-v3-turbo, but a cuda model can construct yet fail at encode if
            # libcublas/libcudnn aren't loadable — so force one encode to validate, else CPU.
            gpu_model = model or "large-v3-turbo"
            try:
                m = WhisperModel(gpu_model, device="cuda", compute_type=compute_type or "int8_float16")
                list(m.transcribe(np.zeros(16000, dtype=np.float32), language="en")[0])
                self.model, self.device = m, "cuda"
                self.model_name, self.compute_type = gpu_model, compute_type or "int8_float16"
                return
            except Exception:
                pass
            cpu_model = model or "base.en"
            self.model = WhisperModel(cpu_model, device="cpu", compute_type="int8")
            self.device, self.model_name, self.compute_type = "cpu", cpu_model, "int8"
            return

        model = model or ("large-v3-turbo" if device == "cuda" else "base.en")
        compute_type = compute_type or ("int8_float16" if device == "cuda" else "int8")
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        self.device, self.model_name, self.compute_type = device, model, compute_type

    def _decode(self, source, language: str) -> Transcript:
        segments, info = self.model.transcribe(source, language=language, **self._DECODE_OPTS)
        text = " ".join(s.text.strip() for s in segments).strip()
        return Transcript(text=text, language=info.language, duration=info.duration)

    def transcribe_file(self, path: str, language: str = "en") -> Transcript:
        return self._decode(path, language)

    def transcribe_array(self, audio, language: str = "en") -> Transcript:
        """Transcribe a float32 numpy array of mono PCM at 16 kHz (live mic path)."""
        return self._decode(audio, language)
