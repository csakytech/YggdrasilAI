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


def capture_png() -> bytes | None:
    """Grab the whole screen as PNG bytes, or None if no capture tool is available/working."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        out = tf.name
    try:
        for binary, template in _TOOLS:
            if not shutil.which(binary):
                continue
            argv = [a.replace("{out}", out) for a in template]
            try:
                subprocess.run(argv, timeout=15, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=True)
            except Exception:
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
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")) and any(
        shutil.which(b) for b, _ in _TOOLS)
