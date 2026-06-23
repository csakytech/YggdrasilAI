"""Active-window context (X11) — what the user is currently focused on.

Lets the planner route commands to the focused app (see agents/focus_agent.py): after you open
a terminal, "list files" becomes `ls` typed into it. Returns ('', '') when there's no X11 active
window (headless, or a Wayland session), so the rest of the system just behaves as before.

Needs an X11 session (GDM WaylandEnable=false), `xdotool`, and `xprop` (x11-utils). We read the
window's WM_CLASS via xprop rather than its title, because a terminal's title is often just the
shell path (e.g. "user@host: ~/dir") with no hint of what kind of app it is.
"""
from __future__ import annotations

import re
import subprocess


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


def active_window() -> tuple[str, str]:
    """Return (name, kind) for the focused window, or ('', '') if none.

    kind is one of: terminal | browser | editor | application.
    """
    wid = _run(["xdotool", "getactivewindow"])
    if not wid:
        return ("", "")
    wm_class = _run(["xprop", "-id", wid, "WM_CLASS"])  # WM_CLASS(STRING) = "inst", "Class"
    title = _run(["xdotool", "getwindowname", wid])
    blob = f"{wm_class} {title}".lower()
    if not blob.strip():
        return ("", "")
    if any(t in blob for t in ("terminal", "konsole", "xterm", "kitty", "alacritty", "tilix", "wezterm")):
        kind = "terminal"
    elif any(b in blob for b in ("firefox", "chrom", "navigator", "epiphany", "brave")):
        kind = "browser"
    elif any(e in blob for e in ("gedit", "vscode", "code", "gnome-text", "writer", "soffice", "libreoffice")):
        kind = "editor"
    else:
        kind = "application"
    classes = re.findall(r'"([^"]+)"', wm_class)
    name = (classes[-1] if classes else "").strip() or title
    return (name, kind)
