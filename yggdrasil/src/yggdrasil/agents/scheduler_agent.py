"""Scheduler Agent: turn "schedule …" / "remind me …" into persistent jobs (reminders + briefings).

The local LLM parses the request into a structured spec (schema-constrained — it only has to produce
fields, not compute dates), then this agent does the actual time math in Python. Jobs are stored via
the shared Schedule; the Runner in the voice service fires them. Managed by voice: list / cancel.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..core.permissions import Capability
from ..core.scheduler import Schedule, compute_next, shared_schedule
from .base import BaseAgent

_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "kind": {"type": "string", "enum": ["reminder", "briefing"]},
        "message": {"type": "string"},
        "query": {"type": "string"},
        "recurrence": {"type": "string", "enum": ["once", "daily", "weekdays", "weekly", "hourly"]},
        "time": {"type": "string"},
        "weekday": {"type": "string"},
        "day_offset": {"type": "integer"},
        "in_minutes": {"type": "integer"},
        "lead_minutes": {"type": "integer"},
    },
    "required": ["label", "kind", "recurrence"],
}


class SchedulerAgent(BaseAgent):
    domain = "schedule"
    module_id = "core.schedule"
    planner_examples = [
        'remind me to call mom at 5pm -> {"steps":[{"action":"schedule.add","argument":"remind me to call mom at 5pm"}]}',
        'schedule the bitcoin report every weekday at 9am -> {"steps":[{"action":"schedule.add","argument":"the bitcoin report every weekday at 9am"}]}',
        'every morning at 8 tell me the weather in denver -> {"steps":[{"action":"schedule.add","argument":"every morning at 8 tell me the weather in denver"}]}',
        'what do I have scheduled -> {"steps":[{"action":"schedule.list","argument":""}]}',
        'cancel the bitcoin report -> {"steps":[{"action":"schedule.cancel","argument":"bitcoin report"}]}',
    ]
    capabilities = {
        "add": Capability("add", False, "Schedule a reminder or recurring briefing"),
        "list": Capability("list", False, "List scheduled reminders and briefings"),
        "cancel": Capability("cancel", False, "Cancel a scheduled item"),
    }

    def __init__(self, bus, perms, llm=None, store: Schedule | None = None) -> None:
        super().__init__(bus, perms)
        self.llm = llm
        self.store = store or shared_schedule()

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        arg = (params.get("argument") or "").strip()
        if verb == "add":
            return {"speech": await self._add(arg)}
        if verb == "list":
            return {"speech": self._list()}
        if verb == "cancel":
            return {"speech": self._cancel(arg)}
        raise ValueError(f"unhandled verb '{verb}'")

    async def _add(self, request: str) -> str:
        if not request:
            return "What would you like me to schedule?"
        if not self.llm:
            return "I need a language model to understand schedules."
        spec = await self._parse(request)
        if not spec:
            return "Sorry, I couldn't work out the timing for that."
        job = self._to_job(spec)
        if job is None or not job.get("next_run"):
            return ("I couldn't figure out when to do that — try a clearer time, "
                    "like 'every weekday at 9am' or 'tomorrow at 1pm'.")
        self.store.add(job)
        verb = "give you" if job["kind"] == "briefing" else "remind you about"
        article = "the " if job["recurrence"] != "once" else ""
        return f"Okay — I'll {verb} {article}{job['label']} {self._when(job)}."

    async def _parse(self, request: str) -> dict | None:
        now = dt.datetime.now()
        system = (
            "You convert a scheduling request into JSON for an assistant named Jarvis. "
            f"Right now it is {now:%A %Y-%m-%d %H:%M} local time. "
            "For a RECURRING request set recurrence to daily, weekdays, weekly or hourly and time to a "
            "24-hour HH:MM (for weekly also set weekday mon..sun). For a ONE-OFF set recurrence 'once' "
            "and EITHER day_offset (0=today, 1=tomorrow, 2=...) with time HH:MM, OR in_minutes for "
            "'in N minutes/hours'. If they want reminding BEFORE an event, set lead_minutes "
            "('an hour before' = 60). Use kind 'briefing' for things to look up (price, weather, news) "
            "and set query to a short web query like 'price of bitcoin'; otherwise kind 'reminder' and "
            "set message to the full natural sentence Jarvis should SAY when it fires. label is a short "
            "name. /no_think"
        )
        try:
            resp = await self.llm.generate(system=system, prompt=request, schema=_SCHEMA, temperature=0.1)
            return resp.parsed if isinstance(resp.parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _to_job(spec: dict) -> dict | None:
        job = {
            "label": (spec.get("label") or "reminder").strip(),
            "kind": spec.get("kind", "reminder"),
            "message": (spec.get("message") or "").strip(),
            "query": (spec.get("query") or "").strip(),
            "recurrence": spec.get("recurrence", "once"),
            "time": (spec.get("time") or "").strip(),
            "weekday": (spec.get("weekday") or "").strip().lower()[:3],
        }
        if job["recurrence"] == "once":
            now = dt.datetime.now()
            lead = int(spec.get("lead_minutes") or 0)
            if spec.get("in_minutes"):
                base = now + dt.timedelta(minutes=int(spec["in_minutes"]))
            else:
                hh, _, mm = (job["time"] or "09:00").partition(":")
                base = (now + dt.timedelta(days=int(spec.get("day_offset") or 0))).replace(
                    hour=int(hh or 9), minute=int(mm or 0), second=0, microsecond=0)
            fire = base - dt.timedelta(minutes=lead)
            if fire <= now:
                return None
            job["next_run"] = fire.isoformat()
            if job["kind"] == "reminder" and not job["message"]:
                job["message"] = f"Reminder: {job['label']}."
        else:
            nr = compute_next(job)
            job["next_run"] = nr.isoformat() if nr else None
        return job

    @staticmethod
    def _when(job: dict) -> str:
        nr = dt.datetime.fromisoformat(job["next_run"])
        t = nr.strftime("%I:%M %p").lstrip("0")
        rec = job["recurrence"]
        if rec == "once":
            today = dt.date.today()
            day = ("today" if nr.date() == today else
                   "tomorrow" if nr.date() == today + dt.timedelta(days=1) else nr.strftime("%A"))
            return f"{day} at {t}"
        return {"weekdays": f"every weekday at {t}", "daily": f"every day at {t}",
                "hourly": "every hour", "weekly": f"every {nr.strftime('%A')} at {t}"}.get(rec, f"at {t}")

    def _list(self) -> str:
        jobs = self.store.list()
        if not jobs:
            return "You don't have anything scheduled."
        parts = [f"{j['label']} {self._when(j)}" for j in jobs[:8]]
        return f"You have {len(jobs)} scheduled: " + "; ".join(parts) + "."

    def _cancel(self, query: str) -> str:
        hit = self.store.cancel(query)
        if not hit:
            return f"I didn't find anything scheduled matching '{query}'."
        return "Cancelled " + ", ".join(j["label"] for j in hit) + "."
