"""HelpAgent (domain "help") — context-aware "what can I say?" for wherever the user is.

"Jarvis, help" anywhere → Jarvis works out WHERE you are (an active Development mission, or the
program in front of you — Firefox, a word processor, the terminal, your files, or any app),
writes that context to help.json, opens the small always-on-top Help window listing the exact
commands that work THERE, and speaks a short summary. Works no matter the environment — an
unknown program still gets named and the universal ThorOS commands, so it never dead-ends.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..core import context
from ..core.permissions import Capability
from .base import BaseAgent


def _state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "help.json"


def _write_state(snap: dict) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    except OSError:
        pass


def _has_display() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def _open_window() -> bool:
    if not _has_display():
        return False
    try:
        subprocess.Popen([sys.executable, "-m", "yggdrasil.ui.help"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _close_window() -> str:
    closed = False
    try:
        for line in subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True,
                                   timeout=5).stdout.splitlines():
            low = line.lower()
            if "thoros help" in low or "org.yggdrasil.help" in low:
                subprocess.run(["wmctrl", "-i", "-c", line.split(None, 1)[0]],
                               capture_output=True, timeout=5)
                closed = True
    except Exception:
        pass
    if not closed:
        try:
            if subprocess.run(["pkill", "-f", "yggdrasil.ui.help"],
                              capture_output=True, timeout=5).returncode == 0:
                closed = True
        except Exception:
            pass
    return "Closed the help window." if closed else "The help window wasn't open."


class HelpAgent(BaseAgent):
    domain = "help"
    module_id = "core.help"
    planner_examples: list[str] = []  # reached by a deterministic route, not the planner
    capabilities = {
        "show": Capability("show", False, "Show context-aware help for wherever the user is"),
        "hide": Capability("hide", False, "Close the help window"),
    }

    def __init__(self, bus, perms, llm=None) -> None:
        super().__init__(bus, perms)
        self.llm = llm  # reserved for future free-form "how do I …" answering

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if verb == "hide":
            return {"speech": _close_window(), "help_commands": []}
        snap = context.snapshot()
        _write_state(snap)
        shown = _open_window()
        speech = context.spoken(snap)
        if not shown:
            # No desktop (headless / SSH) — the spoken version is the whole help.
            speech = speech.replace("The help window has the rest. ", "")
        # Hand the numbered commands back so the orchestrator can run "do number 3".
        return {"speech": speech, "help_commands": snap.get("commands", [])}
