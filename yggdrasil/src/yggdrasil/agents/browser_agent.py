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

from ..core import browsing, webdriver
from ..core import resolve as resolver
from ..core.focus import working_window
from ..core.permissions import Capability
from .base import BaseAgent

SETTLE_S = 0.3


# "one" is deliberately excluded — "the Wikipedia one" means a link, not number 1. "number one"
# is handled explicitly below.
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _spoken_number(text: str) -> int | None:
    """'number 3' / 'the third' / '3' / 'the 2nd' -> 3, else None. 'the Wikipedia one' -> None."""
    t = (text or "").lower()
    m = re.search(r"\b(\d+)", t)  # matches '3' and the digits in '2nd'
    if m:
        return int(m.group(1))
    if re.search(r"\b(?:number|link|result|item)\s+one\b", t):
        return 1
    for word, n in _ORDINALS.items():
        if re.search(rf"\b{word}\b", t):
            return n
    return None


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
        "read_links": Capability("read_links", False, "Read out the links on the page, numbered"),
        "open_link": Capability("open_link", False, "Open a link by its number or description"),
        "read_page": Capability("read_page", False, "Read/summarize the page's content aloud"),
        "expand": Capability("expand", False, "Click a Show-more / expand button on the page"),
    }

    def __init__(self, bus, perms, llm=None) -> None:
        super().__init__(bus, perms)
        self.llm = llm                 # reasoner — match spoken descriptions + summarize pages
        self._links: list[dict] = []   # last read link list (text + href), for "open number 3"

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
        if verb == "read_links":
            return self._read_links()
        if verb == "open_link":
            return await self._open_link(arg)
        if verb == "read_page":
            return await self._read_page()
        if verb == "expand":
            return self._expand(arg)
        raise ValueError(f"unhandled verb '{verb}'")

    # --- deep page reading (Marionette) ----------------------------------------------
    @staticmethod
    def _no_reader() -> dict:
        return {"speech": "I can't read this page yet — I can only read pages I opened for you. "
                          "Say “search for …” or “open firefox” and I'll set it up so I can read "
                          "the links and content."}

    def _read_links(self):
        if not webdriver.available():
            return self._no_reader()
        try:
            self._links = webdriver.get_links()
        except Exception:
            return self._no_reader()
        if not self._links:
            return {"speech": "I don't see any links to read on this page."}
        shown = self._links[:12]
        spoken = ". ".join(f"{i + 1}, {ln['text']}" for i, ln in enumerate(shown))
        more = f" There are {len(self._links) - len(shown)} more." if len(self._links) > len(shown) else ""
        return {"speech": f"Here are the links. {spoken}.{more} Say “open number …”, or "
                          "describe one, like “open the Wikipedia one”.",
                "list": [f"{i + 1}. {ln['text']}" for i, ln in enumerate(self._links)]}

    async def _open_link(self, ref: str):
        if not webdriver.available():
            return self._no_reader()
        if not self._links:
            try:
                self._links = webdriver.get_links()
            except Exception:
                self._links = []
        if not self._links:
            return {"speech": "I don't have any links yet — say “read me the links” first."}
        idx = await self._resolve_link(ref)
        if idx is None or not (0 <= idx < len(self._links)):
            return {"speech": f"I couldn't tell which link you meant by “{ref}”. Say a number, "
                              "like “open number three”, or read me the links again."}
        ln = self._links[idx]
        try:
            webdriver.client().navigate(ln["href"])
        except Exception:
            return {"speech": "I found the link but couldn't open it."}
        self._links = []  # the page is changing; re-read on the new one
        return {"speech": f"Opening {ln['text']}."}

    async def _resolve_link(self, ref: str) -> int | None:
        ref = (ref or "").strip()
        # 1) an explicit number / ordinal ("number 3", "the third", "3")
        n = _spoken_number(ref)
        if n is not None:
            return n - 1
        texts = [ln["text"] for ln in self._links]
        # 2) fuzzy text match (fast, offline)
        got, confident, _ = resolver.resolve(ref, texts, texts)
        if got and confident:
            return texts.index(got)
        # 3) the model matches a loose description ("the video one", "the news article")
        if self.llm is not None and ref:
            listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
            try:
                r = await self.llm.generate(
                    system=("The user wants to open one of these numbered web links by "
                            "describing it. Reply with JSON {\"number\": N} giving the best "
                            "matching link number, or {\"number\": 0} if none clearly match."),
                    prompt=f"Links:\n{listing}\n\nUser said: {ref}",
                    schema={"type": "object", "properties": {"number": {"type": "integer"}},
                            "required": ["number"]})
                num = int((r.parsed or {}).get("number", 0))
                if 1 <= num <= len(texts):
                    return num - 1
            except Exception:
                pass
        return None

    async def _read_page(self):
        if not webdriver.available():
            return self._no_reader()
        try:
            text = webdriver.get_main_text()
        except Exception:
            return self._no_reader()
        if not text.strip():
            return {"speech": "There's no readable text on this page."}
        if self.llm is not None:
            try:
                r = await self.llm.generate(
                    system=("Read this web page for someone who cannot see it. Give a clear, "
                            "natural spoken summary in 2-4 sentences — what the page is and its "
                            "key points. Don't invent anything. Plain text."),
                    prompt=text[:5000], temperature=0.3)
                s = (r.text or "").strip()
                if s:
                    return {"speech": s}
            except Exception:
                pass
        return {"speech": text[:600]}

    def _expand(self, arg: str):
        if not webdriver.available():
            return self._no_reader()
        for label in ([arg] if arg else []) + ["show more", "more", "read more", "expand", "load more"]:
            try:
                if webdriver.click_button(label):
                    return {"speech": "Expanded it. Say “read the page” to hear the rest."}
            except Exception:
                break
        return {"speech": "I didn't find a “show more” button on this page."}

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
