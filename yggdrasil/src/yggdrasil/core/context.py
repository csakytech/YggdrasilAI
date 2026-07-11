"""Smart Help context — a live snapshot of WHERE the user is and what they can say there.

Answers the question every voice interface fails to: "what can I even say right now?". It
combines ThorOS's OWN state (an active Development mission always wins — Jarvis knows he put
you there) with the active desktop window (via core/focus's xdotool/WM_CLASS detection) to
produce a small, accurate card: where you are, the vital live facts, and the commands that
ACTUALLY work in that context. Every command listed here is a real route — a help screen that
suggests commands that do nothing is worse than none.

Each command is a dict: ``say`` (the phrase to speak), ``does`` (what it does), and an optional
``run`` — the concrete phrase to execute when the user picks it by number ("do number 3"). A
command with NO ``run`` is a template or example (e.g. "delete the drafts folder", "find
<words>"): picking it by number must never fire it blindly, so Jarvis guides instead.

Used by the Help agent ("Jarvis, help") and the orchestrator (numbered run + grounding
free-form "how do I …" questions in where the user actually is).
"""
from __future__ import annotations

from . import browsing, focus, mission


def _c(say: str, does: str, run: str | None = None) -> dict:
    d = {"say": say, "does": does}
    if run:
        d["run"] = run
    return d


def _dev_card(m: dict) -> dict:
    stage = m.get("stage") or "interview"
    pending = (m.get("pending") or "").strip()
    summary = m.get("summary") or m.get("goal") or "your project"
    vital = [f"Project: {summary}"]
    if stage == "describe":
        vital.append("You're describing your project — take all the time you need.")
        cmds = [_c("go ahead", "finish describing — I'll start the questions", "go ahead"),
                _c("that's it", "same — you're done describing", "that's it"),
                _c("(just keep talking)", "add more detail; it all adds up, you won't be cut off"),
                _c("cancel development", "leave Development Mode", "cancel development")]
    elif stage == "interview":
        if pending:
            vital.append(f"Current question: {pending}")
        cmds = [_c("(your answer)", "answer the question above, however you like"),
                _c("you choose", "let me pick a sensible default for this one", "you choose"),
                _c("just decide the rest", "skip ahead — I'll decide the rest", "just decide the rest"),
                _c("show the mission", "open the Mission window to see the plan so far", "show the mission"),
                _c("cancel development", "leave Development Mode", "cancel development")]
    elif stage == "proposal":
        vital.append("I've proposed a plan — review it in the Mission window.")
        cmds = [_c("set it up", "approve the plan and build the workspace", "set it up"),
                _c("change the language / editor / …", "adjust part of the plan"),
                _c("show the mission", "open the Mission window", "show the mission"),
                _c("cancel development", "leave Development Mode", "cancel development")]
    elif stage == "setup":
        vital.append("Workspace is ready — waiting for your go.")
        cmds = [_c("start building", "set the Agents building the project", "start building"),
                _c("show the mission", "open the Mission window", "show the mission"),
                _c("cancel development", "leave Development Mode", "cancel development")]
    elif stage == "build":
        vital.append("The Agents are building your project.")
        cmds = [_c("how's the build going", "hear the current build progress", "how's the build going"),
                _c("run the project", "launch what's been built so you can see it", "run the project"),
                _c("show the mission", "open the Mission window", "show the mission")]
    else:
        cmds = [_c("show the mission", "open the Mission window", "show the mission"),
                _c("cancel development", "leave Development Mode", "cancel development")]
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
    cmds = [_c("click", "put a number on every link and button on the page", "click"),
            _c("select 4", "click item number 4 (or “select the Wikipedia one”)"),
            _c("read the links", "read the links aloud, numbered, for anyone who can't see", "read the links"),
            _c("read the page", "read/summarise the page aloud", "read the page"),
            _c("hide the numbers", "clear the number labels", "hide the numbers"),
            _c("scroll down / scroll up", "move down or up the page", "scroll down"),
            _c("go back / go forward", "browser history", "go back"),
            _c("next page / go to page 4", "move through search results", "next page"),
            _c("find <words>", "jump to text on the page"),
            _c("search for <something>", "run a new web search")]
    return {"where": "browser", "icon": "🌐", "title": f"Firefox — {name}" if name else "Firefox",
            "vital": vital, "commands": cmds}


def _editor_card(name: str) -> dict:
    return {"where": "editor", "icon": "📝",
            "title": f"Writing — {name}" if name else "Word processor",
            "vital": ["You're in a document. Just talk to dictate; these shape and save it."],
            "commands": [
                _c("make it bold / italic / centred", "format the selected text", "make it bold"),
                _c("make it a bulleted list", "turn lines into a bullet list", "make it a bulleted list"),
                _c("insert a page break", "start a new page", "insert a page break"),
                _c("undo that", "undo the last change", "undo that"),
                _c("how many words is this", "count the words", "how many words is this"),
                _c("replace michael with Michael", "find-and-replace text (say your own words)"),
                _c("save the document", "save it (say “save as budget” to name it)", "save the document"),
                _c("save it as a pdf", "export the document to PDF", "save it as a pdf"),
                _c("save and close the document", "save, then close it", "save and close the document")]}


def _terminal_card(name: str) -> dict:
    return {"where": "terminal", "icon": "⌨️",
            "title": f"Terminal — {name}" if name else "Terminal",
            "vital": ["Say a command in plain words and I'll type it into this terminal."],
            "commands": [
                _c("list the files", "type a listing into the terminal", "list the files"),
                _c("go to my Documents folder", "change directory (say your own folder)"),
                _c("(any command)", "I type what you say into the terminal"),
                _c("open the file viewer", "switch to files without typing", "open the file viewer"),
                _c("what's in my Downloads folder", "I can read a folder out for you",
                   "what's in my Downloads folder")]}


def _files_card() -> dict:
    # Every entry here carries a placeholder name (reports/taxes/drafts), so NONE is directly
    # runnable — picking one by number guides you to say it with your own name. This is also the
    # safety guarantee: "delete the drafts folder" can never fire from a number.
    return {"where": "files", "icon": "📂", "title": "Files",
            "vital": ["Browsing your files. I always confirm before deleting or renaming."],
            "commands": [
                _c("what's in my reports folder", "list a folder (say your own folder name)"),
                _c("open the reports folder", "open a folder (say your own folder name)"),
                _c("create a folder called taxes", "make a new folder (say your own name)"),
                _c("find files named invoice", "search your files (say your own term)"),
                _c("rename budget.txt to 2026.txt", "rename — I'll confirm first"),
                _c("delete the drafts folder", "delete — I always confirm before deleting")]}


def _app_card(name: str) -> dict:
    # A program ThorOS doesn't have a dedicated voice pack for yet — never dead-end. Name where
    # they are, and offer the universal commands that work everywhere.
    return {"where": "app", "icon": "🪟", "title": name or "This program",
            "vital": [f"You're in {name}." if name else "",
                      "I don't have special commands for this program yet, but I can still help."],
            "commands": list(_universal())}


def _desktop_card() -> dict:
    return {"where": "desktop", "icon": "🏠", "title": "Desktop",
            "vital": ["Say “Jarvis”, then what you'd like to do."],
            "commands": list(_universal())}


def _universal() -> list[dict]:
    # Works anywhere — the ThorOS backbone. Examples with specific details (a reminder time, a
    # search term) carry no ``run`` so picking them by number guides rather than firing literally.
    return [
        _c("open firefox / open a terminal", "launch a program", "open firefox"),
        _c("open a blank document", "start writing", "open a blank document"),
        _c("search for <something>", "search the web"),
        _c("what's in my Downloads folder", "look at your files", "what's in my Downloads folder"),
        _c("remind me to stretch in ten minutes", "set a reminder (say your own)"),
        _c("what was I working on yesterday", "recall your recent activity", "what was I working on yesterday"),
        _c("enter development mode", "build a program with the Agents", "enter development mode"),
        _c("change your voice", "pick a different voice", "change your voice"),
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
    """A concise spoken version — names where you are, then reads the top few commands WITH their
    numbers, so a listener can pick one ("do number 2"). The window carries the full list."""
    where = snap.get("title", "here")
    lead = {"development": "You're in Development Mode.",
            "browser": f"You're in {where}.",
            "editor": f"You're in {where}.",
            "terminal": "You're at the terminal.",
            "files": "You're in your files.",
            "app": f"You're in {where}.",
            "desktop": "You're on the desktop."}.get(snap.get("where"), f"You're in {where}.")
    # number every command (matches the window), then read the first few that make sense aloud
    picks = []
    for i, c in enumerate(snap.get("commands", []), 1):
        if c["say"].startswith("("):
            continue
        picks.append(f"{i}, “{c['say']}”, to {c['does']}")
        if len(picks) >= 4:
            break
    says = "; ".join(picks)
    tail = ("Say the number to run it — for example “do number 1”. The help window has the rest. "
            "What would you like to do?")
    return f"{lead} You can say: {says}. {tail}"
