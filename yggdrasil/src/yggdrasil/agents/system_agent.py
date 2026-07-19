"""System Agent (Core module): OS + desktop status, control, and the system-info library.

"What's my local IP", "how much memory does this system have", "find out the external IP",
"what CPU is this" — normal questions about the machine, answered from REAL commands and /proc,
never from the language model's imagination (an LLM will confidently invent an IP address).
The orchestrator routes these deterministically to `system.info`; keyword classification here
picks the topic. All capabilities are read-only or benign, so none require authorization.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
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
        'what is my local ip -> {"steps":[{"action":"system.info","argument":"local ip"}]}',
        'find out the external ip of this computer -> {"steps":[{"action":"system.info","argument":"external ip"}]}',
        'how much memory does this system have -> {"steps":[{"action":"system.info","argument":"memory"}]}',
        'what cpu is in this machine -> {"steps":[{"action":"system.info","argument":"cpu"}]}',
        'what graphics card do I have -> {"steps":[{"action":"system.info","argument":"gpu"}]}',
        'stop asking for confirmation -> {"steps":[{"action":"system.autonomy","argument":"on"}]}',
        'enable autonomous mode -> {"steps":[{"action":"system.autonomy","argument":"on"}]}',
        'be careful again -> {"steps":[{"action":"system.autonomy","argument":"off"}]}',
    ]
    capabilities = {
        "time": Capability("time", dangerous=False, description="Current date and time"),
        "disk": Capability("disk", dangerous=False, description="Free disk space"),
        "status": Capability("status", dangerous=False, description="CPU load, memory, uptime"),
        "running": Capability("running", dangerous=False, description="Top running programs"),
        "info": Capability("info", dangerous=False,
                           description="Answer questions about this machine: IPs, memory, CPU, GPU, hostname, OS"),
        "autonomy": Capability("autonomy", dangerous=False, description="Turn autonomous (no-confirmation) mode on or off"),
    }

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if verb == "time":
            return {"speech": datetime.now().strftime("It's %A, %B %-d, %-I:%M %p.")}
        if verb == "disk":
            return {"speech": self._disk()}
        if verb == "status":
            load = os.getloadavg()[0]
            return {"speech": f"Load is {load:.1f}. {self._mem()} Up {self._uptime()}."}
        if verb == "running":
            procs = self._top_procs()
            return {"speech": ("Top programs: " + ", ".join(procs) + ".") if procs
                    else "Nothing notable is running."}
        if verb == "info":
            return {"speech": await self._info((params.get("argument") or "").strip())}
        if verb == "autonomy":
            on = (params.get("argument") or "").strip().lower() in ("on", "true", "yes", "enable", "enabled", "start")
            self.perms.set_mode("autonomous" if on else "guarded")
            return {"speech": "Autonomous mode on — I won't ask for confirmations." if on
                    else "Back to careful mode — I'll confirm risky actions."}
        raise ValueError(f"unhandled verb '{verb}'")

    # ---- the system-info library --------------------------------------------------------------
    @staticmethod
    def classify(question: str) -> str:
        """Map a spoken question to an info topic. Order matters: 'external ip' contains 'ip'."""
        q = question.lower()
        if re.search(r"\b(?:external|public|internet|outside)\b.*\bip\b|\bip\b.*\b(?:external|public)\b", q):
            return "external_ip"
        if re.search(r"\bip\b", q):
            return "local_ip"
        if re.search(r"\bmemory\b|\bram\b", q):
            return "memory"
        if re.search(r"\bcpu\b|\bprocessor\b|\bcores?\b", q):
            return "cpu"
        if re.search(r"\bgpu\b|\bgraphics\b|\bvideo card\b", q):
            return "gpu"
        if re.search(r"\bhostname\b|\bcomputer(?:'s)? name\b|\bmachine(?:'s)? name\b|name of (?:this|the|my) (?:computer|machine)", q):
            return "hostname"
        if re.search(r"\bkernel\b", q):
            return "kernel"
        if re.search(r"\bthoros\b|\bversion\b|\boperating system\b|\bwhat os\b|\bos is\b", q):
            return "os"
        if re.search(r"\bbattery\b", q):
            return "battery"
        if re.search(r"\buptime\b|how long.*\b(?:on|running|up)\b", q):
            return "uptime"
        if re.search(r"\bdisk\b|\bstorage\b|\bspace\b", q):
            return "disk"
        return "status"

    async def _info(self, question: str) -> str:
        topic = self.classify(question)
        if topic == "external_ip":
            return await self._external_ip()
        return {
            "local_ip": self._local_ip,
            "memory": self._memory_answer,
            "cpu": self._cpu,
            "gpu": self._gpu,
            "hostname": lambda: f"This computer's name is {socket.gethostname()}.",
            "kernel": self._kernel,
            "os": self._os,
            "battery": self._battery,
            "uptime": lambda: f"The system has been up {self._uptime()}.",
            "disk": self._disk,
            "status": lambda: f"Load is {os.getloadavg()[0]:.1f}. {self._mem()} Up {self._uptime()}.",
        }[topic]()

    @staticmethod
    def _local_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(("1.1.1.1", 80))  # no packet is sent — just picks the outbound interface
            ip = s.getsockname()[0]
            s.close()
            return f"Your local IP address is {ip}."
        except Exception:
            try:
                out = subprocess.run(["hostname", "-I"], capture_output=True, text=True,
                                     timeout=3).stdout.split()
                if out:
                    return f"Your local IP address is {out[0]}."
            except Exception:
                pass
            return "I couldn't work out the local IP — the network may be down."

    @staticmethod
    async def _external_ip() -> str:
        import asyncio
        import urllib.request

        def _fetch() -> str:
            with urllib.request.urlopen("https://api.ipify.org", timeout=6) as r:
                return r.read().decode().strip()

        try:
            ip = await asyncio.to_thread(_fetch)
            if re.fullmatch(r"[0-9a-fA-F:.]+", ip or ""):
                return f"Your external IP address is {ip}."
        except Exception:
            pass
        return "I couldn't reach the internet to check the external IP."

    def _memory_answer(self) -> str:
        m = self._mem()
        return m or "I couldn't read the memory information."

    @staticmethod
    def _cpu() -> str:
        model = ""
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        model = re.sub(r"\s+", " ", line.split(":", 1)[1]).strip()
                        break
        except Exception:
            pass
        cores = os.cpu_count() or 0
        if model:
            return f"The processor is a {model}, with {cores} threads."
        return f"This machine has {cores} processor threads."

    @staticmethod
    def _gpu() -> str:
        name = ""
        try:
            out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if re.search(r"vga|3d controller|display controller", line, re.I):
                    name = line.split(":", 2)[-1].strip()
                    break
        except Exception:
            pass
        if not name:
            return "I couldn't find a graphics card."
        vram = 0
        try:  # NVIDIA first, then the AMD/driver-agnostic sysfs path
            q = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
            vram = int(q.splitlines()[0]) if q else 0
        except Exception:
            pass
        if not vram:
            try:
                import glob
                for f in glob.glob("/sys/class/drm/card*/device/mem_info_vram_total"):
                    vram = max(vram, int(open(f).read()) // 1048576)
            except Exception:
                pass
        extra = f" with {vram / 1024:.0f} gigabytes of video memory" if vram else ""
        return f"The graphics card is {name}{extra}."

    @staticmethod
    def _kernel() -> str:
        try:
            return f"The kernel is Linux {os.uname().release}."
        except Exception:
            return "I couldn't read the kernel version."

    @staticmethod
    def _os() -> str:
        pretty = ""
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        pretty = line.split("=", 1)[1].strip().strip('"')
                        break
        except Exception:
            pass
        try:
            from .. import __version__
            thoros = f"ThorOS {__version__}"
        except Exception:
            thoros = "ThorOS"
        return f"This is {thoros}, built on {pretty}." if pretty else f"This is {thoros}."

    @staticmethod
    def _battery() -> str:
        try:
            import glob
            for cap in glob.glob("/sys/class/power_supply/BAT*/capacity"):
                pct = open(cap).read().strip()
                status = "unknown"
                try:
                    status = open(os.path.join(os.path.dirname(cap), "status")).read().strip().lower()
                except Exception:
                    pass
                return f"The battery is at {pct} percent and {status}."
        except Exception:
            pass
        return "This machine doesn't have a battery — it's on mains power."

    @staticmethod
    def _disk() -> str:
        u = shutil.disk_usage(os.path.expanduser("~"))
        return f"You have {u.free / 1e9:.0f} gigabytes free of {u.total / 1e9:.0f}."

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
