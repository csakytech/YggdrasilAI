"""Development Mode — the multi-agent project builder (Milestone 1: enter → interview →
proposal → approve).

"I want to build a small game for Android" → "Entering Development Mode." Jarvis then
interviews the user until he FULLY understands the project — as many questions as that
takes (a wrong plan wastes hours; questions cost minutes), each answer ticking visibly
into the Mission window. The coding-mode question is always asked first: code it yourself /
hybrid with the Agents / full Agent coding. "You choose" answers any question; "just decide
the rest" jumps straight to the proposal. Then Jarvis presents the complete recommended
plan — language, editor, folders, Agent roster, test stages — and only builds after the
user approves. Terminology: user-facing text says AGENT, never "bot".
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..core import mission
from ..core.permissions import Capability
from .base import BaseAgent

_MODE_Q = ("First, the big one — how would you like to work? You can code it yourself with "
           "me assisting, we can code it together — you and the Agents — or the Agents can "
           "build it for you while you direct.")
_NAME_Q = "What should we call the project?"

_MODE_MANUAL = re.compile(r"\b(myself|my ?self|manual|i(?:'| wi)ll (?:code|write)|on my own|i code)\b", re.I)
_MODE_HYBRID = re.compile(r"\b(hybrid|together|both|with (?:the )?agents?|pair|code with me|mix)\b", re.I)
_MODE_FULL = re.compile(r"\b(full|agents? (?:do|build|code)|you (?:do|build|code)|vibe|for me|"
                        r"you choose|automatic)\b", re.I)
_DECIDE_REST = re.compile(r"\b(?:just )?(?:you )?decide (?:the )?rest\b|\bstop asking\b|"
                          r"\byou (?:take it|figure) from here\b", re.I)
_APPROVE = re.compile(r"^\s*(?:yes|yeah|yep|ok(?:ay)?|sure|go ahead|do it|approve[d]?|"
                      r"build it|set it up|make it so|sounds? (?:good|great)|perfect|"
                      r"let'?s (?:go|do it|build))\b", re.I)

MAX_LLM_QUESTIONS = 12  # depth is the point; this is only a runaway stop

_Q_SCHEMA = {"type": "object",
             "properties": {"question": {"type": "string"}, "done": {"type": "boolean"}},
             "required": ["question", "done"]}
_SUM_SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
               "required": ["summary"]}
_PLAN_SCHEMA = {"type": "object", "properties": {
    "language": {"type": "string"},
    "why_language": {"type": "string"},
    "editor": {"type": "string"},
    "folders": {"type": "array", "items": {"type": "string"}},
    "agents": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "specialty": {"type": "string"}},
        "required": ["name", "specialty"]}},
    "test_stages": {"type": "array", "items": {"type": "string"}},
    "speech": {"type": "string"}},
    "required": ["language", "why_language", "editor", "folders", "agents",
                 "test_stages", "speech"]}


class DevAgent(BaseAgent):
    domain = "dev"
    module_id = "core.dev"
    planner_examples = [
        'i want to build a small game for android -> {"steps":[{"action":"dev.enter","argument":"i want to build a small game for android"}]}',
        'help me create an app that tracks my expenses -> {"steps":[{"action":"dev.enter","argument":"help me create an app that tracks my expenses"}]}',
        'let\'s develop a website for my shop -> {"steps":[{"action":"dev.enter","argument":"let\'s develop a website for my shop"}]}',
        'show the mission -> {"steps":[{"action":"dev.show","argument":""}]}',
        'cancel development -> {"steps":[{"action":"dev.cancel","argument":""}]}',
    ]
    capabilities = {
        "enter": Capability("enter", False, "Start Development Mode for a software project"),
        "answer": Capability("answer", False, "Take the user's answer to the current interview question"),
        "status": Capability("status", False, "Where the current mission stands"),
        "cancel": Capability("cancel", False, "Cancel the current development mission"),
        "show": Capability("show", False, "Open the Mission window"),
        "hide": Capability("hide", False, "Close the Mission window"),
    }

    def __init__(self, bus, perms, llm=None, sandbox_root=None) -> None:
        super().__init__(bus, perms)
        self.llm = llm
        self.sandbox_root = Path(sandbox_root) if sandbox_root else (Path.home() / "YggdrasilSandbox")

    async def _execute(self, verb, params):
        arg = (params.get("argument") or "").strip()
        if verb == "enter":
            return await self._enter(arg)
        if verb == "answer":
            return await self._answer(arg)
        if verb == "status":
            return self._status()
        if verb == "cancel":
            mission.cancel()
            return {"speech": "Okay — development cancelled. The plan stays in the mission "
                              "window if you want to pick it up again later."}
        if verb == "show":
            return {"speech": "Here's the mission." if _open_window()
                    else "I can only show the mission window on the desktop."}
        if verb == "hide":
            return {"speech": _close_window()}
        raise ValueError(f"unhandled verb '{verb}'")

    # --- stages ------------------------------------------------------------------
    async def _enter(self, goal: str):
        if self.llm is None:
            return {"speech": "Development Mode needs the language model, which isn't running."}
        if not goal:
            return {"speech": "Tell me what you'd like to build."}
        old = mission.load()
        if old.get("active") and old.get("stage") in ("interview", "proposal"):
            _open_window()
            q = old.get("pending") or "shall I set it up?"
            return {"speech": f"We already have a mission going — {old.get('summary') or old.get('goal')}. "
                              f"Current question: {q} (Or say “cancel development” to start over.)",
                    "await_reply": True, "agent": self.domain}
        m = mission.start(goal)
        try:
            r = await self.llm.generate(
                system="Summarize what the user wants to BUILD in at most 8 plain words, "
                       "like 'a small game for Android'. JSON only.",
                prompt=goal, schema=_SUM_SCHEMA)
            m["summary"] = (r.parsed or {}).get("summary", "").strip() or goal[:60]
        except Exception:
            m["summary"] = goal[:60]
        mission.log(m, f"Goal captured: {m['summary']}")
        _open_window()
        mission.ask(m, _MODE_Q)
        return {"speech": f"Entering Development Mode — {m['summary']}. I'll ask questions "
                          f"until I fully understand what you want; answer “you choose” to "
                          f"any of them, or “just decide the rest” anytime. {_MODE_Q}",
                "await_reply": True, "agent": self.domain}

    async def _answer(self, text: str):
        m = mission.load()
        if not m.get("active"):
            return {"speech": "There's no development mission running. Tell me what you'd "
                              "like to build and we'll start one."}
        if _DECIDE_REST.search(text):
            mission.log(m, "User delegated the remaining decisions.")
            return await self._propose(m)
        if m.get("stage") == "proposal":
            return await self._proposal_answer(m, text)

        pending = m.get("pending", "")
        if pending == _MODE_Q:  # deterministic question 1: coding mode
            mode = ("manual" if _MODE_MANUAL.search(text) else
                    "hybrid" if _MODE_HYBRID.search(text) else
                    "full" if _MODE_FULL.search(text) else "")
            if not mode:
                mission.ask(m, _MODE_Q)
                return {"speech": "Say “myself”, “together”, or “the Agents build it” — "
                                  "which would you like?",
                        "await_reply": True, "agent": self.domain}
            m["coding_mode"] = mode
            mission.decide(m, "Coding mode",
                           {"manual": "you code, Agents assist",
                            "hybrid": "you and the Agents code together",
                            "full": "the Agents build it, you direct"}[mode])
            mission.ask(m, _NAME_Q)
            return {"speech": f"Got it — {mission.load()['decisions'][-1]['a']}. {_NAME_Q}",
                    "await_reply": True, "agent": self.domain}
        if pending == _NAME_Q:  # deterministic question 2: project name
            name = re.sub(r"[^A-Za-z0-9 _\-]", "", text).strip()[:40] or "Project"
            m["name"] = name
            mission.decide(m, "Project name", name)
            mission.log(m, f"Project named: {name}")
            return await self._next_question(m)
        if pending:  # an LLM-generated question
            mission.decide(m, pending, text.strip())
            return await self._next_question(m)
        return await self._next_question(m)

    async def _next_question(self, m: dict):
        asked = len([d for d in m.get("decisions", []) if d["q"] not in ("Coding mode", "Project name")])
        if asked >= MAX_LLM_QUESTIONS:
            return await self._propose(m)
        transcript = "\n".join(f"Q: {d['q']}\nA: {d['a']}" for d in m.get("decisions", []))
        try:
            r = await self.llm.generate(
                system=("You are a lead developer interviewing a user before building their "
                        "software project. Ask the ONE next question that most changes the "
                        "plan (features, audience, platform details, look and feel, saving, "
                        "connectivity, publishing…). Never re-ask anything in the transcript. "
                        "Short, plain, voice-friendly questions — no jargon. Set done=true "
                        "ONLY when a competent lead developer would have no plan-changing "
                        "questions left. JSON only."),
                prompt=f"Project: {m.get('summary')}\nOriginal request: {m.get('goal')}\n"
                       f"Interview so far:\n{transcript or '(none yet)'}",
                schema=_Q_SCHEMA)
            parsed = r.parsed or {}
        except Exception:
            parsed = {"done": True, "question": ""}
        if parsed.get("done") or not parsed.get("question", "").strip():
            return await self._propose(m)
        q = parsed["question"].strip()
        mission.ask(m, q)
        return {"speech": q, "await_reply": True, "agent": self.domain}

    async def _propose(self, m: dict, change: str = ""):
        transcript = "\n".join(f"Q: {d['q']}\nA: {d['a']}" for d in m.get("decisions", []))
        note = f"\nThe user asked for this change to the previous plan: {change}" if change else ""
        try:
            r = await self.llm.generate(
                system=("Produce the complete recommended plan for this software project. "
                        "Be opinionated and practical for a Linux desktop with local AI. "
                        "agents = 2 to 4 team members, each with a name like 'Kotlin "
                        "specialist' or 'Build and test runner' and a one-line specialty. "
                        "folders = relative paths. test_stages = 2 to 4 concrete pass/fail "
                        "checks. speech = at most 3 spoken sentences summarizing the plan, "
                        "ending by asking whether to set it up or change anything. "
                        "Where the user delegated a choice, choose confidently. JSON only."),
                prompt=f"Project: {m.get('summary')}\nName: {m.get('name')}\n"
                       f"Coding mode: {m.get('coding_mode')}\nInterview:\n{transcript}{note}",
                schema=_PLAN_SCHEMA)
            plan = r.parsed
        except Exception:
            plan = None
        if not plan:
            return {"speech": "I hit a snag drafting the plan — say “propose the plan” to "
                              "try again, or “cancel development”.",
                    "await_reply": True, "agent": self.domain}
        m = mission.load()
        m["plan"] = plan
        m["agents"] = [{"name": a["name"], "specialty": a["specialty"], "status": "planned"}
                       for a in plan.get("agents", [])]
        m["stage"] = "proposal"
        m["pending"] = "Shall I set it up, or would you like to change anything?"
        mission.save(m)
        mission.log(m, f"Proposal drafted: {plan.get('language')} · {plan.get('editor')} · "
                       f"{len(m['agents'])} Agents")
        return {"speech": plan.get("speech", "The plan is on screen — shall I set it up?"),
                "await_reply": True, "agent": self.domain}

    async def _proposal_answer(self, m: dict, text: str):
        if _APPROVE.match(text):
            return self._approve(m)
        if re.search(r"\bpropose (?:the )?plan\b|\btry again\b", text, re.I):
            return await self._propose(m)
        mission.log(m, f"Change requested: {text.strip()}")
        return await self._propose(m, change=text.strip())

    def _approve(self, m: dict):
        name = m.get("name") or "Project"
        safe = re.sub(r"\s+", "", name) or "Project"
        pdir = self.sandbox_root / safe
        created = []
        try:
            pdir.mkdir(parents=True, exist_ok=True)
            for rel in (m.get("plan", {}).get("folders") or [])[:12]:
                rel = str(rel).strip().lstrip("/")
                if rel and ".." not in rel:
                    (pdir / rel).mkdir(parents=True, exist_ok=True)
                    created.append(rel)
            (pdir / "MISSION.md").write_text(mission.render_markdown(m), encoding="utf-8")
        except OSError as e:
            return {"speech": f"I couldn't create the workspace: {e}"}
        m["project_dir"] = str(pdir)
        m["stage"] = "setup"
        m["pending"] = ""
        mission.save(m)
        mission.log(m, f"Workspace created: {pdir.name}/ (+{len(created)} folders) + MISSION.md")
        editor = self._open_editor(pdir)
        if editor:
            mission.log(m, f"Editor opened: {editor}")
        m = mission.load()
        for a in m.get("agents", []):
            a["status"] = "ready to activate"
        mission.save(m)
        mission.log(m, "Agent crew configured — activation lands with the build stage.")
        return {"speech": f"Done — {name} is set up: project folders and the mission plan are "
                          f"in your workspace{', and your editor is open' if editor else ''}. "
                          "The full plan stays on screen in the mission window. The Agents "
                          "writing the code is the next piece I'm building — your plan and "
                          "workspace are ready for it."}

    def _status(self):
        m = mission.load()
        if not m.get("active"):
            return {"speech": "No development mission is running."}
        stage = m.get("stage", "?")
        q = m.get("pending")
        extra = f" Current question: {q}" if q else ""
        return {"speech": f"Mission {m.get('name') or m.get('summary')}: stage {stage}, "
                          f"{len(m.get('decisions', []))} decisions made.{extra}"}

    @staticmethod
    def _open_editor(pdir: Path) -> str:
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            return ""
        for cmd, label in ((["code", str(pdir)], "Visual Studio Code"),
                           (["gnome-text-editor", str(pdir / "MISSION.md")], "Text Editor"),
                           (["xdg-open", str(pdir)], "file manager")):
            if shutil.which(cmd[0]):
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return label
                except Exception:
                    continue
        return ""


def _open_window() -> bool:
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False
    try:
        subprocess.Popen([sys.executable, "-m", "yggdrasil.ui.mission"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _close_window() -> str:
    closed = False
    try:
        for line in subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True,
                                   timeout=5).stdout.splitlines():
            low = line.lower()
            if "development mission" in low or "org.yggdrasil.mission" in low:
                subprocess.run(["wmctrl", "-i", "-c", line.split(None, 1)[0]],
                               capture_output=True, timeout=5)
                closed = True
    except Exception:
        pass
    if not closed:
        try:
            if subprocess.run(["pkill", "-f", "yggdrasil.ui.mission"],
                              capture_output=True, timeout=5).returncode == 0:
                closed = True
        except Exception:
            pass
    return "Closed the mission window." if closed else "The mission window wasn't open."
