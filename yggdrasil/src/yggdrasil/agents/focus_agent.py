"""Focus Agent — interact with whatever window is currently focused (X11).

Types text, presses keys, and runs commands in the active terminal via xdotool, so the system
feels context-aware: after you open a terminal, "list files" becomes `ls` typed into it (the
planner does that routing using the active-window context — see core/focus.py + orchestrator).
Needs X11 (GDM WaylandEnable=false) + xdotool. `focus.enter` reuses the command denylist so a
mis-heard command can't run something catastrophic in the terminal.
"""
from __future__ import annotations

import re
import subprocess
from typing import Any

from ..core.focus import active_window
from ..core.permissions import Capability
from .base import BaseAgent
from .command_agent import _DENY  # reuse the catastrophic-command denylist


def _xdo(args: list[str]) -> bool:
    try:
        subprocess.run(["xdotool", *args], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


class FocusAgent(BaseAgent):
    domain = "focus"
    module_id = "core.focus"
    planner_examples = [
        'type hello world -> {"steps":[{"action":"focus.type","argument":"hello world"}]}',
        'press enter -> {"steps":[{"action":"focus.key","argument":"Return"}]}',
        'press escape -> {"steps":[{"action":"focus.key","argument":"Escape"}]}',
    ]
    capabilities = {
        "type": Capability("type", dangerous=False, description="Type text into the focused window"),
        "key": Capability("key", dangerous=False, description="Press a key in the focused window"),
        "enter": Capability("enter", dangerous=False, description="Run a command in the focused terminal"),
    }

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        text = (params.get("argument") or "").strip()
        name, kind = active_window()
        if not kind:
            return {"speech": "There's no focused window to act on — open one at the desktop first."}

        if verb == "type":
            if not text:
                return {"speech": "Type what?"}
            _xdo(["type", "--clearmodifiers", "--", text])
            return {"speech": f"Typed it into {name or 'the window'}."}

        if verb == "key":
            _xdo(["key", "--clearmodifiers", text or "Return"])
            return {"speech": "Done."}

        if verb == "enter":
            if not text:
                return {"speech": "Run what?"}
            if any(re.search(p, text.lower()) for p in _DENY):
                return {"speech": f"I won't run that in the terminal — it looks destructive: {text}"}
            _xdo(["type", "--clearmodifiers", "--", text])
            _xdo(["key", "Return"])
            return {"speech": f"Ran {text}."}

        raise ValueError(f"unhandled verb '{verb}'")
