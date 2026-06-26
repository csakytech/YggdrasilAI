"""Scheduler: persistent one-off + recurring jobs that fire reminders and research briefings.

A job is WHEN (once / daily / weekdays / weekly / hourly, at a local HH:MM) + WHAT (speak a
reminder, or run a research lookup and speak the summary). Stored in ~/.config/yggdrasil/schedule.json
so it survives reboots. The Runner (a daemon thread in the voice service) checks every 20s, fires
due jobs — speaks them, posts a desktop notification, logs — and reschedules the recurring ones.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

_WD = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "yggdrasil" / "schedule.json"


def _now() -> dt.datetime:
    return dt.datetime.now()


def compute_next(job: dict, after: dt.datetime | None = None) -> dt.datetime | None:
    """Next fire time (local) strictly after `after`. None means a finished one-off."""
    after = after or _now()
    rec = job.get("recurrence", "once")
    if rec == "once":
        nr = job.get("next_run")
        t = dt.datetime.fromisoformat(nr) if nr else None
        return t if (t and t > after) else None
    hh, _, mm = (job.get("time") or "09:00").partition(":")
    hh, mm = int(hh or 9), int(mm or 0)
    if rec == "hourly":
        nxt = after.replace(minute=mm, second=0, microsecond=0)
        return nxt if nxt > after else nxt + dt.timedelta(hours=1)
    cand = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= after:
        cand += dt.timedelta(days=1)
    if rec == "weekdays":
        while cand.weekday() >= 5:
            cand += dt.timedelta(days=1)
    elif rec == "weekly":
        target = _WD.get((job.get("weekday") or "mon").lower()[:3], 0)
        while cand.weekday() != target:
            cand += dt.timedelta(days=1)
    return cand


class Schedule:
    """Thread-safe persistent job list."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else default_path()
        self._lock = threading.Lock()
        self.jobs: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            return list(json.loads(self.path.read_text(encoding="utf-8")).get("jobs", []))
        except (OSError, json.JSONDecodeError, AttributeError):
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"jobs": self.jobs}, indent=2, default=str), encoding="utf-8")

    def add(self, job: dict) -> dict:
        job.setdefault("id", uuid.uuid4().hex[:8])
        job.setdefault("created", _now().isoformat())
        if not job.get("next_run"):
            nr = compute_next(job)
            job["next_run"] = nr.isoformat() if nr else None
        with self._lock:
            self.jobs.append(job)
            self._save()
        return job

    def list(self) -> list[dict]:
        with self._lock:
            return list(self.jobs)

    def cancel(self, query: str) -> list[dict]:
        q = (query or "").lower().strip()
        with self._lock:
            hit = [j for j in self.jobs if q and q in (j.get("label", "").lower())]
            if hit:
                ids = {id(j) for j in hit}
                self.jobs = [j for j in self.jobs if id(j) not in ids]
                self._save()
        return hit

    def due(self, now: dt.datetime | None = None) -> list[dict]:
        now = now or _now()
        with self._lock:
            return [j for j in self.jobs
                    if j.get("next_run") and dt.datetime.fromisoformat(j["next_run"]) <= now]

    def reschedule_or_remove(self, job: dict) -> None:
        with self._lock:
            if job.get("recurrence", "once") == "once":
                self.jobs = [j for j in self.jobs if j.get("id") != job.get("id")]
            else:
                nr = compute_next(job, after=_now())
                for j in self.jobs:
                    if j.get("id") == job.get("id"):
                        j["next_run"] = nr.isoformat() if nr else None
            self._save()


_SHARED: Schedule | None = None


def shared_schedule() -> Schedule:
    """One Schedule per process, shared by the SchedulerAgent (writes) and the Runner (fires)."""
    global _SHARED
    if _SHARED is None:
        _SHARED = Schedule()
    return _SHARED


def _notify(title: str, body: str) -> None:
    try:
        subprocess.Popen(["notify-send", "-a", "Jarvis", title, body[:300]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


class Runner(threading.Thread):
    """Background thread: fire due jobs (speak + notify + log) and reschedule recurring ones.

    `speak(text)` speaks via TTS; `briefing(query)` runs a research lookup and returns a summary.
    Both are injected by the voice service so the scheduler can act on its own.
    """

    def __init__(self, schedule: Schedule, speak, briefing=None, interval: float = 20.0) -> None:
        super().__init__(daemon=True, name="yggdrasil-scheduler")
        self.schedule = schedule
        self.speak = speak
        self.briefing = briefing
        self.interval = interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                for job in self.schedule.due():
                    self._fire(job)
                    self.schedule.reschedule_or_remove(job)
            except Exception as e:  # never let the scheduler thread die
                print(f"[scheduler] {e!r}", file=sys.stderr, flush=True)

    def _fire(self, job: dict) -> None:
        label = job.get("label", "reminder")
        if job.get("kind") == "briefing" and self.briefing:
            try:
                summary = self.briefing(job.get("query", label)) or "I couldn't fetch that just now."
            except Exception:
                summary = "I couldn't fetch your briefing right now."
            text = f"Here's your {label}. {summary}"
        else:
            text = job.get("message") or f"Reminder: {label}."
        print(f"[scheduler] firing {label!r}: {text}", file=sys.stderr, flush=True)
        _notify(label.title(), text)
        try:
            self.speak(text)
        except Exception as e:
            print(f"[scheduler] speak failed: {e!r}", file=sys.stderr, flush=True)
