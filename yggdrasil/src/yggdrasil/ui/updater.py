"""ThorOS Updates — a GTK4 window shown when a new version is available.

The user chooses Update Now or Later — never forced. Updating is in place and non-destructive (your
settings, schedules, installed agents, and files are kept). Launched by ``yggdrasil-update-check`` at
login + daily, and by the voice command "check for updates".
"""
from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core import updater  # noqa: E402

CSS = """
.ygg-title { font-size: 22px; font-weight: 800; }
.ygg-sub   { color: alpha(currentColor, 0.6); }
.ygg-item  { font-size: 14px; }
"""


class UpdateWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="ThorOS Updates")
        self.set_default_size(560, 520)
        self.rel = updater.update_available()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for fn in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(outer, fn)(22)

        if not self.rel:  # launched with nothing to do (e.g. via the voice "check")
            t = Gtk.Label(label="✅  You're up to date", xalign=0)
            t.add_css_class("ygg-title")
            outer.append(t)
            outer.append(self._sub(f"ThorOS {updater.installed_version()} — the latest version."))
            outer.append(self._close_btn("Close"))
            self.set_child(outer)
            return

        ver = self.rel.get("version", "?")
        head = Gtk.Label(label=f"🚀  ThorOS {ver} is available", xalign=0)
        head.add_css_class("ygg-title")
        outer.append(head)
        outer.append(self._sub(f"You're on {updater.installed_version()}. Update in place — your "
                               f"settings, schedules, agents, and files are all kept."))

        notes = self.rel.get("changelog") or []
        if notes:
            whats = Gtk.Label(label="What's new", xalign=0)
            whats.add_css_class("ygg-title")
            outer.append(whats)
            for line in (notes if isinstance(notes, list) else [notes]):
                row = Gtk.Label(label=f"•  {line}", xalign=0, wrap=True)
                row.add_css_class("ygg-item")
                outer.append(row)

        self.status = Gtk.Label(label="", xalign=0)
        self.status.add_css_class("ygg-sub")
        outer.append(self.status)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.update_btn = Gtk.Button(label="Update Now")
        self.update_btn.add_css_class("suggested-action")
        self.update_btn.connect("clicked", self._apply)
        self.later_btn = Gtk.Button(label="Later")
        self.later_btn.connect("clicked", lambda *_: self.close())
        btns.append(self.update_btn)
        btns.append(self.later_btn)
        outer.append(btns)

        sc = Gtk.ScrolledWindow()
        sc.set_child(outer)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(sc)

    def _sub(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text, xalign=0, wrap=True)
        lbl.add_css_class("ygg-sub")
        return lbl

    def _close_btn(self, label: str) -> Gtk.Button:
        b = Gtk.Button(label=label)
        b.connect("clicked", lambda *_: self.close())
        return b

    def _apply(self, _btn) -> None:
        self.update_btn.set_sensitive(False)
        self.later_btn.set_sensitive(False)
        self.status.set_text("Updating… this takes a few seconds.")
        threading.Thread(target=self._do, daemon=True).start()

    def _do(self) -> None:
        ok, msg = updater.apply_update(self.rel.get("tag"))
        GLib.idle_add(self._done, ok, msg)

    def _done(self, ok: bool, msg: str) -> bool:
        if ok:
            self.status.set_text(f"✅  Updated to {self.rel.get('version','?')}. "
                                 "Jarvis is restarting — you can close this.")
            self.later_btn.set_label("Close")
            self.later_btn.set_sensitive(True)
        else:
            self.status.set_text(f"Update didn't complete: {msg}. You can try again later.")
            self.update_btn.set_sensitive(True)
            self.later_btn.set_sensitive(True)
        return False


class UpdateApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.yggdrasil.Updates")

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
            self.win = UpdateWindow(self)
        self.win.present()


def main() -> None:
    try:
        GLib.set_prgname("yggdrasil-updates")
    except Exception:
        pass
    UpdateApp().run(None)


if __name__ == "__main__":
    main()
