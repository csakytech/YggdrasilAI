"""Yggdrasil Mission — the live "Development Plan" window (GTK4).

The visible half of Development Mode: while Jarvis interviews, plans, and builds, this
window shows the plan assembling in real time — ✓ decisions, the ? question on the table,
the proposed plan, the Agent roster, and a running log. Polls mission.json every 2s
(single writer = the Dev agent), so it survives assistant restarts and never blocks it.
"""
from __future__ import annotations

import datetime as dt

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core import mission  # noqa: E402

CSS = """
.ygg-title { font-size: 20px; font-weight: 800; }
.ygg-stage { color: #2ec27e; font-weight: 700; }
.ygg-sub   { color: alpha(currentColor, 0.6); }
.ygg-q     { color: #e5a50a; font-weight: 700; }
.ygg-head  { font-size: 15px; font-weight: 700; margin-top: 6px; }
.ygg-card  { padding: 12px; }
.ygg-log   { font-family: monospace; font-size: 12px; color: alpha(currentColor, 0.65); }
"""

_STAGE_LABEL = {"interview": "Interview — building the full picture",
                "proposal": "Proposal — awaiting your approval",
                "setup": "Setup — workspace ready",
                "build": "Build — Agents at work",
                "done": "Finished"}


class MissionWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Development Mission")
        self.set_default_size(700, 720)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for fn in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(self.box, fn)(18)
        sc = Gtk.ScrolledWindow()
        sc.set_child(self.box)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(sc)
        self._refresh()
        GLib.timeout_add_seconds(2, self._refresh)

    def _clear(self) -> None:
        child = self.box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.box.remove(child)
            child = nxt

    def _label(self, text: str, css: str = "", wrap: bool = True) -> Gtk.Label:
        lbl = Gtk.Label(label=text, xalign=0, wrap=wrap)
        if css:
            lbl.add_css_class(css)
        self.box.append(lbl)
        return lbl

    def _refresh(self) -> bool:
        m = mission.load()
        self._clear()
        if not m:
            self._label("No mission yet. Say: “Jarvis, I want to build …” to start one.", "ygg-sub")
            return True
        title = m.get("name") or m.get("summary") or "Development Mission"
        self._label(f"🚀  {title}", "ygg-title")
        stage = m.get("stage", "")
        self._label(_STAGE_LABEL.get(stage, stage) + ("" if m.get("active") else "  ·  (inactive)"),
                    "ygg-stage")
        if m.get("summary"):
            self._label(f"Goal: {m['summary']}", "ygg-sub")

        if m.get("decisions"):
            self._label("Decisions", "ygg-head")
            for d in m["decisions"]:
                self._label(f"✓  {d['q']}  —  {d['a']}")
        if m.get("pending"):
            self._label(f"?  {m['pending']}", "ygg-q")
            self._label("   (answer by voice — “you choose” works; “just decide the rest” skips ahead)",
                        "ygg-sub")

        plan = m.get("plan") or {}
        if plan:
            self._label("Plan", "ygg-head")
            self._label(f"•  Language: {plan.get('language', '')} — {plan.get('why_language', '')}")
            self._label(f"•  Editor: {plan.get('editor', '')}")
            if plan.get("folders"):
                self._label("•  Folders: " + ", ".join(plan["folders"]))
            if plan.get("test_stages"):
                self._label("Test stages", "ygg-head")
                for i, t in enumerate(plan["test_stages"], 1):
                    self._label(f"{i}.  {t}")
        if m.get("agents"):
            self._label("Agents", "ygg-head")
            for a in m["agents"]:
                self._label(f"🤖  {a['name']} — {a['specialty']}   [{a.get('status', 'planned')}]")
        if m.get("project_dir"):
            self._label(f"Workspace: {m['project_dir']}", "ygg-sub")

        if m.get("log"):
            self._label("Log", "ygg-head")
            for entry in m["log"][-8:]:
                t = dt.datetime.fromtimestamp(entry.get("ts", 0)).strftime("%H:%M")
                self._label(f"{t}  {entry.get('text', '')}", "ygg-log")
        return True


class MissionApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.yggdrasil.Mission")

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
            self.win = MissionWindow(self)
        self.win.present()


def main() -> None:
    try:
        GLib.set_prgname("yggdrasil-mission")
    except Exception:
        pass
    MissionApp().run(None)


if __name__ == "__main__":
    main()
