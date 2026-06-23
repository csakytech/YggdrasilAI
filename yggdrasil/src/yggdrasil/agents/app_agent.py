"""Apps Agent (Core module): launch and use desktop applications, and write documents.

Deliberately SEPARATE from package management (install/remove/upgrade programs): launching and
using apps is benign and frequent; changing what software is installed is system-level and
dangerous, so that will live in its own gated Software agent (see docs/MODULES.md). For
"write a story", Jarvis generates the text with the local LLM, saves it as a document, and
opens it in the editor — robust, unlike injecting keystrokes into a window.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..core.permissions import Capability
from .base import BaseAgent

_THINK = re.compile(r"<think>.*?</think>", re.S)

# Friendly names → actual launch commands.
_ALIASES = {
    "word editor": "libreoffice --writer", "word processor": "libreoffice --writer",
    "writer": "libreoffice --writer", "text editor": "gnome-text-editor",
    "editor": "gnome-text-editor", "browser": "firefox", "web browser": "firefox",
    "files": "nautilus", "file manager": "nautilus", "terminal": "gnome-terminal",
    "calculator": "gnome-calculator",
    "dashboard": "yggdrasil-dashboard", "yggdrasil dashboard": "yggdrasil-dashboard",
    "control panel": "yggdrasil-dashboard",
    "settings": "gnome-control-center", "system settings": "gnome-control-center",
}


class AppsAgent(BaseAgent):
    domain = "app"
    module_id = "core.apps"
    planner_examples = [
        'open firefox -> {"steps":[{"action":"app.launch","argument":"firefox"}]}',
        'open the dashboard -> {"steps":[{"action":"app.launch","argument":"dashboard"}]}',
        'open a word editor -> {"steps":[{"action":"app.launch","argument":"word editor"}]}',
        'write a short story about a dragon -> {"steps":[{"action":"app.write_document","argument":"a short story about a dragon"}]}',
        'write a poem about the sea -> {"steps":[{"action":"app.write_document","argument":"a poem about the sea"}]}',
        'what apps are installed -> {"steps":[{"action":"app.list_apps","argument":""}]}',
    ]
    capabilities = {
        "launch": Capability("launch", False, "Open/launch a desktop application"),
        "write_document": Capability("write_document", False, "Write a document with the AI and open it"),
        "list_apps": Capability("list_apps", False, "List installed applications"),
    }

    def __init__(self, bus, perms, llm=None, workspace: str | os.PathLike = ".") -> None:
        super().__init__(bus, perms)
        self.llm = llm
        self.workspace = Path(workspace)

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if verb == "launch":
            return {"speech": self._launch((params.get("argument") or "").strip())}
        if verb == "list_apps":
            return {"speech": self._list_apps()}
        if verb == "write_document":
            return await self._write_document((params.get("argument") or "").strip())
        raise ValueError(f"unhandled verb '{verb}'")

    @staticmethod
    def _has_display() -> bool:
        return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))

    def _launch(self, name: str) -> str:
        if not name:
            return "Which application?"
        if not self._has_display():
            return f"I can only open {name} when you're signed in at the desktop."
        # Normalize phrasing: "the dashboard program" -> "dashboard"
        key = re.sub(r"\s+(program|app|application|window)$", "",
                     re.sub(r"^(the|my|a)\s+", "", name.lower().strip()))
        parts = _ALIASES.get(key, key).split()
        exe = shutil.which(parts[0])
        try:
            cmd = ([exe, *parts[1:]]) if exe else ["gtk-launch", parts[0]]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Opening {name}."
        except Exception:
            return f"I couldn't find an app called {name}."

    @staticmethod
    def _list_apps() -> str:
        apps: set[str] = set()
        for d in ("/usr/share/applications", os.path.expanduser("~/.local/share/applications")):
            try:
                for f in os.listdir(d):
                    if f.endswith(".desktop"):
                        apps.add(f[:-8].split(".")[-1])
            except OSError:
                pass
        sample = ", ".join(sorted(apps)[:12])
        return f"You have about {len(apps)} apps installed, including: {sample}."

    async def _write_document(self, topic: str) -> dict:
        topic = topic or "a short note"
        if not self.llm:
            return {"speech": "I need a language model to write documents."}
        resp = await self.llm.generate(
            system=("You are a skilled writer. Produce ONLY the requested text as plain prose — "
                    "no preamble, no markdown, no commentary. /no_think"),
            prompt=f"Write {topic}.",
            temperature=0.7,
        )
        text = _THINK.sub("", resp.text).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40] or "document"
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = self.workspace / f"{slug}.txt"
        path.write_text(text, encoding="utf-8")
        opened = ""
        if self._has_display():
            try:
                subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                opened = " It's open now."
            except Exception:
                opened = ""
        return {"speech": f"I wrote {topic} — {len(text.split())} words, saved as {path.name}.{opened}"}
