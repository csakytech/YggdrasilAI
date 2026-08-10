"""ThorAI Settings — a simple window for how Jarvis behaves.

Everything here writes to the same user config the assistant reads live (~/.config/yggdrasil/
config.json), so changes take effect on the next thing you say — no restart. Built to grow: add
a new setting by adding one row to _build(). GTK3.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..core import config  # noqa: E402


class SettingsWindow(Gtk.Window):
    def __init__(self) -> None:
        name = config.get_name()
        super().__init__(title="ThorAI Settings")
        self.set_default_size(480, 420)
        self.set_border_width(16)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(outer)

        title = Gtk.Label()
        title.set_markup(f"<big><b>ThorAI Settings</b></big>\n<span foreground='#888'>How {name} behaves</span>")
        title.set_xalign(0.0)
        outer.pack_start(title, False, False, 0)

        grid = Gtk.Grid(row_spacing=16, column_spacing=14)
        outer.pack_start(grid, True, True, 0)
        self._row = 0

        # --- Reply style (the verbosity control) ---
        self._verbosity = Gtk.ComboBoxText()
        for vid, label in (("full", "Full — “Opening a browser and searching for robots”"),
                           ("simple", "Simple — “Searching.”"),
                           ("off", "Off — do it silently")):
            self._verbosity.append(vid, label)
        self._verbosity.set_active_id(config.get_verbosity())
        self._verbosity.connect("changed",
                                lambda c: config.set_verbosity(c.get_active_id() or "full"))
        self._add_row("Spoken confirmations",
                      f"What {name} says when DOING something you asked. Questions, answers, "
                      "and problems are always spoken in full.",
                      self._verbosity)

        # --- Full-duplex conversation ---
        self._duplex = Gtk.Switch()
        self._duplex.set_active(config.get_duplex())
        self._duplex.set_halign(Gtk.Align.START)
        self._duplex.connect("notify::active", lambda s, _p: config.set_duplex(s.get_active()))
        self._add_row("Interrupt me while I'm talking",
                      f"Let {name} keep listening while speaking, so you can talk over the "
                      "assistant. Takes effect the next time it starts.",
                      self._duplex)

        # --- Conversation log ---
        # Off by default: it records what you say to the assistant, so it is the user's call.
        # Worth having on when something goes wrong, because "it misheard me" and "it understood
        # and chose badly" look identical from the outside until you can see the transcript.
        self._convlog = Gtk.Switch()
        self._convlog.set_active(config.get_conversation_log())
        self._convlog.set_halign(Gtk.Align.START)
        self._convlog.connect("notify::active",
                              lambda s, _p: config.set_conversation_log(s.get_active()))
        self._add_row("Conversation log",
                      f"Keep the last few things you said and what {name} replied, so you can "
                      "check what he actually heard. Open it from the Conversation app. Stored "
                      "on this machine only.",
                      self._convlog)

        # --- Web search engine ---
        self._engine = Gtk.ComboBoxText()
        for eid, label in (("duckduckgo", "DuckDuckGo (private, no CAPTCHAs)"),
                           ("google", "Google (may show CAPTCHAs)"),
                           ("bing", "Bing")):
            self._engine.append(eid, label)
        self._engine.set_active_id(config.get_search_engine())
        self._engine.connect("changed",
                             lambda c: config.set_search_engine(c.get_active_id() or "duckduckgo"))
        self._add_row("Web search",
                      "Which search engine Jarvis opens for voice searches.",
                      self._engine)

        note = Gtk.Label()
        note.set_markup("<span foreground='#888' size='small'>Changes save instantly and apply "
                        "to what you say next.</span>")
        note.set_xalign(0.0)
        outer.pack_start(note, False, False, 0)

    def _add_row(self, title: str, subtitle: str, control: Gtk.Widget) -> None:
        label = Gtk.Label()
        label.set_markup(f"<b>{title}</b>\n<span foreground='#888' size='small'>{subtitle}</span>")
        label.set_xalign(0.0)
        label.set_line_wrap(True)
        label.set_max_width_chars(34)
        self.grid_attach(label, control)

    def grid_attach(self, label: Gtk.Widget, control: Gtk.Widget) -> None:
        grid = self.get_child().get_children()[1]
        control.set_halign(Gtk.Align.END)
        control.set_valign(Gtk.Align.CENTER)
        grid.attach(label, 0, self._row, 1, 1)
        grid.attach(control, 1, self._row, 1, 1)
        label.set_hexpand(True)
        self._row += 1


def main() -> None:
    win = SettingsWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    win.present()
    Gtk.main()


if __name__ == "__main__":
    main()
