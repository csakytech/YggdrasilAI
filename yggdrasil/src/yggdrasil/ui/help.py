"""Yggdrasil Smart Help — a small native GTK4 card that says WHERE you are and what you can say.

Opened by voice ("Jarvis, help") through the Help agent, which writes the current context to
~/.local/state/yggdrasil/help.json first. This window renders it LIVE (re-reads every couple of
seconds), so saying "help" again after switching programs updates the card in place. Compact and
kept above other windows so it works as a glanceable reference while you speak your next command.
App id ``org.yggdrasil.Help`` (the WM_CLASS used to close it).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

CSS = """
.ygg-title { font-size: 19px; font-weight: 800; }
.ygg-where { font-size: 14px; font-weight: 700; color: alpha(currentColor, 0.75); }
.ygg-vital { font-size: 13px; color: alpha(currentColor, 0.65); }
.ygg-say   { font-size: 15px; font-weight: 800; }
.ygg-does  { font-size: 13px; color: alpha(currentColor, 0.6); }
.ygg-foot  { font-size: 12px; color: alpha(currentColor, 0.5); }
.ygg-row   { padding: 6px 8px; }
"""


def _state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "help.json"


def _load() -> dict:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


class HelpWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="ThorOS Help")
        self.set_default_size(470, 600)
        self._sig = None  # last-rendered fingerprint, so we only rebuild on change

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for fn in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(outer, fn)(16)

        self.head = Gtk.Label(label="ThorOS Help", xalign=0)
        self.head.add_css_class("ygg-title")
        outer.append(self.head)
        self.where = Gtk.Label(label="", xalign=0, wrap=True)
        self.where.add_css_class("ygg-where")
        outer.append(self.where)

        self.vital_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.append(self.vital_box)

        sep = Gtk.Label(label="You can say — or say the number to run it:", xalign=0)
        sep.add_css_class("ygg-where")
        sep.set_margin_top(6)
        outer.append(sep)

        self.cmd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.append(self.cmd_box)

        foot = Gtk.Label(
            label="Say the command, or “do number 3”. Say “hide help” to close this.",
            xalign=0, wrap=True)
        foot.add_css_class("ygg-foot")
        foot.set_margin_top(8)
        outer.append(foot)

        sc = Gtk.ScrolledWindow()
        sc.set_child(outer)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(sc)

        self._refresh()
        GLib.timeout_add_seconds(2, self._refresh)

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _refresh(self) -> bool:
        d = _load()
        sig = json.dumps(d, sort_keys=True)
        if sig == self._sig:
            return True
        self._sig = sig

        icon = d.get("icon", "💡")
        title = d.get("title", "Help")
        self.head.set_text(f"{icon}  {title}")
        self.where.set_text("Here's what you can do, right where you are.")

        self._clear(self.vital_box)
        for v in [v for v in d.get("vital", []) if v]:
            lbl = Gtk.Label(label=v, xalign=0, wrap=True, selectable=True)
            lbl.add_css_class("ygg-vital")
            self.vital_box.append(lbl)

        self._clear(self.cmd_box)
        cmds = d.get("commands", [])
        if not cmds:
            self.cmd_box.append(Gtk.Label(label="Say “help” any time.", xalign=0))
            return True
        for i, cmd in enumerate(cmds, 1):
            if isinstance(cmd, dict):
                say, does = cmd.get("say", ""), cmd.get("does", "")
            else:  # tolerate the old (say, does) tuple form
                try:
                    say, does = cmd[0], cmd[1]
                except Exception:
                    continue
            frame = Gtk.Frame()
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            row.add_css_class("ygg-row")
            s = Gtk.Label(label=f"{i}.  🗣  {say}", xalign=0, wrap=True, selectable=True)
            s.add_css_class("ygg-say")
            row.append(s)
            d2 = Gtk.Label(label=does, xalign=0, wrap=True)
            d2.add_css_class("ygg-does")
            row.append(d2)
            frame.set_child(row)
            self.cmd_box.append(frame)
        return True


class HelpApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.yggdrasil.Help")

    def do_activate(self) -> None:
        try:
            from gi.repository import Gdk
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass
        if getattr(self, "win", None) is None:
            self.win = HelpWindow(self)
        self.win.present()
        # Best-effort keep-above on X11 so the card stays glanceable while you speak.
        try:
            import subprocess
            subprocess.Popen(["wmctrl", "-r", "ThorOS Help", "-b", "add,above"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def main() -> None:
    try:
        GLib.set_prgname("yggdrasil-help")
    except Exception:
        pass
    HelpApp().run(None)


if __name__ == "__main__":
    main()
