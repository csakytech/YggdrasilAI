"""LLM provider interface + Ollama implementation + VRAM->model tier table.

Local-first: ``OllamaProvider`` talks to a local Ollama daemon and uses schema-constrained
decoding so small models cannot emit malformed output. The orchestrator/agents depend only
on ``LLMProvider``; a cloud provider can be added behind the same interface as an opt-in.
See docs/ARCHITECTURE.md (ADR-0002).
"""
from __future__ import annotations

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

    async def generate(self, *, system, prompt, schema=None, temperature=0.2):
        import json

        import httpx

        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if schema is not None:
            payload["format"] = schema  # JSON-schema constrained decoding

        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self.host}/api/chat", json=payload)
            r.raise_for_status()
            content = r.json()["message"]["content"]

        parsed = None
        if schema is not None:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
        return LLMResponse(text=content, parsed=parsed, raw=content)
