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
# wins. SILENT grabbers come first: the assistant should look at the screen invisibly — no
# shutter sound, no white flash — so it feels seamless. scrot/maim/ImageMagick-import are
# silent on X11; grim is silent on Wayland. gnome-screenshot (flash + camera sound) and the
# GNOME Shell D-Bus path are LAST resorts, only if nothing quiet is installed.
_TOOLS = [
    ("scrot", ["scrot", "-o", "{out}"]),
    ("maim", ["maim", "-u", "{out}"]),
    ("import", ["import", "-silent", "-window", "root", "{out}"]),
    ("grim", ["grim", "{out}"]),
    ("spectacle", ["spectacle", "-b", "-n", "-o", "{out}"]),
    ("gnome-screenshot", ["gnome-screenshot", "-f", "{out}"]),  # flashes — last resort
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
        # Silent tools first (see _TOOLS); the flashy GNOME Shell D-Bus path is the very last
        # resort — and it's locked in modern GNOME anyway, so it rarely fires.
        attempts = []
        for binary, template in _TOOLS:
            if shutil.which(binary):
                argv = [a.replace("{out}", out) for a in template]
                attempts.append(lambda argv=argv: subprocess.run(
                    argv, timeout=15, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL).returncode == 0)
        attempts.append(lambda: _gnome_shell_capture(out))
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


# --- pointer control (X11 via xdotool; ThorOS runs an X11 session by design) -------------------

def geometry() -> tuple[int, int] | None:
    """(width, height) of the screen in pixels, for scaling the vision model's coordinates."""
    try:
        out = subprocess.run(["xdotool", "getdisplaygeometry"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        if len(out) == 2:
            return int(out[0]), int(out[1])
    except Exception:
        pass
    return None


def click_at(x: int, y: int, button: int = 1) -> bool:
    """Move the pointer to (x, y) and click. Returns False if xdotool isn't available."""
    if not shutil.which("xdotool"):
        return False
    try:
        subprocess.run(["xdotool", "mousemove", str(int(x)), str(int(y))],
                       timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["xdotool", "click", str(int(button))],
                       timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def scroll(direction: str = "down", amount: int = 5) -> bool:
    """Scroll the surface under the pointer. X11 wheel buttons: 4 = up, 5 = down."""
    if not shutil.which("xdotool"):
        return False
    button = "4" if direction == "up" else "5"
    try:
        for _ in range(max(1, min(amount, 20))):
            subprocess.run(["xdotool", "click", button],
                           timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False
