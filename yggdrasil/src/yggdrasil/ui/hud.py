"""Yggdrasil HUD — a bottom-of-screen status strip (GTK3, X11).

Reads the activity feed (core/activity.py) and shows what the assistant is doing, fading out
when idle and back in on the next action. Needs an X11 session — Wayland forbids apps from
self-positioning + staying always-on-top. Run via `yggdrasil-hud` (uses the system python3 for
GTK). GTK3 (not 4) because GTK4 removed window positioning.

It also shows a PERSISTENT mode pill whenever a special mode is running (currently Development
Mode). Modes capture what you say — a Development mission files every follow-up as project
description — so a user who has forgotten which mode they're in gets baffling answers with no
clue why. A live case: a question about the IP address was filed as project description, and
closing the Mission window changed nothing because the mission lives on disk. The pill states
the mode AND the way out, and unlike the activity text it never fades: forgetting is the whole
problem it exists to solve.
"""
from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")  # both 3.0 and 4.0 typelibs exist — pin or the import is ambiguous
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..core import mission  # noqa: E402
from ..core.activity import Activity  # noqa: E402

CSS = b"""
#hud-box { background-color: rgba(10,10,26,0.88); border-radius: 16px;
           padding: 10px 24px; border: 1px solid rgba(90,160,255,0.40); }
#hud-label { color: #cfe8ff; font-size: 16px; font-weight: 700; }
/* Amber, not the assistant's blue: a mode is a state you are IN, not something being done. */
#hud-mode { color: #ffcf7a; font-size: 15px; font-weight: 800; }
#hud-sep  { color: rgba(207,232,255,0.35); font-size: 15px; }
"""

IDLE_AFTER = 4.0   # seconds with no new activity before it fades out
FADE = 0.10        # opacity step per tick
MODE_POLL_TICKS = 8  # ~1s: the mission is a file on disk, no need to stat it every frame


class HUD(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.activity = Activity()
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        try:
            self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        except Exception:
            pass

        # transparency
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_name("hud-box")
        self.mode_label = Gtk.Label(label="")
        self.mode_label.set_name("hud-mode")
        self.mode_sep = Gtk.Label(label="•")
        self.mode_sep.set_name("hud-sep")
        self.label = Gtk.Label(label="")
        self.label.set_name("hud-label")
        box.pack_start(self.mode_label, False, False, 0)
        box.pack_start(self.mode_sep, False, False, 0)
        box.pack_start(self.label, False, False, 0)
        self.add(box)

        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        except Exception:
            pass

        self._opacity = 0.0
        self.set_opacity(0.0)
        self._last = None
        self._mode = ""
        self._mode_ticks = 0
        self.connect("realize", lambda *_: self._reposition())
        GLib.timeout_add(120, self._tick)

    @staticmethod
    def _current_mode() -> str:
        """The special mode the assistant is in, or "" for none. Never raises — a HUD that
        crashes on a malformed mission file would take the status strip down with it."""
        try:
            if mission.active():
                return "◆ Development Mode — say “cancel development” to leave"
        except Exception:
            pass
        return ""

    def _reposition(self) -> None:
        try:
            screen = self.get_screen()
            geo = screen.get_monitor_geometry(screen.get_primary_monitor())
            w, h = self.get_size()
            x = geo.x + (geo.width - w) // 2
            y = geo.y + geo.height - h - 64
            self.move(x, max(0, y))
        except Exception:
            pass

    def _tick(self) -> bool:
        text, ts = self.activity.read()
        age = (time.time() - ts) if ts else 1e9
        active = bool(text.strip()) and age < IDLE_AFTER

        self._mode_ticks -= 1
        if self._mode_ticks <= 0:
            self._mode_ticks = MODE_POLL_TICKS
            mode = self._current_mode()
            if mode != self._mode:
                self._mode = mode
                self.mode_label.set_text(mode)
                self.mode_label.set_visible(bool(mode))
                # The separator only earns its place when BOTH halves are showing.
                self.mode_sep.set_visible(bool(mode and text.strip()))
                self.resize(10, 10)
                GLib.idle_add(self._reposition)

        if text != self._last:
            self._last = text
            self.label.set_text(text or "")
            self.mode_sep.set_visible(bool(self._mode and text.strip()))
            if text.strip() or self._mode:
                self.resize(10, 10)  # shrink to content, then re-center
                GLib.idle_add(self._reposition)

        # A MODE holds the strip open. The activity text still comes and goes underneath it, but
        # the pill must not fade — a mode you can't see is exactly how the user ends up talking
        # into an interview that files their questions as project notes.
        target = 1.0 if (active or self._mode) else 0.0
        if self._opacity < target:
            self._opacity = min(target, self._opacity + FADE)
        elif self._opacity > target:
            self._opacity = max(target, self._opacity - FADE)
        self.set_opacity(self._opacity)
        if self._opacity > 0.001 and not self.get_visible():
            self.show_all()
        # show_all() force-shows EVERY child, so re-assert which halves belong on screen or an
        # empty mode pill and an orphaned separator appear the first time the strip opens.
        self.mode_label.set_visible(bool(self._mode))
        self.mode_sep.set_visible(bool(self._mode and (self._last or "").strip()))
        return True


def main() -> None:
    hud = HUD()
    hud.show_all()
    hud.set_opacity(0.0)
    Gtk.main()


if __name__ == "__main__":
    main()
