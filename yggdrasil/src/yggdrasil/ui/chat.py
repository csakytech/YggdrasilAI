"""Yggdrasil Chat — the local AI chat window.

Two modes, switchable in the header bar (the choice is remembered):
  • "Do things"  — typed commands to the same brain as the voice loop (orchestrator, agents,
                   memory, planner): "open firefox", "install gimp", "what's the weather".
  • "Just chat"  — a pure ChatGPT-style conversation with a LOCAL model. Nothing routes to
                   agents, nothing leaves the machine; pick any installed Ollama model from
                   the dropdown. This is the "AI chat window" people know from the cloud
                   chatbots — except private, offline-capable, and yours.

Conversations are saved to ~/.local/share/yggdrasil/chats/ as JSONL; "New chat" starts a
fresh one. GTK3; the agent stack and the model calls run in a background asyncio loop so the
window stays responsive, and results are marshalled back to the UI thread via GLib.idle_add.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..app import build_orchestrator  # noqa: E402
from ..core import config  # noqa: E402
from ..core.permissions import AuthChallenge, UserChannel  # noqa: E402

_CHAT_DIR = Path.home() / ".local" / "share" / "yggdrasil" / "chats"
_MAX_TURNS = 24  # rolling context for "just chat" mode — keeps prompts inside small models


def _chat_system(name: str) -> str:
    return (f"You are {name}, a friendly AI assistant running entirely on this computer — "
            "private and local, part of ThorOS. Have a natural conversation: answer questions, "
            "brainstorm, explain, write. Be warm and concise; use plain text, not markdown.")


class _ChatChannel(UserChannel):
    """Bridges the permission system's challenge to the chat view."""

    def __init__(self, show) -> None:
        self._show = show

    async def present_challenge(self, challenge: AuthChallenge) -> None:
        self._show("Jarvis", f"🔒 {challenge.summary}\nTo approve, type:  Authorize {challenge.code}", "dim")


class ChatWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="Chat")
        self.set_default_size(560, 660)
        self.set_border_width(8)

        self._loop = asyncio.new_event_loop()
        self._orch = None
        self._name = "Jarvis"
        self._auth_future: asyncio.Future | None = None
        self._mode, self._chat_model = config.get_chat_pref()
        self._messages: list[dict] = []  # "just chat" rolling history (no system msg; added per call)
        self._log_path: Path | None = None

        # --- header: mode switch, model picker (chat mode), new chat ---
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "Chat"
        self.set_titlebar(header)
        self._mode_combo = Gtk.ComboBoxText()
        self._mode_combo.append("assistant", "Do things")
        self._mode_combo.append("chat", "Just chat")
        self._mode_combo.set_active_id(self._mode)
        self._mode_combo.connect("changed", self._on_mode_changed)
        header.pack_start(self._mode_combo)
        self._model_combo = Gtk.ComboBoxText()
        self._model_combo.append("", "(default model)")
        self._model_combo.set_active_id("")
        self._model_combo.set_tooltip_text("Which local model to chat with (Just chat mode)")
        self._model_combo.connect("changed", self._on_model_changed)
        header.pack_start(self._model_combo)
        new_btn = Gtk.Button(label="New chat")
        new_btn.connect("clicked", self._on_new_chat)
        header.pack_end(new_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._view = Gtk.TextView()
        self._view.set_editable(False)
        self._view.set_cursor_visible(False)
        self._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._view.set_left_margin(8)
        self._view.set_right_margin(8)
        self._buf = self._view.get_buffer()
        self._tag_you = self._buf.create_tag("you", weight=Pango.Weight.BOLD)
        self._tag_jarvis = self._buf.create_tag("jarvis", weight=Pango.Weight.BOLD, foreground="#4a90d9")
        self._tag_dim = self._buf.create_tag("dim", style=Pango.Style.ITALIC, foreground="#888888")
        self._end_mark = self._buf.create_mark("end", self._buf.get_end_iter(), False)
        scroll.add(self._view)
        box.pack_start(scroll, True, True, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Connecting…")
        self._entry.set_sensitive(False)
        self._entry.connect("activate", self._on_send)
        self._send = Gtk.Button(label="Send")
        self._send.set_sensitive(False)
        self._send.connect("clicked", self._on_send)
        row.pack_start(self._entry, True, True, 0)
        row.pack_start(self._send, False, False, 0)
        box.pack_start(row, False, False, 0)

        self.connect("destroy", self._on_destroy)
        self._apply_mode_ui()
        threading.Thread(target=self._run_loop, daemon=True).start()
        threading.Thread(target=self._load_models, daemon=True).start()

    # --- background asyncio loop owning the agent stack -----------------------------------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            _bus, orch, _fa, _st, name = self._loop.run_until_complete(
                build_orchestrator(_ChatChannel(self._post), self._auth_resolver))
            self._orch, self._name = orch, name
            GLib.idle_add(self._on_ready)
            self._loop.run_forever()
        except Exception as e:  # noqa: BLE001
            GLib.idle_add(self._post_inline, "Jarvis", f"(couldn't start: {e!r})", "dim")

    def _load_models(self) -> None:
        """Populate the model dropdown from the LOCAL Ollama daemon (local-only by design)."""
        try:
            import httpx

            r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=10)
            names = sorted(m.get("name", "") for m in r.json().get("models", []) if m.get("name"))
        except Exception:
            return
        GLib.idle_add(self._fill_models, names)

    def _fill_models(self, names: list[str]) -> bool:
        for n in names:
            self._model_combo.append(n, n)
        if self._chat_model and self._chat_model in names:
            self._model_combo.set_active_id(self._chat_model)
        return False

    def _on_ready(self) -> bool:
        self.set_title(self._name)
        greet = (f"Hi, I'm {self._name}. Type a request or a question." if self._mode == "assistant"
                 else f"Hi, I'm {self._name} — let's chat. Everything stays on this machine.")
        self._show(self._name, greet, "dim")
        self._set_busy(False)
        return False

    async def _auth_resolver(self, challenge: AuthChallenge) -> str:
        GLib.idle_add(self._enable_for_auth)
        self._auth_future = self._loop.create_future()
        code = await self._auth_future
        self._auth_future = None
        return code.strip().split()[-1] if code.strip() else ""

    def _enable_for_auth(self) -> bool:
        self._entry.set_sensitive(True)
        self._send.set_sensitive(True)
        self._entry.set_placeholder_text("Enter the authorization code…")
        self._entry.grab_focus()
        return False

    # --- header actions ---------------------------------------------------------------------------
    def _on_mode_changed(self, combo) -> None:
        self._mode = combo.get_active_id() or "assistant"
        config.set_chat_pref(self._mode, self._chat_model)
        self._apply_mode_ui()
        hint = ("I'll do things — files, apps, web, installs. Ask away."
                if self._mode == "assistant" else
                "Pure conversation with the local model — nothing routes to agents.")
        self._show(self._name, hint, "dim")

    def _on_model_changed(self, combo) -> None:
        self._chat_model = combo.get_active_id() or ""
        config.set_chat_pref(self._mode, self._chat_model)

    def _apply_mode_ui(self) -> None:
        self._model_combo.set_visible(self._mode == "chat")
        self._model_combo.set_no_show_all(self._mode != "chat")

    def _on_new_chat(self, _btn) -> None:
        self._messages = []
        self._log_path = None
        self._buf.set_text("")
        self._show(self._name, "Fresh start — what's on your mind?", "dim")
        self._entry.grab_focus()

    # --- sending --------------------------------------------------------------------------------
    def _on_send(self, _w) -> None:
        text = self._entry.get_text().strip()
        if not text or self._orch is None:
            return
        self._entry.set_text("")
        if self._auth_future is not None and not self._auth_future.done():  # feeding an auth code
            self._show("You", text)
            self._set_busy(True)
            self._loop.call_soon_threadsafe(self._auth_future.set_result, text)
            return
        self._show("You", text)
        self._set_busy(True)
        self._log("you", text)
        if self._mode == "chat":
            fut = asyncio.run_coroutine_threadsafe(self._chat_turn(text), self._loop)
        else:
            fut = asyncio.run_coroutine_threadsafe(self._orch.handle(text), self._loop)
        fut.add_done_callback(self._on_reply)

    async def _chat_turn(self, text: str) -> str:
        """One 'just chat' exchange: rolling history -> local model -> reply."""
        from ..core.llm import OllamaProvider

        model = self._chat_model or os.environ.get("YGGDRASIL_MODEL", "")
        if not model:
            return "I don't have a chat model configured yet — pick one from the dropdown."
        self._messages.append({"role": "user", "content": text})
        del self._messages[:-_MAX_TURNS]
        msgs = [{"role": "system", "content": _chat_system(self._name)}, *self._messages]
        resp = await OllamaProvider(model).chat(messages=msgs)
        reply = resp.text.strip() or "(no reply)"
        self._messages.append({"role": "assistant", "content": reply})
        return reply

    def _on_reply(self, fut) -> None:
        try:
            reply = fut.result()
        except Exception as e:  # noqa: BLE001
            reply = f"(error: {e!r})"
        GLib.idle_add(self._deliver, reply)

    def _deliver(self, reply: str) -> bool:
        self._show(self._name, reply)
        self._log("assistant", reply)
        self._set_busy(False)
        return False

    def _set_busy(self, busy: bool) -> None:
        self._entry.set_sensitive(not busy)
        self._send.set_sensitive(not busy)
        if busy:
            self._entry.set_placeholder_text("Thinking…")
        else:
            self._entry.set_placeholder_text(
                f"Message {self._name}…" if self._mode == "assistant" else "Say anything…")
            self._entry.grab_focus()

    # --- history on disk --------------------------------------------------------------------------
    def _log(self, role: str, text: str) -> None:
        try:
            if self._log_path is None:
                _CHAT_DIR.mkdir(parents=True, exist_ok=True)
                self._log_path = _CHAT_DIR / f"chat-{int(time.time())}.jsonl"
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": self._mode,
                                    "model": self._chat_model, "role": role, "text": text},
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass

    # --- text view (main thread only) -----------------------------------------------------------
    def _show(self, who: str, text: str, tag: str | None = None) -> bool:
        buf = self._buf
        if buf.get_char_count():
            buf.insert(buf.get_end_iter(), "\n\n")
        who_tag = self._tag_you if who == "You" else self._tag_jarvis
        buf.insert_with_tags(buf.get_end_iter(), f"{who}: ", who_tag)
        if tag == "dim":
            buf.insert_with_tags(buf.get_end_iter(), text, self._tag_dim)
        else:
            buf.insert(buf.get_end_iter(), text)
        buf.move_mark(self._end_mark, buf.get_end_iter())
        self._view.scroll_mark_onscreen(self._end_mark)
        return False

    def _post(self, who: str, text: str, tag: str | None = None) -> None:
        GLib.idle_add(self._show, who, text, tag)

    def _post_inline(self, who: str, text: str, tag: str | None = None) -> bool:
        return self._show(who, text, tag)

    def _on_destroy(self, _w) -> None:
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        Gtk.main_quit()


def main() -> None:
    win = ChatWindow()
    win.show_all()
    win.present()
    Gtk.main()


if __name__ == "__main__":
    main()
