"""Yggdrasil Chat — a window to type to Jarvis.

Same brain as the voice loop (orchestrator, agents, memory, planner) — just typed instead of
spoken. Launch it from the Dashboard, a desktop icon, or by voice ("open the chat window").
GTK3; the agent stack runs in a background asyncio loop so the window stays responsive, and
results are marshalled back to the UI thread via GLib.idle_add.
"""
from __future__ import annotations

import asyncio
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..app import build_orchestrator  # noqa: E402
from ..core.permissions import AuthChallenge, UserChannel  # noqa: E402


class _ChatChannel(UserChannel):
    """Bridges the permission system's challenge to the chat view."""

    def __init__(self, show) -> None:
        self._show = show

    async def present_challenge(self, challenge: AuthChallenge) -> None:
        self._show("Jarvis", f"🔒 {challenge.summary}\nTo approve, type:  Authorize {challenge.code}", "dim")


class ChatWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="Jarvis")
        self.set_default_size(560, 660)
        self.set_border_width(8)

        self._loop = asyncio.new_event_loop()
        self._orch = None
        self._name = "Jarvis"
        self._auth_future: asyncio.Future | None = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._view = Gtk.TextView()
        self._view.set_editable(False)
        self._view.set_cursor_visible(False)
        self._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._view.set_left_margin(8)
        self._view.set_right_margin(8)
        self._buf = self._view.get_buffer()
        self._tag_you = self._buf.create_tag("you", weight=Pango.Weight.BOLD)
        self._tag_jarvis = self._buf.create_tag("jarvis", weight=Pango.Weight.BOLD, foreground="#4a90d9")
        self._tag_dim = self._buf.create_tag("dim", style=Pango.Style.ITALIC, foreground="#888888")
        self._end_mark = self._buf.create_mark("end", self._buf.get_end_iter(), False)
        scroll.add(self._view)
        box.pack_start(scroll, True, True, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Connecting…")
        self._entry.set_sensitive(False)
        self._entry.connect("activate", self._on_send)
        self._send = Gtk.Button(label="Send")
        self._send.set_sensitive(False)
        self._send.connect("clicked", self._on_send)
        row.pack_start(self._entry, True, True, 0)
        row.pack_start(self._send, False, False, 0)
        box.pack_start(row, False, False, 0)

        self.connect("destroy", self._on_destroy)
        threading.Thread(target=self._run_loop, daemon=True).start()

    # --- background asyncio loop owning the agent stack -----------------------------------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            _bus, orch, _fa, _st, name = self._loop.run_until_complete(
                build_orchestrator(_ChatChannel(self._post), self._auth_resolver))
            self._orch, self._name = orch, name
            GLib.idle_add(self._on_ready)
            self._loop.run_forever()
        except Exception as e:  # noqa: BLE001
            GLib.idle_add(self._post_inline, "Jarvis", f"(couldn't start: {e!r})", "dim")

    def _on_ready(self) -> bool:
        self.set_title(self._name)
        self._show(self._name, f"Hi, I'm {self._name}. Type a request or a question.", "dim")
        self._entry.set_placeholder_text(f"Message {self._name}…")
        self._set_busy(False)
        return False

    async def _auth_resolver(self, challenge: AuthChallenge) -> str:
        GLib.idle_add(self._enable_for_auth)
        self._auth_future = self._loop.create_future()
        code = await self._auth_future
        self._auth_future = None
        return code.strip().split()[-1] if code.strip() else ""

    def _enable_for_auth(self) -> bool:
        self._entry.set_sensitive(True)
        self._send.set_sensitive(True)
        self._entry.set_placeholder_text("Enter the authorization code…")
        self._entry.grab_focus()
        return False

    # --- sending --------------------------------------------------------------------------------
    def _on_send(self, _w) -> None:
        text = self._entry.get_text().strip()
        if not text or self._orch is None:
            return
        self._entry.set_text("")
        if self._auth_future is not None and not self._auth_future.done():  # feeding an auth code
            self._show("You", text)
            self._set_busy(True)
            self._loop.call_soon_threadsafe(self._auth_future.set_result, text)
            return
        self._show("You", text)
        self._set_busy(True)
        fut = asyncio.run_coroutine_threadsafe(self._orch.handle(text), self._loop)
        fut.add_done_callback(self._on_reply)

    def _on_reply(self, fut) -> None:
        try:
            reply = fut.result()
        except Exception as e:  # noqa: BLE001
            reply = f"(error: {e!r})"
        GLib.idle_add(self._deliver, reply)

    def _deliver(self, reply: str) -> bool:
        self._show(self._name, reply)
        self._set_busy(False)
        return False

    def _set_busy(self, busy: bool) -> None:
        self._entry.set_sensitive(not busy)
        self._send.set_sensitive(not busy)
        if not busy:
            self._entry.set_placeholder_text(f"Message {self._name}…")
            self._entry.grab_focus()

    # --- text view (main thread only) -----------------------------------------------------------
    def _show(self, who: str, text: str, tag: str | None = None) -> bool:
        buf = self._buf
        if buf.get_char_count():
            buf.insert(buf.get_end_iter(), "\n")
        who_tag = self._tag_you if who == "You" else self._tag_jarvis
        buf.insert_with_tags(buf.get_end_iter(), f"{who}: ", who_tag)
        if tag == "dim":
            buf.insert_with_tags(buf.get_end_iter(), text, self._tag_dim)
        else:
            buf.insert(buf.get_end_iter(), text)
        buf.move_mark(self._end_mark, buf.get_end_iter())
        self._view.scroll_mark_onscreen(self._end_mark)
        return False

    def _post(self, who: str, text: str, tag: str | None = None) -> None:
        GLib.idle_add(self._show, who, text, tag)

    def _post_inline(self, who: str, text: str, tag: str | None = None) -> bool:
        return self._show(who, text, tag)

    def _on_destroy(self, _w) -> None:
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        Gtk.main_quit()


def main() -> None:
    win = ChatWindow()
    win.show_all()
    win.present()
    Gtk.main()


if __name__ == "__main__":
    main()
