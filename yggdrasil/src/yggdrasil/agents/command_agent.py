"""Command Agent — run shell commands by voice/text (Core module).

The most powerful and most dangerous capability, layered for safety:
  1. `run` is **dangerous** → gated by the authorization code (session grants + autonomous
     mode let a batch run after one approval).
  2. A hard **denylist** refuses catastrophic patterns regardless of mode (defence in depth).
Interactive/TUI programs (top, htop, vim…) open in a terminal; everything else is captured and
reported. Sequences ("run X, then Y") come from the planner's multi-step plans — no extra work.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Any

from ..core.permissions import Capability
from .base import BaseAgent

# Programs that take over a terminal — launch in a terminal rather than capturing output.
_INTERACTIVE = {
    "top", "htop", "btop", "vi", "vim", "nano", "emacs", "less", "more", "man", "watch",
    "nmtui", "ncdu", "ranger", "bash", "sh", "zsh", "python", "python3", "ipython", "ssh",
}
# Catastrophic patterns refused in EVERY mode.
_DENY = [
    r"\brm\s+-\w*[rf]\w*[rf]\w*\s+/",     # rm -rf / (and variants)
    r"\bmkfs", r"\bdd\b.*\bof=/dev/", r":\s*\(\s*\)\s*\{",  # fork bomb
    r"\bshred\b", r"\bwipefs\b", r">\s*/dev/sd", r"\bof=/dev/sd",
    r"\bchmod\s+-R\s+0*7{3}\s+/", r"/dev/sd[a-z]\b",
    r"\b(halt|poweroff|reboot|init\s+0)\b",
]


class CommandAgent(BaseAgent):
    domain = "command"
    module_id = "core.command"
    planner_examples = [
        'run command top with flags -d -> {"steps":[{"action":"command.run","argument":"top -d"}]}',
        'run ifconfig -> {"steps":[{"action":"command.run","argument":"ifconfig"}]}',
        'run command df -h -> {"steps":[{"action":"command.run","argument":"df -h"}]}',
        'run whoami then run uname -a -> {"steps":[{"action":"command.run","argument":"whoami"},{"action":"command.run","argument":"uname -a"}]}',
    ]
    capabilities = {
        "run": Capability("run", dangerous=True, description="Run a shell command"),
    }

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        cmdline = (params.get("argument") or params.get("path") or "").strip()
        if not cmdline:
            return {"speech": "What command should I run?"}
        if self._denied(cmdline):
            return {"speech": f"I won't run that — it looks destructive: {cmdline}"}
        try:
            parts = shlex.split(cmdline)
        except ValueError:
            parts = cmdline.split()
        if not parts:
            return {"speech": "What command should I run?"}
        prog = os.path.basename(parts[0])
        if prog in _INTERACTIVE:
            return {"speech": self._terminal(cmdline)}
        return {"speech": self._capture(cmdline, parts)}

    @staticmethod
    def _denied(cmdline: str) -> bool:
        c = cmdline.lower()
        return any(re.search(p, c) for p in _DENY)

    @staticmethod
    def _terminal(cmdline: str) -> str:
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            return f"'{cmdline}' is interactive — open a terminal at the desktop and I'll run it there."
        try:
            subprocess.Popen(
                ["gnome-terminal", "--", "bash", "-c", f"{cmdline}; exec bash"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return f"Running {cmdline} in a terminal."
        except Exception:
            return f"I couldn't open a terminal for {cmdline}."

    @staticmethod
    def _capture(cmdline: str, parts: list[str]) -> str:
        try:
            r = subprocess.run(parts, capture_output=True, text=True, timeout=15)
            out = (r.stdout or r.stderr or "").strip()
            if not out:
                return f"Ran {cmdline}. (no output)"
            lines = out.splitlines()
            snippet = "\n".join(lines[:12])
            if len(lines) > 12 or len(snippet) > 600:
                snippet = snippet[:600].rstrip() + " …(truncated)"
            return f"{cmdline}:\n{snippet}"
        except subprocess.TimeoutExpired:
            return f"{cmdline} took too long and was stopped."
        except FileNotFoundError:
            return f"There's no command called {parts[0]}."
        except Exception as e:  # noqa: BLE001
            return f"That failed: {e!r}"
