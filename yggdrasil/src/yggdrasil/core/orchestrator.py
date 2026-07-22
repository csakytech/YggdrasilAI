"""Orchestrator: goal -> plan -> dispatch -> authorize -> results, with memory + chat.

If the goal maps to agent actions, they run (with the authorization-code flow for dangerous
ones). If it doesn't, and an LLM is available, Yggdrasil answers conversationally using its
persisted memory — so it can hold a conversation and "know" the user across sessions.
"""
from __future__ import annotations

import copy
import os
import re
import sys
from collections import deque
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

import time as _time

from . import config, jobs, journal, mission, trace, transcript
from . import models as models_mod
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
    "steps list. "
    "FINISH THE GOAL, not just the setup: the user states an outcome, you handle the "
    "logistics AND deliver the outcome. If they want to start DOING something (writing, "
    "browsing, editing), the LAST step opens the tool or thing they need — folders alone "
    "leave them stranded. Examples:"
)
# Negative examples are always present; positive examples come from the active agents (so
# installing an agent extends the planner — see core/registry.py and docs/MODULES.md §9).
_PLANNER_NEGATIVE = [
    'what is my name -> {"steps":[]}',
    'how are you -> {"steps":[]}',
    'dance a jig -> {"steps":[]}',
    # "build/make/create me a program/app/tool" routes to Development Mode (dev.enter) —
    # positive examples come from the Dev agent's manifest.
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
# Answers to a pending "…? Say yes or no." prompt (e.g. confirming a delete).
_YES_RE = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok|okay|correct|do it|go ahead|confirm|please do|"
                     r"that'?s right|affirmative)\b", re.I)
_NO_RE = re.compile(r"^\s*(no|nope|nah|don'?t|do not|cancel|stop|negative|never ?mind)\b", re.I)

# Hard safety backstop: catastrophic intent (wipe the disk, delete all my files, rm -rf /) gets a fixed
# refusal and NEVER reaches the model — an LLM instruction must not be the only thing between the user
# and a "how to destroy your system" answer. Tuned to whole-system / whole-account scope, so "delete the
# files in this folder" still works.
_DANGER_RE = re.compile(
    r"\b(?:erase|wipe|format|reformat|destroy|nuke)\b[\w\s]*\b(?:hard ?drive|disk|drive|"
    r"entire (?:system|computer)|the system|operating system|my computer)\b"
    r"|\b(?:delete|remove|erase|wipe)\b[\w\s]*\ball (?:my|the) (?:files|data)\b"
    r"|\brm\s+-rf\s*(?:/(?:\s|$)|~|\*|\$HOME)",
    re.I)

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
# Rename must catch polite/wordy phrasings ("CAN YOU change your name to Data SO THAT you're
# known as Data FROM NOW ON") — if it slips through to the LLM, the model tends to invent a
# "I can't change my name" limitation that doesn't exist. Trailing purpose-clauses are cut
# from the captured name by _RENAME_TRAIL.
_RENAME_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |will you |would you |please |i want you to )*"
    r"(?:change your name to|call yourself|rename yourself(?: to| as)?|set your name to|"
    r"your name (?:is|will be)(?: now)?|from now on,?\s+(?:you'?re|your name is|call yourself)|"
    r"i(?:'?ll| will| want to| wanna) call you|we(?:'?ll| will) call you|"
    r"you(?:'?ll| will| should| can)? ?(?:now )?(?:be|are) (?:called|named|known as)|"
    r"go by(?: the name(?: of)?)?)\s+(.+)$",
    re.I,
)
# Cut "so that…", "from now on", etc. off the captured name ("Data so that…" -> "Data").
_RENAME_TRAIL = re.compile(
    r"\b(?:so that|so you|so we|so i|because|since|instead|from now on|that way|going forward|"
    r"and (?:answer|respond|reply))\b.*$", re.I)

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

# "Repeat that / say that again / what did you say" -> re-speak the last reply, verbatim and
# deterministically. Essential with full duplex: a barge-in legitimately CUTS a reply
# mid-sentence, so asking for it again is the most natural utterance in the system — it must
# never depend on the planner guessing.
_REPEAT_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?:can you |could you |would you |please )*"
    r"(?:repeat (?:that|it|this|what you (?:just )?said)|say (?:that|it) again|"
    r"what did you (?:just )?say|come again)\b",
    re.I,
)

# "How's the install going / what are you working on / are you still working on that" -> read
# the TRUTH from the background-jobs registry, never the language model (which fabricated "I'm
# still trying to install it" with no basis — the live bug this fixes). Also "open the tasks
# window".
_JOBS_STATUS_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?:can you |could you |please )?(?:"
    r"(?:how(?:'s| is| are| did))\b.{0,40}\b(?:going|install|installing|download|coming along|progress|do)"
    r"|what(?:'s| are| is)? (?:you|jarvis)? ?(?:working on|doing|up to)\b"
    r"|are you (?:still |done )?(?:working|installing|downloading|busy)"
    r"|are you (?:done|finished|still going)\b"
    r"|what(?:'s| is) (?:the )?(?:status|progress)\b"
    r")",
    re.I,
)
_JOBS_WINDOW_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?:can you |could you |please )?"
    r"(?:open|show|bring up|pull up|display)\s+(?:the\s+)?(?:tasks?|jobs?|activity|work)\s+"
    r"(?:window|list|panel)?\b", re.I)

# "What am I looking at / what's on my screen / read the screen / what does this say" -> the
# Vision agent looks at the screen with a local multimodal model. Deterministic because the
# text planner has no concept of sight and would either refuse or hallucinate. Excludes
# file-reading ("read the file"), which is the Documents agent, and web pages.
_VISION_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?:can you |could you |please )*"
    r"(?!.*\b(?:file|document|folder|web ?page|website|url|out loud from)\b)"
    r"(?:"
    r"what(?:'s| is| am i| do you)?\s*(?:on |see|looking at|seeing)"
    r"|what(?:'s| is| does)\b.{0,40}\b(?:on (?:the |my )?screen|this (?:say|mean|error|window|button|icon))"
    r"|(?:read|look at|describe|check|see)\s+(?:the\s+|my\s+|this\s+)?screen"
    r"|read (?:this|what'?s on screen|the error|it) (?:aloud|out loud|to me)?"
    r"|(?:can you )?see (?:this|that|the screen|my screen|what'?s on|what this)"
    r"|describe (?:what'?s on |)(?:the |my )?screen"
    r")",
    re.I,
)

# "Click the <thing>" / "scroll down" -> the Vision agent grounds the element to a pixel and
# clicks (or scrolls). This is the CONTROL rung of sight. "click 4" / "select 4" stay the
# browser's link-number commands (numeric); "click the X button" is vision. Scroll is its own.
_VCLICK_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?:can you |could you |please |go ahead and )*"
    r"(?:click|press|tap|hit|push|select)\s+(?:on\s+)?"
    # exclusions anchored HERE (after the verb) so backtracking the optional article can't
    # escape them: link numbers stay the browser's job, key names are keystrokes not clicks.
    r"(?!(?:the\s+|that\s+|a\s+)?\d+\b)(?!link\s+\d)"
    r"(?!(?:the\s+|that\s+|a\s+)?(?:enter|return|escape|esc|tab|space ?bar|space|delete|"
    r"backspace|control|shift|alt|page ?up|page ?down|arrow)\b)"
    r"(?:the\s+|that\s+|a\s+)?"
    r"(.+?)(?:\s+(?:button|link|icon|tab|box|field|menu))?\s*$",
    re.I,
)
_VSCROLL_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?:can you |could you |please )?"
    r"scroll\s+(up|down|to the top|to the bottom|back up|a little|a lot|all the way)?\b",
    re.I,
)

# "Reboot this computer / shut down / put it to sleep" -> system.power, deterministically —
# behind a spoken yes/no. Left to the planner this misrouted into system.autonomy and flipped
# the security mode (live bug). Excludes "restart yourself/jarvis" (the assistant, not the
# machine) and app restarts ("restart firefox").
_POWER_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?:can you |could you |please |go ahead and )*"
    r"(?!.*\b(?:yourself|jarvis|assistant|firefox|browser|app|window|router|modem|phone)\b)"
    r"(?:"
    r"(?:reboot|restart|shut ?down|power (?:off|down)|turn off|suspend)\s+(?:th(?:is|e|at)\s+)?"
    r"(?:computer|machine|system|pc|box)\b"
    r"|put (?:th(?:is|e)\s+)?(?:computer|machine|system|pc)\s+to sleep\b"
    r"|(?:reboot|shut ?down)\s*$"
    r")",
    re.I,
)

# Machine questions ("what's my local IP", "how much memory does this system have", "find out
# the external IP") -> system.info, deterministically. Left to the planner these misroute
# (the live bug: "what is my local IP" got answered with the top running programs) — and an
# LLM asked for an IP will happily invent one. Guarded against web-search phrasings.
_SYSINFO_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?!.*\b(?:search|google|browse|look up|open|remind|schedule|every)\b)"
    r"(?:can you |could you |please |find out |check |tell me |show me |do you know )*"
    r"(?:what(?:'s| is| are)?|how much|how many|how big|how long|what kind of)?\s*"
    r".{0,30}?\b(?:"
    r"(?:local|internal|external|public|my)\s+ip\b|ip address\b|"
    r"memory(?: does| is| in)|ram\b|"
    r"cpu\b|processor\b|"
    r"gpu\b|graphics card\b|video card\b|"
    r"hostname\b|name of (?:this|the|my) (?:computer|machine)|"
    r"kernel\b|battery\b|uptime\b|"
    r"(?:which|what) (?:os|operating system|version of thoros)"
    r")",
    re.I,
)

# "Open ThorAI/assistant/voice settings" -> the ThorAI Settings window. Scoped to the
# assistant's own settings so it never shadows "open settings" (GNOME's system settings).
_SETTINGS_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?:can you |could you |please )?"
    r"(?:open|show|launch|bring up|pull up|go to)\s+"
    r"(?:the\s+)?(?:thor\s?ai|thoros|assistant|jarvis|voice)\s+settings\b",
    re.I,
)

# "Open google and search for X" (or "…and look up X") -> ONE app.search, deterministically.
# Left to the planner this becomes browse-google + search — two firefox invocations that race
# the browser's first startup, and the second (the actual search) gets silently dropped.
_OPEN_AND_SEARCH_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?\w+\s*,\s*)?(?:can you |could you |please )?"
    r"(?:open|go to|open up|start|launch)\s+(?:google|the browser|firefox)\s+and\s+"
    r"(?:search(?: the web)?(?: for)?|look up|google|find)\s+(.+?)\s*$",
    re.I,
)

# "Recommend/suggest software (an app, a program) for X" -> the Research agent's recommend flow,
# deterministically — left to the LLM planner this routes to app.search, which opens a browser
# with a Google search and dead-ends ("Searching the web for…" is not an answer). The research
# agent answers aloud and hands back a top pick the orchestrator offers to install.
_RECOMMEND_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:.{0,60}?\b(?:can|could|would) you\s+|please\s+)?(?:"
    r"(?:recommend|suggest)\b.{0,80}\b(?:software|apps?|application|programs?|tools?)\b"
    r"|(?:what|which)\s+(?:software|apps?|application|programs?|tools?)\b.{0,40}\b(?:install|use|get|download)\b"
    r"|(?:what|which)(?:'s| is)? (?:a |the )?(?:good|best)\s+(?:software|app|application|program|tool)\b"
    r"|(?:software|program|app|tool)s?\s+(?:should i|to)\s+(?:install|use|get)\b"
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

# "show / open / close my scheduled tasks" -> the Schedule window (NOT schedule.add). Checked before
# _SCHEDULE_RE so "show scheduled tasks" opens the window instead of trying to schedule something.
_SCHED_UI_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please )?"
    r"(show|open|display|view|pull up|bring up|see|close|hide|dismiss)\b.{0,30}?\bschedul", re.I)


# "Open the directory WHERE YOU PLACED those files" / "the folder you just made" -> the common
# parent of what this session created. References to my OWN recent work must never fail with
# "couldn't find that directory" — I know exactly what I made.
_WHERE_CREATED_RE = re.compile(
    r"\b(?:open|show(?: me| us)?|go to|take me to|bring up|pull up)\b.{0,50}?"
    r"(?:\bwhere\b.{0,30}\b(?:placed|created|put|saved|made)\b"
    r"|\b(?:director(?:y|ies)|folders?|files?)\b.{0,20}\byou (?:just )?(?:placed|created|put|saved|made)\b)",
    re.I)


# "SHOW me X" is a request to SEE something -> open the file manager (file.open), never a spoken
# listing. ("Read out / what's in X" stays spoken via file.list.) Requires an explicit
# folder/directory/file word so it can't hijack "show me the weather" or "show my agents".
_SHOW_FILES_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please |would you )?"
    r"(?:show (?:me|us)?|pull up|bring up)\s+(?:the |those |these |that |this |my )*"
    r"(.*?)\s*(folders?|director(?:y|ies)|files?)\s*(?:again\s*)?[.?!]?\s*$", re.I)
_SHOW_VAGUE = {"", "them", "all", "all of them", "all the", "everything"}


def _sched_ui_route(goal: str):
    m = _SCHED_UI_RE.match(goal.strip())
    if not m:
        return None
    return "hide" if m.group(1).lower() in ("close", "hide", "dismiss") else "show"


# "check for updates" / "update yourself" -> the Update agent.
_UPD_APPLY = re.compile(r"^\s*(?:can you |could you |please )?(?:update|upgrade)\s+"
                        r"(?:yourself|thor ?os|the (?:system|os)|jarvis)\b|\binstall (?:the )?update\b", re.I)
_UPD_CHECK = re.compile(r"\bcheck for updates?\b|\bare there (?:any )?updates?\b|"
                        r"\bis there (?:an?|a new) (?:update|version)\b|\bany (?:new )?updates?\b", re.I)


def _update_route(goal: str):
    g = goal.strip()
    if _UPD_APPLY.search(g):
        return "apply"
    if _UPD_CHECK.search(g):
        return "check"
    return None


# Model roles -> the Model agent ("what models do I have", "use qwen coder for coding",
# "download the X model"). List/pull/status require the word model/LLM so they never hijack
# ordinary requests; bind additionally requires a known role word ("for coding") plus a
# model-ish target, so "use the terminal for coding" can't stage a model download.
_MDL_ROLE_WORDS = "|".join(sorted(models_mod.ROLE_ALIASES, key=len, reverse=True))
_MDL_LIST = re.compile(
    r"\b(?:what|which|list|show)\b.{0,24}\b(?:models?|llms?)\b"
    r"(?:.{0,26}\b(?:installed|do i have|have|available|downloaded)\b)?", re.I)
_MDL_STATUS = re.compile(
    r"\b(?:what|which)\s+(?:model|llm)\b.{0,32}\b(?:you use|you'?re using|are you using|"
    r"do you use|handles?|runs?|is used)\b"
    r"|\bhow(?:'s| is| far)\b.{0,28}\b(?:model|download)\b", re.I)
_MDL_BIND = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please )?(?:use|switch to|set)\s+"
    r"(?:the\s+)?(.+?)\s+(?:model\s+)?(?:as|for)\s+(?:the\s+)?(" + _MDL_ROLE_WORDS + r")\b", re.I)
_MDL_PULL_A = re.compile(r"\b(?:download|pull|fetch|get)\s+(?:the\s+|a\s+)?(.+?)\s+(?:model|llm)\b", re.I)
_MDL_PULL_B = re.compile(r"\b(?:download|pull|fetch|get)\s+the\s+(?:model|llm)\s+(.+?)\s*$", re.I)
_MDL_RESET = re.compile(r"\b(?:reset|go back to the default|back to default)\b.{0,26}\b(?:model|llm)\b"
                        r"|\breset\b.{0,20}\b(?:" + _MDL_ROLE_WORDS + r")\s+(?:model|llm)\b", re.I)
_MDL_MODELISH = re.compile(r"\d|qwen|llama|mistral|gemma|deepseek|phi|coder|granite|command-r", re.I)


# Development Mode -> the Dev agent. "I want to build a small game for Android" starts a
# mission (interview -> proposal -> approve -> setup). The software-noun requirement keeps
# "create a folder", "make a note", and "write a poem" untouched; the article+noun adjacency
# keeps "make a list of all the apps" out.
_DEV_NOUN = r"(?:game|app|application|program|website|web ?app|software|tool|prototype)"
_DEV_ENTER_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?[a-z]+,\s+)?(?:can you |could you |please )?"
    r"(?:i(?:'d| would)? (?:like|want) to|help me|let'?s|i'?m going to|i wanna|can we|we should)?\s*"
    r"(?:build|create|make|develop|code|start building|write)\s+(?:me\s+)?"
    r"(?:a|an|my|some)\s+(?:\w+[ -]){0,3}" + _DEV_NOUN + r"\b", re.I)
# Smart Help — "Jarvis, help" anywhere pops the context card. Anchored + $-terminated so it
# fires ONLY on a bare ask for help, never on "help me write a book" / "help me set up …"
# (those carry an object and are real requests that must route normally).
_HELP_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:jarvis[,\s]+)?"
    r"(?:"
    r"help(?:\s+me)?(?:\s+please)?|"
    r"i\s+(?:need|want)\s+(?:some\s+)?help|"
    r"(?:can|could)\s+you\s+help(?:\s+me)?|"
    r"what\s+can\s+i\s+(?:say|do)(?:\s+(?:here|now))?|"
    r"what\s+are\s+my\s+options|"
    r"what\s+commands?(?:\s+(?:can\s+i\s+(?:say|use)|are\s+(?:there|available)))?|"
    r"(?:show|tell)\s+me\s+(?:the\s+|my\s+)?(?:commands?|options|help)|"
    r"how\s+d(?:o\s+i\s+use|oes)\s+this(?:\s+work)?|"
    r"what\s+can\s+you\s+do(?:\s+here)?"
    r")\s*[?.!]*\s*$", re.I)
_HELP_HIDE_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:jarvis[,\s]+)?(?:close|hide|dismiss|get rid of)\s+"
    r"(?:the\s+|this\s+)?help(?:\s+(?:window|card))?\s*[?.!]*\s*$", re.I)

# Picking a command off the help card by number: "do number 3", "number three", "the second
# option", "run the first one". Only consulted while a help card is live (self._help_commands),
# and a token that isn't a number (e.g. "select 4", "run the project") yields None and falls
# through to normal routing — so real commands are never swallowed.
_HELP_NUMWORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_HELP_ORDWORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
                 "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
                 "twelfth": 12, "last": -1}


def _token_to_index(tok: str):
    tok = tok.strip().lower()
    if tok in _HELP_ORDWORD:
        return _HELP_ORDWORD[tok]
    if tok in _HELP_NUMWORD:
        return _HELP_NUMWORD[tok]
    m = re.match(r"(\d+)(?:st|nd|rd|th)?$", tok)
    return int(m.group(1)) if m else None


def _help_run_index(goal: str):
    # Anchored at the START so browser link verbs ("open number 5", "select 4") are NOT hijacked
    # — only a leading menu-pick phrasing counts.
    g = goal.strip().rstrip("?.!").lower()
    m = re.match(
        r"^(?:hey\s+\w+[, ]+)?(?:jarvis[, ]+)?"
        r"(?:"
        r"(?:do|run|use|pick|choose|go with|execute)\s+(?:the\s+)?(?:number|option|item|command)\s+([\w-]+)"
        r"|(?:number|option|item|command)\s+([\w-]+)"
        r"|(?:do|run|use|pick|choose|go with|execute)\s+(?:the\s+)?([\w-]+)\s+(?:one|option|command)"
        r"|the\s+([\w-]+)\s+(?:one|option|command)"
        r"|(?:do|run|pick|choose|use|execute)\s+(?:the\s+)?([\w-]+)"
        r")\s*$", g)
    if not m:
        return None
    tok = next((x for x in m.groups() if x), None)
    return _token_to_index(tok) if tok else None


# Bare entry with no project yet — "Jarvis, enter development mode" / "let's build something".
# This is the door Michael asked for: it opens Dev Mode, then invites a full free-form
# description before any questions, so a long spoken description never gets cut off up front.
_DEV_MODE_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?[a-z]+,\s+)?(?:can you |could you |please |let'?s |okay,? |ok,? )?"
    r"(?:(?:i(?:'d| would)? (?:like|want) to|i wanna|i'?m going to|help me|we should|can we)\s+)?"
    r"(?:enter|start|go(?: in)?to|open|begin|activate|launch)?\s*"
    r"(?:development|dev)\s*mode\b"
    r"|^\s*(?:(?:hey\s+)?[a-z]+,\s+)?(?:let'?s|i(?:'d| would)? (?:like|want) to|i wanna|can we)\s+"
    r"(?:build|make|create|develop|write|code|start(?: on)?)\s+(?:me\s+)?(?:something|a project)\b",
    re.I)
_DEV_CANCEL_RE = re.compile(
    r"^\s*(?:cancel|stop|abort|quit|end)(?: the| this)? ?(?:development|dev ?mode|mission|project)\b", re.I)
_DEV_WIN_RE = re.compile(
    r"^\s*(?:can you |could you |please )?(show(?: me)?|open|display|pull up|bring up|close|hide|dismiss)"
    r"\s+(?:the |my )?(?:mission|development (?:plan|mission|window))\b", re.I)


_DEV_BUILD_RE = re.compile(r"^\s*(?:can you |please )?(?:start|begin)(?: the)? build(?:ing)?\b"
                           r"|^\s*agents?,? (?:start|get) (?:building|to work)\b", re.I)
_DEV_RUN_RE = re.compile(r"^\s*(?:can you |please )?(?:run|launch|play|start)\s+(?:the |my )?"
                         r"(?:project|game)\b", re.I)
# Status questions arrive in many spoken shapes ("hows the build going", "is the build done",
# "are the agents done", "build status") and STT mangles words ("built") — match the intent,
# not one phrasing. Guarded so "how do I build an app" (a how-to, not a status ask) stays out.
_DEV_STATUS_RE = re.compile(
    r"\bhow(?:'?s| is| far(?: along)?| are)\b.{0,30}\b(?:buil(?:d|ds|ding|t)|mission|project|agents?)\b"
    r"|\bhow\b.{0,30}\b(?:buil(?:d|ds|ding|t)|mission|project)\b.{0,16}\b(?:going|coming|doing|progress)\b"
    r"|\b(?:build|mission|project)\s+(?:status|progress|update)\b"
    r"|\b(?:is|are)\s+(?:the\s+|my\s+)?(?:buil(?:d|ds|t)|project|agents?)\b.{0,18}\b(?:done|ready|finished|complete)\b"
    r"|\bare\s+(?:the\s+)?agents?\b.{0,18}\b(?:working|building|busy|still going)\b", re.I)


# Browser control -> the Browser agent. Deterministic (small planners fumble these). Scroll is
# universal (any window); pagination and back/forward/reload target the browser.
_SCROLL_RE = re.compile(
    r"\bscroll\b|\bpage (?:up|down)\b|\b(?:go|jump|take me) to (?:the )?(?:top|bottom) "
    r"of (?:the )?(?:page|screen|document|list)\b|\bgo to (?:the )?(?:top|bottom)\b", re.I)
# first/last/next/previous page (with or without "the"/"results") ALL route here, so a
# pagination phrase never leaks to the planner and gets searched (the "first" -> Google
# search-for-"first" bug). Numeric: "go to page 4" / "page 4".
_PAGE_RE = re.compile(
    r"\b(?:the )?(?:next|previous|prev|last|first)(?: results?| search)? page\b"
    r"|\bgo (?:to |back to )?page (?:number )?\d+\b"
    r"|\b(?:go to |show |jump to )?page (?:number )?\d+\b", re.I)
_BROWSER_NAV_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please )?"
    r"(go back|back|go forward|forward|reload(?: the page| this page)?|refresh(?: the page| this page)?)"
    r"\s*[.?!]*\s*$", re.I)


def _scroll_direction(goal: str) -> str:
    """Turn a scroll utterance into the Browser agent's scroll argument."""
    g = goal.lower()
    if "top" in g or "beginning" in g:
        return "top"
    if "bottom" in g or "end of" in g:
        return "bottom"
    base = "up" if re.search(r"\b(up|back up)\b", g) else "down"
    m = re.search(r"(\d+)\s*(?:lines?|times?|clicks?)", g)
    if m:
        return f"{base} {m.group(1)} lines"
    if any(w in g for w in ("little", "bit", "few")):
        return f"{base} a little"
    return base


def _page_target(goal: str) -> str:
    g = goal.lower()
    m = re.search(r"page (?:number )?(\d+)", g)  # an explicit number wins ("go back to page 4")
    if m:
        return m.group(1)
    if "next" in g:
        return "next"
    if "previous" in g or "prev" in g:
        return "previous"
    if "first" in g:
        return "first"
    if "last" in g:
        return "last"
    return "next"


# Deep page reading (Marionette). "read me the links" / "open the Wikipedia one" / "read the page".
# Show/hide visible numbered badges — for sighted, hands-free users who can SEE but not click.
# Bare "click" (the Handsfree pattern) + "number the links" -> paint the badges. Bare click must
# be near-standalone so "click number 3" / "click the video" fall through to open_link (select).
_SHOWNUM_RE = re.compile(
    r"\bnumber the links?\b|\blabel the links?\b"
    r"|\b(?:show|display|put|add|highlight)\b.{0,24}\b(?:numbers?|labels?)\b"
    r"|\bshow (?:me )?(?:the )?(?:link |clickable )?numbers?\b"
    r"|^\s*(?:hey\s+\w+[,\s]+)?(?:can you |please )?click"
    r"(?:\s+(?:here|links?|things?|mode|on (?:the )?(?:page|links?)))?\s*[.?!]*$", re.I)
_HIDENUM_RE = re.compile(
    r"\b(?:hide|remove|clear|turn off|get rid of)\b.{0,18}\b(?:numbers?|labels?|badges?)\b", re.I)
_READLINKS_RE = re.compile(
    r"\b(?:read|list|show|give me|tell me|what are)\b.{0,22}\blinks?\b"
    r"|\bwhat links?\b|\bwhat can i (?:click|open)\b", re.I)
_READPAGE_RE = re.compile(
    r"\bread (?:me |it |this |out )*(?:the |this )?(?:page|article|website|site|it|this)\b"
    r"|\bwhat does (?:this|the) (?:page|article|website|it) say\b"
    r"|\b(?:summari[sz]e|read out) (?:the |this )?(?:page|article|website)\b"
    r"|\bread (?:this|it) to me\b", re.I)
_EXPAND_RE = re.compile(
    r"\bshow more\b|\bshow me more\b|\bexpand (?:it|that|the .{0,20})?\b|\bload more\b"
    r"|\b(?:read|see) (?:me )?the rest\b", re.I)
_OPEN_VERB = r"(?:open|click|select|choose|take me to)"
_OPENLINK_RE = re.compile(rf"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please )?{_OPEN_VERB}\s+(.+)$", re.I)
_OPENLINK_CUE = re.compile(
    r"\bnumber\s+\w+|\blink\s+\w+|\bresult\s+\w+"
    r"|\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last)\b"
    r"|\b\d+(?:st|nd|rd|th)?\b|\bthe\s+.+?\s+(?:one|link|result|video|article)\b", re.I)


def _openlink_route(goal: str):
    """A reference to open a listed link ('number 3', 'the Wikipedia one'), or None."""
    m = _OPENLINK_RE.match(goal.strip())
    if not m:
        return None
    ref = m.group(1).strip(" .?!")
    if _OPENLINK_CUE.search(ref) or re.fullmatch(r"(?:number\s+)?\d+", ref, re.I):
        return ref
    return None


def _browser_nav_route(goal: str):
    m = _BROWSER_NAV_RE.match(goal.strip())
    if not m:
        return None
    w = m.group(1).lower()
    if w.startswith("go forward") or w == "forward":
        return "forward"
    if w.startswith("reload") or w.startswith("refresh"):
        return "reload"
    return "back"


def _dev_route(goal: str):
    g = goal.strip()
    if _DEV_CANCEL_RE.match(g):
        return ("cancel", "")
    m = _DEV_WIN_RE.match(g)
    if m:
        return ("hide" if m.group(1).lower() in ("close", "hide", "dismiss") else "show", "")
    if _DEV_BUILD_RE.match(g):
        return ("build", "")
    if _DEV_RUN_RE.match(g):
        return ("run", "")
    if _DEV_STATUS_RE.search(g):
        return ("status", "")
    if _DEV_MODE_RE.search(g) or _DEV_ENTER_RE.search(g):
        return ("enter", g)  # _enter strips any trigger phrase and keeps the rest as the description
    return None


# Voice management -> the Voice agent. Every pattern requires the word "voice(s)", so none of
# this can hijack ordinary requests. "Change your voice" (no target) opens the PICKER — seeing
# and previewing the options beats guessing a name.
_VOICE_CLOSE = re.compile(r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please )?"
                          r"(?:close|hide|dismiss)\b.{0,24}\bvoices?\b", re.I)
_VOICE_TO = re.compile(r"\bchange (?:your|the) voice to\s+(.+?)\s*[.?!]?\s*$", re.I)
_VOICE_USE = re.compile(r"\b(?:use|switch to|speak (?:with|in)|talk (?:with|in))\s+"
                        r"(?:the\s+|a\s+)?(.+?)\s+voice\b", re.I)
# plain "download the X voice" (no "use") -> fetch it and play a sample; the user then decides
_VOICE_DL = re.compile(r"\b(?:download|install|get|grab|fetch)\s+(?:the\s+|a\s+)?(.+?)\s+voice\b", re.I)
_VOICE_PREVIEW = re.compile(r"\b(?:preview|try|demo|play|let me hear|hear)\s+(?:the\s+|a\s+)?(.+?)\s+voice\b"
                            r"|\bwhat does\s+(?:the\s+)?(.+?)\s+(?:voice\s+)?sound like\b", re.I)
_VOICE_STATUS = re.compile(r"\b(?:what|which) voice (?:are you using|do you use|is (?:that|this))\b"
                           r"|\bhow(?:'s| is)\b.{0,20}\bvoice download\b", re.I)
_VOICE_OPEN = re.compile(
    r"\bchange (?:your|the) voice\b|\b(?:what|which) voices?\b|\bshow (?:me )?(?:the |your )?voices?\b|"
    r"\bopen (?:the )?voices?(?: manager| picker| window| settings)?\b|\blist (?:the |your )?voices\b|"
    r"\b(?:pick|choose) a (?:new |different )?voice\b|\bsound different\b", re.I)


def _voice_route(goal: str):
    """Classify a voice-management command into (verb, argument), or None."""
    g = goal.strip()
    if _VOICE_CLOSE.search(g):
        return ("close", "")
    m = _VOICE_TO.search(g)
    if m:
        return ("use", m.group(1).strip(" .?"))
    m = _VOICE_USE.search(g)
    if m:
        return ("use", m.group(1).strip(" .?"))
    m = _VOICE_DL.search(g)
    if m:
        return ("preview", m.group(1).strip(" .?"))  # download + hear it, switch only if asked
    m = _VOICE_PREVIEW.search(g)
    if m:
        return ("preview", (m.group(1) or m.group(2) or "").strip(" .?"))
    if _VOICE_STATUS.search(g):
        return ("status", "")
    if _VOICE_OPEN.search(g):
        return ("open", "")
    return None


def _model_route(goal: str):
    """Classify a model-management command into (verb, params), or None if it isn't one."""
    g = goal.strip()
    if _MDL_RESET.search(g):
        return ("reset", {"argument": g})
    m = _MDL_BIND.match(g)
    if m:
        target, role = m.group(1).strip(" .?"), m.group(2).lower()
        if "default" in target.lower():
            return ("reset", {"argument": role})
        # only claim the phrase when the target actually sounds like a model
        if re.search(r"\bmodel|llm\b", g, re.I) or _MDL_MODELISH.search(target):
            return ("bind", {"argument": target, "role": role})
    m = _MDL_PULL_A.search(g) or _MDL_PULL_B.search(g)
    if m:
        return ("pull", {"argument": m.group(1).strip(" .?")})
    if _MDL_STATUS.search(g) and re.search(r"\bmodels?|llms?|download\b", g, re.I):
        return ("status", {})
    if _MDL_LIST.search(g):
        return ("list", {})
    return None


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


# "open a terminal window" -> LAUNCH the terminal app (benign), never command.run (which is
# auth-gated). The word 'terminal' pulls small planners toward 'run a shell command', so pin
# this deterministically. Requires the sentence to END at terminal/window, so "use the terminal
# TO convert this" still goes to the CLI-synthesis Task agent, not here.
_TERMINAL_OPEN_RE = re.compile(
    r"^\s*(?:can you |could you |please )?(?:open|launch|start|bring up|pull up|give me|"
    r"get me|i(?:'d| would)? like)\s+(?:me\s+)?(?:a|an|the|my)?\s*(?:new\s+)?"
    r"(?:terminal|console|command[ -]?line|shell)(?:\s+(?:window|emulator|app|prompt))?\s*[.?!]*\s*$",
    re.I)
_TERMINAL_CLOSE_RE = re.compile(
    r"^\s*(?:can you |could you |please )?(?:close|quit|exit|kill|shut|end)\s+(?:down\s+)?"
    r"(?:the|my|a|this)?\s*(?:terminal|console|command[ -]?line|shell)(?:\s+(?:window|emulator|app))?"
    r"\s*[.?!]*\s*$", re.I)


# CLI-synthesis rung -> the Task agent. Explicit, unambiguous lead-ins ("use the terminal to X",
# "figure out how to X") so it never hijacks ordinary requests; the planner can also route here.
_TASK_TRIGGER = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please )?"
    r"(?:use the (?:terminal|command ?line|shell)(?: to)?|figure out how to|work out how to|"
    r"find a way to|do this for me[:,]?)\s+(.*)$", re.I)
_TASK_CONFIRM = re.compile(r"^\s*(?:run it|run that|execute(?: it| that)?|go ahead and run(?: it)?|"
                           r"yes,? run it)\b", re.I)
_TASK_CANCEL = re.compile(r"^\s*(?:don'?t run(?: it| that)?|cancel that command)\b", re.I)


def _task_route(goal: str):
    g = goal.strip()
    if _TASK_CONFIRM.match(g):
        return ("confirm", "")
    if _TASK_CANCEL.match(g):
        return ("cancel", "")
    m = _TASK_TRIGGER.match(g)
    if m and m.group(1).strip():
        return ("do", m.group(1).strip())
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


# Verbs worth remembering in the activity journal (past-tense templates). Trivial reads
# (time/disk/status/list/recall) are deliberately omitted — the diary is about WORK done.
_JOURNAL_TMPL = {
    "create_folder": "Created the folder {a}",
    "create_file": "Created the file {a}",
    "write_file": "Saved {a}",
    "append_file": "Updated {a}",
    "delete": "Deleted {a}",
    "rename": "Renamed {a}",
    "move": "Moved {a}",
    "copy": "Copied {a}",
    "write_document": "Wrote a document about {a}",
    "lookup": "Looked up {a}",
}


# "What was I working on yesterday?" -> the activity recap (memory.recap). Requires a
# self/past-activity shape so it can't grab "what's the weather" or "what did YOU do".
_RECAP_RE = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:can you |could you |please )?"
    r"(?:what (?:was|were|have|did) (?:i|we)\b.{0,40}?\b(?:work|doing|do|been|up to|get done|accomplish|make|build)"
    r"|remind me what (?:i|we)\b.{0,30}?\b(?:did|worked|was|were|made|built)"
    r"|(?:give me |show me )?(?:a |my )?(?:recap|summary)\b.{0,24}?\b(?:day|activity|work|did|yesterday|week)"
    r"|what'?s? (?:my|the) (?:activity|work history|recap)"
    r"|catch me up (?:on )?(?:my )?(?:day|work|activity))"
    r".*$", re.I)


def _journal_task(task: Task, result: Result) -> None:
    """Record a completed action in the activity diary, if it's work worth remembering."""
    if result.status is not Status.OK:
        return
    tmpl = _JOURNAL_TMPL.get(task.action.split(".")[-1])
    if not tmpl:
        return
    a = (task.params.get("argument") or task.params.get("path")
         or task.params.get("text") or "").strip()
    if not a:
        return
    journal.record(task.action.split(".", 1)[0], tmpl.format(a=a[:80]))


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
        self._heard = ""          # the last transcript — shown in the HUD to diagnose mishearings
        self._last_path: str | None = None
        self._recent_created: list[str] = []  # paths created this session — "those files you made"
        self._pending_confirm: str | None = None  # an agent awaiting a spoken yes/no (e.g. file delete)
        self._pending_reply: str | None = None  # an agent mid-conversation (e.g. the Dev interview)
        # Short-term conversational memory: the last few exchanges, fed to the planner and the
        # conversational fallback so "tell me more" / "what was that called?" have something to
        # refer to, and "repeat that" can re-speak the last reply verbatim.
        self._dialogue: deque[tuple[str, str]] = deque(maxlen=6)
        self._help_commands: list[dict] = []  # numbered commands from the last "help" card ("do number 3")

    def _publish(self, text: str) -> None:
        # Show WHAT I HEARD alongside what I'm doing, so a wrong transcript (the usual cause of
        # "why did he do that?") is visible on the HUD as it happens — Michael's debugging aid.
        if not self.activity:
            return
        if text and self._heard:
            h = self._heard if len(self._heard) <= 48 else self._heard[:47] + "…"
            text = f"“{h}”  ·  {text}"
        self.activity.publish(text)

    async def _answer_dev(self, goal: str) -> str:
        """Route an utterance to the Dev agent as the mission's answer/continuation, capturing
        whatever question it asks next. The mission is the conversation's focus while it's active."""
        self._pending_reply = None
        if _DEV_CANCEL_RE.match(goal.strip()):
            task = Task(action="dev.cancel", agent="dev", params={})
            return self._render(task, await self._dispatch(task))
        self._publish("Listening…")
        task = Task(action="dev.answer", agent="dev", params={"argument": goal})
        result = await self._dispatch(task)
        if isinstance(result.data, dict):
            if result.data.get("await_reply"):
                self._pending_reply = result.data.get("agent")
            if result.data.get("await_confirm"):
                self._pending_confirm = result.data.get("agent")
        return self._render(task, result)

    async def _run_help_command(self, idx: int) -> str:
        """Execute the command the user picked by number off the help card. A command with a
        concrete ``run`` phrase is re-dispatched exactly as if spoken; a template/example (no
        ``run`` — e.g. "delete the drafts folder", "find <words>") is never fired blindly, only
        explained, so a number can't trigger something destructive or wrong."""
        cmds = self._help_commands
        n = len(cmds)
        if idx == -1:
            idx = n
        if not (1 <= idx <= n):
            self._publish("")
            return f"The help card has {n} option{'s' if n != 1 else ''}. Say a number from 1 to {n}."
        cmd = cmds[idx - 1]
        say, does, run = cmd.get("say", ""), cmd.get("does", ""), cmd.get("run")
        if run:
            self._publish(f"Help ▸ {say}")
            return await self.handle(run, addressed=True)
        self._publish("")
        if say.startswith("("):
            return f"Number {idx}: {does}."
        return f"That one takes your own details — {does}. Say it like: “{say}”."

    async def handle(self, goal: str, addressed: bool = True) -> str:
        # ``addressed`` = the user spoke the assistant's name this utterance (topic-change signal).
        # Typed input and follow-ups default sensibly; the voice loop sets it explicitly.
        self._heard = (goal or "").strip()  # surface the transcript on the HUD while we work
        transcript.log("user", text=self._heard, addressed=addressed)  # dev builds: the QA record
        # Never let a transient error (e.g. an LLM/network hiccup) crash the assistant.
        try:
            reply = await self._handle(goal, addressed)
        except Exception as e:  # noqa: BLE001
            print(f"[orchestrator] error handling goal: {e!r}", file=sys.stderr)
            transcript.log("error", error=repr(e))
            msg = str(e).lower()
            if any(s in msg for s in ("not found", "404", "connect", "refus", "no such model")):
                reply = ("I'm still getting set up — my language model may still be downloading. "
                         "Give me a few minutes, then try again.")
            else:
                try:  # don't dead-end on an error — let the backbone still try to help
                    ctx = self.memory.context() if self.memory else ""
                    reply = await self._assist(goal, ctx, problem="an internal error")
                except Exception:
                    reply = ("I couldn't complete that one, but I can help another way — I work with files "
                             "and folders, apps, web search, lookups, reminders and memory. What do you need?")
        transcript.log("reply", text=reply)
        if self._heard and reply:
            self._dialogue.append((self._heard, reply))
        return reply

    async def _handle(self, goal: str, addressed: bool = True) -> str:
        # Users address the assistant by name mid-conversation too ("Jarvis, set it up") —
        # inside the conversation window the voice loop doesn't strip it, and every anchored
        # matcher downstream (approvals, yes/no confirms, interview answers) would miss.
        # Strip a leading "<name>," / "hey <name>" here, once, for every route. A name present
        # this utterance also means "addressed" even mid-conversation (a topic-change signal).
        stripped = re.sub(rf"^\s*(?:hey\s+)?{re.escape(config.get_name())}\b[,.!:]?\s*", "",
                          goal.strip(), flags=re.I)
        if stripped and stripped != goal.strip():
            addressed = True
            goal = stripped
        goal = self._rewrite_pronouns(goal)
        if _DANGER_RE.search(goal):  # catastrophic intent -> hard refusal, never reaches the model
            self._publish("")
            return ("I won't help erase or destroy your drive, files, or system — that's irreversible and "
                    "could break your machine. If you genuinely need to wipe a disk or reinstall, do that "
                    "deliberately yourself, with backups — not by voice.")
        if self._pending_confirm:  # we just asked a yes/no (e.g. "Delete X?") — interpret the answer
            if _REPEAT_RE.match(goal.strip()) and self._dialogue:
                return self._dialogue[-1][1]  # re-ask; the question stays pending for the real answer
            agent = self._pending_confirm
            self._pending_confirm = None
            if _YES_RE.match(goal.strip()):
                task = Task(action=f"{agent}.confirm", agent=agent, params={})
                return self._render(task, await self._dispatch(task))
            if _NO_RE.match(goal.strip()):
                task = Task(action=f"{agent}.cancel", agent=agent, params={})
                return self._render(task, await self._dispatch(task))
            # not a yes/no -> drop the pending confirmation and handle the new request normally
        # SMART HELP. "Jarvis, help" (or "what can I say here?") pops a context-aware card for
        # wherever the user is — checked BEFORE the mission-focus redirect so asking for help
        # mid-interview shows Development-Mode help instead of being taken as an answer.
        if _HELP_HIDE_RE.match(goal.strip()):
            self._help_commands = []
            task = Task(action="help.hide", agent="help", params={"argument": ""})
            return self._render(task, await self._dispatch(task))
        if _HELP_RE.match(goal.strip()):
            self._publish("Help…")
            task = Task(action="help.show", agent="help", params={"argument": ""})
            result = await self._dispatch(task)
            if isinstance(result.data, dict):  # remember the numbered list for "do number 3"
                self._help_commands = list(result.data.get("help_commands") or [])
            return self._render(task, result)
        if self._help_commands:  # a help card is live — "do number 3" runs that command
            ridx = _help_run_index(goal)
            if ridx is not None:
                return await self._run_help_command(ridx)
        # FOCUS. While a Development mission is mid-setup it IS the topic. A FOLLOW-UP (no name)
        # continues it — the answer to the assistant's question — so it never leaks to a global
        # route or a mishearing-driven topic jump. Saying the NAME signals a possible topic change,
        # so a name-addressed utterance goes through normal routing first and only falls back to the
        # mission if nothing else matched (so "Jarvis, set it up" still answers, but "Jarvis, open a
        # file window" switches away — the mission stays alive, resumable by name).
        in_dev = self._pending_reply is not None or (
            mission.active() and mission.load().get("stage") in ("describe", "interview", "proposal"))
        if in_dev and not addressed:
            return await self._answer_dev(goal)
        if _REPEAT_RE.match(goal.strip()):  # "repeat that" -> the last reply, verbatim (vital
            self._publish("")               # with barge-in, which can cut a reply mid-sentence)
            if self._dialogue:
                return self._dialogue[-1][1]
            return "I haven't said anything yet."
        if _EXPLAIN_RE.match(goal.strip()):  # "why did you…" -> explain my last action, reliably
            self._publish("")
            task = Task(action="explain.why", agent="explain", params={"argument": ""})
            return self._render(task, await self._dispatch(task))
        rn = _RENAME_RE.match(goal.strip())
        if rn:  # "call yourself Athena" -> rename (the name is also the wake word)
            self._publish("")
            raw = _RENAME_TRAIL.sub("", rn.group(1))
            raw = re.sub(r"\b(please|thanks|thank you|now|okay|ok)\b", "", raw, flags=re.I)
            new = config.set_name(raw)
            reply = f"Okay — I'm {new} now. Just say “{new}” to get my attention."
            # A new name often wants a new voice — show the options and let the USER decide
            # (each row has a Preview button; nothing changes unless they pick one).
            from . import voices
            if voices.open_picker():
                reply += (" I've also put my voices on screen — if you'd like me to sound "
                          "different too, preview one and say, for example, “use the Ryan voice”.")
            return reply
        mkt = _market_route(goal)  # "install the X agent" / "what agents are available" / "yes install it"
        if mkt:
            verb, arg = mkt
            self._publish("Marketplace…")
            task = Task(action=f"market.{verb}", agent="market", params={"argument": arg})
            return self._render(task, await self._dispatch(task))
        # Deep page reading — voice-browse the actual content (checked before app-launch routes
        # so "open the Wikipedia one" opens a listed link, not an app).
        if _HIDENUM_RE.search(goal):
            self._publish("")
            task = Task(action="browser.hide_numbers", agent="browser", params={})
            return self._render(task, await self._dispatch(task))
        if _SHOWNUM_RE.search(goal):  # checked before read_links ("show link numbers" vs "read links")
            self._publish("Numbering the links…")
            task = Task(action="browser.show_numbers", agent="browser", params={})
            return self._render(task, await self._dispatch(task))
        if _READLINKS_RE.search(goal):
            self._publish("Reading the page…")
            task = Task(action="browser.read_links", agent="browser", params={})
            return self._render(task, await self._dispatch(task))
        olink = _openlink_route(goal)
        if olink:
            self._publish("Opening the link…")
            task = Task(action="browser.open_link", agent="browser", params={"argument": olink})
            return self._render(task, await self._dispatch(task))
        if _READPAGE_RE.search(goal):
            self._publish("Reading the page…")
            task = Task(action="browser.read_page", agent="browser", params={})
            return self._render(task, await self._dispatch(task))
        if _EXPAND_RE.search(goal):
            self._publish("")
            task = Task(action="browser.expand", agent="browser", params={"argument": goal})
            return self._render(task, await self._dispatch(task))
        # Browser control — checked before the planner (unambiguous, common phrases).
        if _PAGE_RE.search(goal):  # "next page" / "go to page 4" of search results
            self._publish("Turning the page…")
            task = Task(action="browser.page", agent="browser",
                        params={"argument": _page_target(goal)})
            return self._render(task, await self._dispatch(task))
        if _SCROLL_RE.search(goal):  # "scroll down a page" / "scroll to the bottom" / "page down"
            self._publish("Scrolling…")
            task = Task(action="browser.scroll", agent="browser",
                        params={"argument": _scroll_direction(goal)})
            return self._render(task, await self._dispatch(task))
        bnav = _browser_nav_route(goal)  # "go back" / "go forward" / "reload the page"
        if bnav:
            self._publish("")
            task = Task(action=f"browser.{bnav}", agent="browser", params={})
            return self._render(task, await self._dispatch(task))
        if _TERMINAL_OPEN_RE.match(goal):  # "open a terminal window" -> launch it, no auth needed
            self._publish("Opening…")
            task = Task(action="app.launch", agent="app", params={"argument": "terminal"})
            return self._render(task, await self._dispatch(task))
        if _TERMINAL_CLOSE_RE.match(goal):  # "close the terminal" -> close the app (deterministic)
            self._publish("Closing…")
            task = Task(action="app.close", agent="app", params={"argument": "terminal"})
            return self._render(task, await self._dispatch(task))
        tsk = _task_route(goal)  # "use the terminal to X" / "figure out how to X" / "run it"
        if tsk:
            verb, arg = tsk
            self._publish("Working it out…")
            task = Task(action=f"task.{verb}", agent="task", params={"argument": arg})
            result = await self._dispatch(task)
            if isinstance(result.data, dict) and result.data.get("assist"):  # not a shell task
                return await self._assist(goal, self.memory.context() if self.memory else "")
            return self._render(task, result)
        su = _sched_ui_route(goal)  # "show/close my scheduled tasks" -> the schedule window
        if su:
            self._publish("")
            task = Task(action=f"schedule.{su}", agent="schedule", params={})
            return self._render(task, await self._dispatch(task))
        if _WHERE_CREATED_RE.search(goal):  # "open the directory where you placed those files"
            root = self._recent_root()
            if root is not None or self._last_path:
                self._publish("Opening…")
                target = root if root is not None else os.path.dirname(self._last_path)
                task = Task(action="file.open", agent="file", params={"path": target})
                return self._render(task, await self._dispatch(task))
        sf = self._show_files_route(goal)  # "show me those directories" -> the file manager
        if sf is not None:
            self._publish("Opening…")
            task = Task(action="file.open", agent="file", params={"path": sf})
            return self._render(task, await self._dispatch(task))
        upd = _update_route(goal)  # "check for updates" / "update yourself" -> the Update agent
        if upd:
            self._publish("")
            task = Task(action=f"update.{upd}", agent="update", params={})
            return self._render(task, await self._dispatch(task))
        mdl = _model_route(goal)  # "what models do I have" / "use X for coding" / "download the X model"
        if mdl:
            verb, prms = mdl
            self._publish("Models…")
            task = Task(action=f"model.{verb}", agent="model", params=prms)
            result = await self._dispatch(task)
            if isinstance(result.data, dict) and result.data.get("await_confirm"):
                self._pending_confirm = result.data.get("agent")  # the next yes/no answers this
            return self._render(task, result)
        vc = _voice_route(goal)  # "change your voice" / "use the ryan voice" / "preview amy"
        if vc:
            verb, arg = vc
            self._publish("Voices…")
            task = Task(action=f"voice.{verb}", agent="voice", params={"argument": arg})
            result = await self._dispatch(task)
            if isinstance(result.data, dict) and result.data.get("await_confirm"):
                self._pending_confirm = result.data.get("agent")
            return self._render(task, result)
        dv = _dev_route(goal)  # "I want to build a small game for android" -> Development Mode
        if dv:
            verb, arg = dv
            self._publish("Development Mode…")
            task = Task(action=f"dev.{verb}", agent="dev", params={"argument": arg})
            result = await self._dispatch(task)
            if isinstance(result.data, dict) and result.data.get("await_reply"):
                self._pending_reply = result.data.get("agent")
            return self._render(task, result)
        if _RECAP_RE.match(goal.strip()):  # "what was I working on yesterday?" -> activity recap
            self._publish("Recalling…")
            task = Task(action="memory.recap", agent="memory", params={"argument": goal})
            return self._render(task, await self._dispatch(task))
        if _SCHEDULE_RE.match(goal.strip()):  # "remind me…" / "schedule…" / "every weekday at 9…"
            self._publish("Scheduling…")
            task = Task(action="schedule.add", agent="schedule", params={"argument": goal})
            return self._render(task, await self._dispatch(task))
        if _JOBS_STATUS_RE.match(goal.strip()):  # "how's the install going" -> the REAL status
            self._publish("")
            now = _time.time()
            return jobs.describe(jobs.recent(now), now)
        if _JOBS_WINDOW_RE.match(goal.strip()):  # "open the tasks window"
            self._publish("")
            task = Task(action="app.launch", agent="app", params={"argument": "tasks window"})
            return self._render(task, await self._dispatch(task))
        if _VISION_RE.match(goal.strip()):  # "what am I looking at" -> Jarvis looks at the screen
            self._publish("Looking at the screen…")
            task = Task(action="vision.look", agent="vision", params={"argument": goal})
            return self._render(task, await self._dispatch(task))
        if _VSCROLL_RE.match(goal.strip()):  # "scroll down" -> scroll the screen
            self._publish("")
            task = Task(action="vision.scroll", agent="vision", params={"argument": goal})
            return self._render(task, await self._dispatch(task))
        vclk = _VCLICK_RE.match(goal.strip())
        if vclk and not self._help_commands:  # "click the Watch Demo button" -> find + click it
            # a live help card owns bare "click N"; this is named-element clicking by sight
            self._publish("Finding it on screen…")
            task = Task(action="vision.click", agent="vision", params={"argument": vclk.group(1).strip()})
            return self._render(task, await self._dispatch(task))
        if _POWER_RE.match(goal.strip()):  # "reboot this computer" -> confirm, then really do it
            self._publish("")
            task = Task(action="system.power", agent="system", params={"argument": goal.lower()})
            result = await self._dispatch(task)
            if isinstance(result.data, dict) and result.data.get("await_confirm"):
                self._pending_confirm = result.data.get("agent")
            return self._render(task, result)
        if _SYSINFO_RE.match(goal.strip()):  # "what's my local IP" -> real commands, never the LLM
            self._publish("Checking…")
            task = Task(action="system.info", agent="system", params={"argument": goal})
            return self._render(task, await self._dispatch(task))
        if _SETTINGS_RE.match(goal.strip()):  # "open ThorAI settings" -> the settings window
            self._publish("Opening settings…")
            task = Task(action="app.launch", agent="app", params={"argument": "thorai settings"})
            return self._render(task, await self._dispatch(task))
        oas = _OPEN_AND_SEARCH_RE.match(goal.strip())
        if oas:  # "open google and search for X" -> one search (browse+search would race firefox)
            self._publish("Searching…")
            task = Task(action="app.search", agent="app", params={"argument": oas.group(1)})
            return self._render(task, await self._dispatch(task))
        if _RECOMMEND_RE.match(goal.strip()):  # "recommend software for X" -> research + offer install
            self._publish("Researching options…")
            task = Task(action="research.lookup", agent="research", params={"argument": goal})
            result = await self._dispatch(task)
            reply = self._render(task, result)
            return reply + await self._maybe_offer_install(result)
        if _RESEARCH_RE.match(goal.strip()):  # "price of bitcoin" / "weather in X" / "news on Y"
            self._publish("Looking that up…")
            task = Task(action="research.lookup", agent="research", params={"argument": goal})
            return self._render(task, await self._dispatch(task))
        ctx = self.memory.context() if self.memory else ""
        if self._dialogue:  # short-term memory: "tell me more" / "what was that called?" refer here
            recent = "\n".join(f"User said: \"{g[:160]}\" — you replied: \"{r[:220]}\""
                               for g, r in list(self._dialogue)[-4:])
            ctx += "\nThe conversation so far (oldest first):\n" + recent
        if self._recent_created:  # session awareness: the planner can resolve "the book folder",
            ctx += ("\nFolders/files you created earlier in this session (references like "
                    "'those files' or 'the folder you made' mean these): "
                    + ", ".join(self._recent_created[-8:]))
        self._publish("Thinking…")
        active = active_window()
        tasks = await self.planner.plan(goal, memory_context=ctx, active=active)
        print(f"[plan] active={active} goal={goal!r} -> {[t.action for t in tasks]}",
              file=sys.stderr, flush=True)
        transcript.log("plan", goal=goal, active=list(active or ()), actions=[t.action for t in tasks])
        if not tasks:
            self._publish("")
            # Name-addressed, nothing global matched, and a mission is waiting -> it was the answer
            # after all ("Jarvis, set it up" / "Jarvis, you choose"), not idle chatter.
            if in_dev:
                return await self._answer_dev(goal)
            reply = await self._assist(goal, ctx)
            trace.record(trace.Decision(goal=goal, active=active, memory_used=bool(ctx),
                                        route="conversation", outcome=reply))
            return reply
        replies, steps, ok, any_ok, denied = [], [], True, False, False
        for task in tasks:
            self._resolve_pronoun(task)
            self._publish(_activity_label(task))
            result = await self._dispatch(task)
            if isinstance(result.data, dict) and result.data.get("await_confirm"):
                self._pending_confirm = result.data.get("agent")  # the next yes/no answers this
            if result.status is Status.OK and task.params.get("path"):
                self._last_path = task.params["path"]
                if task.action in ("file.create_folder", "file.create_file", "file.write_file"):
                    self._recent_created.append(task.params["path"])
                    del self._recent_created[:-20]  # keep the tail
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
            if task.action == "research.lookup":  # a planned recommendation can offer an install too
                replies[-1] += await self._maybe_offer_install(result)
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

    async def _maybe_offer_install(self, result: Result) -> str:
        """The recommend -> offer -> install glue. When the Research agent hands back a top pick,
        stage it with the Software agent (which validates it against apt) and, only if it's really
        installable, ask the one question that keeps the conversation going instead of dead-ending.
        Returns the sentence to append to the spoken reply ("" when there's nothing to offer)."""
        data = result.data if isinstance(result.data, dict) else {}
        pick = (data.get("offer_install") or "").strip()
        if not pick or result.status is not Status.OK:
            return ""
        arg = (data.get("offer_pkg") or "").strip() or pick
        prime = await self._dispatch(Task(action="software.prime", agent="software",
                                          params={"argument": arg}))
        pdata = prime.data if isinstance(prime.data, dict) else {}
        if prime.status is Status.OK and pdata.get("ok"):
            self._pending_confirm = "software"
            return f" Would you like me to install {pick} for you?"
        if prime.status is Status.OK and pdata.get("already"):
            return f" You already have {pick} installed — just say “open {pick}”."
        return ""

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
            "the maximum. The request below was not handled by a specific skill. SAFETY FIRST: never give "
            "instructions that could destroy data, damage the system, or harm someone (e.g. wiping a disk, "
            "deleting system files, dd/mkfs on a drive) — briefly decline that specific part and offer a "
            "safe alternative instead. RULES: never give a "
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

    def _recent_root(self) -> str | None:
        """The folder that holds everything created this session ('where you put those files'):
        the common parent of the recently created paths."""
        if not self._recent_created:
            return None
        import posixpath
        paths = [p.strip("/").replace("\\", "/") for p in self._recent_created]
        try:
            root = posixpath.commonpath(paths) if len(paths) > 1 else paths[0]
        except ValueError:
            return ""
        return root

    def _show_files_route(self, goal: str) -> str | None:
        """'Show me those directories' -> the path to open in the file manager, or None if this
        isn't a show-files request. Vague targets ('those folders') resolve via what this session
        created (their common parent), falling back to the last path touched."""
        m = _SHOW_FILES_RE.match(goal.strip())
        if not m:
            return None
        name = (m.group(1) or "").strip(" ,.").lower()
        if name in _SHOW_VAGUE:
            root = self._recent_root()
            if root is not None:
                return root
            if not self._last_path:
                return ""  # the workspace root
            kw = m.group(2).lower()
            plural = kw.endswith("s") or "ies" in kw
            import os.path
            return os.path.dirname(self._last_path) if plural else self._last_path
        return m.group(1).strip(" ,.")

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
        transcript.log("task", action=task.action, params=task.params)
        result = await self.bus.request(task.agent, task)
        transcript.log("result", action=task.action, status=result.status.name,
                       data=result.data, error=result.error)
        if result.status is Status.AWAITING_AUTH and result.challenge is not None:
            code = await self.auth_resolver(result.challenge)
            token = self.perms.verify(result.challenge.challenge_id, code)
            if token is None:
                return Result(task.task_id, Status.DENIED, agent=task.agent,
                              error="authorization failed or timed out")
            task.auth_token = token
            result = await self.bus.request(task.agent, task)
        return result

    # Action CONFIRMATIONS whose spoken echo the verbosity setting may shorten or silence. A verb
    # here means "when this succeeds, the reply is just telling the user I did what they said" —
    # so 'simple' can shrink it and 'off' can drop it. Everything NOT listed (research/weather,
    # help, memory recall, explain, list contents, anything asking a question) is always spoken
    # in full. `action` (agent.verb) is matched first so a verb like 'open' is scoped per agent.
    _CONFIRM_BRIEF = {
        "app.launch": "Opening.", "app.close": "Closing.", "app.browse": "Opening.",
        "app.search": "Searching.", "app.write_document": "Done.",
        "file.open": "Opening.", "file.create_folder": "Done.", "file.create_file": "Done.",
        "file.write_file": "Done.", "file.delete": "Done.", "file.move": "Done.",
        "file.rename": "Done.", "file.confirm": "Done.",
        "software.install": "Done.", "software.confirm": "Done.",
    }

    def _verbosity_adjust(self, action: str, verb: str, data: dict) -> str:
        """Apply the reply-verbosity setting to a confirmation. Confirmations are identified by
        an explicit ``brief`` from the agent, or by being a known confirm action. Anything a
        question ('await_confirm'), a not-found ('missing'), or a fallback ('assist') is NEVER
        shortened — those must always be heard in full regardless of the setting."""
        speech = data["speech"]
        if data.get("await_confirm") or data.get("missing") or data.get("assist"):
            return speech
        brief = data.get("brief") or self._CONFIRM_BRIEF.get(action)
        if brief is None:
            return speech  # not a confirmation — informational/question/error, untouched
        level = config.get_verbosity()
        if level == "simple":
            return brief
        if level == "off":
            return ""  # silent confirmation (say("") is a no-op; nothing dead-ends on this)
        return speech

    def _render(self, task: Task, result: Result) -> str:
        _journal_task(task, result)  # remember work done — every routed + planned task flows here
        verb = task.action.split(".")[-1]
        data = result.data if isinstance(result.data, dict) else {}
        name = data.get("name") or task.params.get("path") or "it"
        if result.status is Status.OK:
            # Generic hook: any module can return a ready-to-speak string, so the orchestrator
            # never needs to know a new agent's verbs (see docs/MODULES.md).
            if data.get("speech"):
                return self._verbosity_adjust(task.action, verb, data)
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
                if not items:
                    return f"{name} is empty."
                shown = "; ".join(f"{i + 1}, {it}" for i, it in enumerate(items[:12]))
                more = f", and {len(items) - 12} more" if len(items) > 12 else ""
                return f"{name} has {len(items)}: {shown}{more}."
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
