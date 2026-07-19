"""LLM provider interface + Ollama implementation + VRAM->model tier table.

Local-first: ``OllamaProvider`` talks to a local Ollama daemon and uses schema-constrained
decoding so small models cannot emit malformed output. The orchestrator/agents depend only
on ``LLMProvider``; a cloud provider can be added behind the same interface as an opt-in.
See docs/ARCHITECTURE.md (ADR-0002).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

# VRAM (MiB) -> (floor, model tag, note). First-boot detection picks the highest tier at
# or below detected VRAM. Mirrored by yggdrasil-iso first-boot logic. Tags are [VERIFY] at
# build time — the local-model landscape moves fast.
MODEL_TIERS: list[tuple[int, str, str]] = [
    (24000, "qwen3:32b", "24GB+: best agentic tier"),
    (16000, "qwen3:14b", "16GB: planner resident + small worker"),
    (12000, "qwen3:14b", "12GB (RTX 3060): default; drop to qwen3:8b if running voice+image"),
    (6000, "qwen3:8b", "6-8GB: single model"),
    (0, "llama3.2:3b", "CPU-only / no GPU: degraded, warn user"),
]


def select_model_for_vram(vram_mib: int) -> str:
    for floor, tag, _ in MODEL_TIERS:
        if vram_mib >= floor:
            return tag
    return MODEL_TIERS[-1][1]


@dataclass(slots=True)
class LLMResponse:
    text: str
    parsed: Optional[dict] = None  # schema-validated object when a schema was given
    raw: Any = None


class LLMProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: Optional[dict] = None,
        temperature: float = 0.2,
    ) -> LLMResponse: ...


class OllamaProvider(LLMProvider):
    """Local Ollama via its HTTP API.

    Passing ``schema`` sets Ollama's ``format`` field, which constrains generation to valid
    JSON for that schema (grammar-constrained decoding) — the single biggest reliability win
    for small local models.
    """

    def __init__(self, model: str, host: str = "http://127.0.0.1:11434") -> None:
        self.model = model
        self.host = host.rstrip("/")
        # Per-model residency policy (core.models sets this): -1 pins the model in VRAM
        # (the planner — it answers every utterance), "10m" lets a specialist idle out.
        # None = leave it to the daemon's OLLAMA_KEEP_ALIVE.
        self.keep_alive: Any = None

    async def generate(self, *, system, prompt, schema=None, temperature=0.2):
        return await self._request(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            schema=schema, temperature=temperature)

    async def describe_image(self, *, system, prompt, image_b64, temperature=0.2):
        """Vision: ask a multimodal model about an image (base64 PNG/JPEG, no data: prefix).
        Ollama attaches images via the message's ``images`` array. Same timeout/retry path as
        generate(); a non-vision model simply ignores the image, so the caller should route a
        real VLM here (the 'vision' model role)."""
        return await self._request(
            [{"role": "system", "content": system},
             {"role": "user", "content": prompt, "images": [image_b64]}],
            schema=None, temperature=temperature)

    async def chat(self, *, messages, temperature=0.7):
        """Multi-turn conversation — the whole message history each call (the Chat window's
        'just talk' mode). Same think-off, timeout, and retry behavior as generate()."""
        return await self._request(list(messages), schema=None, temperature=temperature)

    async def _request(self, messages, *, schema, temperature):
        import json

        import httpx

        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": temperature},
            "messages": messages,
        }
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        # Ollama 0.30+ surfaces qwen3-style chain-of-thought as a separate channel. The old
        # ``/no_think`` prompt trick no longer disables it, so the model reasons before every reply —
        # pure latency for our direct-answer prompts (and it can starve a length-capped response).
        # Turn reasoning off natively for thinking-capable models (harmless/absent for others).
        if any(t in self.model.lower() for t in ("qwen3", "qwq", "deepseek-r1")):
            payload["think"] = False
        if schema is not None:
            payload["format"] = schema  # JSON-schema constrained decoding

        content, last_err = None, None
        # Fast CONNECT (a down daemon fails in ~10s) but a generous READ: loading a multi-GB
        # model into VRAM — or swapping two models on a small GPU — legitimately takes longer
        # than a chat reply. Normal responses are ~3-10s with the model resident; the long read
        # ceiling only bites on a cold load. Override with YGGDRASIL_LLM_READ_TIMEOUT.
        read_to = float(os.environ.get("YGGDRASIL_LLM_READ_TIMEOUT", "180"))
        timeout = httpx.Timeout(read_to, connect=10.0, write=15.0, pool=10.0)
        for _attempt in range(2):  # one retry — Ollama can be briefly busy (e.g. model load)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    r = await client.post(f"{self.host}/api/chat", json=payload)
                    r.raise_for_status()
                    content = r.json()["message"]["content"]
                break
            except httpx.HTTPError as e:
                last_err = e
        if content is None:
            raise last_err if last_err is not None else RuntimeError("LLM request failed")

        parsed = None
        if schema is not None:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
        return LLMResponse(text=content, parsed=parsed, raw=content)
