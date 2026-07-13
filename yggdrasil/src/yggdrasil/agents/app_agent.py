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
import shlex
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

from ..core import transcript
from ..core.focus import set_target
from ..core.permissions import Capability
from .base import BaseAgent

_THINK = re.compile(r"<think>.*?</think>", re.S)

# Friendly SYNONYMS only — layered on top of the system app database below, NOT a hard-coded list
# of every program. "browser" → firefox, "word processor" → libreoffice, etc.
_ALIASES = {
    "word editor": "libreoffice --writer", "word processor": "libreoffice --writer",
    "writer": "libreoffice --writer", "text editor": "gnome-text-editor",
    "editor": "gnome-text-editor", "browser": "firefox --marionette",
    "web browser": "firefox --marionette", "firefox": "firefox --marionette",
    "files": "nautilus", "file manager": "nautilus", "terminal": "gnome-terminal",
    "calculator": "gnome-calculator",
    "dashboard": "yggdrasil-dashboard", "yggdrasil dashboard": "yggdrasil-dashboard",
    "control panel": "yggdrasil-dashboard",
    "thorai settings": "yggdrasil-settings", "thor ai settings": "yggdrasil-settings",
    "assistant settings": "yggdrasil-settings", "jarvis settings": "yggdrasil-settings",
    "voice settings": "yggdrasil-settings",
    "chat": "yggdrasil-chat", "chat window": "yggdrasil-chat", "chat box": "yggdrasil-chat",
    "text chat": "yggdrasil-chat", "jarvis chat": "yggdrasil-chat",
    "settings": "gnome-control-center", "system settings": "gnome-control-center",
}

# Where the OS records every installed GUI app (the freedesktop .desktop database). Reading this is
# how Jarvis launches ANY installed program by name without per-app code.
_APP_DIRS = (
    "/usr/share/applications",
    "/usr/local/share/applications",
    os.path.expanduser("~/.local/share/applications"),
    "/var/lib/flatpak/exports/share/applications",
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
)


def _clean_exec(exec_str: str) -> list[str] | None:
    """A .desktop Exec= line → an argv, dropping the %-field codes (%U %F %i %c …)."""
    try:
        toks = shlex.split(exec_str)
    except ValueError:
        toks = exec_str.split()
    argv = [t for t in toks if not t.startswith("%")]
    return argv or None


def _read_desktop_apps() -> list[dict]:
    """Scan the .desktop database into launchable apps (skipping hidden / no-display entries)."""
    apps: list[dict] = []
    for d in _APP_DIRS:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for fn in names:
            if not fn.endswith(".desktop"):
                continue
            info: dict[str, str] = {}
            in_entry = False
            try:
                with open(os.path.join(d, fn), encoding="utf-8", errors="ignore") as fh:
                    for raw in fh:
                        line = raw.rstrip("\n")
                        if line.startswith("["):
                            in_entry = line.strip() == "[Desktop Entry]"
                            continue
                        if not in_entry or "=" not in line or "[" in line.split("=", 1)[0]:
                            continue  # other groups, or localized keys like Name[de]=
                        k, _, v = line.partition("=")
                        info[k.strip()] = v.strip()
            except OSError:
                continue
            if info.get("Type", "Application") != "Application":
                continue
            if info.get("NoDisplay", "").lower() == "true" or info.get("Hidden", "").lower() == "true":
                continue
            argv = _clean_exec(info.get("Exec", ""))
            if not argv:
                continue
            apps.append({
                "id": fn[:-8],
                "name": info.get("Name", fn[:-8]),
                "argv": argv,
                "extra": f"{info.get('GenericName', '')} {info.get('Keywords', '')}".lower(),
            })
    return apps


class AppsAgent(BaseAgent):
    domain = "app"
    module_id = "core.apps"
    planner_examples = [
        'open firefox -> {"steps":[{"action":"app.launch","argument":"firefox"}]}',
        'open the dashboard -> {"steps":[{"action":"app.launch","argument":"dashboard"}]}',
        'open a word editor -> {"steps":[{"action":"app.launch","argument":"word editor"}]}',
        'open a terminal window -> {"steps":[{"action":"app.launch","argument":"terminal"}]}',
        # Goal-oriented: "set me up to X" ends with the TOOL OPEN, not just folders made.
        'help me set up so I can write a book -> {"steps":[{"action":"file.create_folder","argument":"Book"},'
        '{"action":"file.create_folder","argument":"Book/Chapters"},'
        '{"action":"file.create_folder","argument":"Book/Notes"},'
        '{"action":"app.launch","argument":"libreoffice writer"}]}',
        'set up a place for my recipes so I can add one -> {"steps":[{"action":"file.create_folder","argument":"Recipes"},'
        '{"action":"app.launch","argument":"libreoffice writer"}]}',
        'write a short story about a dragon -> {"steps":[{"action":"app.write_document","argument":"a short story about a dragon"}]}',
        'write a poem about the sea -> {"steps":[{"action":"app.write_document","argument":"a poem about the sea"}]}',
        'what apps are installed -> {"steps":[{"action":"app.list_apps","argument":""}]}',
        'close firefox -> {"steps":[{"action":"app.close","argument":"firefox"}]}',
        'close it -> {"steps":[{"action":"app.close","argument":"it"}]}',
        'go to google.com -> {"steps":[{"action":"app.browse","argument":"google.com"}]}',
        'open youtube.com -> {"steps":[{"action":"app.browse","argument":"youtube.com"}]}',
        'search for robots -> {"steps":[{"action":"app.search","argument":"robots"}]}',
        'google self driving cars -> {"steps":[{"action":"app.search","argument":"self driving cars"}]}',
        # "open google AND search X" is ONE search, not browse-then-search — two steps race the
        # browser's first startup and the search can be dropped.
        'open google and search for cats -> {"steps":[{"action":"app.search","argument":"cats"}]}',
        'go to google and look up electric bikes -> {"steps":[{"action":"app.search","argument":"electric bikes"}]}',
    ]
    capabilities = {
        "launch": Capability("launch", False, "Open/launch a desktop application"),
        "close": Capability("close", False, "Close a running application"),
        "browse": Capability("browse", False, "Open a web page in the browser"),
        "search": Capability("search", False, "Search the web"),
        "write_document": Capability("write_document", False, "Write a document with the AI and open it"),
        "list_apps": Capability("list_apps", False, "List installed applications"),
    }

    def __init__(self, bus, perms, llm=None, workspace: str | os.PathLike = ".") -> None:
        super().__init__(bus, perms)
        self.llm = llm
        self.workspace = Path(workspace)
        self.last_app: str | None = None  # for "close it" and active-app feel

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if verb == "launch":
            r = self._launch((params.get("argument") or "").strip())
            return r if isinstance(r, dict) else {"speech": r}
        if verb == "close":
            return {"speech": self._close((params.get("argument") or "").strip())}
        if verb == "browse":
            return {"speech": self._browse((params.get("argument") or "").strip())}
        if verb == "search":
            return {"speech": self._search((params.get("argument") or "").strip())}
        if verb == "list_apps":
            return {"speech": self._list_apps()}
        if verb == "write_document":
            return await self._write_document((params.get("argument") or "").strip())
        raise ValueError(f"unhandled verb '{verb}'")

    @staticmethod
    def _has_display() -> bool:
        return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))

    @staticmethod
    def _window_ids() -> set[str]:
        try:
            out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=3).stdout
            return {ln.split()[0] for ln in out.splitlines() if ln.strip()}
        except Exception:
            return set()

    def _track_launched(self, before: set[str], timeout: float = 2.5) -> None:
        """Record the window the launch produced as the working target, so a follow-up like
        "list files" routes to the new terminal. We deliberately do NOT try to focus it here —
        GNOME blocks programmatic activation; the Focus agent grabs focus itself (XSetInputFocus)
        only at the moment it types. Best-effort, X11 only; needs wmctrl."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            new = self._window_ids() - before
            if new:
                try:  # wmctrl ids are hex; focus tracking uses decimal
                    set_target(str(int(sorted(new)[-1], 16)))
                except Exception:
                    pass
                return
            time.sleep(0.15)

    def _resolve_app(self, key: str) -> tuple[list[str] | None, str | None]:
        """Map a spoken app name to (argv, friendly label): friendly aliases first, then the
        system's .desktop database (so ANY installed program resolves), then a bare binary on PATH.
        Returns (None, None) when nothing matches — so the caller can be honest instead of pretending."""
        if not key:
            return None, None
        if key in _ALIASES:
            return _ALIASES[key].split(), key
        apps = _read_desktop_apps()
        for match in (  # exact name → substring either way → generic-name/keywords
            lambda a: a["name"].lower() == key,
            lambda a: key in a["name"].lower() or a["name"].lower() in key,
            lambda a: key in a["extra"],
        ):
            for a in apps:
                if match(a):
                    return a["argv"], a["name"]
        if shutil.which(key):
            return [key], key
        return None, None

    def _launch(self, name: str) -> str:
        if not name:
            return "Which application?"
        if not self._has_display():
            return f"I can only open {name} when you're signed in at the desktop."
        # Normalize phrasing: "the dashboard program" -> "dashboard"
        key = re.sub(r"\s+(program|app|application|window)$", "",
                     re.sub(r"^(the|my|a)\s+", "", name.lower().strip()))
        argv, label = self._resolve_app(key)
        if not argv:
            # Not a real installed app — often an open-ended request mis-routed here ("create an app
            # to track my workouts"). Don't dead-end: signal the orchestrator to let the backbone help.
            return {"speech": f"There's no app called {name} installed.", "assist": True}
        before = self._window_ids()
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.last_app = (label or key).lower()
        except Exception:
            return f"I couldn't open {name}."
        self._track_launched(before)  # so the next command routes to it
        return f"Opening {label or name}."

    def _close(self, name: str) -> str:
        key = name.lower().strip()
        if key in ("it", "that", "this", "") and self.last_app:
            key = self.last_app
        if not key:
            return "Close what?"
        alias = _ALIASES.get(key, key)
        # Terms to match against a window's class+title (e.g. "dashboard" -> {dashboard, yggdrasil},
        # since the dashboard's WM_CLASS is org.yggdrasil.Dashboard).
        terms = [t for t in re.split(r"[\s.\-]+", f"{key} {alias}".lower()) if len(t) >= 3 and t != "the"]
        closed = False

        # 1) Close matching windows by id (graceful) — robust regardless of the process name.
        try:
            for line in subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True,
                                       timeout=5).stdout.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 3:
                    continue
                hay = (parts[2] + " " + (parts[4] if len(parts) > 4 else "")).lower()
                if any(t in hay for t in terms):
                    subprocess.run(["wmctrl", "-i", "-c", parts[0]], capture_output=True, timeout=5)
                    closed = True
        except Exception:
            pass

        # 2) Fallback: kill by process name. Try the launcher, the `python -m yggdrasil.ui.X` module
        #    form (our GUIs), and the bare name — fixes "yggdrasil-dashboard" vs "yggdrasil.ui.dashboard".
        if not closed:
            proc = os.path.basename(alias.split()[0])
            for pat in dict.fromkeys([proc, proc.replace("yggdrasil-", "ui."), key]):
                try:
                    if subprocess.run(["pkill", "-f", pat], capture_output=True, timeout=5).returncode == 0:
                        closed = True
                        break
                except Exception:
                    pass

        if closed:
            if self.last_app == key:
                self.last_app = None
            return f"Closed {key}."
        return f"{key} doesn't seem to be running."

    @staticmethod
    def _open_in_firefox(url: str) -> None:
        """Open a URL in Firefox WITH Marionette enabled, so the Browser agent can read/drive the
        page (list links, open one, read aloud). Marionette must be on from the FIRST launch, so we
        launch Firefox directly with --marionette rather than xdg-open. If Firefox is already up
        (with Marionette), this just opens a new tab.

        Firefox is SINGLE-INSTANCE: while the first instance is still STARTING (fresh boot, or the
        slow first-ever run right after install), a second invocation's remote handoff is silently
        dropped. That was the "open google and search for X" bug — google opened, the search never
        arrived. If a firefox process exists but hasn't mapped a window yet, wait for it first."""
        ff = shutil.which("firefox") or shutil.which("firefox-esr") or "firefox"
        if AppsAgent._firefox_process() and not AppsAgent._firefox_window_up():
            deadline = time.time() + 15
            while time.time() < deadline and not AppsAgent._firefox_window_up():
                time.sleep(0.4)
            time.sleep(0.8)  # window mapped != remoting ready — give it a beat
        subprocess.Popen([ff, "--marionette", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @staticmethod
    def _firefox_process() -> bool:
        try:
            return subprocess.run(["pgrep", "-af", "firefox"], capture_output=True,
                                  timeout=3).returncode == 0
        except Exception:
            return False

    @staticmethod
    def _firefox_window_up() -> bool:
        try:
            out = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True, timeout=3).stdout
            return "firefox" in out.lower()
        except Exception:
            return False

    def _browse(self, target: str) -> str:
        if not self._has_display():
            return "I can only browse when you're signed in at the desktop."
        t = target.strip().rstrip(".")
        if not t:
            return "Go where?"
        if not re.match(r"^https?://", t):
            if "." in t and " " not in t:
                t = "https://" + t
            else:
                return self._search(target)  # not a URL -> treat as a search
        try:
            before = self._window_ids()
            self._open_in_firefox(t)
            self.last_app = "firefox"
            self._track_launched(before, timeout=1.5)
            return f"Opening {t}."
        except Exception as e:
            transcript.log("agent_error", agent="app", verb="browse", error=repr(e))
            return f"I couldn't open {t}."

    def _search(self, query: str) -> str:
        if not self._has_display():
            return "I can only search the web when you're signed in at the desktop."
        q = query.strip()
        if not q:
            return "Search for what?"
        from ..core import browsing, config
        engine = config.get_search_engine()  # duckduckgo default — google CAPTCHAs our
        qs = urllib.parse.quote(q)           # marionette browser, a hard wall hands-free
        if engine == "google":
            url = f"https://www.google.com/search?q={qs}"
        elif engine == "bing":
            url = f"https://www.bing.com/search?q={qs}"
        else:
            url = f"https://duckduckgo.com/?q={qs}"
        try:
            before = self._window_ids()
            self._open_in_firefox(url)
            self.last_app = "firefox"
            browsing.set_search(q, engine=engine)  # so "next page" pages the same engine
            self._track_launched(before, timeout=1.5)
            return f"Searching the web for {q}."
        except Exception as e:
            transcript.log("agent_error", agent="app", verb="search", error=repr(e))
            return f"I couldn't search for {q}."

    def _list_apps(self) -> str:
        names = sorted({a["name"] for a in _read_desktop_apps()})
        if not names:
            return "I couldn't find any installed apps."
        # "Make a list" means a real list — not reciting 50 names aloud. Save the full set and open it
        # on screen; speak a short summary so the answer is actually useful.
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = self.workspace / "installed-apps.txt"
        path.write_text(f"Installed applications ({len(names)}):\n\n" + "\n".join(names) + "\n",
                        encoding="utf-8")
        opened = ""
        if self._has_display():
            try:
                subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                opened = " I've opened the full list on screen."
            except Exception:
                opened = ""
        sample = ", ".join(names[:6])
        return (f"You have {len(names)} apps installed. I saved the full list to {path.name}.{opened} "
                f"A few examples: {sample}.")

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
