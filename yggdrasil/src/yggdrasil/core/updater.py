"""Self-update — check a release feed and apply updates IN PLACE.

ThorOS separates the app (a git checkout in /opt/yggdrasil) from your data (~/.config/yggdrasil,
~/.local/share/yggdrasil, your home). So an update only swaps the app code: your memory, schedules,
settings, installed agents, and files are never touched — no reinstall, nothing lost. The privileged
git pull runs via the root helper ``/usr/local/sbin/yggdrasil-update`` (allowed for the admin user
without a password); then Jarvis restarts so the new code loads.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request

from .. import __version__ as INSTALLED

FEED = os.environ.get("YGGDRASIL_UPDATE_FEED", "https://www.yggdrasilai.org/updates/latest.json")
HELPER = "/usr/local/sbin/yggdrasil-update"


def _vtuple(v) -> tuple:
    out = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def installed_version() -> str:
    return INSTALLED


def latest_release(url: str | None = None) -> dict:
    """Fetch the release feed (raises on network/parse error)."""
    with urllib.request.urlopen(url or FEED, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def update_available(url: str | None = None) -> dict | None:
    """Return the release dict if a NEWER version is available, else None (or on any error)."""
    try:
        rel = latest_release(url)
    except Exception:
        return None
    if _vtuple(rel.get("version", "0")) > _vtuple(INSTALLED):
        return rel
    return None


def apply_update(tag: str | None = None, restart: bool = True) -> tuple[bool, str]:
    """Run the privileged updater helper, then (detached) restart Jarvis so the new code loads.
    Returns (ok, message)."""
    cmd = ["sudo", "-n", HELPER]
    if tag:
        cmd.append(tag)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:  # noqa: BLE001
        return False, f"couldn't run the updater: {e}"
    if res.returncode != 0:
        return False, (res.stderr or res.stdout or "update failed").strip()[:300]
    if restart:
        # A detached restart that OUTLIVES this process (which may be Jarvis itself). Inherits the
        # session env (DISPLAY etc.) so the new Jarvis comes back up in the same desktop session.
        try:
            subprocess.Popen(
                "sleep 2; pkill -f yggdrasil-voice; sleep 1; exec /usr/local/bin/jarvis",
                shell=True, start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
        except Exception:
            pass
    return True, (res.stdout or "updated").strip()[:300]
