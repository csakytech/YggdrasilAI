"""Background jobs — a shared, on-disk registry of work Jarvis is doing in the background.

When an agent kicks off something slow (a software install, a big download), it registers a job
here instead of blocking the conversation or — worse — letting the assistant invent a status.
Two readers share this file:
  - the Tasks window (ui/tasks.py), which shows who's working, on what, and for how long;
  - the orchestrator's status route ("how's the install going?"), which reads the TRUTH here
    rather than asking the language model, which will happily fabricate progress.

Stored as JSON at ~/.local/state/yggdrasil/jobs.json (survives across the separate voice / UI
processes, like search.json). Small and append-mostly; the worker thread updates its own row.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "jobs.json"


def _load() -> list[dict]:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(jobs: list[dict]) -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(jobs), encoding="utf-8")
    except OSError:
        pass


def start(job_id: str, agent: str, title: str, now: float, done_message: str = "") -> None:
    """Register a new running job. `now` is passed in (Date.now() is fine in agents) so the
    registry never calls time itself in a way that breaks determinism in tests. `done_message`
    is what Jarvis SAYS aloud when it finishes ("OBS Studio has finished installing"); blank
    lets the announcer derive one from the title."""
    with _LOCK:
        jobs = [j for j in _load() if j.get("id") != job_id]
        jobs.append({"id": job_id, "agent": agent, "title": title, "state": "running",
                     "progress": None, "detail": "", "started": now, "updated": now,
                     "ended": None, "done_message": done_message, "announced": False})
        _save(jobs[-30:])  # keep the tail; the window shows recent history too


def update(job_id: str, now: float, *, progress: float | None = None,
           detail: str | None = None) -> None:
    with _LOCK:
        jobs = _load()
        for j in jobs:
            if j.get("id") == job_id:
                if progress is not None:
                    j["progress"] = progress
                if detail is not None:
                    j["detail"] = detail
                j["updated"] = now
                break
        _save(jobs)


def finish(job_id: str, now: float, *, ok: bool, detail: str = "") -> None:
    with _LOCK:
        jobs = _load()
        for j in jobs:
            if j.get("id") == job_id:
                j["state"] = "done" if ok else "error"
                j["detail"] = detail or j.get("detail", "")
                j["progress"] = 100.0 if ok else j.get("progress")
                j["updated"] = j["ended"] = now
                break
        _save(jobs)


def unannounced_finished(now: float, within: float = 300.0) -> list[dict]:
    """Jobs that just finished and haven't been SPOKEN yet — for the voice announcer. Only
    recent completions, so a restart doesn't re-announce old history."""
    return [j for j in _load()
            if j.get("state") in ("done", "error") and not j.get("announced")
            and j.get("ended") and (now - j["ended"]) < within]


def spoken_completion(job: dict) -> str:
    """What Jarvis says when this job finishes. Prefer the job's own done_message; otherwise
    derive a natural line from the title ('Installing OBS Studio' -> 'OBS Studio has finished
    installing')."""
    if job.get("state") == "error":
        d = job.get("detail", "")
        return f"{job.get('title', 'A task')} didn't finish{' — ' + d if d else ''}."
    if job.get("done_message"):
        return job["done_message"]
    title = job.get("title", "The task")
    m = re.match(r"(?i)installing\s+(.+)", title)
    if m:
        return f"{m.group(1)} has finished installing."
    m = re.match(r"(?i)downloading\s+(.+)", title)
    if m:
        return f"{m.group(1)} has finished downloading."
    return f"{title} — finished."


def mark_announced(job_id: str) -> None:
    with _LOCK:
        jobs = _load()
        for j in jobs:
            if j.get("id") == job_id:
                j["announced"] = True
                break
        _save(jobs)


def active(now: float, stale_after: float = 3600.0) -> list[dict]:
    """Jobs still running (and not stale). `now` lets callers filter without a time call here."""
    return [j for j in _load() if j.get("state") == "running"
            and (now - j.get("updated", 0)) < stale_after]


def recent(now: float, within: float = 120.0) -> list[dict]:
    """Running jobs plus anything that finished in the last couple of minutes (so "how did the
    install go?" right after it completes still gets a real answer)."""
    out = []
    for j in _load():
        if j.get("state") == "running":
            out.append(j)
        elif j.get("ended") and (now - j["ended"]) < within:
            out.append(j)
    return out


def describe(jobs: list[dict], now: float) -> str:
    """A spoken-friendly status line for a set of jobs (the truthful answer to 'what are you
    working on?' / 'how's it going?'). Empty list -> honest 'nothing'."""
    if not jobs:
        return "I'm not working on anything in the background right now."
    parts = []
    for j in jobs:
        secs = max(0, int(now - j.get("started", now)))
        mins = secs // 60
        elapsed = f"{mins} minute{'s' if mins != 1 else ''}" if mins else f"{secs} seconds"
        title = j.get("title", "a task")
        if j.get("state") == "done":
            parts.append(f"{title} — finished")
        elif j.get("state") == "error":
            parts.append(f"{title} — didn't work ({j.get('detail', 'unknown error')[:80]})")
        else:
            pct = j.get("progress")
            prog = f", {int(pct)}% done" if isinstance(pct, (int, float)) else ""
            parts.append(f"{title} — running for {elapsed}{prog}")
    if len(parts) == 1:
        return parts[0][0].upper() + parts[0][1:] + "."
    return "Here's what I'm working on: " + "; ".join(parts) + "."
