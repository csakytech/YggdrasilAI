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
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

from ..core.focus import track_new_window, window_ids, working_window
from ..core.permissions import Capability
from .base import BaseAgent

_DOCS_DIR = os.path.expanduser("~/Documents")  # where "save as X" writes, so "open X" finds it later
_SETTLE = 0.7  # wait after focusing / between keystroke steps so dialogs are ready


def _xdo(args: list[str]) -> bool:
    try:
        subprocess.run(["xdotool", *args], capture_output=True, timeout=8)
        return True
    except Exception:
        return False

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
# Files LibreOffice handles — open them with a UNO socket so the Writer agent (writer_agent.py)
# can drive the running instance.
_OFFICE_EXTS = {".odt", ".doc", ".docx", ".rtf", ".txt", ".md", ".ods", ".xls", ".xlsx",
                ".odp", ".ppt", ".pptx"}
_UNO_ACCEPT = "--accept=socket,host=localhost,port=2002;urp;"


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
        'save the document -> {"steps":[{"action":"documents.save","argument":""}]}',
        'save this document as testone -> {"steps":[{"action":"documents.save","argument":"testone"}]}',
        'save as testone and exit -> {"steps":[{"action":"documents.save","argument":"testone","argument2":"exit"}]}',
        'save and close the document -> {"steps":[{"action":"documents.save","argument":"","argument2":"exit"}]}',
    ]
    capabilities = {
        "new": Capability("new", False, "Open a new blank document"),
        "open": Capability("open", False, "Find and open a document by name"),
        "recent": Capability("recent", False, "List recently opened documents"),
        "save": Capability("save", False, "Save the open document (optionally with a name) and optionally close it"),
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
        if verb == "save":
            return {"speech": self._save(arg, (params.get("argument2") or ""))}
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
        if cmd[0] in ("libreoffice", "soffice"):
            cmd = [*cmd, _UNO_ACCEPT]  # enable UNO so the Writer agent can drive it
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
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if target.suffix.lower() in _OFFICE_EXTS and soffice:  # open with a UNO socket
            subprocess.Popen([soffice, _UNO_ACCEPT, str(target)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.last_doc = target
        self._track_bg(before)
        if len(matches) > 1:
            others = ", ".join(f.stem for _, f in matches[1:3])
            return f"Opening {target.stem}. I also found {others} — say its name if you meant that one."
        return f"Opening {target.stem}."

    def _save(self, name: str, flag: str) -> str:
        if not self._has_display():
            return "I can only save a document at the desktop."
        win_id, _, kind = working_window()
        if not kind:
            return "I don't see a document open to save."
        exit_after = bool(re.search(r"\b(exit|close|quit|done|finish)\b", f"{name} {flag}", re.I))
        # Reduce the spoken phrase to a clean filename stem.
        n = _FILLER.sub(" ", name)
        n = re.sub(r"\b(and|then|exit|close|quit|done|finish|save|as|it|this)\b", " ", n, flags=re.I)
        n = re.sub(r"[^\w .-]", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        n = re.sub(r"\.(odt|docx?|txt|rtf|md|odp|ods|pdf)$", "", n, flags=re.I).strip()

        _xdo(["windowfocus", win_id])
        time.sleep(_SETTLE)
        if n:
            os.makedirs(_DOCS_DIR, exist_ok=True)
            # Type the full path WITH .odt so LibreOffice saves native ODF (no "keep format?" dialog)
            # to a known folder, and a later "open <name>" finds it.
            path = os.path.join(_DOCS_DIR, n + ".odt")
            _xdo(["key", "ctrl+shift+s"])  # Save As
            time.sleep(1.4)
            _xdo(["type", "--clearmodifiers", "--delay", "25", "--", path])
            time.sleep(0.4)
            _xdo(["key", "Return"])
            time.sleep(1.3)
            saved = f" as {n}"
        else:
            _xdo(["key", "ctrl+s"])  # plain save for an already-named document
            time.sleep(0.8)
            saved = ""
        if exit_after:
            time.sleep(0.5)
            _xdo(["key", "ctrl+q"])  # close the program
            return f"Saved{saved} and closed it."
        return f"Saved{saved}."

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
