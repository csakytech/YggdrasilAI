"""Models screen — which brain sits in which seat.

ThorOS has several LLM "roles" (planner, reasoner, coder, writer, vision) and any installed
Ollama model can fill any of them. Until now that was a power-user trick: you had to know the
`hf.co/...` model string AND which role to bind. This makes it clickable — pick a role, see what's
installed, switch it.

Why it matters, in Michael's words: different users have different specialties and will install
specialty models — a security model for pen-testing, a prose model for writing, a code model for
programming. The roles layer already supports that; this is the surface that makes it usable, and
it's sovereignty over the brain made visible.

The data logic lives in role_options()/apply_choice() so it can be tested without a display; the
GTK half is a thin shell over them.
"""
from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..core.models import (  # noqa: E402
    ModelManager, apply_choice, list_installed_sync, role_options)


def _default_model() -> str:
    """What an unbound role falls back to — the session's default model."""
    return os.environ.get("YGGDRASIL_MODEL", "").strip() or "(the default model)"


class ModelsWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="Models")
        self.set_default_size(560, 480)
        self.set_border_width(16)
        self._manager = ModelManager(_default_model())

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(outer)

        head = Gtk.Label()
        head.set_markup("<big><b>Models</b></big>\n<span foreground='#888'>Choose which AI "
                        "model does each job. Any model you've installed can fill any role.</span>")
        head.set_xalign(0.0)
        head.set_line_wrap(True)
        outer.pack_start(head, False, False, 0)

        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self._body)
        outer.pack_start(scroll, True, True, 0)

        foot = Gtk.Label()
        foot.set_markup("<span foreground='#888' size='small'>Install more with your voice — say "
                        "“download the qwen2.5-coder model”. Changes apply to your next "
                        "request.</span>")
        foot.set_xalign(0.0)
        foot.set_line_wrap(True)
        outer.pack_start(foot, False, False, 0)

        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", lambda _b: self._populate())
        refresh.set_halign(Gtk.Align.END)
        outer.pack_start(refresh, False, False, 0)

        self._populate()

    def _populate(self) -> None:
        for child in self._body.get_children():
            self._body.remove(child)

        installed = [m["name"] for m in list_installed_sync(self._manager.host)]
        if not installed:
            note = Gtk.Label()
            note.set_markup("<span foreground='#888'>Couldn't reach the model service, so I "
                            "can't list your models. Is Ollama running?</span>")
            note.set_xalign(0.0)
            note.set_line_wrap(True)
            self._body.pack_start(note, False, False, 0)
            self._body.show_all()
            return

        for opt in role_options(installed, self._manager.bindings(), _default_model()):
            self._body.pack_start(self._role_row(opt), False, False, 0)
        self._body.show_all()

    def _role_row(self, opt: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)

        title = Gtk.Label()
        title.set_markup(f"<b>{opt['role'].capitalize()}</b>  "
                         f"<span foreground='#888' size='small'>{opt['desc']}</span>")
        title.set_xalign(0.0)
        title.set_line_wrap(True)
        box.pack_start(title, False, False, 0)

        combo = Gtk.ComboBoxText()
        active = 0
        for i, (value, label) in enumerate(opt["choices"]):
            combo.append(value, label)
            if value == opt["current"]:
                active = i
        combo.set_active(active)
        combo.connect("changed", self._on_change, opt["role"])
        box.pack_start(combo, False, False, 0)

        if opt["warning"]:
            warn = Gtk.Label()
            warn.set_markup(f"<span foreground='#c9803a' size='small'>! {opt['warning']}</span>")
            warn.set_xalign(0.0)
            warn.set_line_wrap(True)
            box.pack_start(warn, False, False, 0)
        return box

    def _on_change(self, combo: Gtk.ComboBoxText, role: str) -> None:
        value = combo.get_active_id()
        if value is not None:
            apply_choice(self._manager, role, value)


def main() -> None:
    win = ModelsWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
