"""LibreOffice Agent (Core deep-adapter) — controls a running LibreOffice via the UNO API.

This is the flagship "deep adapter" (docs/MODULES.md): instead of faking menu clicks, it connects
to LibreOffice over a UNO socket and dispatches the *real* commands — so the planner maps a request
to one of LibreOffice's hundreds of `.uno:` commands (bold, page break, …) or a high-level API call
(find/replace, export PDF), robustly and without per-menu fragility. Documents opened through Jarvis
are launched with `--accept=socket,…,port=2002` so this agent can connect. Needs `python3-uno`.

It's the template community deep-adapter agents (GIMP, Inkscape, …) should follow: a thin map from
intent → the app's own command surface.
"""
from __future__ import annotations

import os
import re
from typing import Any

from ..core.permissions import Capability
from .base import BaseAgent

PORT = 2002

# Friendly name (normalized) -> .uno: command for no-argument toggles/actions. This is a curated
# slice of LibreOffice's command surface; extend freely — each line adds a voice-reachable command.
_UNO = {
    "bold": ".uno:Bold", "italic": ".uno:Italic", "italics": ".uno:Italic",
    "underline": ".uno:Underline", "strikethrough": ".uno:Strikeout", "strike through": ".uno:Strikeout",
    "center": ".uno:CenterPara", "centre": ".uno:CenterPara", "center align": ".uno:CenterPara",
    "left align": ".uno:LeftPara", "align left": ".uno:LeftPara",
    "right align": ".uno:RightPara", "align right": ".uno:RightPara",
    "justify": ".uno:JustifyPara", "justified": ".uno:JustifyPara",
    "select all": ".uno:SelectAll",
    "page break": ".uno:InsertPagebreak", "insert page break": ".uno:InsertPagebreak",
    "bullet list": ".uno:DefaultBullet", "bullets": ".uno:DefaultBullet", "bulleted list": ".uno:DefaultBullet",
    "numbered list": ".uno:DefaultNumbering", "numbering": ".uno:DefaultNumbering",
    "undo": ".uno:Undo", "redo": ".uno:Redo",
    "copy": ".uno:Copy", "cut": ".uno:Cut", "paste": ".uno:Paste",
    "subscript": ".uno:SubScript", "superscript": ".uno:SuperScript",
    "uppercase": ".uno:ChangeCaseToUpper", "lowercase": ".uno:ChangeCaseToLower",
    "title case": ".uno:ChangeCaseToTitleCase", "sentence case": ".uno:ChangeCaseToSentenceCase",
    "grow font": ".uno:Grow", "bigger font": ".uno:Grow", "increase font size": ".uno:Grow",
    "shrink font": ".uno:Shrink", "smaller font": ".uno:Shrink", "decrease font size": ".uno:Shrink",
}


class _Office:
    """Thin UNO connection to a running LibreOffice (connect per-call; bridges can go stale)."""

    def __init__(self, port: int = PORT) -> None:
        self.port = port

    def _ctx(self):
        import uno  # system python3-uno (available in the venv via system-site-packages)

        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local)
        try:
            remote = resolver.resolve(
                f"uno:socket,host=localhost,port={self.port};urp;StarOffice.ComponentContext")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("cannot connect to LibreOffice") from e
        smgr = remote.ServiceManager
        desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", remote)
        return remote, smgr, desktop

    def _doc(self):
        remote, smgr, desktop = self._ctx()
        doc = desktop.getCurrentComponent()
        if doc is None:
            raise RuntimeError("no document open")
        return remote, smgr, doc

    def dispatch(self, command: str) -> None:
        remote, smgr, doc = self._doc()
        frame = doc.getCurrentController().getFrame()
        helper = smgr.createInstanceWithContext("com.sun.star.frame.DispatchHelper", remote)
        helper.executeDispatch(frame, command, "", 0, ())

    def replace_all(self, find: str, repl: str) -> int:
        _r, _s, doc = self._doc()
        rd = doc.createReplaceDescriptor()
        rd.SearchString = find
        rd.ReplaceString = repl
        rd.SearchCaseSensitive = True  # so "michael" -> "Michael" actually changes the case
        return doc.replaceAll(rd)

    def export_pdf(self, path: str) -> str:
        import unohelper
        from com.sun.star.beans import PropertyValue

        _r, _s, doc = self._doc()
        pv = PropertyValue()
        pv.Name = "FilterName"
        pv.Value = "writer_pdf_Export"
        doc.storeToURL(unohelper.systemPathToFileUrl(path), (pv,))
        return path

    def doc_path(self) -> str:
        import unohelper

        _r, _s, doc = self._doc()
        url = getattr(doc, "URL", "") or ""
        return unohelper.fileUrlToSystemPath(url) if url else ""

    def word_count(self) -> tuple[int, int]:
        _r, _s, doc = self._doc()
        text = doc.getText().getString()
        return len(text.split()), len(text)


class WriterAgent(BaseAgent):
    domain = "writer"
    module_id = "core.libreoffice"
    planner_examples = [
        'make it bold -> {"steps":[{"action":"writer.format","argument":"bold"}]}',
        'italicize this -> {"steps":[{"action":"writer.format","argument":"italic"}]}',
        'center this -> {"steps":[{"action":"writer.format","argument":"center"}]}',
        'select all -> {"steps":[{"action":"writer.format","argument":"select all"}]}',
        'insert a page break -> {"steps":[{"action":"writer.format","argument":"page break"}]}',
        'make it a bulleted list -> {"steps":[{"action":"writer.format","argument":"bullet list"}]}',
        'undo that -> {"steps":[{"action":"writer.format","argument":"undo"}]}',
        'export this to pdf -> {"steps":[{"action":"writer.export","argument":""}]}',
        'save it as a pdf -> {"steps":[{"action":"writer.export","argument":""}]}',
        'replace michael with Michael -> {"steps":[{"action":"writer.replace","argument":"michael","argument2":"Michael"}]}',
        'capitalize the word michael -> {"steps":[{"action":"writer.replace","argument":"michael","argument2":"Michael"}]}',
        'how many words is this -> {"steps":[{"action":"writer.count","argument":""}]}',
    ]
    capabilities = {
        "format": Capability("format", False, "Apply a LibreOffice formatting or structural command"),
        "replace": Capability("replace", False, "Find and replace text in the document"),
        "export": Capability("export", False, "Export the document to PDF"),
        "count": Capability("count", False, "Count the words in the document"),
    }

    def __init__(self, bus, perms) -> None:
        super().__init__(bus, perms)
        self.office = _Office()

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        arg = (params.get("argument") or "").strip()
        try:
            if verb == "format":
                return {"speech": self._format(arg)}
            if verb == "replace":
                return {"speech": self._replace(arg, (params.get("argument2") or "").strip())}
            if verb == "export":
                return {"speech": self._export(arg)}
            if verb == "count":
                words, chars = self.office.word_count()
                return {"speech": f"This document has {words} words and {chars} characters."}
        except Exception as e:  # noqa: BLE001
            return {"speech": self._friendly_error(e)}
        raise ValueError(f"unhandled verb '{verb}'")

    def _format(self, name: str) -> str:
        key = re.sub(r"\s+", " ", name.lower()).strip()
        cmd = _UNO.get(key) or next((v for k, v in _UNO.items() if k in key or key in k), None)
        if not cmd:
            return f"I don't have a LibreOffice command for “{name}” yet."
        self.office.dispatch(cmd)
        return f"Done — {key}."

    def _replace(self, find: str, repl: str) -> str:
        if not find or not repl:
            return "Replace what with what?"
        n = self.office.replace_all(find, repl)
        if n == 0:
            return f"I didn't find “{find}” in the document."
        return f"Replaced {n} occurrence{'s' if n != 1 else ''} of “{find}” with “{repl}.”"

    def _export(self, name: str) -> str:
        src = self.office.doc_path()
        if name:
            safe = re.sub(r"[^\w .-]", "", name).strip() or "document"
            path = os.path.expanduser(f"~/Documents/{safe}.pdf")
        elif src:
            path = os.path.splitext(src)[0] + ".pdf"
        else:
            path = os.path.expanduser("~/Documents/document.pdf")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.office.export_pdf(path)
        return f"Exported to {os.path.basename(path)}."

    @staticmethod
    def _friendly_error(e: Exception) -> str:
        msg = str(e).lower()
        if "no module named 'uno'" in msg or "no module named \"uno\"" in msg:
            return "LibreOffice scripting (python3-uno) isn't installed."
        if "no document" in msg:
            return "There's no document open. Open one and try again."
        if "cannot connect" in msg:
            return "I can't reach LibreOffice. Open a document through me first, then try again."
        return "I couldn't do that in LibreOffice."
