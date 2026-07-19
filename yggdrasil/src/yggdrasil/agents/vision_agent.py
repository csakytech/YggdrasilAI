"""Vision Agent — Jarvis looks at the screen and tells you what's there.

The first rung of the sight ladder: READ-ONLY. It captures the screen and asks a local
multimodal model (the 'vision' model role) to describe it, read it aloud, or answer a question
about it ("what does this error say?", "what's the blue button?"). No clicking, no control —
that's a later, gated rung. Everything stays on the machine: the screenshot never leaves it.

"what am I looking at" / "read the screen" / "what does this say" -> vision.look.
"""
from __future__ import annotations

from typing import Any

from ..core import screen
from ..core.permissions import Capability
from .base import BaseAgent

_DESCRIBE = (
    "You are the eyes of a local voice assistant. You are shown a screenshot of the user's "
    "screen. Describe what's on it in a brief, natural, SPOKEN style — 2 to 4 short sentences, "
    "no markdown, no bullet lists. Lead with what the screen mainly shows (which app or page), "
    "then the key details. If the user asked a specific question, answer THAT directly and "
    "concisely. Read out any error messages verbatim. Never invent UI that isn't visible."
)


class VisionAgent(BaseAgent):
    domain = "vision"
    module_id = "core.vision"
    planner_examples = [
        'what am I looking at -> {"steps":[{"action":"vision.look","argument":""}]}',
        'what is on my screen -> {"steps":[{"action":"vision.look","argument":""}]}',
        'read the screen -> {"steps":[{"action":"vision.look","argument":"read all the text on the screen aloud"}]}',
        'what does this error say -> {"steps":[{"action":"vision.look","argument":"what does the error message say"}]}',
        'can you see what this is -> {"steps":[{"action":"vision.look","argument":""}]}',
    ]
    capabilities = {
        "look": Capability("look", dangerous=False,
                           description="Look at the screen and describe it or answer a question about it"),
    }

    def __init__(self, bus, perms, vision_llm=None, models=None) -> None:
        super().__init__(bus, perms)
        self.vision_llm = vision_llm  # the 'vision' RoleProvider
        self.models = models          # ModelManager, for the "is the VLM installed?" check + pull

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if verb == "look":
            return {"speech": await self._look((params.get("argument") or "").strip())}
        raise ValueError(f"unhandled verb '{verb}'")

    async def _look(self, question: str) -> str:
        if self.vision_llm is None:
            return "I need a vision model to look at the screen. There isn't one set up yet."
        if not screen.available():
            return "I can only look at the screen when you're signed in at the desktop."
        img = screen.capture_b64()
        if not img:
            return "I couldn't capture the screen just now."
        # Is the vision model actually installed? If not, offer to fetch it rather than erroring
        # out with a raw 404 — the VLM is a separate download from the text model.
        model = getattr(self.vision_llm, "model", "")
        if self.models is not None and model:
            try:
                installed = {m["name"] for m in await self.models.installed()}
            except Exception:
                installed = set()
            if model not in installed and not any(n.split(":")[0] == model.split(":")[0] for n in installed):
                self.models.start_pull(model)
                return (f"I'm downloading my vision model ({model.split(':')[0]}) so I can see the "
                        "screen — this happens once. Ask me again in a few minutes.")
        prompt = question or "Describe what is on the screen right now."
        try:
            resp = await self.vision_llm.describe_image(
                system=_DESCRIBE, prompt=prompt, image_b64=img, temperature=0.2)
            return resp.text.strip() or "I looked, but couldn't make out what's on the screen."
        except Exception:
            return ("I couldn't look at the screen — my vision model may still be downloading. "
                    "Give it a few minutes and try again.")
