"""Documents Agent (Core module): open documents the way people actually ask.

- "open up an empty document"            -> documents.new    (launch the installed writer, blank)
- "open my receipts document"            -> documents.open    (fuzzy-find a file by name, open it)
- "what was I working on yesterday"       -> documents.recent  (GNOME's recent-files history)

App choice is automatic: existing files open with `xdg-open` (the system default handler — here
LibreOffice Writer), and a blank doc launches the first installed writer. Opening makes that
window the working target (see core/focus.py), so you can then dictate into it ("add a line: …")
and the Focus agent types it in. Content search ("the doc where I mentioned X") is a later v2 —
this matches on filename + recency, which covers the common cases.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

from ..core.focus import track_new_window, window_ids
from ..core.permissions import Capability
from .base import BaseAgent

# Words people say around a document name that aren't part of the name itself.
_FILLER = re.compile(
    r"\b(my|the|a|an|please|open|up|find|document|documents|doc|file|files|called|named|"
    r"titled|spreadsheet|presentation|slides|slideshow)\b",
    re.I,
)
_DOC_EXTS = (".odt", ".doc", ".docx", ".rtf", ".txt", ".md", ".ods", ".xls", ".xlsx",
             ".odp", ".ppt", ".pptx", ".pdf")
_SEARCH_DIRS = ["~/Documents", "~/Desktop", "~/Downloads", "~/YggdrasilSandbox"]
# Preferred word processors for a *blank* new doc, in order; existing files use xdg-open instead.
_WRITERS = [["libreoffice", "--writer"], ["soffice", "--writer"], ["abiword"], ["gnome-text-editor"]]


class DocumentsAgent(BaseAgent):
    domain = "documents"
    module_id = "core.documents"
    planner_examples = [
        'open an empty document -> {"steps":[{"action":"documents.new","argument":""}]}',
        'open a blank document -> {"steps":[{"action":"documents.new","argument":""}]}',
        'open my receipts document -> {"steps":[{"action":"documents.open","argument":"receipts"}]}',
        'open the budget spreadsheet -> {"steps":[{"action":"documents.open","argument":"budget"}]}',
        'pull up my resume -> {"steps":[{"action":"documents.open","argument":"resume"}]}',
        'what was I working on yesterday -> {"steps":[{"action":"documents.recent","argument":"yesterday"}]}',
        'what documents did I open recently -> {"steps":[{"action":"documents.recent","argument":""}]}',
    ]
    capabilities = {
        "new": Capability("new", False, "Open a new blank document"),
        "open": Capability("open", False, "Find and open a document by name"),
        "recent": Capability("recent", False, "List recently opened documents"),
    }

    def __init__(self, bus, perms) -> None:
        super().__init__(bus, perms)
        self.last_doc: Path | None = None  # for "open it" / dictating into the one just opened

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        arg = (params.get("argument") or "").strip()
        if verb == "new":
            return {"speech": self._new()}
        if verb == "open":
            return {"speech": self._open(arg)}
        if verb == "recent":
            return {"speech": self._recent(arg)}
        raise ValueError(f"unhandled verb '{verb}'")

    @staticmethod
    def _has_display() -> bool:
        return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))

    def _track_bg(self, before: set[str]) -> None:
        # Office apps can take several seconds to map a window (cold LibreOffice ~10s) — track in
        # the background so the spoken reply isn't delayed, while the target still gets set before
        # the next command.
        threading.Thread(target=track_new_window, args=(before, 14.0), daemon=True).start()

    def _new(self) -> str:
        if not self._has_display():
            return "I can only open a document when you're signed in at the desktop."
        cmd = next((c for c in _WRITERS if shutil.which(c[0])), None)
        if not cmd:
            return "I couldn't find a word processor to open."
        before = window_ids()
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._track_bg(before)
        return "Opening a blank document."

    def _candidates(self):
        for d in _SEARCH_DIRS:
            p = Path(os.path.expanduser(d))
            if not p.is_dir():
                continue
            try:
                for f in p.iterdir():
                    if f.is_file() and f.suffix.lower() in _DOC_EXTS:
                        yield f
            except OSError:
                continue

    def _open(self, query: str) -> str:
        if not self._has_display():
            return "I can only open a document when you're signed in at the desktop."
        key = re.sub(r"\s+", " ", _FILLER.sub(" ", query)).strip().lower()
        if not key:
            return "Which document?"
        terms = key.split()
        matches = [
            (f.stat().st_mtime, f)
            for f in self._candidates()
            if key in f.stem.lower() or all(t in f.stem.lower() for t in terms)
        ]
        if not matches:
            where = ", ".join(d.replace("~/", "") for d in _SEARCH_DIRS)
            return f"I couldn't find a document matching \"{query}\" in {where}."
        matches.sort(reverse=True)  # most recently modified first
        target = matches[0][1]
        before = window_ids()
        subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.last_doc = target
        self._track_bg(before)
        if len(matches) > 1:
            others = ", ".join(f.stem for _, f in matches[1:3])
            return f"Opening {target.stem}. I also found {others} — say its name if you meant that one."
        return f"Opening {target.stem}."

    def _recent(self, when: str) -> str:
        path = Path(os.path.expanduser("~/.local/share/recently-used.xbel"))
        if not path.is_file():
            return "I don't have any recent documents on record yet."
        try:
            root = ElementTree.parse(path).getroot()
        except Exception:
            return "I couldn't read the recent-documents history."
        dates = self._dates_for(when)
        seen, items = set(), []
        for b in root.iter("bookmark"):
            href = b.get("href", "")
            if not href.startswith("file://"):
                continue
            fp = Path(unquote(urlparse(href).path))
            if fp.suffix.lower() not in _DOC_EXTS:
                continue
            mod = b.get("modified") or b.get("visited") or ""
            if dates is not None and mod[:10] not in dates:
                continue
            if fp.name not in seen:
                seen.add(fp.name)
                items.append((mod, fp.name))
        if not items:
            return f"I don't see any documents from {when or 'recently'}."
        items.sort(reverse=True)
        names = [n for _, n in items[:5]]
        lead = "Recently you worked on" if dates is None else f"From {when}, you worked on"
        return f"{lead}: " + ", ".join(names) + "."

    @staticmethod
    def _dates_for(when: str) -> set[str] | None:
        """Set of 'YYYY-MM-DD' strings to keep, or None for no date filter (just 'recent')."""
        w = (when or "").lower()
        today = datetime.now().date()
        if "yesterday" in w:
            return {(today - timedelta(days=1)).isoformat()}
        if "today" in w or "this morning" in w:
            return {today.isoformat()}
        if "week" in w:
            return {(today - timedelta(days=i)).isoformat() for i in range(8)}
        return None
