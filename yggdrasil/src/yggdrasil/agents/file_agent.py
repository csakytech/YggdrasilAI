"""Phase-1 File Agent.

All paths are jailed to a sandbox root, so even a bug or a malicious plan cannot reach the
wider filesystem — defence in depth alongside the permission manager. ``delete`` is marked
dangerous, so it always routes through the authorization-code challenge.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from ..core.permissions import Capability
from .base import BaseAgent


class FileAgent(BaseAgent):
    domain = "file"
    capabilities = {
        "create_folder": Capability(
            "create_folder", dangerous=False, description="Create a folder inside the sandbox"
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
        target = self._safe_path(params["path"])
        if verb == "create_folder":
            target.mkdir(parents=True, exist_ok=True)
            return {"created": str(target)}
        if verb == "delete":
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            return {"deleted": str(target)}
        raise ValueError(f"unhandled verb '{verb}'")

    def _safe_path(self, rel: str) -> Path:
        candidate = (self.sandbox_root / rel).resolve()
        if candidate != self.sandbox_root and self.sandbox_root not in candidate.parents:
            raise PermissionError("path escapes the sandbox root")
        return candidate
