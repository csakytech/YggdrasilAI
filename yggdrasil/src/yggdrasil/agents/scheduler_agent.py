"""Scheduler Agent: turn "schedule …" / "remind me …" into persistent jobs (reminders + briefings).

The local LLM parses the request into a structured spec (schema-constrained — it only has to produce
fields, not compute dates), then this agent does the actual time math in Python. Jobs are stored via
the shared Schedule; the Runner in the voice service fires them. Managed by voice: list / cancel.
"""
from __future__ import annotations

import datetime as dt
import re
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
        job = self._to_job(spec, request)
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
            "Convert the scheduling request into JSON. "
            f"Current time: {now:%A %Y-%m-%d %H:%M} local.\n"
            "Examples:\n"
            'request: "the bitcoin report every weekday at 9am"\n'
            'json: {"label":"bitcoin report","kind":"briefing","query":"price of bitcoin",'
            '"recurrence":"weekdays","time":"09:00"}\n'
            'request: "every morning at 8 give me the weather in denver"\n'
            'json: {"label":"denver weather","kind":"briefing","query":"weather in denver",'
            '"recurrence":"daily","time":"08:00"}\n'
            'request: "remind me to call mom at 5pm"\n'
            'json: {"label":"call mom","kind":"reminder","message":"Reminder to call mom.",'
            '"recurrence":"once","day_offset":0,"time":"17:00"}\n'
            'request: "meeting with mom tomorrow at 1pm, remind me an hour before"\n'
            'json: {"label":"meeting with mom","kind":"reminder",'
            '"message":"Your meeting with mom is in an hour, at 1 PM.",'
            '"recurrence":"once","day_offset":1,"time":"13:00","lead_minutes":60}\n'
            'request: "remind me to take my pills in 2 hours"\n'
            'json: {"label":"pills","kind":"reminder","message":"Reminder to take your pills.",'
            '"recurrence":"once","in_minutes":120}\n'
            "Rules: weekdays = Monday–Friday. ALWAYS set time as HH:MM 24-hour unless using in_minutes. "
            "For kind briefing ALWAYS set query to a short web search. day_offset 0=today, 1=tomorrow. "
            "Use lead_minutes for 'before' reminders. /no_think"
        )
        try:
            resp = await self.llm.generate(system=system, prompt=f'request: "{request}"\njson:',
                                           schema=_SCHEMA, temperature=0.0)
            return resp.parsed if isinstance(resp.parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _to_job(spec: dict, request: str = "") -> dict | None:
        job = {
            "label": (spec.get("label") or "reminder").strip(),
            "kind": spec.get("kind", "reminder"),
            "message": (spec.get("message") or "").strip(),
            "query": (spec.get("query") or "").strip(),
            "recurrence": spec.get("recurrence", "once"),
            "time": (spec.get("time") or "").strip(),
            "weekday": (spec.get("weekday") or "").strip().lower()[:3],
        }
        if job["kind"] == "briefing" and not job["query"]:  # small model sometimes drops it
            job["query"] = job["label"] or request.strip()
        if not job["time"] and not spec.get("in_minutes"):  # model dropped the clock time — recover it
            job["time"] = SchedulerAgent._extract_time(request)
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
                at = base.strftime("%I:%M %p").lstrip("0")
                job["message"] = (f"Heads up — {job['label']} at {at}." if lead
                                  else f"Reminder: {job['label']}.")
        else:
            nr = compute_next(job)
            job["next_run"] = nr.isoformat() if nr else None
        return job

    @staticmethod
    def _extract_time(text: str) -> str:
        """Best-effort clock time from natural text -> 'HH:MM' (24h), '' if none."""
        t = text.lower()
        if "noon" in t:
            return "12:00"
        if "midnight" in t:
            return "00:00"
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", t)
        if m:
            h = int(m.group(1)) % 12 + (12 if m.group(3).startswith("p") else 0)
            return f"{h:02d}:{int(m.group(2) or 0):02d}"
        m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
        return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""

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
