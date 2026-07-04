"""Yggdrasil Voices — a native GTK4 picker for the assistant's voice.

Every voice gets a row: name, description, and buttons — ▶ Preview (hear a sample before
deciding) and Use (switch instantly; the running assistant picks it up on its next sentence).
Not-yet-downloaded voices show a Download button with live progress. The active voice is
re-checked every couple of seconds, so switching by voice command updates the window too.
Opened automatically after a rename ("so you can pick a voice that fits the new name") and
by saying "change your voice".
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core import voices  # noqa: E402

CSS = """
.ygg-title { font-size: 20px; font-weight: 800; }
.ygg-name  { font-size: 15px; font-weight: 700; }
.ygg-sub   { color: alpha(currentColor, 0.6); }
.ygg-cur   { color: #2ec27e; font-weight: 700; }
.ygg-card  { padding: 12px; }
"""


class VoicesWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Voices")
        self.set_default_size(640, 660)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for fn in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(outer, fn)(18)
        head = Gtk.Label(label="🎙️  Voices", xalign=0)
        head.add_css_class("ygg-title")
        outer.append(head)
        sub = Gtk.Label(label="Preview a voice, then use the one you like — it switches instantly.\n"
                              "You can also just say: “preview Ryan” or “use the Amy voice”.",
                        xalign=0, wrap=True)
        sub.add_css_class("ygg-sub")
        outer.append(sub)
        self.rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.append(self.rows_box)
        sc = Gtk.ScrolledWindow()
        sc.set_child(outer)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(sc)
        self._rows: dict[str, dict] = {}
        self._build_rows()
        GLib.timeout_add_seconds(2, self._poll)

    def _all_ids(self) -> list[str]:
        ids = list(voices.CATALOG)
        for vid in voices.installed():  # anything user-added but not in the catalog
            if vid not in ids:
                ids.append(vid)
        return ids

    def _build_rows(self) -> None:
        for vid in self._all_ids():
            meta = voices.CATALOG.get(vid) or {}
            frame = Gtk.Frame()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.add_css_class("ygg-card")
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            text.set_hexpand(True)
            name = Gtk.Label(label=voices.label(vid), xalign=0)
            name.add_css_class("ygg-name")
            text.append(name)
            blurb = Gtk.Label(label=meta.get("blurb", vid), xalign=0, wrap=True)
            blurb.add_css_class("ygg-sub")
            text.append(blurb)
            state = Gtk.Label(label="", xalign=0)
            state.add_css_class("ygg-sub")
            text.append(state)
            box.append(text)

            btn_prev = Gtk.Button(label="▶ Preview")
            btn_prev.set_valign(Gtk.Align.CENTER)
            btn_prev.connect("clicked", self._on_preview, vid)
            box.append(btn_prev)
            btn_use = Gtk.Button(label="Use")
            btn_use.set_valign(Gtk.Align.CENTER)
            btn_use.connect("clicked", self._on_use, vid)
            box.append(btn_use)

            frame.set_child(box)
            self.rows_box.append(frame)
            self._rows[vid] = {"state": state, "prev": btn_prev, "use": btn_use,
                               "mb": meta.get("mb")}
        self._poll()

    # --- actions -------------------------------------------------------------------
    def _on_preview(self, _btn, vid: str) -> None:
        if voices.path_for(vid).is_file():
            voices.preview(vid, delay=0.0)
        else:
            voices.start_download(vid, on_done=lambda v, err: (not err) and voices.preview(v, 0.5))

    def _on_use(self, _btn, vid: str) -> None:
        if voices.path_for(vid).is_file():
            voices.set_active(vid)
            voices.preview(vid, delay=0.0)  # immediate audible confirmation
        else:
            voices.start_download(
                vid, on_done=lambda v, err: (not err) and (voices.set_active(v), voices.preview(v, 0.5)))
        self._poll()

    # --- live state ---------------------------------------------------------------
    def _poll(self) -> bool:
        cur = voices.active_id()
        dls = voices.download_status()
        for vid, row in self._rows.items():
            have = voices.path_for(vid).is_file()
            st = dls.get(vid)
            if st and not st.get("done"):
                row["state"].set_text(f"downloading… {st['pct']:.0f}%")
                row["use"].set_sensitive(False)
                row["prev"].set_sensitive(False)
                continue
            if st and st.get("done") and st.get("error") and not have:
                row["state"].set_text("download failed — click to retry")
            elif vid == cur:
                row["state"].set_text("✓ current voice")
            elif have:
                row["state"].set_text("installed")
            else:
                mb = row["mb"]
                row["state"].set_text(f"not downloaded ({mb} MB)" if mb else "not downloaded")
            row["state"].remove_css_class("ygg-cur")
            if vid == cur:
                row["state"].add_css_class("ygg-cur")
            row["use"].set_sensitive(vid != cur)
            row["use"].set_label("Use" if have else "Download && use")
            row["prev"].set_sensitive(True)
            row["prev"].set_label("▶ Preview" if have else "▶ Download && preview")
        return True


class VoicesApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.yggdrasil.Voices")

    def do_activate(self) -> None:
        try:
            from gi.repository import Gdk
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass
        VoicesWindow(self).present()


def main() -> None:
    try:
        GLib.set_prgname("yggdrasil-voices")
    except Exception:
        pass
    VoicesApp().run(None)


if __name__ == "__main__":
    main()
