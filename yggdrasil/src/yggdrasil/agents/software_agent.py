"""Software Agent — install real programs from the Debian repos by voice.

"Install OBS Studio" (or a yes to the Research agent's recommendation) resolves the spoken name
to a Debian package, always confirms aloud first, then installs via the validated root helper
/usr/local/sbin/yggdrasil-install (a %sudo NOPASSWD drop-in, same trust pattern as the updater).
The agent itself never builds shell — the helper accepts exactly one strictly-validated package
name, so nothing spoken can smuggle options or commands through.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from typing import Any

from ..core.permissions import Capability
from .base import BaseAgent

_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,80}$")
_HELPER = "/usr/local/sbin/yggdrasil-install"

# Spoken names -> Debian package for the software people actually ask for. Anything not here is
# resolved by normalizing the name and asking apt itself (policy, then a names-only search).
_KNOWN = {
    "obs": "obs-studio", "obs studio": "obs-studio",
    "kdenlive": "kdenlive", "gimp": "gimp", "inkscape": "inkscape", "krita": "krita",
    "blender": "blender", "audacity": "audacity", "handbrake": "handbrake",
    "shotcut": "shotcut", "openshot": "openshot-qt", "vlc": "vlc",
    "libreoffice": "libreoffice", "libre office": "libreoffice",
    "thunderbird": "thunderbird", "chromium": "chromium", "darktable": "darktable",
    "steam": "steam-installer", "discord": "discord", "telegram": "telegram-desktop",
    "signal": "signal-desktop", "spotify": "spotify-client",
    "visual studio code": "code", "vs code": "code", "vscode": "code",
}


class SoftwareAgent(BaseAgent):
    domain = "software"
    module_id = "core.software"
    planner_examples = [
        'install obs studio -> {"steps":[{"action":"software.install","argument":"obs studio"}]}',
        'install gimp for me -> {"steps":[{"action":"software.install","argument":"gimp"}]}',
        'can you install vlc -> {"steps":[{"action":"software.install","argument":"vlc"}]}',
        'i need a video editor, install kdenlive -> {"steps":[{"action":"software.install","argument":"kdenlive"}]}',
    ]
    capabilities = {
        "install": Capability("install", False, "Install a program from the Debian repos (always confirms first)"),
        "prime": Capability("prime", False, "Stage a recommended program so a spoken yes installs it"),
        "confirm": Capability("confirm", False, "Carry out the staged install after a spoken yes"),
        "cancel": Capability("cancel", False, "Cancel the staged install"),
    }

    def __init__(self, bus, perms) -> None:
        super().__init__(bus, perms)
        self._pending: dict | None = None  # {"pkg": ..., "spoken": ...} awaiting yes/no

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        arg = (params.get("argument") or params.get("path") or "").strip()
        if verb == "install":
            return await self._stage(arg, speak_offer=True)
        if verb == "prime":
            return await self._stage(arg, speak_offer=False)
        if verb == "confirm":
            return await self._run_pending()
        if verb == "cancel":
            had = self._pending is not None
            self._pending = None
            return {"speech": "Okay, I won't install it." if had else "There's nothing to confirm."}
        raise ValueError(f"unhandled verb '{verb}'")

    # ---- staging ----
    async def _stage(self, spoken: str, speak_offer: bool) -> dict:
        """Resolve a spoken program name to a package and stage it behind the yes/no gate.
        prime (speak_offer=False) is the Research-recommendation path: it stays silent and just
        reports ok/not-ok so the orchestrator can append one natural offer to the summary."""
        if not spoken:
            return {"speech": "Install what?"} if speak_offer else {"ok": False}
        pkg = await self._resolve(spoken)
        if pkg is None:
            if not speak_offer:
                return {"ok": False}
            near = await self._nearby(spoken)
            if near:
                return {"speech": f"I couldn't find {spoken} exactly — the closest packages are "
                                  f"{', '.join(near[:3])}. Want one of those? Just say install and the name.",
                        "assist": False}
            return {"speech": f"I couldn't find {spoken} in the software repositories.", "assist": True}
        if await self._installed(pkg):
            name = spoken if speak_offer else pkg
            return ({"speech": f"Good news — {name} is already installed. Say “open {spoken}” to start it."}
                    if speak_offer else {"ok": False, "already": True})
        self._pending = {"pkg": pkg, "spoken": spoken}
        if not speak_offer:
            return {"ok": True, "pkg": pkg}
        extra = f" (package {pkg})" if pkg != spoken.lower().replace(" ", "-") else ""
        return {"await_confirm": True, "agent": "software",
                "speech": f"Install {spoken}{extra}? Say yes or no."}

    async def _run_pending(self) -> dict:
        p, self._pending = self._pending, None
        if not p:
            return {"speech": "There's nothing waiting to install."}
        pkg, spoken = p["pkg"], p["spoken"]
        code, out = await self._helper(pkg)
        if code == 0:
            return {"speech": f"Done — {spoken} is installed. Say “open {spoken}” when you want it."}
        tail = (out or "").strip().splitlines()[-1:] or ["unknown error"]
        return {"speech": f"The install of {spoken} failed — {tail[0][:120]}. "
                          "You can try again in a few minutes, or I can look up an alternative.",
                "assist": True}

    # ---- resolution (read-only apt queries, no root) ----
    async def _resolve(self, spoken: str) -> str | None:
        s = re.sub(r"\s+", " ", spoken.lower().strip(" .!?"))
        candidates = []
        if s in _KNOWN:
            candidates.append(_KNOWN[s])
        norm = s.replace(" ", "-")
        if _PKG_RE.match(norm):
            candidates.append(norm)
        joined = s.replace(" ", "")
        if _PKG_RE.match(joined):
            candidates.append(joined)
        for c in candidates:
            if await self._available(c):
                return c
        near = await self._nearby(s)
        # only auto-pick a search hit when it's an exact-word match (obs -> obs-studio is NOT
        # auto-picked here; the curated map handles the popular cases — search is the long tail)
        for hit in near:
            if hit == norm or hit == joined:
                return hit
        return None

    async def _available(self, pkg: str) -> bool:
        out = await self._run(["apt-cache", "policy", pkg])
        return out is not None and "Candidate:" in out and "Candidate: (none)" not in out

    async def _installed(self, pkg: str) -> bool:
        out = await self._run(["dpkg", "-s", pkg])
        return out is not None and "Status: install ok installed" in out

    async def _nearby(self, spoken: str) -> list[str]:
        q = spoken.lower().replace(" ", "-")
        if not _PKG_RE.match(q):
            return []
        out = await self._run(["apt-cache", "search", "--names-only", q]) or ""
        return [line.split(" ", 1)[0] for line in out.splitlines() if line.strip()][:5]

    # ---- process helpers ----
    @staticmethod
    async def _run(cmd: list[str], timeout: float = 10.0) -> str | None:
        def _go():
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                return r.stdout if r.returncode == 0 else None
            except Exception:
                return None
        return await asyncio.to_thread(_go)

    @staticmethod
    async def _helper(pkg: str) -> tuple[int, str]:
        """Run the root install helper. Long timeout — a desktop app plus its dependencies can
        legitimately take minutes on the first download."""
        if not _PKG_RE.match(pkg):
            return 2, "invalid package name"
        if not shutil.which("sudo"):
            return 1, "sudo is not available"
        def _go():
            try:
                r = subprocess.run(["sudo", "-n", _HELPER, pkg],
                                   capture_output=True, text=True, timeout=900)
                return r.returncode, (r.stdout or "") + (r.stderr or "")
            except subprocess.TimeoutExpired:
                return 1, "the install timed out"
            except Exception as e:
                return 1, repr(e)
        return await asyncio.to_thread(_go)
