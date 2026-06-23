"""Activity feed — the assistant publishes what it's doing right now.

A tiny JSON status file (text + timestamp) that the HUD polls and the dashboard can show.
Staleness drives the HUD's fade-out: fresh = visible, old = fades away. Decoupled on purpose
so the assistant and the HUD are separate processes.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "activity.json"


class Activity:
    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else _path()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def publish(self, text: str) -> None:
        try:
            self.path.write_text(json.dumps({"text": text, "ts": time.time()}), encoding="utf-8")
        except OSError:
            pass

    def read(self) -> tuple[str, float]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return str(d.get("text", "")), float(d.get("ts", 0))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return "", 0.0
