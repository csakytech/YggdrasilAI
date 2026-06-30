"""Update Agent — check for and apply ThorOS updates by voice.

"Are there any updates?" → checks the release feed and, if there's one, opens the Updates window so you
can see what's new and choose. "Update yourself" → applies it in place (non-destructive) and restarts.
"""
from __future__ import annotations

import os
import subprocess
import sys

from ..core import updater
from ..core.permissions import Capability
from .base import BaseAgent


class UpdateAgent(BaseAgent):
    domain = "update"
    module_id = "core.update"
    planner_examples = [
        'check for updates -> {"steps":[{"action":"update.check","argument":""}]}',
        'are there any updates -> {"steps":[{"action":"update.check","argument":""}]}',
        'update yourself -> {"steps":[{"action":"update.apply","argument":""}]}',
        'install the update -> {"steps":[{"action":"update.apply","argument":""}]}',
    ]
    capabilities = {
        "check": Capability("check", False, "Check whether a ThorOS update is available"),
        "apply": Capability("apply", False, "Download and apply the latest ThorOS update"),
    }

    def __init__(self, bus, perms, llm=None) -> None:
        super().__init__(bus, perms)
        self.llm = llm

    async def _execute(self, verb, params):
        if verb == "check":
            return {"speech": self._check()}
        if verb == "apply":
            return {"speech": self._apply()}
        raise ValueError(f"unhandled verb '{verb}'")

    def _check(self) -> str:
        rel = updater.update_available()
        if not rel:
            return f"You're up to date — ThorOS {updater.installed_version()}."
        self._open_window()  # show what's new + the choice on screen
        return (f"There's an update available — ThorOS {rel.get('version')}. I've put the details on "
                "screen. Say “update yourself” to install it now, or do it later from the window.")

    def _apply(self) -> str:
        rel = updater.update_available()
        if not rel:
            return f"You're already on the latest version, ThorOS {updater.installed_version()}."
        ok, msg = updater.apply_update(rel.get("tag"))
        if ok:
            return f"Updating to ThorOS {rel.get('version')} now — I'll be right back in a few seconds."
        return f"I couldn't install the update: {msg}"

    @staticmethod
    def _open_window() -> None:
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            return
        try:
            subprocess.Popen([sys.executable, "-m", "yggdrasil.ui.updater"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
