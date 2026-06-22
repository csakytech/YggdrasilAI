"""Yggdrasil dashboard — a native GTK4 desktop window.

Shows the live system: active agents + capabilities, system/GPU status (refreshed every 2s),
the model in use, trust mode, and remembered facts. The data comes from ``status.py`` (pure,
headless-testable); this module is only the GTK shell, so run it on the desktop.

Run on FusionOS:  yggdrasil-dashboard   (uses the SYSTEM python3 for GTK; see the launcher)
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import status  # noqa: E402

CSS = """
.ygg-title { font-size: 20px; font-weight: 800; }
.ygg-sub   { color: alpha(currentColor, 0.6); }
.ygg-card  { padding: 12px; }
.ygg-danger{ color: #e06c75; font-weight: 700; }
.ygg-key   { color: alpha(currentColor, 0.6); }
"""


def _card(title: str) -> tuple[Gtk.Frame, Gtk.Box]:
    frame = Gtk.Frame()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.add_css_class("ygg-card")
    head = Gtk.Label(label=title, xalign=0)
    head.add_css_class("ygg-title")
    box.append(head)
    frame.set_child(box)
    return frame, box


def _row(text: str, css: str | None = None) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
    if css:
        lbl.add_css_class(css)
    return lbl


class DashboardWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Yggdrasil — Thor")
        self.set_default_size(760, 860)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for fn in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(outer, fn)(18)

        st = status.status_info()
        header = Gtk.Label(label="🌳  Yggdrasil", xalign=0)
        header.add_css_class("ygg-title")
        outer.append(header)
        sub = _row(f"Release {st['release']}  ·  assistant: {st['name']}", "ygg-sub")
        outer.append(sub)

        # --- Status card ---
        sframe, sbox = _card("Status")
        sbox.append(_row(f"Model:  {st['model']}"))
        sbox.append(_row(f"Trust mode:  {st['trust']}"))
        outer.append(sframe)

        # --- System card (live) ---
        yframe, ybox = _card("System")
        self.sys_label = _row("…")
        ybox.append(self.sys_label)
        self.gpu_label = _row("…")
        ybox.append(self.gpu_label)
        outer.append(yframe)

        # --- Agents card ---
        aframe, abox = _card("Active agents")
        for ag in status.agents_info():
            line = Gtk.Label(label=f"● {ag['domain']}", xalign=0)
            line.add_css_class("ygg-title")
            abox.append(line)
            for c in ag["capabilities"]:
                mark = "  ⚠ needs authorization" if c["dangerous"] else ""
                row = _row(f"    {ag['domain']}.{c['name']} — {c['description']}{mark}",
                           "ygg-danger" if c["dangerous"] else None)
                abox.append(row)
        outer.append(aframe)

        # --- Memory card ---
        mframe, mbox = _card("Memory")
        facts = status.memory_facts()
        if facts:
            for f in facts:
                mbox.append(_row(f"• {f}"))
        else:
            mbox.append(_row("Nothing remembered yet.", "ygg-sub"))
        outer.append(mframe)

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(outer)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(scroller)

        self._refresh()
        GLib.timeout_add_seconds(2, self._refresh)

    def _refresh(self) -> bool:
        s = status.system_info()
        self.sys_label.set_text(
            f"CPU load {s['load']:.1f}   ·   "
            f"RAM {s['mem_used']:.1f}/{s['mem_total']:.1f} GB   ·   "
            f"Disk {s['disk_free']:.0f} GB free   ·   up {s['uptime_h']:.1f} h"
        )
        g = s["gpu"]
        if g:
            self.gpu_label.set_text(
                f"GPU {g['name']}   ·   VRAM {g['used_mb']}/{g['total_mb']} MB   ·   "
                f"{g['util']}% util   ·   {g['temp']}°C"
            )
        else:
            self.gpu_label.set_text("GPU: not detected")
        return True  # keep the timer running


class DashboardApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.yggdrasil.Dashboard")

    def do_activate(self) -> None:
        try:
            from gi.repository import Gdk

            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        except Exception:
            pass  # styling is cosmetic — never let it block the window
        DashboardWindow(self).present()


def main() -> None:
    DashboardApp().run(None)


if __name__ == "__main__":
    main()
