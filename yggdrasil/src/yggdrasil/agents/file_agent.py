"""Phase-1 File Agent.

All paths are jailed to a sandbox root, so even a bug or a confused plan cannot reach the
wider filesystem — defence in depth alongside the permission manager. Input paths are treated
as workspace-relative (a leading "/" is stripped) so the LLM emitting an absolute path can't
escape; ``..`` traversal is still blocked. ``delete`` is dangerous → authorization challenge.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..core.permissions import Capability
from .base import BaseAgent


class FileAgent(BaseAgent):
    domain = "file"
    capabilities = {
        "create_folder": Capability(
            "create_folder", dangerous=False, description="Create a folder in the workspace"
        ),
        "list": Capability(
            "list", dangerous=False, description="List the contents of a folder"
        ),
        "open": Capability(
            "open", dangerous=False, description="Open a file or folder in the desktop file manager"
        ),
        "delete": Capability(
            "delete", dangerous=True, description="Delete a file or folder (irreversible)"
        ),
    }

    def __init__(self, bus, perms, sandbox_root: str | os.PathLike) -> None:
        super().__init__(bus, perms)
        self.sandbox_root = Path(sandbox_root).resolve()
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        target = self._safe_path(params.get("path", ""))
        if verb in ("list", "open", "delete"):
            target = self._resolve_existing(target)
        if verb == "create_folder":
            target.mkdir(parents=True, exist_ok=True)
            return {"created": str(target), "name": target.name}

        if verb == "list":
            if not target.exists():
                return {"missing": str(target), "name": target.name}
            items = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
            return {"path": str(target), "name": target.name, "items": items}

        if verb == "open":
            if not target.exists():
                return {"missing": str(target), "name": target.name}
            if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
                return {"no_display": True, "name": target.name}
            subprocess.Popen(
                ["xdg-open", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"opened": str(target), "name": target.name}

        if verb == "delete":
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            return {"deleted": str(target), "name": target.name}

        raise ValueError(f"unhandled verb '{verb}'")

    def _safe_path(self, rel: str) -> Path:
        # Treat input as workspace-relative: strip leading slashes so an absolute path from
        # the model can't escape. ".." traversal is still caught by the parent check below.
        rel = (rel or "").strip().lstrip("/").strip()
        candidate = (self.sandbox_root / rel).resolve()
        if candidate != self.sandbox_root and self.sandbox_root not in candidate.parents:
            raise PermissionError("path escapes the sandbox root")
        return candidate

    def _resolve_existing(self, target: Path) -> Path:
        """For operations on existing items, fall back to a case-insensitive match
        ('voice test' -> 'Voice Test') so spoken names don't have to match case."""
        if target == self.sandbox_root or target.exists():
            return target
        parent = target.parent
        if parent.is_dir():
            for p in parent.iterdir():
                if p.name.lower() == target.name.lower():
                    return p
        return target
