"""Orchestrator: goal -> plan -> dispatch -> authorize -> results, with memory + chat.

If the goal maps to agent actions, they run (with the authorization-code flow for dangerous
ones). If it doesn't, and an LLM is available, Yggdrasil answers conversationally using its
persisted memory — so it can hold a conversation and "know" the user across sessions.
"""
from __future__ import annotations

import copy
import re
import sys
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from . import config, trace
from .bus import Bus, Result, Status, Task
from .focus import active_window
from .llm import LLMProvider
from .permissions import AuthChallenge, PermissionManager

# Supplied by the CLI (stdin) or the voice loop (speech): given a challenge, return the code.
AuthResolver = Callable[[AuthChallenge], Awaitable[str]]


def _params_for(action: str, argument: str) -> dict:
    """Map the planner's generic 'argument' to the param each domain expects."""
    domain = action.split(".", 1)[0]
    if domain == "file":
        return {"path": argument}
    if domain == "memory":
        return {"text": argument}
    return {"argument": argument}


class Planner(ABC):
    @abstractmethod
    async def plan(self, goal: str, memory_context: str = "", active: tuple = ("", "")) -> list[Task]: ...


class HeuristicPlanner(Planner):
    """No-model fallback: pattern-matches the common verbs so the spine runs without Ollama."""

    _CREATE = re.compile(r"(?:create|make|new)\s+(?:a\s+)?folder\s+(?:called\s+|named\s+)?(.+)", re.I)
    _DELETE = re.compile(r"(?:delete|remove)\s+(?:the\s+)?(?:file\s+|folder\s+)?(.+)", re.I)
    _OPEN = re.compile(r"open\s+(?:the\s+)?(?:folder\s+|file\s+)?(.+)", re.I)
    _LIST = re.compile(r"(?:list|show|what(?:'s| is) in)\s+(?:the\s+)?(?:contents of\s+)?(.+)", re.I)
    _REMEMBER = re.compile(r"(?:remember that|remember|note that)\s+(.+)", re.I)

    async def plan(self, goal: str, memory_context: str = "", active: tuple = ("", "")) -> list[Task]:
        g = goal.strip().rstrip(".")
        for pat, action in (
            (self._CREATE, "file.create_folder"),
            (self._DELETE, "file.delete"),
            (self._OPEN, "file.open"),
            (self._LIST, "file.list"),
            (self._REMEMBER, "memory.remember"),
        ):
            m = pat.search(g)
            if m:
                arg = m.group(1).strip().strip("\"'")
                domain = action.split(".", 1)[0]
                return [Task(action=action, agent=domain, params=_params_for(action, arg))]
        return []


PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "argument": {"type": "string"},
                    "argument2": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["action"],
            },
        }
    },
    "required": ["steps"],
}

_PLANNER_BASE = (
    "You are Yggdrasil's task planner. Output JSON only: an ordered list of steps using ONLY "
    "the allowed actions, each with an 'argument' (a folder name, or the thing to remember). "
    "If the request is a question, greeting, or small talk — NOT an action — return an empty "
    "steps list. Examples:"
)
# Negative examples are always present; positive examples come from the active agents (so
# installing an agent extends the planner — see core/registry.py and docs/MODULES.md §9).
_PLANNER_NEGATIVE = [
    'what is my name -> {"steps":[]}',
    'how are you -> {"steps":[]}',
    'dance a jig -> {"steps":[]}',
    # Open-ended "build/make/create me a program/app/tool" is NOT "launch an app" — return no steps so
    # the assistant backbone handles it honestly (offer to scaffold files, open an editor, etc.).
    'build me a budgeting program -> {"steps":[]}',
    'create an app that tracks my expenses -> {"steps":[]}',
    'make me a tool to organize my photos -> {"steps":[]}',
]


class LLMPlanner(Planner):
    """Schema-constrained planner. ``action`` is restricted to the available tools (enum); the
    few-shot ``examples`` are supplied by the registry from each active agent's manifest."""

    def __init__(self, llm: LLMProvider, allowed_actions: list[str], examples=None) -> None:
        self.llm = llm
        self.allowed_actions = allowed_actions
        self.examples = list(examples or [])

    async def plan(self, goal: str, memory_context: str = "", active: tuple = ("", "")) -> list[Task]:
        schema = copy.deepcopy(PLAN_SCHEMA)
        schema["properties"]["steps"]["items"]["properties"]["action"] = {
            "type": "string",
            "enum": self.allowed_actions,
        }
        system = _PLANNER_BASE + "\n" + "\n".join(self.examples + _PLANNER_NEGATIVE)
        name, kind = active if isinstance(active, (tuple, list)) and len(active) == 2 else ("", "")
        if kind == "terminal":
            system += (f"\nThe user is focused on a TERMINAL ({name}). Shell-style requests like "
                       "'list files', 'show processes', 'clear', 'go up a folder' must be "
                       "`focus.enter` with the equivalent shell command (ls, ps aux, clear, cd ..).")
        elif kind == "browser":
            system += (f"\nThe user is focused on a BROWSER ({name}). 'go to X' -> app.browse(X); "
                       "'search X' -> app.search(X).")
        elif kind:
            system += f"\nThe user is focused on a {kind} window ({name})."
        if memory_context:
            system += f"\nWhat you know about the user:\n{memory_context}"
        system += "\n/no_think"  # qwen3: skip the reasoning phase so it can't leak into arguments
        resp = await self.llm.generate(system=system, prompt=goal, schema=schema)
        steps = (resp.parsed or {}).get("steps", [])
        tasks: list[Task] = []
        for s in steps:
            action = s.get("action", "")
            arg = (s.get("argument") or s.get("path") or "").strip()
            arg = re.sub(r"</?think>|/no_?think", "", arg, flags=re.I).strip()  # belt-and-suspenders
            domain = action.split(".", 1)[0] if "." in action else action
            params = _params_for(action, arg)
            if domain == "file":
                if s.get("argument2"):
                    params["dest"] = str(s["argument2"]).strip()
                if s.get("content") is not None:
                    params["content"] = str(s["content"])
            else:  # generic passthrough so any agent can take a second argument (e.g. documents.save)
                if s.get("argument2"):
                    params["argument2"] = str(s["argument2"]).strip()
                if s.get("content") is not None:
                    params["content"] = str(s["content"])
            tasks.append(Task(action=action, agent=domain, params=params))
        return tasks


_PRONOUNS = {"it", "that", "this", "them", "the folder", "the file"}
_PRONOUN_GOAL = re.compile(
    r"^(open|list|show|delete|remove)\s+(it|that|this|them|the folder|the file)\s*$", re.I
)
_THINK = re.compile(r"<think>.*?</think>", re.S)  # strip qwen3 reasoning if it leaks

# "Why did you do that?" is a meta-question about my own last action. Route it deterministically
# to the Explain agent rather than trusting the planner (which is flaky on these). Tuned to NOT
# catch general questions like "why is the sky blue" or "explain photosynthesis".
_EXPLAIN_RE = re.compile(
    r"^\s*(why\??$|why did (you|it|that)|why'd you|why that\b|"
    r"explain (that|why|your|the last|what you|yourself)|"
    r"what were you thinking|how did you (decide|know|choose|pick|do)|"
    r"what made you|how come you)",
    re.I,
)

# "Call yourself Athena" — rename the assistant (the name is also the wake word). Pre-checked so
# it's reliable, and so the new name takes effect immediately.
_RENAME_RE = re.compile(
    r"^\s*(?:change your name to|call yourself|rename yourself to|set your name to|"
    r"your name (?:is|will be)(?: now)?|from now on,?\s+(?:you'?re|your name is)|i'?ll call you)\s+(.+)$",
    re.I,
)

# Current-info questions ("price of bitcoin", "weather in Seattle", "news on Tesla") go to the
# Research agent deterministically — the planner is flaky on these and might answer from stale model
# knowledge or open a browser. Excludes "remember/open …" by requiring a lookup lead-in or a bare
# "price of / weather in / news on X" shape.
_RESEARCH_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please |any |some |the latest )?(?:"
    r"(?:check|get me|look up|find out|tell me|what'?s|what is|how'?s|how is|how much (?:is|are))\b"
    r".*\b(?:price|worth|value|cost|weather|forecast|temperature|news|headlines?|trading)\b"
    r"|(?:the\s+)?(?:price|value) of \w+"
    r"|weather (?:in|for|at|like in) \w+"
    r"|news (?:on|about|regarding) \w+"
    r")",
    re.I,
)

# "Remind me… / schedule… / every weekday at 9am…" -> the Scheduler agent (creates a reminder or a
# recurring briefing). Checked before _RESEARCH_RE so "schedule the bitcoin report" schedules it
# instead of looking it up once.
_SCHEDULE_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please )?(?:"
    r"remind me\b|set (?:a |an )?reminder\b|schedule\b|wake me\b|"
    r"every (?:morning|day|night|evening|weekday|weekend|hour|other day|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r")",
    re.I,
)

# Marketplace voice flow -> the Market agent. Routed deterministically (the planner is unreliable on
# these meta-commands). Install/remove/browse REQUIRE the word "agent"/"module"/"marketplace" so they
# never collide with installing an app (the future Software agent) or with general yes/no in chat.
_MKT_CONFIRM = re.compile(
    r"^\s*(?:yes|yeah|yep|sure|okay|ok|confirm|go ahead|do it|please do|absolutely)\b"
    r".*\b(?:install|remove|uninstall)\b", re.I)
_MKT_CANCEL = re.compile(r"^\s*(?:cancel|never ?mind|forget it|don'?t (?:install|remove))\b", re.I)
_MKT_REMOVE = re.compile(r"\b(?:remove|uninstall|delete)\s+(?:the\s+)?(.+?)\s+(?:agent|module)s?\b", re.I)
_MKT_INSTALL = re.compile(r"\b(?:install|add|download|set up|get)\s+(?:the\s+|an?\s+)?(.+?)\s+(?:agent|module)s?\b", re.I)
_MKT_INSTALLED = re.compile(
    r"\b(?:installed|my)\s+(?:agent|module)s?\b|\bwhat (?:agent|module)s? do i have\b"
    r"|\b(?:agent|module)s? (?:i have|i've) installed\b", re.I)
_MKT_BROWSE = re.compile(r"\b(?:agent|module)s?\b", re.I)
_MKT_BROWSE_CUE = re.compile(r"\b(?:what|which|list|show|browse|search|find|are there|available|marketplace|market)\b", re.I)


def _market_route(goal: str):
    """Classify a marketplace command into (verb, argument), or None if it isn't one."""
    g = goal.strip()
    gl = g.lower()
    if _MKT_CONFIRM.match(g):
        return ("confirm", "")
    if _MKT_CANCEL.match(g):
        return ("cancel", "")
    m = _MKT_REMOVE.search(g)
    if m:
        return ("remove", m.group(1).strip())
    m = _MKT_INSTALL.search(g)
    if m:
        return ("install", m.group(1).strip())
    if _MKT_INSTALLED.search(g):
        return ("installed", "")
    if "marketplace" in gl or (_MKT_BROWSE.search(gl) and _MKT_BROWSE_CUE.search(gl)):
        fm = re.search(r"\bfor\s+(.+)$", gl)
        return ("search", fm.group(1).strip(" .?") if fm else "")
    return None

_VERB_LABEL = {
    "run": "Running", "create_folder": "Creating", "create_file": "Creating",
    "write_file": "Writing", "append_file": "Updating", "read_file": "Reading",
    "delete": "Deleting", "open": "Opening", "list": "Listing", "move": "Moving",
    "rename": "Renaming", "copy": "Copying", "search": "Searching", "info": "Checking",
    "permissions": "Permissions", "audit": "Security audit", "updates": "Checking updates",
    "write_document": "Writing", "launch": "Opening", "list_apps": "Listing apps",
    "remember": "Remembering", "forget": "Forgetting", "recall": "Recalling",
    "time": "Clock", "disk": "Disk", "status": "System status", "running": "Processes",
    "autonomy": "Trust mode",
}


def _activity_label(task: Task) -> str:
    """A short human label for the HUD, e.g. 'Running top -d' / 'Creating reports'."""
    verb = task.action.split(".")[-1]
    arg = (task.params.get("argument") or task.params.get("path")
           or task.params.get("text") or "").strip()
    label = _VERB_LABEL.get(verb, verb.replace("_", " ").title())
    return f"{label} {arg}".strip() if arg else label


class Orchestrator:
    def __init__(
        self,
        bus: Bus,
        perms: PermissionManager,
        planner: Planner,
        auth_resolver: AuthResolver,
        memory=None,
        llm: LLMProvider | None = None,
        assistant_name: str = "Jarvis",
        activity=None,
    ) -> None:
        self.bus = bus
        self.perms = perms
        self.planner = planner
        self.auth_resolver = auth_resolver
        self.memory = memory
        self.llm = llm
        self.assistant_name = assistant_name
        self.activity = activity  # publishes "what I'm doing" for the HUD/dashboard
        self._last_path: str | None = None

    def _publish(self, text: str) -> None:
        if self.activity:
            self.activity.publish(text)

    async def handle(self, goal: str) -> str:
        # Never let a transient error (e.g. an LLM/network hiccup) crash the assistant.
        try:
            return await self._handle(goal)
        except Exception as e:  # noqa: BLE001
            print(f"[orchestrator] error handling goal: {e!r}", file=sys.stderr)
            msg = str(e).lower()
            if any(s in msg for s in ("not found", "404", "connect", "refus", "no such model")):
                return ("I'm still getting set up — my language model may still be downloading. "
                        "Give me a few minutes, then try again.")
            try:  # don't dead-end on an error — let the backbone still try to help
                ctx = self.memory.context() if self.memory else ""
                return await self._assist(goal, ctx, problem="an internal error")
            except Exception:
                return ("I couldn't complete that one, but I can help another way — I work with files "
                        "and folders, apps, web search, lookups, reminders and memory. What do you need?")

    async def _handle(self, goal: str) -> str:
        goal = self._rewrite_pronouns(goal)
        if _EXPLAIN_RE.match(goal.strip()):  # "why did you…" -> explain my last action, reliably
            self._publish("")
            task = Task(action="explain.why", agent="explain", params={"argument": ""})
            return self._render(task, await self._dispatch(task))
        rn = _RENAME_RE.match(goal.strip())
        if rn:  # "call yourself Athena" -> rename (the name is also the wake word)
            self._publish("")
            raw = re.sub(r"\b(please|thanks|thank you|now|okay|ok)\b", "", rn.group(1), flags=re.I)
            new = config.set_name(raw)
            return f"Okay — I'm {new} now. Just say “{new}” to get my attention."
        mkt = _market_route(goal)  # "install the X agent" / "what agents are available" / "yes install it"
        if mkt:
            verb, arg = mkt
            self._publish("Marketplace…")
            task = Task(action=f"market.{verb}", agent="market", params={"argument": arg})
            return self._render(task, await self._dispatch(task))
        if _SCHEDULE_RE.match(goal.strip()):  # "remind me…" / "schedule…" / "every weekday at 9…"
            self._publish("Scheduling…")
            task = Task(action="schedule.add", agent="schedule", params={"argument": goal})
            return self._render(task, await self._dispatch(task))
        if _RESEARCH_RE.match(goal.strip()):  # "price of bitcoin" / "weather in X" / "news on Y"
            self._publish("Looking that up…")
            task = Task(action="research.lookup", agent="research", params={"argument": goal})
            return self._render(task, await self._dispatch(task))
        ctx = self.memory.context() if self.memory else ""
        self._publish("Thinking…")
        active = active_window()
        tasks = await self.planner.plan(goal, memory_context=ctx, active=active)
        print(f"[plan] active={active} goal={goal!r} -> {[t.action for t in tasks]}",
              file=sys.stderr, flush=True)
        if not tasks:
            self._publish("")
            reply = await self._assist(goal, ctx)
            trace.record(trace.Decision(goal=goal, active=active, memory_used=bool(ctx),
                                        route="conversation", outcome=reply))
            return reply
        replies, steps, ok, any_ok, denied = [], [], True, False, False
        for task in tasks:
            self._resolve_pronoun(task)
            self._publish(_activity_label(task))
            result = await self._dispatch(task)
            if result.status is Status.OK and task.params.get("path"):
                self._last_path = task.params["path"]
            steps.append({
                "action": task.action,
                "arg": (task.params.get("argument") or task.params.get("path")
                        or task.params.get("text") or ""),
                "status": result.status.name,
            })
            # An agent can return OK but flag `assist` ("I ran, but couldn't really help") — e.g. a
            # launch request that isn't a real app. Treat that as not-handled so the backbone steps in.
            wants_assist = isinstance(result.data, dict) and result.data.get("assist")
            if result.status is Status.OK and not wants_assist:
                any_ok = True
            if result.status is not Status.OK:
                ok = False
                denied = denied or result.status is Status.DENIED
            replies.append(self._render(task, result))
        self._publish("")  # done — let the HUD fade out
        # Never dead-end: if nothing worked (and the user didn't deliberately cancel), hand off to the
        # backbone to explain and offer a path forward instead of a flat "I couldn't do that".
        if not any_ok and not denied:
            reply = await self._assist(goal, ctx, problem="; ".join(s["action"] for s in steps))
        else:
            reply = " ".join(replies)
        trace.record(trace.Decision(goal=goal, active=active, memory_used=bool(ctx),
                                    route="action", steps=steps, outcome=reply, ok=ok))
        return reply

    async def _assist(self, goal: str, ctx: str, problem: str = "") -> str:
        """The fallback backbone — Jarvis's job is to help to the maximum, so this NEVER dead-ends.
        It answers questions, points the user at the right skill, or honestly says what isn't possible
        yet and offers the nearest thing it CAN do. Capability-aware, honest, and spoken-friendly."""
        if not self.llm:
            return ("I can't do that one directly yet, but I can work with files and folders, open and "
                    "close apps, search the web, look things up, set reminders, and remember things. "
                    "Which of those would help?")
        caps = ""
        acts = getattr(self.planner, "allowed_actions", None)
        if acts:
            caps = "Skills you can actually run (action ids): " + ", ".join(sorted(acts)) + ".\n"
        system = (
            f"You are {config.get_name()}, a capable local voice assistant, and your job is to help to "
            "the maximum. The request below was not handled by a specific skill. RULES: never give a "
            "dead-end answer like 'I can't' or 'please try again' and stop. Always do ONE of: (1) answer "
            "it directly if it's a question; (2) if it maps to one of your skills, tell the user the "
            "simple thing to say to trigger it; (3) if it isn't possible yet, say so honestly in one "
            "breath and immediately offer the closest thing you CAN do, or a concrete next step. Be "
            "honest — never claim you did something you didn't. Speak naturally, no markdown or lists, "
            "brief but genuinely useful.\n" + caps
        )
        if ctx:
            system += f"What you know about the user:\n{ctx}\n"
        if problem:
            system += f"(A skill just failed — {problem}. Help the user move forward anyway.)\n"
        system += "/no_think"
        resp = await self.llm.generate(system=system, prompt=goal, temperature=0.4)
        return (_THINK.sub("", resp.text).strip()
                or "Let me help another way — tell me what you're trying to get done.")

    def _rewrite_pronouns(self, goal: str) -> str:
        if self._last_path:
            m = _PRONOUN_GOAL.match(goal.strip().rstrip("."))
            if m:
                return f"{m.group(1)} {self._last_path}"
        return goal

    def _resolve_pronoun(self, task: Task) -> None:
        if not task.action.startswith("file."):
            return
        path = (task.params.get("path") or "").strip()
        if (not path or path.lower() in _PRONOUNS) and self._last_path:
            task.params["path"] = self._last_path

    async def _dispatch(self, task: Task) -> Result:
        result = await self.bus.request(task.agent, task)
        if result.status is Status.AWAITING_AUTH and result.challenge is not None:
            code = await self.auth_resolver(result.challenge)
            token = self.perms.verify(result.challenge.challenge_id, code)
            if token is None:
                return Result(task.task_id, Status.DENIED, agent=task.agent,
                              error="authorization failed or timed out")
            task.auth_token = token
            result = await self.bus.request(task.agent, task)
        return result

    @staticmethod
    def _render(task: Task, result: Result) -> str:
        verb = task.action.split(".")[-1]
        data = result.data if isinstance(result.data, dict) else {}
        name = data.get("name") or task.params.get("path") or "it"
        if result.status is Status.OK:
            # Generic hook: any module can return a ready-to-speak string, so the orchestrator
            # never needs to know a new agent's verbs (see docs/MODULES.md).
            if data.get("speech"):
                return data["speech"]
            if verb == "remember":
                return "I'll remember that."
            if verb == "forget":
                return "Forgotten." if data.get("forgot") else "I didn't have anything like that."
            if verb == "recall":
                facts = data.get("facts", [])
                return ("Here's what I remember: " + "; ".join(facts) + ".") if facts \
                    else "I don't know much about you yet."
            if data.get("missing"):
                return f"I couldn't find {name}."
            if verb == "list":
                items = data.get("items", [])
                return (f"{name} contains: " + ", ".join(items) + ".") if items else f"{name} is empty."
            if verb == "open":
                if data.get("no_display"):
                    return "I can only open a window when you're signed in at the desktop."
                return f"Opened {name}."
            if verb == "delete":
                return f"Deleted {name}."
            if verb == "create_folder":
                return f"Created {name}."
            return "Done."
        if result.status is Status.DENIED:
            return f"Cancelled. {result.error}." if result.error else "Cancelled."
        if result.status is Status.TIMEOUT:
            return "That took too long."
        if "sandbox" in (result.error or "").lower():
            return "I can only work inside my workspace, so I can't reach that path."
        return "Sorry, I couldn't do that one."
