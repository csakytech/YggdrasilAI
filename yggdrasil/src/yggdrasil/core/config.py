"""Persistent user config — the assistant's name (which is also its wake word) and the wake mode.

Stored as JSON the user owns (``~/.config/yggdrasil/config.json``); first-boot onboarding, a
settings screen, or a spoken command ("call yourself Athena") write it. The name IS the wake word:
in the default "name" wake mode you wake the assistant by saying just its name — any name, no
"hey" required. A saved name wins over the launcher's ``YGGDRASIL_NAME`` env default.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_DEFAULT_NAME = "Jarvis"
# "name"  = wake by spotting the spoken name in the STT transcript (any name, just say it)
# "model" = classic openWakeWord neural wake word (efficient, but only the bundled phrases)
_DEFAULT_MODE = "name"
_BADNAME = re.compile(r"[^A-Za-z0-9 '\-]")


def _path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "yggdrasil" / "config.json"


def _raw() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(cfg: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def clean_name(name: str) -> str:
    """Sanitize a spoken/typed name into a usable wake word."""
    n = re.sub(r"\s+", " ", _BADNAME.sub("", name or "").strip())
    return n[:24] or _DEFAULT_NAME


def get_name() -> str:
    """The assistant's name = its wake word. Saved config wins over the env default."""
    return _raw().get("name") or os.environ.get("YGGDRASIL_NAME") or _DEFAULT_NAME


def set_name(name: str) -> str:
    name = clean_name(name)
    cfg = _raw()
    cfg["name"] = name
    _save(cfg)
    return name


def get_wake_mode() -> str:
    return (os.environ.get("YGGDRASIL_WAKE_MODE") or _raw().get("wake_mode") or _DEFAULT_MODE).lower()


def get_search_engine() -> str:
    """The visual web-search engine. DuckDuckGo by default — NOT Google: we launch Firefox with
    Marionette for voice browsing, which sets navigator.webdriver, and Google answers webdriver
    browsers with a CAPTCHA. A CAPTCHA is a hard wall for hands-free users (that's its whole
    point), so the default engine must be one that doesn't throw them."""
    e = (os.environ.get("YGGDRASIL_SEARCH_ENGINE") or _raw().get("search_engine") or "duckduckgo")
    return e.lower() if e.lower() in ("duckduckgo", "google", "bing") else "duckduckgo"


def set_search_engine(engine: str) -> None:
    cfg = _raw()
    cfg["search_engine"] = (engine or "duckduckgo").lower()
    _save(cfg)


def get_chat_pref() -> tuple[str, str]:
    """The Chat window's remembered setup: (mode, model). mode is 'assistant' (route through
    the agents — types like the voice loop) or 'chat' (pure conversation with the local model);
    model '' = whatever the launcher's default model is."""
    d = _raw()
    mode = d.get("chat_mode") or "assistant"
    return (mode if mode in ("assistant", "chat") else "assistant", d.get("chat_model") or "")


def set_chat_pref(mode: str, model: str) -> None:
    cfg = _raw()
    cfg["chat_mode"] = mode if mode in ("assistant", "chat") else "assistant"
    cfg["chat_model"] = model or ""
    _save(cfg)


def get_voice() -> str:
    """The chosen voice id (e.g. 'en_US-ryan-high'); '' = whatever the launcher provides."""
    return _raw().get("voice") or ""


def set_voice(voice_id: str) -> None:
    cfg = _raw()
    cfg["voice"] = voice_id
    _save(cfg)
