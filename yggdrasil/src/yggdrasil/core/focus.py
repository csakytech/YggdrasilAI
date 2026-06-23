"""Active-window context (X11) — what the user is currently focused on.

Lets the planner route commands to the focused app (see agents/focus_agent.py): after you open
a terminal, "list files" becomes `ls` typed into it. Returns ('', '') when there's no X11 active
window (headless, or a Wayland session), so the rest of the system just behaves as before. Needs
`xdotool` and an X11 session (GDM WaylandEnable=false).
"""
from __future__ import annotations

import subprocess


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


def active_window() -> tuple[str, str]:
    """Return (window_class, kind) for the focused window, or ('', '') if none.

    kind is one of: terminal | browser | editor | application.
    """
    cls = _run(["xdotool", "getactivewindow", "getwindowclassname"])
    c = cls.lower()
    if not c:
        return ("", "")
    if any(t in c for t in ("terminal", "konsole", "xterm", "kitty", "alacritty", "tilix")):
        kind = "terminal"
    elif any(b in c for b in ("firefox", "chrom", "navigator", "epiphany", "brave")):
        kind = "browser"
    elif any(e in c for e in ("gedit", "code", "gnome-text", "writer", "soffice", "libreoffice")):
        kind = "editor"
    else:
        kind = "application"
    return (cls, kind)
