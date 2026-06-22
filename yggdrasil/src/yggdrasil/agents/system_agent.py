"""System Agent (Core module): OS + desktop status and control.

Clean demonstration of the modular architecture — a brand-new `system` domain that plugs into
the data-driven planner with ZERO orchestrator changes (it just returns a `speech` string;
see docs/MODULES.md §5). All capabilities here are read-only or benign (launching a GUI app),
so none require an authorization challenge.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from typing import Any

from ..core.permissions import Capability
from .base import BaseAgent


class SystemAgent(BaseAgent):
    domain = "system"
    module_id = "core.system"
    planner_examples = [
        'what time is it -> {"steps":[{"action":"system.time","argument":""}]}',
        'how much disk space do I have -> {"steps":[{"action":"system.disk","argument":""}]}',
        'what is my system status -> {"steps":[{"action":"system.status","argument":""}]}',
        'what is running -> {"steps":[{"action":"system.running","argument":""}]}',
        'open firefox -> {"steps":[{"action":"system.open_app","argument":"firefox"}]}',
        'stop asking for confirmation -> {"steps":[{"action":"system.autonomy","argument":"on"}]}',
        'enable autonomous mode -> {"steps":[{"action":"system.autonomy","argument":"on"}]}',
        'be careful again -> {"steps":[{"action":"system.autonomy","argument":"off"}]}',
    ]
    capabilities = {
        "time": Capability("time", dangerous=False, description="Current date and time"),
        "disk": Capability("disk", dangerous=False, description="Free disk space"),
        "status": Capability("status", dangerous=False, description="CPU load, memory, uptime"),
        "running": Capability("running", dangerous=False, description="Top running programs"),
        "open_app": Capability("open_app", dangerous=False, description="Launch a desktop application"),
        "autonomy": Capability("autonomy", dangerous=False, description="Turn autonomous (no-confirmation) mode on or off"),
    }

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if verb == "time":
            return {"speech": datetime.now().strftime("It's %A, %B %-d, %-I:%M %p.")}
        if verb == "disk":
            u = shutil.disk_usage(os.path.expanduser("~"))
            return {"speech": f"You have {u.free / 1e9:.0f} gigabytes free of {u.total / 1e9:.0f}."}
        if verb == "status":
            load = os.getloadavg()[0]
            return {"speech": f"Load is {load:.1f}. {self._mem()} Up {self._uptime()}."}
        if verb == "running":
            procs = self._top_procs()
            return {"speech": ("Top programs: " + ", ".join(procs) + ".") if procs
                    else "Nothing notable is running."}
        if verb == "open_app":
            return {"speech": self._open_app((params.get("argument") or "").strip())}
        if verb == "autonomy":
            on = (params.get("argument") or "").strip().lower() in ("on", "true", "yes", "enable", "enabled", "start")
            self.perms.set_mode("autonomous" if on else "guarded")
            return {"speech": "Autonomous mode on — I won't ask for confirmations." if on
                    else "Back to careful mode — I'll confirm risky actions."}
        raise ValueError(f"unhandled verb '{verb}'")

    def _open_app(self, name: str) -> str:
        if not name:
            return "Which application?"
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            return "I can only open apps when you're signed in at the desktop."
        exe = shutil.which(name) or shutil.which(name.lower())
        try:
            cmd = [exe] if exe else ["gtk-launch", name]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Opening {name}."
        except Exception:
            return f"I couldn't find an app called {name}."

    @staticmethod
    def _mem() -> str:
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k] = int(v.strip().split()[0])
            return f"Memory {info.get('MemAvailable', 0) / 1e6:.1f} of {info['MemTotal'] / 1e6:.1f} gigabytes free."
        except Exception:
            return ""

    @staticmethod
    def _uptime() -> str:
        try:
            secs = float(open("/proc/uptime").read().split()[0])
            h, m = int(secs // 3600), int((secs % 3600) // 60)
            return f"{h} hours {m} minutes" if h else f"{m} minutes"
        except Exception:
            return "a while"

    @staticmethod
    def _top_procs() -> list[str]:
        try:
            rows = subprocess.run(
                ["ps", "-eo", "comm,%cpu", "--sort=-%cpu"],
                capture_output=True, text=True, timeout=5,
            ).stdout.splitlines()[1:6]
            return [r.split()[0] for r in rows if r.strip()]
        except Exception:
            return []
