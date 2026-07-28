"""Conversation window — what Jarvis HEARD, and what he said back.

The last several exchanges, oldest at the top, scrollable so you can look back. Polls the
rolling log (core/conversation.py) once a second; read-only; GTK3, matching the Tasks window.

The point is separating two failures that look identical from the outside: the assistant
MISHEARD you, or it heard you fine and chose badly. "Cancel" logged as "Council" answers the
question instantly, where guessing from the spoken reply alone can burn an hour. Useful to
anyone, not just while developing — a user who can see "I heard: open my dogs" understands what
went wrong and simply says it again.

Off by default (Settings > Conversation log): it records what is said to the assistant, so it
is the user's choice. When it's off this window says so and points at the switch, rather than
sitting there looking broken.
"""
from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..core import config, conversation  # noqa: E402

POLL_MS = 1000
SHOW = 10  # newest N rendered; the store keeps more, so scrollback goes further back


def _ago(ts: float) -> str:
    d = max(0, int(time.time() - (ts or 0)))
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    return f"{d // 3600}h ago"


class ConversationWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="Conversation")
        self.set_default_size(560, 460)
        self.set_border_width(14)
        self.name = config.get_name()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(box)

        self._head = Gtk.Label()
        self._head.set_markup(f"<big><b>What {self.name} heard</b></big>")
        self._head.set_xalign(0.0)
        box.pack_start(self._head, False, False, 0)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.add(self._list)
        box.pack_start(self._scroll, True, True, 0)

        self._note = Gtk.Label()
        self._note.set_xalign(0.0)
        self._note.set_line_wrap(True)
        box.pack_start(self._note, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_halign(Gtk.Align.END)
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", self._clear)
        btns.pack_start(clear, False, False, 0)
        box.pack_start(btns, False, False, 0)

        self._sig = None  # last rendered signature, so we only rebuild on change
        self._tick()
        GLib.timeout_add(POLL_MS, self._tick)

    def _clear(self, _btn) -> None:
        conversation.clear()
        self._sig = None
        self._tick()

    @staticmethod
    def _row(item: dict, name: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        when = Gtk.Label()
        when.set_markup(f"<span foreground='#888' size='small'>{_ago(item.get('ts', 0))}"
                        f"{'' if item.get('addressed', True) else '  ·  follow-up'}</span>")
        when.set_xalign(0.0)
        row.pack_start(when, False, False, 0)

        heard = Gtk.Label()
        heard.set_markup(f"<b>You:</b> {GLib.markup_escape_text(item.get('heard', ''))}")
        heard.set_xalign(0.0)
        heard.set_line_wrap(True)
        heard.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        row.pack_start(heard, False, False, 0)

        reply = Gtk.Label()
        reply.set_markup(f"<span foreground='#5aa0ff'><b>{GLib.markup_escape_text(name)}:</b></span> "
                         f"{GLib.markup_escape_text(item.get('reply', ''))}")
        reply.set_xalign(0.0)
        reply.set_line_wrap(True)
        reply.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        row.pack_start(reply, False, False, 0)
        return row

    def _tick(self) -> bool:
        # Re-read the name every poll: renaming ("call yourself Amy") must show up in an ALREADY
        # OPEN window. Caching it at construction left the new name invisible until you thought
        # to close and reopen — small, but it makes the rename feel like it didn't take.
        name = config.get_name()
        if name != self.name:
            self.name = name
            self._head.set_markup(f"<big><b>What {name} heard</b></big>")
            self._sig = None  # force a rebuild so existing rows re-label too

        on = config.get_conversation_log()
        items = conversation.read(limit=SHOW) if on else []
        sig = (on, name, tuple((i.get("ts"), i.get("heard"), i.get("reply")) for i in items))
        if sig == self._sig:
            return True
        self._sig = sig

        for child in self._list.get_children():
            self._list.remove(child)

        if not on:
            self._note.set_markup(
                "<span foreground='#888'>The conversation log is off. Turn it on in "
                "<b>ThorAI Settings › Conversation log</b> to see what was heard here.</span>")
        elif not items:
            self._note.set_markup(f"<span foreground='#888'>Nothing yet — say “{self.name}” "
                                  "and it'll appear here.</span>")
        else:
            self._note.set_markup("<span foreground='#888'>Newest at the bottom. "
                                  "Scroll up for earlier.</span>")

        for it in items:
            self._list.pack_start(self._row(it, self.name), False, False, 0)
        self._list.show_all()

        # Newest is at the bottom, so follow it — the live end is what you're watching.
        GLib.idle_add(self._scroll_to_end)
        return True

    def _scroll_to_end(self) -> bool:
        adj = self._scroll.get_vadjustment()
        if adj is not None:
            adj.set_value(max(0.0, adj.get_upper() - adj.get_page_size()))
        return False


def main() -> None:
    win = ConversationWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
