"""Speech-to-text via faster-whisper.

Default is CPU `base.en` (int8): on the i7-3770 it transcribes short commands FASTER than
real-time and keeps the whole GPU free for the LLM brain — the right split for this box.
GPU `large-v3-turbo` is available for higher accuracy later, but needs the CUDA math libs
(`pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` + LD_LIBRARY_PATH) — not installed yet,
so don't default to it. A Piper-generated WAV can be transcribed for a mic-free round-trip
smoke test. See docs/ARCHITECTURE.md voice-stack notes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class Transcript:
    text: str
    language: str
    duration: float


class Recognizer:
    def __init__(
        self,
        model: str = "base.en",
        device: str = "cpu",  # CPU is fast enough for commands; GPU stays free for the LLM
        compute_type: Optional[str] = None,
    ) -> None:
        from faster_whisper import WhisperModel

        if device == "auto":
            device, compute_type = self._auto(compute_type)
        elif compute_type is None:
            compute_type = "float16" if device == "cuda" else "int8"
        self.device = device
        self.compute_type = compute_type
        self.model = WhisperModel(model, device=device, compute_type=compute_type)

    @staticmethod
    def _auto(compute_type: Optional[str]) -> tuple[str, str]:
        """Use CUDA if CTranslate2 can see a GPU (and cuDNN loads), else CPU int8."""
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda", compute_type or "float16"
        except Exception:
            pass
        return "cpu", compute_type or "int8"

    # Anti-hallucination decode options. Whisper invents text ("thanks for watching",
    # motivational lines) when fed silence/quiet audio; vad_filter drops non-speech BEFORE
    # decoding so silence yields nothing, and the thresholds reject low-confidence garbage.
    _DECODE_OPTS = dict(
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        condition_on_previous_text=False,  # stops it looping prior context
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        temperature=0.0,
    )

    def _decode(self, source, language: str) -> Transcript:
        segments, info = self.model.transcribe(source, language=language, **self._DECODE_OPTS)
        text = " ".join(s.text.strip() for s in segments).strip()
        return Transcript(text=text, language=info.language, duration=info.duration)

    def transcribe_file(self, path: str, language: str = "en") -> Transcript:
        return self._decode(path, language)

    def transcribe_array(self, audio, language: str = "en") -> Transcript:
        """Transcribe a float32 numpy array of mono PCM at 16 kHz (live mic path)."""
        return self._decode(audio, language)
