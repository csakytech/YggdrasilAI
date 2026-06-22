"""Dashboard data layer — pure functions, no GUI (so it's testable headless).

Reflects the live system: which agents are active (read from their class metadata, so the
dashboard updates itself as agents are added), system + GPU status, the model, trust mode, and
remembered facts.
"""
from __future__ import annotations

import os
import subprocess

from .. import __codename__, __version__


def agents_info() -> list[dict]:
    """Active Core agents and their capabilities, read from class metadata."""
    from ..agents.app_agent import AppsAgent
    from ..agents.file_agent import FileAgent
    from ..agents.memory_agent import MemoryAgent
    from ..agents.system_agent import SystemAgent

    out = []
    for cls in (FileAgent, MemoryAgent, SystemAgent, AppsAgent):
        caps = [
            {"name": c.name, "dangerous": c.dangerous, "description": c.description}
            for c in cls.capabilities.values()
        ]
        out.append({"domain": cls.domain, "module_id": cls.module_id, "capabilities": caps})
    return out


def status_info() -> dict:
    return {
        "release": f"{__codename__} {__version__}",
        "model": os.environ.get("YGGDRASIL_MODEL", "(none — heuristic)"),
        "trust": os.environ.get("YGGDRASIL_TRUST", "guarded"),
        "name": os.environ.get("YGGDRASIL_NAME", "Jarvis"),
    }


def memory_facts() -> list[str]:
    try:
        from ..core.memory import MemoryStore

        return MemoryStore().recall()
    except Exception:
        return []


def system_info() -> dict:
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    mem_total = mem_avail = 0.0
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.strip().split()[0])
        mem_total = info["MemTotal"] / 1e6
        mem_avail = info.get("MemAvailable", 0) / 1e6
    except Exception:
        pass
    disk_free = disk_total = 0.0
    try:
        u = __import__("shutil").disk_usage(os.path.expanduser("~"))
        disk_free, disk_total = u.free / 1e9, u.total / 1e9
    except Exception:
        pass
    up = 0.0
    try:
        up = float(open("/proc/uptime").read().split()[0])
    except Exception:
        pass
    return {
        "load": load,
        "mem_used": mem_total - mem_avail,
        "mem_total": mem_total,
        "disk_free": disk_free,
        "disk_total": disk_total,
        "uptime_h": up / 3600,
        "gpu": gpu_info(),
    }


def gpu_info() -> dict | None:
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip().splitlines()
        if out:
            name, used, total, util, temp = [x.strip() for x in out[0].split(",")]
            return {"name": name, "used_mb": int(used), "total_mb": int(total),
                    "util": int(util), "temp": int(temp)}
    except Exception:
        pass
    return None
