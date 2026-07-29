"""Vision Agent — Jarvis looks at the screen, describes it, and clicks what you name.

Two rungs of the sight ladder:
  - READ-ONLY (vision.look): capture the screen and describe it / read it / answer a question.
  - CONTROL (vision.click, vision.scroll): the vision model GROUNDS the element you named to a
    pixel, and xdotool clicks or scrolls it. "click the Watch Demo button", "scroll down".

Everything stays on the machine — the screenshot never leaves it. Capture is silent (no shutter,
no flash) so it feels seamless.
"""
from __future__ import annotations

import json
import re
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

_LOCATE = (
    "You are the eyes of a voice assistant that can click the screen. You are shown a screenshot. "
    "Find the on-screen element the user wants to click and return ONLY a compact JSON object:\n"
    '{"found": true, "x_pct": <0-100>, "y_pct": <0-100>, "label": "<what you found>"}\n'
    "x_pct and y_pct are the CENTER of that element as a percentage of the image width and "
    "height (top-left is 0,0). If you cannot find it, return {\"found\": false, \"label\": \"\"}. "
    "Never guess a location — only return found:true if you actually see the element."
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
        'click the watch demo button -> {"steps":[{"action":"vision.click","argument":"the Watch Demo button"}]}',
        'click the blue subscribe button -> {"steps":[{"action":"vision.click","argument":"the blue Subscribe button"}]}',
        'press the X to close that -> {"steps":[{"action":"vision.click","argument":"the X close button"}]}',
        'scroll down -> {"steps":[{"action":"vision.scroll","argument":"down"}]}',
        'scroll back up -> {"steps":[{"action":"vision.scroll","argument":"up"}]}',
    ]
    capabilities = {
        "look": Capability("look", dangerous=False,
                           description="Look at the screen and describe it or answer a question about it"),
        "click": Capability("click", dangerous=False,
                            description="Find an on-screen element by name and click it"),
        "scroll": Capability("scroll", dangerous=False, description="Scroll the screen up or down"),
        "confirm": Capability("confirm", dangerous=False,
                              description="Agree to download the vision model"),
        "cancel": Capability("cancel", dangerous=False,
                             description="Decline downloading the vision model"),
    }

    def __init__(self, bus, perms, vision_llm=None, models=None) -> None:
        super().__init__(bus, perms)
        self.vision_llm = vision_llm  # the 'vision' RoleProvider
        self.models = models          # ModelManager, for the "is the VLM installed?" check + pull

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        arg = (params.get("argument") or "").strip()
        if verb == "look":
            r = await self._look(arg)
            return r if isinstance(r, dict) else {"speech": r}
        if verb == "click":
            r = await self._click(arg)
            return r if isinstance(r, dict) else {"speech": r}
        if verb == "scroll":
            return {"speech": self._scroll(arg)}
        if verb == "confirm":
            return {"speech": self._start_vlm_download()}
        if verb == "cancel":
            return {"speech": "Alright — I won't download it. Everything else still works; "
                              "just ask again if you change your mind."}
        raise ValueError(f"unhandled verb '{verb}'")

    def _scroll(self, arg: str) -> str:
        if not screen.available():
            return "I can only scroll when you're signed in at the desktop."
        direction = "up" if re.search(r"\bup\b|\btop\b|\bback up\b", arg, re.I) else "down"
        amount = 10 if re.search(r"\ba lot\b|\ball the way\b|\bfar\b", arg, re.I) else 5
        if screen.scroll(direction, amount):
            return ""  # seamless — no chatter for a scroll (verbosity 'off'-style by nature)
        return "I couldn't scroll — the screen control tool isn't available."

    async def _click(self, description: str) -> str:
        if self.vision_llm is None:
            return "I need a vision model to see the screen before I can click."
        if not screen.available():
            return "I can only click on the screen when you're signed in at the desktop."
        geo = screen.geometry()
        if not geo:
            return "I couldn't read the screen size, so I can't click accurately."
        if not description:
            return "What would you like me to click?"
        screen.wake()  # a blanked display captures as pure black — wake it before looking
        img = screen.capture_b64()
        if not img:
            return "I couldn't capture the screen just now."
        if await self._need_vlm_download():
            return self._vlm_gate_msg()
        prompt = f"The user wants to click: {description}"
        try:
            resp = await self.vision_llm.describe_image(
                system=_LOCATE, prompt=prompt, image_b64=img, temperature=0.0)
        except Exception:
            return ("I couldn't look at the screen — my vision model may still be downloading. "
                    "Give it a few minutes and try again.")
        loc = self._parse_location(resp.text)
        if not loc or not loc.get("found"):
            return f"I looked, but I couldn't find {description} on the screen."
        w, h = geo
        x = int(w * max(0.0, min(100.0, float(loc.get("x_pct", -1)))) / 100.0)
        y = int(h * max(0.0, min(100.0, float(loc.get("y_pct", -1)))) / 100.0)
        if loc.get("x_pct", -1) < 0 or loc.get("y_pct", -1) < 0:
            return f"I couldn't pin down where {description} is on the screen."
        if not screen.click_at(x, y):
            return "I found it, but the screen control tool isn't available to click."
        what = loc.get("label") or description
        return f"Clicked {what}."

    async def _need_vlm_download(self) -> bool:
        """True if the vision model isn't installed. Deliberately does NOT start the download —
        see _vlm_gate_msg. The VLM is a separate ~3GB fetch from the text model."""
        model = getattr(self.vision_llm, "model", "")
        if self.models is None or not model:
            return False
        try:
            installed = {m["name"] for m in await self.models.installed()}
        except Exception:
            return False
        if model in installed or any(n.split(":")[0] == model.split(":")[0] for n in installed):
            return False
        self._pulling = model
        return True

    def _pull_state(self) -> dict:
        model = getattr(self, "_pulling", "") or getattr(self.vision_llm, "model", "")
        try:
            return (self.models.pull_status() or {}).get(model) or {}
        except Exception:
            return {}

    def _vlm_gate_msg(self) -> dict:
        """The model is missing. ASK before spending 3GB of someone's connection.

        It used to call start_pull() the instant anything needed sight, so a MISROUTE became a
        multi-gigabyte download: "Preview Ryan" (a voice-picker command) once kicked one off.
        That is not ours to decide — it may be a metered or slow connection, and the user may
        simply not want the feature. And on a machine with no internet the old message claimed
        a download was under way that could never happen, which is the fabrication this project
        exists to avoid. So: offer, explain what it buys, and wait for a yes."""
        st = self._pull_state()
        if st.get("error"):
            return {"speech": "I tried to download my vision model but couldn't reach the "
                              "internet. I'll need a connection for that one — everything else "
                              "still works without it."}
        if st and not st.get("done"):
            pct = int(st.get("pct") or 0)
            return {"speech": f"I'm still downloading my vision model — {pct}% so far. "
                              "Ask me again once it's finished."}
        return {"speech": "I can't see the screen yet — that needs my vision model. It's about "
                          "3 gigabytes, downloaded once, and after that it works offline and "
                          "lets me describe what's on screen and click things for you. "
                          "Shall I download it? Say yes or no.",
                "await_confirm": True, "agent": self.domain}

    def _start_vlm_download(self) -> str:
        model = getattr(self, "_pulling", "") or getattr(self.vision_llm, "model", "")
        if not model or self.models is None:
            return "I don't have a vision model configured to download."
        try:
            self.models.start_pull(model)
        except Exception:
            return "I couldn't start the download just now."
        return (f"Downloading my vision model ({model.split(':')[0]}) — about 3 gigabytes. "
                "I'll be able to see the screen once it's done; ask me again in a few minutes.")

    @staticmethod
    def _parse_location(text: str) -> dict | None:
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    async def _look(self, question: str) -> str:
        if self.vision_llm is None:
            return "I need a vision model to look at the screen. There isn't one set up yet."
        if not screen.available():
            return "I can only look at the screen when you're signed in at the desktop."
        screen.wake()  # a blanked display captures as pure black — wake it before looking
        img = screen.capture_b64()
        if not img:
            return "I couldn't capture the screen just now."
        if await self._need_vlm_download():
            return self._vlm_gate_msg()
        prompt = question or "Describe what is on the screen right now."
        try:
            resp = await self.vision_llm.describe_image(
                system=_DESCRIBE, prompt=prompt, image_b64=img, temperature=0.2)
            return resp.text.strip() or "I looked, but couldn't make out what's on the screen."
        except Exception:
            return ("I couldn't look at the screen — my vision model may still be downloading. "
                    "Give it a few minutes and try again.")
