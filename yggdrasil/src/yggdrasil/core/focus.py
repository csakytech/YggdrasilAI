"""Active-window context (X11) — what the user is currently working with.

Lets the planner route commands to the right window (see agents/focus_agent.py): after you open
a terminal, "list files" becomes `ls` typed into it.

GNOME/mutter won't give input focus to a window opened by a background process (us), and it
ignores programmatic activation (`wmctrl -a`, `xdotool windowactivate`). So we can't rely on the
"active window." Instead the Apps agent records the window it just launched here (`set_target`),
and the Focus agent grabs focus on that id itself via `xdotool windowfocus` (XSetInputFocus,
which mutter does NOT block) right before it types. `working_window()` prefers the real X-focused
window (when the user has actually clicked into one) and falls back to that tracked window.

Returns empties when there's no X11 (headless / Wayland), so the rest of the system behaves as
before. Needs an X11 session (GDM WaylandEnable=false), `xdotool`, `xprop` (x11-utils), `wmctrl`.
"""
from __future__ import annotations

import re
import subprocess
import time

# The window the Apps agent most recently launched (decimal X id). GNOME won't focus it for us,
# so we remember it and focus it ourselves when typing.
_TARGET = {"id": "", "kind": "", "name": ""}


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


def _kind_for(blob: str) -> str:
    if any(t in blob for t in ("terminal", "konsole", "xterm", "kitty", "alacritty", "tilix", "wezterm")):
        return "terminal"
    if any(b in blob for b in ("firefox", "chrom", "navigator", "epiphany", "brave")):
        return "browser"
    if any(e in blob for e in ("gedit", "vscode", "code", "gnome-text", "writer", "soffice", "libreoffice")):
        return "editor"
    return "application"


def _classify(win_id: str) -> tuple[str, str]:
    """(name, kind) for a decimal window id, or ('', '') if it has no class/title."""
    if not win_id:
        return ("", "")
    wm_class = _run(["xprop", "-id", win_id, "WM_CLASS"])  # WM_CLASS(STRING) = "inst", "Class"
    title = _run(["xdotool", "getwindowname", win_id])
    blob = f"{wm_class} {title}".lower()
    if not blob.strip():
        return ("", "")
    classes = re.findall(r'"([^"]+)"', wm_class)
    name = (classes[-1] if classes else "").strip() or title
    return (name, _kind_for(blob))


def _windows_dec() -> set[str]:
    """Set of all managed window ids, as decimal strings (wmctrl reports hex)."""
    ids: set[str] = set()
    for ln in _run(["wmctrl", "-l"]).splitlines():
        if ln.strip():
            try:
                ids.add(str(int(ln.split()[0], 16)))
            except ValueError:
                pass
    return ids


def set_target(win_id_dec: str) -> None:
    """Record the window the Apps agent just launched as the working target."""
    name, kind = _classify(win_id_dec)
    if kind:
        _TARGET.update(id=win_id_dec, name=name, kind=kind)


def clear_target() -> None:
    _TARGET.update(id="", kind="", name="")


def window_ids() -> set[str]:
    """All managed window ids (decimal strings) — snapshot before launching to detect the new one."""
    return _windows_dec()


def track_new_window(before: set[str], timeout: float = 2.5) -> None:
    """Record the window that appears (vs ``before``) as the working target, so a follow-up
    command routes to it. Does NOT focus it — the Focus agent grabs focus itself at type time.
    Poll-based; safe to run in a background thread for slow apps (e.g. LibreOffice)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        new = _windows_dec() - before
        if new:
            set_target(sorted(new)[-1])
            return
        time.sleep(0.15)


def _live_target() -> dict | None:
    if _TARGET["id"] and _TARGET["id"] in _windows_dec():
        return dict(_TARGET)
    return None


def working_window() -> tuple[str, str, str]:
    """(win_id, name, kind) for the window the user is working with: the real X-focused window
    if there is one (they clicked into it), else the last window we launched. ('', '', '') when
    there's nothing (e.g. headless / Wayland)."""
    rid = _run(["xdotool", "getactivewindow"])
    if rid:
        name, kind = _classify(rid)
        if kind:
            return (rid, name, kind)
    t = _live_target()
    if t:
        return (t["id"], t["name"], t["kind"])
    return ("", "", "")


def active_window() -> tuple[str, str]:
    """(name, kind) of the working window — used by the planner to route commands."""
    _id, name, kind = working_window()
    return (name, kind)
