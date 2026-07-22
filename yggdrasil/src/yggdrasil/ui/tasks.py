"""Tasks window — what Jarvis is working on in the background, live.

Opens itself when a background job starts (a software install, a download) so the user is never
left wondering whether anything is happening. Shows each job: what it is, who's doing it, how
long it's been running, and a progress bar when the work reports a percentage. Polls the shared
jobs registry (core/jobs.py) twice a second. Read-only; GTK3.
"""
from __future__ import annotations

import sys
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..core import config, jobs  # noqa: E402


def _argv() -> list[str]:
    return sys.argv[1:]


def _elapsed(secs: int) -> str:
    m, s = divmod(max(0, secs), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


class TasksWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="Tasks")
        self.set_default_size(440, 340)
        self.set_border_width(14)
        name = config.get_name()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(box)
        head = Gtk.Label()
        head.set_markup(f"<big><b>What {name} is doing</b></big>")
        head.set_xalign(0.0)
        box.pack_start(head, False, False, 0)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self._list)
        box.pack_start(scroll, True, True, 0)

        self._empty = Gtk.Label()
        self._empty.set_markup("<span foreground='#888'>Nothing running right now.</span>")
        self._empty.set_xalign(0.0)
        box.pack_start(self._empty, False, False, 0)

        self._rows: dict[str, dict] = {}
        self._saw_work = False       # did any job ever run while this window was open?
        self._idle_since: float | None = None
        # Only the window Jarvis auto-opens on an install tidies itself away when done
        # (--autoclose); a window the user opened by hand stays until they close it.
        self._autoclose = "--autoclose" in _argv()
        GLib.timeout_add(500, self._refresh)

    def _refresh(self) -> bool:
        now = time.time()
        shown = jobs.recent(now, within=20.0)  # running + just-finished (fade out after 20s)
        ids = {j["id"] for j in shown}
        for jid in list(self._rows):
            if jid not in ids:
                self._list.remove(self._rows.pop(jid)["frame"])
        for j in shown:
            self._render_job(j, now)
        self._empty.set_visible(not shown)
        self._list.show_all()

        running = any(j.get("state") == "running" for j in shown)
        if running:
            self._saw_work = True
            self._idle_since = None
        elif self._saw_work and self._idle_since is None:
            self._idle_since = now
        # Once everything's finished, linger ~8s so the user reads "finished", then close.
        if (self._autoclose and self._idle_since is not None
                and now - self._idle_since > 8.0):
            self.destroy()
            return False
        return True

    def _render_job(self, j: dict, now: float) -> None:
        jid = j["id"]
        if jid not in self._rows:
            frame = Gtk.Frame()
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            inner.set_border_width(8)
            frame.add(inner)
            title = Gtk.Label(); title.set_xalign(0.0)
            sub = Gtk.Label(); sub.set_xalign(0.0)
            sub.modify_font(Pango.FontDescription("9"))
            bar = Gtk.ProgressBar()
            inner.pack_start(title, False, False, 0)
            inner.pack_start(bar, False, False, 0)
            inner.pack_start(sub, False, False, 0)
            self._list.pack_start(frame, False, False, 0)
            self._rows[jid] = {"frame": frame, "title": title, "sub": sub, "bar": bar}
        r = self._rows[jid]
        state = j.get("state")
        icon = "✓" if state == "done" else "✗" if state == "error" else "⏳"
        r["title"].set_markup(f"<b>{icon} {GLib.markup_escape_text(j.get('title', 'Task'))}</b>")
        pct = j.get("progress")
        if isinstance(pct, (int, float)):
            r["bar"].set_fraction(min(1.0, pct / 100.0))
            r["bar"].set_show_text(True)
            r["bar"].set_text(f"{int(pct)}%")
            r["bar"].set_visible(True)
        elif state == "running":
            r["bar"].pulse()
            r["bar"].set_show_text(False)
            r["bar"].set_visible(True)
        else:
            r["bar"].set_visible(False)
        elapsed = _elapsed(int((j.get("ended") or now) - j.get("started", now)))
        who = j.get("agent", "Jarvis")
        detail = j.get("detail", "")
        tail = {"done": "finished", "error": f"failed — {detail}"}.get(state, detail or "working…")
        r["sub"].set_markup(f"<span foreground='#888'>{who} · {elapsed} · "
                            f"{GLib.markup_escape_text(str(tail))[:70]}</span>")


def main() -> None:
    win = TasksWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    win.present()
    Gtk.main()


if __name__ == "__main__":
    main()
