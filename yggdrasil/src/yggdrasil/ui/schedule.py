"""Yggdrasil Scheduled Tasks — a native GTK4 window listing reminders + briefings.

Reads ~/.config/yggdrasil/schedule.json LIVE (auto-refreshes every few seconds), so you can watch
tasks appear as you add them by voice and verify exactly what was stored — the research query, the
reminder text, the recurrence, and the next run. Run on the desktop. Opened/closed by voice through
the Scheduler agent; the app id ``org.yggdrasil.Schedule`` is the WM_CLASS used to close it.
"""
from __future__ import annotations

import datetime as dt
import json

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core.scheduler import default_path  # noqa: E402

CSS = """
.ygg-title { font-size: 20px; font-weight: 800; }
.ygg-job   { font-size: 15px; font-weight: 700; }
.ygg-sub   { color: alpha(currentColor, 0.6); }
.ygg-tag   { color: alpha(currentColor, 0.55); font-weight: 700; }
.ygg-card  { padding: 12px; }
"""


def _load() -> list[dict]:
    try:
        return list(json.loads(default_path().read_text(encoding="utf-8")).get("jobs", []))
    except Exception:
        return []


def _fmt_time(t: str) -> str:
    try:
        hh, _, mm = (t or "").partition(":")
        return dt.time(int(hh), int(mm or 0)).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return t or "?"


def _fmt_when(job: dict) -> str:
    rec = job.get("recurrence", "once")
    tt = _fmt_time(job.get("time", ""))
    return {"weekdays": f"Every weekday at {tt}", "daily": f"Every day at {tt}", "hourly": "Every hour",
            "weekly": f"Every {(job.get('weekday') or '').title()} at {tt}", "once": "Once"}.get(rec, rec)


def _fmt_next(job: dict) -> str:
    nr = job.get("next_run")
    if not nr:
        return "—"
    try:
        d = dt.datetime.fromisoformat(nr)
    except Exception:
        return nr
    t = d.strftime("%I:%M %p").lstrip("0")
    today = dt.date.today()
    day = ("today" if d.date() == today else
           "tomorrow" if d.date() == today + dt.timedelta(days=1) else d.strftime("%a, %b %d"))
    return f"{day} at {t}"


class ScheduleWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Scheduled Tasks")
        self.set_default_size(680, 620)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for fn in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(outer, fn)(18)
        head = Gtk.Label(label="🗓️  Scheduled Tasks", xalign=0)
        head.add_css_class("ygg-title")
        outer.append(head)
        self.count = Gtk.Label(label="", xalign=0)
        self.count.add_css_class("ygg-sub")
        outer.append(self.count)
        self.jobs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.append(self.jobs_box)
        foot = Gtk.Label(label="Live view — updates automatically as you add or change tasks.", xalign=0)
        foot.add_css_class("ygg-sub")
        outer.append(foot)
        sc = Gtk.ScrolledWindow()
        sc.set_child(outer)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(sc)
        self._refresh()
        GLib.timeout_add_seconds(3, self._refresh)

    def _clear(self) -> None:
        child = self.jobs_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.jobs_box.remove(child)
            child = nxt

    def _refresh(self) -> bool:
        jobs = _load()
        self._clear()
        self.count.set_text(f"{len(jobs)} scheduled" if jobs else "")
        if not jobs:
            self.jobs_box.append(Gtk.Label(
                label="Nothing scheduled yet. Try saying: “remind me to stretch in two minutes”.",
                xalign=0, wrap=True))
            return True
        for j in jobs:
            frame = Gtk.Frame()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.add_css_class("ygg-card")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Label(label="● " + (j.get("label") or "task"), xalign=0)
            name.add_css_class("ygg-job")
            row.append(name)
            tag = Gtk.Label(label="[" + j.get("kind", "") + "]", xalign=0)
            tag.add_css_class("ygg-tag")
            row.append(tag)
            box.append(row)
            if j.get("kind") == "briefing":
                box.append(Gtk.Label(label='Looks up:  "' + (j.get("query") or "") + '"',
                                     xalign=0, wrap=True, selectable=True))
            else:
                box.append(Gtk.Label(label='Says:  "' + (j.get("message") or "") + '"',
                                     xalign=0, wrap=True, selectable=True))
            box.append(Gtk.Label(label=_fmt_when(j) + "    ·    next: " + _fmt_next(j), xalign=0))
            frame.set_child(box)
            self.jobs_box.append(frame)
        return True


class ScheduleApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.yggdrasil.Schedule")

    def do_activate(self) -> None:
        try:
            from gi.repository import Gdk
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass
        # single-instance: re-activation must re-present the same window, not stack a new one
        if getattr(self, "win", None) is None:
            self.win = ScheduleWindow(self)
        self.win.present()


def main() -> None:
    try:
        GLib.set_prgname("yggdrasil-schedule")
    except Exception:
        pass
    ScheduleApp().run(None)


if __name__ == "__main__":
    main()
