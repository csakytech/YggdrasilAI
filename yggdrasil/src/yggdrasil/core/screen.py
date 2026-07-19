"""Screen capture — a PNG of the current display, for the Vision agent.

Tries the tools most likely to be present on a ThorOS desktop, in order, across both X11 and
Wayland. Returns raw PNG bytes (or None if nothing worked). Kept tiny and dependency-free:
capture is a system tool, not a Python imaging stack.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

# (binary, argv-template) — {out} is replaced with the target file. First that exists + works
# wins. gnome-screenshot and spectacle cover the GNOME/KDE desktops; grim is Wayland; scrot,
# maim, and ImageMagick's import cover X11; xdg-desktop-portal's grim path is the Wayland
# fallback most compositors honor.
_TOOLS = [
    ("gnome-screenshot", ["gnome-screenshot", "-f", "{out}"]),
    ("grim", ["grim", "{out}"]),
    ("spectacle", ["spectacle", "-b", "-n", "-o", "{out}"]),
    ("scrot", ["scrot", "-o", "{out}"]),
    ("maim", ["maim", "{out}"]),
    ("import", ["import", "-window", "root", "{out}"]),
]


def _gnome_shell_capture(out: str) -> bool:
    """GNOME's built-in screenshot over D-Bus — no extra package needed (the standalone
    gnome-screenshot binary is often NOT installed, but the Shell's method always is on a
    GNOME desktop, and it works under Wayland where X11 tools can't grab the screen)."""
    if not shutil.which("gdbus"):
        return False
    try:
        r = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Shell.Screenshot",
             "--object-path", "/org/gnome/Shell/Screenshot",
             "--method", "org.gnome.Shell.Screenshot.Screenshot", "false", "false", out],
            capture_output=True, text=True, timeout=15)
        # returns "(true, '/path')" on success
        return r.returncode == 0 and "true" in r.stdout.lower()
    except Exception:
        return False


def capture_png() -> bytes | None:
    """Grab the whole screen as PNG bytes, or None if no capture tool is available/working."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        out = tf.name
    try:
        # GNOME Shell D-Bus first (present on every ThorOS desktop, Wayland-safe), then tools.
        attempts = [lambda: _gnome_shell_capture(out)]
        for binary, template in _TOOLS:
            if shutil.which(binary):
                argv = [a.replace("{out}", out) for a in template]
                attempts.append(lambda argv=argv: subprocess.run(
                    argv, timeout=15, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL).returncode == 0)
        for attempt in attempts:
            try:
                ok = attempt()
            except Exception:
                ok = False
            if not ok:
                continue
            try:
                data = open(out, "rb").read()
            except OSError:
                continue
            if data and len(data) > 100:  # a real image, not an empty/failed file
                return data
        return None
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def capture_b64() -> str | None:
    """Screen as a base64 PNG string (no data: prefix) — what Ollama's images array wants."""
    import base64

    data = capture_png()
    return base64.b64encode(data).decode() if data else None


def available() -> bool:
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False
    return bool(shutil.which("gdbus")) or any(shutil.which(b) for b, _ in _TOOLS)
