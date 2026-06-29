"""File Agent (Core module) — a full file-management toolkit.

Everything is jailed to a sandbox/workspace root (defence in depth alongside the permission
manager); a leading "/" in a path is treated as workspace-relative so the LLM can't escape,
and lookups of existing items are space/case-insensitive (Whisper spaces digits oddly). Like a
desktop in your own folder, routine ops are **safe** (no prompt) — only `delete` is dangerous,
and even that is session-granted / skippable in autonomous mode (see permissions.py).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from ..core.permissions import Capability
from ..core.resolve import resolve
from .base import BaseAgent

# Ops that act on an item that already exists (so we resolve the spoken source to a real name).
_EXISTING = {"list", "open", "delete", "read_file", "append_file", "info", "copy", "move", "rename", "permissions"}
# Ops that change/lose data → ALWAYS confirm the resolved name with a yes/no before acting.
_DESTRUCTIVE = {"delete", "rename", "move"}


class FileAgent(BaseAgent):
    domain = "file"
    module_id = "core.file"
    planner_examples = [
        'create a folder called reports -> {"steps":[{"action":"file.create_folder","argument":"reports"}]}',
        'create a file called notes.txt -> {"steps":[{"action":"file.create_file","argument":"notes.txt"}]}',
        'write hello world to notes.txt -> {"steps":[{"action":"file.write_file","argument":"notes.txt","content":"hello world"}]}',
        'add a line saying done to notes.txt -> {"steps":[{"action":"file.append_file","argument":"notes.txt","content":"done"}]}',
        'read notes.txt -> {"steps":[{"action":"file.read_file","argument":"notes.txt"}]}',
        'what is in reports -> {"steps":[{"action":"file.list","argument":"reports"}]}',
        'how big is notes.txt -> {"steps":[{"action":"file.info","argument":"notes.txt"}]}',
        'find files named report -> {"steps":[{"action":"file.search","argument":"report"}]}',
        'copy notes.txt to backup.txt -> {"steps":[{"action":"file.copy","argument":"notes.txt","argument2":"backup.txt"}]}',
        'move notes.txt to reports -> {"steps":[{"action":"file.move","argument":"notes.txt","argument2":"reports"}]}',
        'rename notes.txt to todo.txt -> {"steps":[{"action":"file.rename","argument":"notes.txt","argument2":"todo.txt"}]}',
        'open reports -> {"steps":[{"action":"file.open","argument":"reports"}]}',
        'make notes.txt executable -> {"steps":[{"action":"file.permissions","argument":"notes.txt","argument2":"executable"}]}',
        'delete reports -> {"steps":[{"action":"file.delete","argument":"reports"}]}',
    ]
    capabilities = {
        "create_folder": Capability("create_folder", False, "Create a folder"),
        "create_file": Capability("create_file", False, "Create a file (optionally with content)"),
        "write_file": Capability("write_file", False, "Write/replace a file's content"),
        "append_file": Capability("append_file", False, "Append content to a file"),
        "read_file": Capability("read_file", False, "Read a file's content"),
        "list": Capability("list", False, "List the contents of a folder"),
        "info": Capability("info", False, "Size and type of a file or folder"),
        "search": Capability("search", False, "Find items by name in the workspace"),
        "copy": Capability("copy", False, "Copy a file or folder"),
        "move": Capability("move", False, "Move a file or folder"),
        "rename": Capability("rename", False, "Rename a file or folder"),
        "open": Capability("open", False, "Open a file or folder in the desktop file manager"),
        "permissions": Capability("permissions", False, "Get or set a file's permissions (executable / read-only / writable)"),
        "delete": Capability("delete", False, "Delete a file or folder (irreversible) — always confirmed"),
        "confirm": Capability("confirm", False, "Confirm a pending delete / rename / move"),
        "cancel": Capability("cancel", False, "Cancel a pending delete / rename / move"),
    }

    def __init__(self, bus, perms, sandbox_root: str | os.PathLike) -> None:
        super().__init__(bus, perms)
        self.sandbox_root = Path(sandbox_root).resolve()
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        self._pending: dict | None = None                      # a destructive op awaiting yes/no
        self._last_list: tuple[str, list[str]] | None = None   # (dir, names) for "the third one"

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if verb == "confirm":
            return await self._run_pending()
        if verb == "cancel":
            had = self._pending is not None
            self._pending = None
            return {"speech": "Okay, I won't." if had else "There's nothing to confirm."}
        if verb == "search":
            q = (params.get("path") or "").strip().lower()
            hits = [p.name for p in self.sandbox_root.rglob("*") if q and q in p.name.lower()][:10]
            return {"speech": ("Found: " + ", ".join(hits) + ".") if hits else f"No matches for {q or 'that'}."}

        if verb in _EXISTING:
            src, _confident, cands = self._resolve_ref(params.get("path", ""))
            if src is None:
                nm = (params.get("path") or "that").strip()
                if cands:
                    return {"speech": f"I found a few — did you mean {self._or_list(cands)}?"}
                if verb == "list":
                    return {"missing": nm, "name": nm}
                return {"speech": f"I couldn't find {nm}."}
        else:
            src = self._safe_path(params.get("path", ""))

        # Destructive ops ALWAYS confirm first, showing the RESOLVED name (a fuzzy match can be wrong).
        if verb in _DESTRUCTIVE:
            if src == self.sandbox_root:
                return {"speech": "I won't do that to your whole workspace."}
            if verb in ("rename", "move"):
                dest = self._safe_path(params.get("dest", ""))
                self._pending = {"op": verb, "src": str(src), "dest": str(dest)}
                return {"await_confirm": True, "agent": "file",
                        "speech": f"{verb.capitalize()} {src.name} to {dest.name}? Say yes or no."}
            self._pending = {"op": "delete", "src": str(src)}
            return {"await_confirm": True, "agent": "file", "speech": f"Delete {src.name}? Say yes or no."}

        if verb == "create_folder":
            src.mkdir(parents=True, exist_ok=True)
            return {"created": str(src), "name": src.name}

        if verb == "create_file":
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(params.get("content", ""), encoding="utf-8")
            return {"speech": f"Created file {src.name}."}

        if verb == "write_file":
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(params.get("content", ""), encoding="utf-8")
            return {"speech": f"Saved {src.name}."}

        if verb == "append_file":
            with open(src, "a", encoding="utf-8") as f:
                f.write(params.get("content", ""))
            return {"speech": f"Updated {src.name}."}

        if verb == "read_file":
            if not src.is_file():
                return {"speech": f"I couldn't find {src.name}."}
            text = src.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                return {"speech": f"{src.name} is empty."}
            snippet = text[:300] + ("…" if len(text) > 300 else "")
            return {"speech": f"{src.name} says: {snippet}"}

        if verb == "info":
            if not src.exists():
                return {"speech": f"I couldn't find {src.name}."}
            kind = "folder" if src.is_dir() else "file"
            return {"speech": f"{src.name} is a {kind}, {self._human(src.stat().st_size)}."}

        if verb == "permissions":
            import stat as _stat

            if not src.exists():
                return {"speech": f"I couldn't find {src.name}."}
            cur = _stat.S_IMODE(src.stat().st_mode)
            word = (params.get("dest") or "").strip().lower()
            if not word:
                return {"speech": f"{src.name} permissions are {oct(cur)}."}
            if word in ("executable", "execute", "runnable"):
                src.chmod(cur | 0o111)
            elif word in ("read-only", "readonly", "locked"):
                src.chmod(cur & ~0o222)
            elif word in ("writable", "writeable", "unlock", "unlocked"):
                src.chmod(cur | 0o200)
            else:
                return {"speech": "I can make it executable, read-only, or writable."}
            return {"speech": f"Set {src.name} to {word}."}

        if verb == "list":
            if not src.exists():
                return {"missing": str(src), "name": src.name}
            items = sorted(p.name + ("/" if p.is_dir() else "") for p in src.iterdir())
            self._last_list = (str(src), [it.rstrip("/") for it in items])  # for "delete the third one"
            return {"path": str(src), "name": src.name, "items": items}

        if verb == "copy":
            if not src.exists():
                return {"speech": f"I couldn't find {src.name}."}
            dest = self._safe_path(params.get("dest", ""))
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)
            return {"speech": f"Copied {src.name} to {dest.name}."}

        if verb == "open":
            if not src.exists():
                return {"missing": str(src), "name": src.name}
            if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
                return {"no_display": True, "name": src.name}
            import subprocess

            subprocess.Popen(["xdg-open", str(src)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"opened": str(src), "name": src.name}

        raise ValueError(f"unhandled verb '{verb}'")

    def _safe_path(self, rel: str) -> Path:
        rel = (rel or "").strip().lstrip("/").strip()
        candidate = (self.sandbox_root / rel).resolve()
        if candidate != self.sandbox_root and self.sandbox_root not in candidate.parents:
            raise PermissionError("path escapes the sandbox root")
        return candidate

    def _resolve_ref(self, raw: str):
        """Resolve a spoken reference to a real path. Uses the last listing for ordinals ("the third
        one") and fuzzy matching for mis-heard names. Returns (path | None, confident, candidates)."""
        raw = (raw or "").strip()
        if not raw:
            return self.sandbox_root, True, []
        base = Path(self._last_list[0]) if self._last_list else self.sandbox_root
        ordered = self._last_list[1] if self._last_list else None
        if "/" in raw:  # a sub-path: look inside its parent instead
            sp = self._safe_path(raw)
            base, ordered, raw = sp.parent, None, sp.name
        try:
            entries = [p.name for p in base.iterdir()]
        except OSError:
            entries = []
        name, confident, cands = resolve(raw, entries, ordered)
        return (base / name if name else None), confident, cands

    async def _run_pending(self):
        p = self._pending
        self._pending = None
        if not p:
            return {"speech": "There's nothing waiting to confirm."}
        src = Path(p["src"])
        try:
            if p["op"] == "delete":
                if src.is_dir():
                    shutil.rmtree(src)
                else:
                    src.unlink(missing_ok=True)
                return {"deleted": str(src), "name": src.name, "speech": f"Deleted {src.name}."}
            dest = Path(p["dest"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            return {"speech": f"{'Renamed' if p['op'] == 'rename' else 'Moved'} {src.name} to {dest.name}."}
        except Exception as e:  # noqa: BLE001
            return {"speech": f"That didn't work: {e}"}

    @staticmethod
    def _or_list(names) -> str:
        names = list(names)[:4]
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + ", or " + names[-1]

    @staticmethod
    def _norm(name: str) -> str:
        return "".join(ch for ch in name.lower() if ch.isalnum())

    @staticmethod
    def _human(n: int) -> str:
        size = float(n)
        for unit in ("bytes", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{n} bytes"
