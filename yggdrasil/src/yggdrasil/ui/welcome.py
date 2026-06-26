"""Yggdrasil Welcome — first-boot onboarding window (GTK4).

Shows on first login while first-boot installs the GPU driver and downloads the AI model, with a
live progress read from ``/run/yggdrasil/status`` (written by yggdrasil-firstboot.sh), then the
getting-started tips. Dismissing it writes a per-user flag so it never nags again; the
"Getting Started" app entry reopens it with ``--force``. This is the cold-start hand-holding for
people new to Linux — the scariest moment is the 10–20 min first-boot wait, so we narrate it.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

STATUS_FILE = "/run/yggdrasil/status"
STAMP_FILE = "/var/lib/yggdrasil/.firstboot-done"
FLAG = Path.home() / ".config" / "yggdrasil" / "welcomed"
_PCT = re.compile(r"(\d{1,3})%")

CSS = """
.w-title { font-size: 26px; font-weight: 800; }
.w-sub   { color: alpha(currentColor, 0.62); font-size: 13px; }
.w-status{ font-size: 15px; font-weight: 600; }
.w-tip   { font-size: 14px; }
.w-tiph  { font-size: 15px; font-weight: 700; }
.w-card  { padding: 16px; }
"""


def _name() -> str:
    try:
        from ..core.config import get_name
        return get_name()
    except Exception:
        return os.environ.get("YGGDRASIL_NAME", "Jarvis")


class WelcomeWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Welcome to ThorOS")
        self.set_default_size(620, 680)
        self.name = _name()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for fn in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(outer, fn)(22)

        title = Gtk.Label(label="👋  Welcome to ThorOS", xalign=0)
        title.add_css_class("w-title")
        outer.append(title)
        sub = Gtk.Label(label="Your computer has its own private AI assistant. Here's how to get going.",
                        xalign=0, wrap=True)
        sub.add_css_class("w-sub")
        outer.append(sub)

        # --- setup status card (live) ---
        frame = Gtk.Frame()
        sbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sbox.add_css_class("w-card")
        self.status_lbl = Gtk.Label(label="Getting set up…", xalign=0, wrap=True)
        self.status_lbl.add_css_class("w-status")
        sbox.append(self.status_lbl)
        self.bar = Gtk.ProgressBar()
        self.bar.set_show_text(False)
        sbox.append(self.bar)
        self.hint_lbl = Gtk.Label(
            label="First-time setup downloads your assistant's AI (~5 GB). It's one-time and takes "
                  "about 10–20 minutes — you can keep using the desktop while it works.",
            xalign=0, wrap=True)
        self.hint_lbl.add_css_class("w-sub")
        sbox.append(self.hint_lbl)
        frame.set_child(sbox)
        outer.append(frame)

        # --- tips ---
        tips = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        def h(t: str) -> None:
            lbl = Gtk.Label(label=t, xalign=0, wrap=True)
            lbl.add_css_class("w-tiph")
            lbl.set_margin_top(8)
            tips.append(lbl)

        def p(t: str) -> None:
            lbl = Gtk.Label(label=t, xalign=0, wrap=True)
            lbl.add_css_class("w-tip")
            tips.append(lbl)

        h("🎙  Talk to it")
        p(f"Say “{self.name}”, then what you want — naturally, in one breath:")
        p(f"      •  “{self.name}, open Firefox”")
        p(f"      •  “{self.name}, write a thank-you note”")
        p(f"      •  “{self.name}, what's 15 percent of 240?”")
        p("No microphone? Open “Jarvis Chat” from the apps menu and type instead.")
        h("🪄  Make it yours")
        p(f"Rename your assistant: say “{self.name}, call yourself Athena” — then just say "
          "“Athena.” The name is the wake word (no “hey”).")
        h("🆘  If it's slow or says it's still setting up")
        p("That's the AI still downloading (see above). On a PC with an NVIDIA graphics card, "
          "restart once after setup finishes to switch to the fast driver.")
        outer.append(tips)

        # --- dismiss ---
        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btnrow.set_halign(Gtk.Align.END)
        btnrow.set_margin_top(6)
        gotit = Gtk.Button(label="Got it — let's go")
        gotit.add_css_class("suggested-action")
        gotit.connect("clicked", self._dismiss)
        btnrow.append(gotit)
        outer.append(btnrow)

        sc = Gtk.ScrolledWindow()
        sc.set_child(outer)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(sc)

        self._tick()
        GLib.timeout_add_seconds(2, self._tick)

    def _tick(self) -> bool:
        if os.path.exists(STAMP_FILE):
            self.status_lbl.set_text(f"✅  Ready! Say “{self.name}” to begin.")
            self.bar.set_fraction(1.0)
            self.hint_lbl.set_text("Your assistant is set up and listening.")
            return True
        text = "Getting set up…"
        try:
            text = Path(STATUS_FILE).read_text(encoding="utf-8").strip() or text
        except Exception:
            pass
        m = _PCT.search(text)
        if m:
            self.bar.set_fraction(min(1.0, int(m.group(1)) / 100.0))
        else:
            self.bar.pulse()
        self.status_lbl.set_text(text)
        return True

    def _dismiss(self, _btn: Gtk.Button) -> None:
        try:
            FLAG.parent.mkdir(parents=True, exist_ok=True)
            FLAG.write_text("1", encoding="utf-8")
        except Exception:
            pass
        self.close()


class WelcomeApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.yggdrasil.Welcome")

    def do_activate(self) -> None:
        try:
            from gi.repository import Gdk

            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        except Exception:
            pass  # styling is cosmetic
        WelcomeWindow(self).present()


def main() -> None:
    # On autostart we only show until the user has dismissed it once. The "Getting Started" app
    # entry passes --force to reopen the tips any time.
    if "--force" not in sys.argv and FLAG.exists():
        return
    WelcomeApp().run(None)


if __name__ == "__main__":
    main()
