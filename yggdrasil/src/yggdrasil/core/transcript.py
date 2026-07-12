"""Dev-build transcript — a complete, persistent record of every exchange for QA.

On a DEVELOPMENT build of ThorOS (never the public ISO), every user utterance, the plan the
orchestrator chose, every task dispatched to an agent, each agent's raw result, and the final
spoken reply are appended as JSON lines. This is the ground truth for debugging routing bugs:
what the user actually said, and what every agent actually answered — not a reconstruction.

Activation (checked once at import):
  - the file /etc/yggdrasil/dev-mode exists           (created by the dev-edition ISO), or
  - /etc/yggdrasil-edition.env says YGG_EDITION=dev   (baked by `YGG_EDITION=dev lb build`), or
  - the environment variable YGGDRASIL_DEV=1          (ad-hoc debugging on any install).

On a normal build every call here is a cheap no-op. The log is plain JSONL at
~/.local/state/yggdrasil/transcript.jsonl, rotated once past ~20MB (one .1 kept). Privacy note:
this records everything said to the assistant — which is exactly its purpose — so it must only
ever ship enabled on the dev ISO.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_LOG_DIR = Path.home() / ".local" / "state" / "yggdrasil"
_LOG = _LOG_DIR / "transcript.jsonl"
_MAX_BYTES = 20 * 1024 * 1024
_SESSION = f"{int(time.time())}-{os.getpid()}"


def _detect() -> bool:
    if os.environ.get("YGGDRASIL_DEV") == "1":
        return True
    if Path("/etc/yggdrasil/dev-mode").exists():
        return True
    try:
        return "YGG_EDITION=dev" in Path("/etc/yggdrasil-edition.env").read_text(encoding="utf-8")
    except Exception:
        return False


ENABLED = _detect()


def log(kind: str, **fields) -> None:
    """Append one event. kind: user | plan | task | result | reply. Never raises."""
    if not ENABLED:
        return
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if _LOG.stat().st_size > _MAX_BYTES:
                _LOG.replace(_LOG.with_suffix(".jsonl.1"))
        except FileNotFoundError:
            pass
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "session": _SESSION, "kind": kind}
        entry.update(fields)
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
