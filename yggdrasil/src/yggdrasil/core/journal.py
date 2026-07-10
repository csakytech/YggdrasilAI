"""Activity journal — an automatic, timestamped record of what the user actually DID, so
ThorOS can answer "what was I working on yesterday?".

Complements ``core.memory`` (facts the user asks to remember): this is the automatic diary
of work — projects started, files created, documents written, things looked up. Stored as
append-only JSONL the user owns (``~/.local/state/yggdrasil/journal.jsonl``), one small JSON
object per line, so writing is cheap and a corrupt tail line never loses the history.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

MAX_ENTRIES = 20000  # a personal work diary; trimmed from the front past this


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "journal.jsonl"


def record(kind: str, summary: str, detail: dict | None = None) -> None:
    """Append one activity entry. ``kind`` groups it (dev/file/doc/research/app); ``summary``
    is a short past-tense human line ("Created the folder Book")."""
    summary = (summary or "").strip()
    if not summary:
        return
    entry = {"ts": time.time(), "kind": kind, "summary": summary}
    if detail:
        entry["detail"] = detail
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _all() -> list[dict]:
    out: list[dict] = []
    try:
        with open(_path(), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # tolerate a torn last line
    except OSError:
        pass
    if len(out) > MAX_ENTRIES:  # keep the file bounded, newest kept
        out = out[-MAX_ENTRIES:]
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                for e in out:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return out


def between(start_ts: float, end_ts: float) -> list[dict]:
    """Entries with start <= ts < end, oldest first."""
    return [e for e in _all() if start_ts <= e.get("ts", 0.0) < end_ts]


def window_for(text: str) -> tuple[float, float, str]:
    """Parse a spoken time window from ``text`` into (start_ts, end_ts, label). Defaults to
    today when no period is named."""
    t = (text or "").lower()
    now = dt.datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def ts(d: dt.datetime) -> float:
        return d.timestamp()

    if "yesterday" in t:
        y = midnight - dt.timedelta(days=1)
        return ts(y), ts(midnight), "yesterday"
    if "this morning" in t:
        end = min(midnight.replace(hour=12), now)
        return ts(midnight), ts(end) + 0.001, "this morning"
    if "this afternoon" in t:
        return ts(midnight.replace(hour=12)), ts(now) + 1, "this afternoon"
    if "last week" in t:
        this_mon = midnight - dt.timedelta(days=midnight.weekday())
        return ts(this_mon - dt.timedelta(days=7)), ts(this_mon), "last week"
    if "this week" in t or "the week" in t:
        this_mon = midnight - dt.timedelta(days=midnight.weekday())
        return ts(this_mon), ts(now) + 1, "this week"
    if "last month" in t:
        first_this = midnight.replace(day=1)
        first_last = (first_this - dt.timedelta(days=1)).replace(day=1)
        return ts(first_last), ts(first_this), "last month"
    if "this month" in t:
        return ts(midnight.replace(day=1)), ts(now) + 1, "this month"
    if "recently" in t or "lately" in t or "past few days" in t or "last few days" in t:
        return ts(midnight - dt.timedelta(days=3)), ts(now) + 1, "the past few days"
    if "today" in t or "so far" in t or "earlier" in t:
        return ts(midnight), ts(now) + 1, "today"
    return ts(midnight), ts(now) + 1, "today"
