"""Browser Agent — operate the web browser by voice: scroll, go back/forward/reload, and page
through search results.

Uses the same proven mechanism as the Focus agent: X11 + xdotool. GNOME won't focus a window
we launched, but ``xdotool windowfocus`` (XSetInputFocus) works, so we grab focus on the target
and synthesize keystrokes. Scrolling drives whatever window you're working in (a browser, but
also a PDF or document — scrolling is universal); back/forward/reload and search pagination
target a browser window specifically. Search pagination rewrites the URL of a search WE opened
(via core.browsing) — reading arbitrary live URLs / clicking page links is the next rung
(a deeper accessibility integration).
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

from ..core import browsing
from ..core.focus import working_window
from ..core.permissions import Capability
from .base import BaseAgent

SETTLE_S = 0.3


def _xdo(args: list[str]) -> bool:
    try:
        subprocess.run(["xdotool", *args], capture_output=True, timeout=8)
        return True
    except Exception:
        return False


def _browser_window() -> tuple[str, str]:
    """(decimal window id, friendly name) of a browser window, or ('', '')."""
    win, name, kind = working_window()
    if kind == "browser":
        return win, name or "the browser"
    try:
        out = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True, timeout=3).stdout
        for ln in out.splitlines():
            low = ln.lower()
            if any(b in low for b in ("firefox", "navigator", "chrom", "brave", "epiphany")):
                try:
                    return str(int(ln.split()[0], 16)), "the browser"
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    return "", ""


class BrowserAgent(BaseAgent):
    domain = "browser"
    module_id = "core.browser"
    planner_examples = [
        'scroll down -> {"steps":[{"action":"browser.scroll","argument":"down"}]}',
        'scroll to the bottom -> {"steps":[{"action":"browser.scroll","argument":"bottom"}]}',
        'go back -> {"steps":[{"action":"browser.back","argument":""}]}',
        'go to the next page -> {"steps":[{"action":"browser.page","argument":"next"}]}',
        'go to page 4 -> {"steps":[{"action":"browser.page","argument":"4"}]}',
    ]
    capabilities = {
        "scroll": Capability("scroll", False, "Scroll the page (up/down/top/bottom/by lines)"),
        "back": Capability("back", False, "Go back to the previous page"),
        "forward": Capability("forward", False, "Go forward to the next page in history"),
        "reload": Capability("reload", False, "Reload the current page"),
        "page": Capability("page", False, "Go to another page of search results"),
        "find": Capability("find", False, "Find text on the current page"),
    }

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return {"speech": "I can only drive the browser when you're signed in at the desktop."}
        arg = (params.get("argument") or "").strip()
        if verb == "scroll":
            return self._scroll(arg)
        if verb == "back":
            return self._nav("alt+Left", "Went back.")
        if verb == "forward":
            return self._nav("alt+Right", "Went forward.")
        if verb == "reload":
            return self._nav("F5", "Reloading the page.")
        if verb == "page":
            return self._page(arg)
        if verb == "find":
            return self._find(arg)
        raise ValueError(f"unhandled verb '{verb}'")

    # --- key sending -----------------------------------------------------------------
    def _send(self, win_id: str, keys: list[str], repeat: int = 1) -> None:
        if win_id:
            _xdo(["windowfocus", win_id])
            time.sleep(SETTLE_S)
        for _ in range(repeat):
            for k in keys:
                _xdo(["key", "--clearmodifiers", k])
                time.sleep(0.02)

    # --- capabilities ----------------------------------------------------------------
    def _scroll(self, arg: str):
        win, name, kind = working_window()
        if not kind:
            return {"speech": "There's nothing open to scroll — open a page first."}
        a = arg.lower()
        if "top" in a or "beginning" in a:
            self._send(win, ["ctrl+Home"])
            return {"speech": "Scrolled to the top."}
        if "bottom" in a or "end" in a:
            self._send(win, ["ctrl+End"])
            return {"speech": "Scrolled to the bottom."}
        up = "up" in a
        m = re.search(r"(\d+)\s*(?:lines?|times?|clicks?)", a)
        if m:
            n = min(int(m.group(1)), 60)
            self._send(win, ["Up" if up else "Down"], repeat=n)
            return {"speech": f"Scrolled {'up' if up else 'down'} {n} lines."}
        if any(w in a for w in ("little", "bit", "few", "some")):
            self._send(win, ["Up" if up else "Down"], repeat=4)
            return {"speech": f"Scrolled {'up' if up else 'down'} a little."}
        # default (a page): "scroll down", "scroll down a page", "page down"
        self._send(win, ["Page_Up" if up else "Page_Down"])
        return {"speech": f"Scrolled {'up' if up else 'down'}."}

    def _nav(self, key: str, msg: str):
        win, _name = _browser_window()
        if not win:
            return {"speech": "I don't see a browser window open."}
        self._send(win, [key])
        return {"speech": msg}

    def _page(self, arg: str):
        win, _name = _browser_window()
        if not win:
            return {"speech": "Open a browser and run a search first, then I can page through "
                              "the results."}
        ctx = browsing.get()
        if not ctx.get("query"):
            return {"speech": "I can page through a web search I opened for you — say "
                              "“search for …” first, then “next page”."}
        cur = int(ctx.get("page", 1))
        a = arg.lower().strip()
        if a == "last":
            return {"speech": "I can't jump to the very last page of an endless list of "
                              "results — say “next page”, or a page number like “page five”."}
        if a == "first" or "first" in a:
            target = 1
        elif a == "previous" or "prev" in a:
            target = cur - 1
            if target < 1:
                return {"speech": "You're already on the first page."}
        elif a == "next" or a in ("", "forward"):
            target = cur + 1
        else:
            m = re.search(r"(\d+)", a)
            target = int(m.group(1)) if m else cur + 1
        target = max(1, min(target, 20))
        url = browsing.page_url(target)
        if not url:
            return {"speech": "I don't have a search to page through."}
        self._navigate(win, url)
        browsing.set_page(target)
        return {"speech": f"Going to page {target} of the results."}

    def _navigate(self, win: str, url: str) -> None:
        if win:
            _xdo(["windowfocus", win])
            time.sleep(SETTLE_S)
        _xdo(["key", "--clearmodifiers", "ctrl+l"])  # focus + select the address bar
        time.sleep(0.2)
        _xdo(["type", "--clearmodifiers", "--delay", "8", "--", url])
        time.sleep(0.1)
        _xdo(["key", "Return"])

    def _find(self, text: str):
        win, _name = _browser_window()
        if not win:
            return {"speech": "Open a page first, then I can search within it."}
        self._send(win, ["ctrl+f"])
        time.sleep(0.2)
        if text:
            _xdo(["type", "--clearmodifiers", "--", text])
            _xdo(["key", "Return"])
            return {"speech": f"Finding “{text}” on the page."}
        return {"speech": "Opened find-on-page — say what to look for."}
