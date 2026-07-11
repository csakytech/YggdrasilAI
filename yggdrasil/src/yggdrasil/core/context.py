"""Smart Help context — a live snapshot of WHERE the user is and what they can say there.

Answers the question every voice interface fails to: "what can I even say right now?". It
combines ThorOS's OWN state (an active Development mission always wins — Jarvis knows he put
you there) with the active desktop window (via core/focus's xdotool/WM_CLASS detection) to
produce a small, accurate card: where you are, the vital live facts, and the commands that
ACTUALLY work in that context. Every command listed here is a real route — a help screen that
suggests commands that do nothing is worse than none.

Used by the Help agent ("Jarvis, help") to fill the help window and speak a short summary, and
by the orchestrator to ground free-form "how do I …" questions in where the user actually is.
"""
from __future__ import annotations

from . import browsing, focus, mission

# A command is (say, does): the exact phrase to speak, and what it does. Kept short — the window
# shows them all; Jarvis speaks the top few.


def _dev_card(m: dict) -> dict:
    stage = m.get("stage") or "interview"
    pending = (m.get("pending") or "").strip()
    summary = m.get("summary") or m.get("goal") or "your project"
    vital = [f"Project: {summary}"]
    if stage == "describe":
        vital.append("You're describing your project — take all the time you need.")
        cmds = [("go ahead", "finish describing — I'll start the questions"),
                ("that's it", "same — you're done describing"),
                ("(just keep talking)", "add more detail; it all adds up, you won't be cut off"),
                ("cancel development", "leave Development Mode")]
    elif stage == "interview":
        if pending:
            vital.append(f"Current question: {pending}")
        cmds = [("(your answer)", "answer the question above, however you like"),
                ("you choose", "let me pick a sensible default for this one"),
                ("just decide the rest", "skip ahead — I'll decide the remaining questions"),
                ("show the mission", "open the Mission window to see the plan so far"),
                ("cancel development", "leave Development Mode")]
    elif stage == "proposal":
        vital.append("I've proposed a plan — review it in the Mission window.")
        cmds = [("set it up", "approve the plan and build the workspace"),
                ("change the language / editor / …", "adjust part of the plan"),
                ("cancel development", "leave Development Mode")]
    elif stage == "setup":
        vital.append("Workspace is ready — waiting for your go.")
        cmds = [("start building", "set the Agents building the project"),
                ("show the mission", "open the Mission window"),
                ("cancel development", "leave Development Mode")]
    elif stage == "build":
        vital.append("The Agents are building your project.")
        cmds = [("how's the build going", "hear the current build progress"),
                ("run the project", "launch what's been built so you can see it"),
                ("show the mission", "open the Mission window")]
    else:
        cmds = [("show the mission", "open the Mission window"),
                ("cancel development", "leave Development Mode")]
    return {"where": "development", "icon": "🛠️", "title": "Development Mode",
            "vital": vital, "commands": cmds}


def _browser_card(name: str) -> dict:
    vital: list[str] = []
    try:
        from . import webdriver  # local import: touching Marionette is best-effort
        url = webdriver.current_url()
        if url:
            vital.append(f"Page: {url[:72]}")
    except Exception:
        pass
    if not vital:
        try:
            s = browsing.get()
            if s.get("query"):
                vital.append(f"Last search: “{s['query']}”" +
                             (f" (page {s.get('page')})" if s.get("page", 1) > 1 else ""))
        except Exception:
            pass
    cmds = [("click", "put a number on every link and button on the page"),
            ("select 4", "click item number 4 (or “select the Wikipedia one”)"),
            ("read the links", "read the links aloud, numbered, for anyone who can't see"),
            ("read the page", "read/summarise the page aloud"),
            ("hide the numbers", "clear the number labels"),
            ("scroll down / scroll up", "move down or up the page"),
            ("go back / go forward", "browser history"),
            ("next page / go to page 4", "move through search results"),
            ("find <words>", "jump to text on the page"),
            ("search for <something>", "run a new web search")]
    return {"where": "browser", "icon": "🌐", "title": f"Firefox — {name}" if name else "Firefox",
            "vital": vital, "commands": cmds}


def _editor_card(name: str) -> dict:
    return {"where": "editor", "icon": "📝",
            "title": f"Writing — {name}" if name else "Word processor",
            "vital": ["You're in a document. Just talk to dictate; these shape and save it."],
            "commands": [("make it bold / italic / centred", "format the selected text"),
                         ("make it a bulleted list", "turn lines into a bullet list"),
                         ("insert a page break", "start a new page"),
                         ("undo that", "undo the last change"),
                         ("how many words is this", "count the words"),
                         ("replace michael with Michael", "find-and-replace text"),
                         ("save the document", "save (say “save as budget” to name it)"),
                         ("save it as a pdf", "export the document to PDF"),
                         ("save and close the document", "save, then close it")]}


def _terminal_card(name: str) -> dict:
    return {"where": "terminal", "icon": "⌨️",
            "title": f"Terminal — {name}" if name else "Terminal",
            "vital": ["Say a command in plain words and I'll type it into this terminal."],
            "commands": [("list the files", "runs the listing here"),
                         ("go to my Documents folder", "changes directory"),
                         ("(any command)", "I type what you say into the terminal"),
                         ("open the file viewer", "switch to files without typing"),
                         ("what's in my Downloads folder", "I can read a folder out for you")]}


def _files_card() -> dict:
    return {"where": "files", "icon": "📂", "title": "Files",
            "vital": ["Browsing your files. I always confirm before deleting or renaming."],
            "commands": [("what's in my reports folder", "list a folder"),
                         ("open the reports folder", "open a folder"),
                         ("create a folder called taxes", "make a new folder"),
                         ("find files named invoice", "search your files"),
                         ("rename budget.txt to 2026.txt", "rename (asks you to confirm)"),
                         ("delete the drafts folder", "delete (asks you to confirm first)")]}


def _app_card(name: str) -> dict:
    # A program ThorOS doesn't have a dedicated voice pack for yet — never dead-end. Name where
    # they are, and offer the universal commands that work everywhere.
    return {"where": "app", "icon": "🪟", "title": name or "This program",
            "vital": [f"You're in {name}." if name else "",
                      "I don't have special commands for this program yet, but I can still help."],
            "commands": _UNIVERSAL}


def _desktop_card() -> dict:
    return {"where": "desktop", "icon": "🏠", "title": "Desktop",
            "vital": ["Say “Jarvis”, then what you'd like to do."],
            "commands": _UNIVERSAL}


# Works anywhere — the ThorOS backbone.
_UNIVERSAL = [
    ("open firefox / open a terminal", "launch a program"),
    ("open a blank document", "start writing"),
    ("search for <something>", "search the web"),
    ("what's in my Downloads folder", "look at your files"),
    ("remind me to stretch in ten minutes", "set a reminder"),
    ("what was I working on yesterday", "recall your recent activity"),
    ("enter development mode", "build a program with the Agents"),
    ("change your voice", "pick a different voice"),
    ("help", "show this — wherever you are"),
]


def snapshot() -> dict:
    """The current context card. A live Development mission wins (Jarvis knows he put you there);
    otherwise it's whatever window you're working in; otherwise the desktop backbone."""
    m = mission.load()
    if m.get("active") and m.get("stage") in ("describe", "interview", "proposal", "setup", "build"):
        return _dev_card(m)
    name, kind = focus.active_window()
    low = (name or "").lower()
    if kind == "browser":
        return _browser_card(name)
    if kind == "editor":
        return _editor_card(name)
    if kind == "terminal":
        return _terminal_card(name)
    if any(f in low for f in ("nautilus", "files", "nemo", "thunar", "dolphin", "caja")):
        return _files_card()
    if kind == "application" and name:
        return _app_card(name)
    return _desktop_card()


def spoken(snap: dict) -> str:
    """A concise spoken version — names where you are and reads the top few commands. The window
    carries the full list (accessibility both directions)."""
    where = snap.get("title", "here")
    lead = {"development": f"You're in Development Mode.",
            "browser": f"You're in {where}.",
            "editor": f"You're in {where}.",
            "terminal": "You're at the terminal.",
            "files": "You're in your files.",
            "app": f"You're in {where}.",
            "desktop": "You're on the desktop."}.get(snap.get("where"), f"You're in {where}.")
    top = [c for c in snap.get("commands", []) if not c[0].startswith("(")][:4]
    says = "; ".join(f"“{say}” to {does}" for say, does in top)
    tail = "I've put the full list in the help window. What would you like to do?"
    return f"{lead} You can say: {says}. {tail}"
