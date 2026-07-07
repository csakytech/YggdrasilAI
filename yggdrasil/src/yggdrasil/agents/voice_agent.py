"""Voice Agent — change how the assistant SOUNDS, by voice.

"What voices do you have?" opens the Voices window (with per-voice Preview buttons) and reads
the installed ones. "Use the Ryan voice" switches instantly — the reply itself is already
spoken in the new voice. "Preview Amy" plays a sample WITHOUT switching, so you can decide.
Voices that aren't downloaded yet are fetched with spoken consent (they're ~60-115 MB).

Policy note: only original synthetic voices (Piper). No cloning of real people's voices —
see core/voices.py.
"""
from __future__ import annotations

import subprocess

from ..core import voices
from ..core.permissions import Capability
from .base import BaseAgent


class VoiceAgent(BaseAgent):
    domain = "voice"
    module_id = "core.voice"
    planner_examples = [
        'what voices do you have -> {"steps":[{"action":"voice.open","argument":""}]}',
        'change your voice -> {"steps":[{"action":"voice.open","argument":""}]}',
        'use the ryan voice -> {"steps":[{"action":"voice.use","argument":"ryan"}]}',
        'preview the amy voice -> {"steps":[{"action":"voice.preview","argument":"amy"}]}',
        'what does the calm male voice sound like -> {"steps":[{"action":"voice.preview","argument":"calm male"}]}',
        'close the voice window -> {"steps":[{"action":"voice.close","argument":""}]}',
    ]
    capabilities = {
        "open": Capability("open", False, "Open the Voices window (see, preview, and pick voices)"),
        "close": Capability("close", False, "Close the Voices window"),
        "list": Capability("list", False, "List the voices installed on this machine"),
        "status": Capability("status", False, "Which voice is in use, and download progress"),
        "use": Capability("use", False, "Switch to a different voice (downloads it if needed)"),
        "preview": Capability("preview", False, "Play a sample of a voice without switching"),
        "confirm": Capability("confirm", False, "Confirm the staged voice download"),
        "cancel": Capability("cancel", False, "Cancel the staged voice download"),
    }

    def __init__(self, bus, perms) -> None:
        super().__init__(bus, perms)
        self._staged: dict | None = None  # {"vid": ..., "use": bool} awaiting yes/no

    async def _execute(self, verb, params):
        arg = (params.get("argument") or "").strip()
        if verb == "open":
            return self._open()
        if verb == "close":
            return {"speech": self._close()}
        if verb == "list":
            return self._list()
        if verb == "status":
            return self._status()
        if verb == "use":
            return self._use(arg)
        if verb == "preview":
            return self._preview(arg)
        if verb == "confirm":
            return self._confirm()
        if verb == "cancel":
            self._staged = None
            return {"speech": "Okay, cancelled."}
        raise ValueError(f"unhandled verb '{verb}'")

    # --- capabilities ----------------------------------------------------------------
    def _open(self):
        have = voices.installed()
        opened = voices.open_picker()
        names = ", ".join(voices.label(v) for v in have) or "none yet"
        if opened:
            return {"speech": f"Here are my voices — I've put them on screen with preview "
                              f"buttons. Installed right now: {names}. Say, for example, "
                              f"“preview Ryan” or “use the Amy voice”."}
        return {"speech": f"Installed voices: {names}. I can only show the picker window "
                          "on the desktop, but you can still say “preview Ryan” or "
                          "“use the Amy voice”."}

    @staticmethod
    def _close() -> str:
        closed = False
        try:  # match by title or app id (the WM_CLASS of python-launched GTK is unreliable)
            for line in subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True,
                                       timeout=5).stdout.splitlines():
                low = line.lower()
                if "voices" in low or "org.yggdrasil.voices" in low:
                    subprocess.run(["wmctrl", "-i", "-c", line.split(None, 1)[0]],
                                   capture_output=True, timeout=5)
                    closed = True
        except Exception:
            pass
        if not closed:
            try:
                if subprocess.run(["pkill", "-f", "yggdrasil.ui.voices"],
                                  capture_output=True, timeout=5).returncode == 0:
                    closed = True
            except Exception:
                pass
        return "Closed the voices window." if closed else "The voices window wasn't open."

    def _list(self):
        have = voices.installed()
        if not have:
            return {"speech": "No voices are installed yet — say “change your voice” and "
                              "I'll show you what's available to download."}
        cur = voices.active_id()
        lines = [voices.label(v) + (" — current" if v == cur else "") for v in have]
        return {"speech": f"I have {len(have)} voice{'s' if len(have) != 1 else ''} installed.",
                "list": lines}

    def _status(self):
        cur = voices.active_id()
        speech = f"I'm using the {voices.label(cur)} voice." if cur else "I'm using the built-in voice."
        for vid, st in voices.download_status().items():
            if not st.get("done"):
                speech += f" Downloading {voices.label(vid)} — {st['pct']:.0f} percent."
            elif st.get("error"):
                speech += f" The {voices.label(vid)} download failed: {st['error']}."
        return {"speech": speech}

    def _use(self, spoken: str):
        if not spoken:
            return self._open()
        vid = voices.resolve_spoken(spoken)
        if not vid:
            near = voices.closest(spoken)
            if near:  # ambiguous ("calm mail"?) -> offer the front-runners, don't spam windows
                opts = " or ".join(voices.label(v) for v in near)
                return {"speech": f"Did you mean {opts}? Say, for example, "
                                  f"“use the {voices.label(near[0])} voice”."}
            voices.open_picker()
            return {"speech": f"I don't know a voice called {spoken} — the list is on screen."}
        if voices.path_for(vid).is_file():
            voices.set_active(vid)
            # this very reply is rendered with the NEW voice (Speaker re-resolves per utterance)
            return {"speech": f"Done — I'm speaking with the {voices.label(vid)} voice now. "
                              "Happy with it? If not, say “change your voice” to pick another."}
        mb = (voices.CATALOG.get(vid) or {}).get("mb", 60)
        self._staged = {"vid": vid, "use": True}
        return {"speech": f"The {voices.label(vid)} voice isn't downloaded yet — it's about "
                          f"{mb} megabytes. Download it and switch? Say yes or no.",
                "await_confirm": True, "agent": self.domain}

    def _preview(self, spoken: str):
        vid = voices.resolve_spoken(spoken) if spoken else None
        if not vid:
            near = voices.closest(spoken) if spoken else []
            if near:
                opts = " or ".join(voices.label(v) for v in near)
                return {"speech": f"Did you mean {opts}?"}
            return self._open()
        if voices.path_for(vid).is_file():
            voices.preview(vid)  # plays a couple of seconds after this reply finishes
            return {"speech": f"Here's the {voices.label(vid)} voice:"}
        mb = (voices.CATALOG.get(vid) or {}).get("mb", 60)
        self._staged = {"vid": vid, "use": False}
        return {"speech": f"{voices.label(vid)} isn't downloaded yet — it's about {mb} "
                          "megabytes. Download it so you can hear it? Say yes or no.",
                "await_confirm": True, "agent": self.domain}

    def _confirm(self):
        if not self._staged:
            return {"speech": "There's nothing staged to download."}
        vid, use = self._staged["vid"], self._staged["use"]
        self._staged = None

        def on_done(v, error):  # worker thread
            if error:
                return
            if use:
                voices.set_active(v)  # takes effect on the next utterance
            voices.preview(v, delay=0.5)

        voices.start_download(vid, on_done=on_done)
        tail = ("I'll switch to it and say hello when it's ready."
                if use else "I'll play a sample when it's ready.")
        return {"speech": f"Downloading the {voices.label(vid)} voice now. {tail}"}
