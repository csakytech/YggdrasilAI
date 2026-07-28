"""Conversation log — what the assistant HEARD and what it said back.

A short rolling list of exchanges in a plain JSON file, written by the voice loop and read by
the Conversation window. Same decoupled-file pattern as activity.json / mission.json: separate
processes, no IPC, the user owns their data.

Why it exists: when the assistant answers oddly there are two very different causes — it
misheard you, or it understood and chose badly — and from the outside they look identical.
Seeing the transcript tells them apart instantly. Real examples that cost hours: "Cancel" heard
as "Council", "Jarvis" as "Drawers", "What is my internet IP?" filed as a project description.

Deliberately NOT core/transcript.py. That one is the deep dev-ISO QA log (every plan, agent task
and result, unbounded, dev builds only). This is a small user-facing memory: the last few
exchanges, both sides, nothing else. It is capped so it can never grow without bound, and it is
off unless the user turns it on.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MAX_KEPT = 60  # generous scrollback; the window shows the tail and lets you scroll up


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "conversation.json"


def enabled() -> bool:
    """Off by default. This records everything spoken to the assistant, so it is the user's
    choice to make, not ours — Settings > Conversation log."""
    try:
        from . import config
        return config.get_conversation_log()
    except Exception:
        return False


def record(heard: str, reply: str, addressed: bool = True) -> None:
    """Append one exchange. Never raises: a logging failure must not break a conversation."""
    if not enabled():
        return
    if not (heard or "").strip() and not (reply or "").strip():
        return
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        items = read(raw=True)
        items.append({
            "heard": (heard or "").strip(),
            "reply": (reply or "").strip(),
            "addressed": bool(addressed),
            "ts": time.time(),
        })
        p.write_text(json.dumps(items[-MAX_KEPT:]), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def read(limit: int = MAX_KEPT, raw: bool = False) -> list[dict]:
    """The most recent exchanges, oldest first. Missing/corrupt file reads as empty rather than
    raising — the window must still open and say "nothing yet"."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return data if raw else data[-limit:]
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return []


def clear() -> None:
    try:
        _path().unlink()
    except OSError:
        pass
