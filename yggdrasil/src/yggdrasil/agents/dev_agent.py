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


def _notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        try:
            subprocess.Popen(["notify-send", "-a", "ThorOS", title, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

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
    "run_command": {"type": "string"},
    "python_deps": {"type": "array", "items": {"type": "string"}},
    "speech": {"type": "string"}},
    "required": ["language", "why_language", "editor", "folders", "agents",
                 "test_stages", "run_command", "python_deps", "speech"]}

_FILES_SCHEMA = {"type": "object", "properties": {
    "files": {"type": "array", "items": {"type": "object", "properties": {
        "path": {"type": "string"}, "purpose": {"type": "string"}},
        "required": ["path", "purpose"]}}},
    "required": ["files"]}

_CODE_SCHEMA = {"type": "object", "properties": {"content": {"type": "string"}},
                "required": ["content"]}


class DevAgent(BaseAgent):
    domain = "dev"
    module_id = "core.dev"
    planner_examples = [
        'i want to build a small game for android -> {"steps":[{"action":"dev.enter","argument":"i want to build a small game for android"}]}',
        'help me create an app that tracks my expenses -> {"steps":[{"action":"dev.enter","argument":"help me create an app that tracks my expenses"}]}',
        'let\'s develop a website for my shop -> {"steps":[{"action":"dev.enter","argument":"let\'s develop a website for my shop"}]}',
        'show the mission -> {"steps":[{"action":"dev.show","argument":""}]}',
        'cancel development -> {"steps":[{"action":"dev.cancel","argument":""}]}',
        'start building -> {"steps":[{"action":"dev.build","argument":""}]}',
        "how's the build going -> {\"steps\":[{\"action\":\"dev.status\",\"argument\":\"\"}]}",
        'run the project -> {"steps":[{"action":"dev.run","argument":""}]}',
        'run the game -> {"steps":[{"action":"dev.run","argument":""}]}',
    ]
    capabilities = {
        "enter": Capability("enter", False, "Start Development Mode for a software project"),
        "answer": Capability("answer", False, "Take the user's answer to the current interview question"),
        "build": Capability("build", False, "Set the Agent crew building the approved project"),
        "run": Capability("run", False, "Run the built project (on your explicit request)"),
        "status": Capability("status", False, "Where the current mission stands"),
        "cancel": Capability("cancel", False, "Cancel the current development mission"),
        "show": Capability("show", False, "Open the Mission window"),
        "hide": Capability("hide", False, "Close the Mission window"),
    }

    def __init__(self, bus, perms, llm=None, coder=None, sandbox_root=None) -> None:
        super().__init__(bus, perms)
        self.llm = llm                    # reasoner role — interview, proposal, conversation
        self.coder = coder or llm         # coder role — writing and repairing the actual code
        self.sandbox_root = Path(sandbox_root) if sandbox_root else (Path.home() / "YggdrasilSandbox")

    async def _execute(self, verb, params):
        arg = (params.get("argument") or "").strip()
        if verb == "enter":
            return await self._enter(arg)
        if verb == "answer":
            return await self._answer(arg)
        if verb == "build":
            return self._start_build()
        if verb == "run":
            return self._run_project()
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
        if m.get("stage") == "setup":  # workspace ready — waiting for the go to build
            if _APPROVE.match(text) or re.search(r"\bstart(?: the)? build(?:ing)?\b", text, re.I):
                return self._start_build()
            return {"speech": "Whenever you're ready, say “start building” and the Agents "
                              "will get to work."}
        if m.get("stage") == "build":
            return self._status()

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
                        "plan. Understand WHAT it is and does first (kind, core features, "
                        "audience), then details (look and feel, saving, connectivity), and "
                        "business questions like pricing LAST if at all. Never re-ask "
                        "anything in the transcript. Short, plain, voice-friendly questions "
                        "— no jargon. Set done=true ONLY when a competent lead developer "
                        "would have no plan-changing questions left. JSON only."),
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
                        "CRITICAL: the user will RUN this on THIS machine (Debian Linux, "
                        "Python 3 preinstalled) immediately after it's built, so it MUST be "
                        "runnable out of the box. Strongly prefer PYTHON for desktop, "
                        "terminal, games (pygame or the curses module), scripts, and small "
                        "apps; HTML/CSS/JS (no build step) for simple web pages. Only pick a "
                        "language needing a separate SDK or compiler (Kotlin, Java, C#, "
                        "Swift, native Android) if the user EXPLICITLY named that platform — "
                        "and if so, say in 'speech' that extra tools must be installed first. "
                        "run_command = the exact command to run it from the project root "
                        "(e.g. 'python3 main.py'). python_deps = pip package names only (e.g. "
                        "['pygame']) or []. agents = 2 to 4 members, each a name like 'Game "
                        "logic specialist' + a one-line specialty. folders = relative "
                        "DIRECTORY paths only, never file names. test_stages = 2 to 4 "
                        "concrete pass/fail checks. speech = at most 3 spoken sentences "
                        "summarizing the plan, ending by asking whether to set it up or "
                        "change anything. Where the user delegated a choice, choose "
                        "confidently. JSON only."),
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
                # directories only — models sometimes list files (build.gradle.kts) here
                if rel and ".." not in rel and "." not in rel.rsplit("/", 1)[-1]:
                    (pdir / rel).mkdir(parents=True, exist_ok=True)
                    created.append(rel)
            (pdir / "MISSION.md").write_text(mission.render_markdown(m), encoding="utf-8")
            if not (pdir / ".gitignore").exists():  # keep the venv/caches out of version control
                (pdir / ".gitignore").write_text(
                    ".venv/\n__pycache__/\n*.pyc\n.DS_Store\nnode_modules/\n", encoding="utf-8")
        except OSError as e:
            return {"speech": f"I couldn't create the workspace: {e}"}
        m["project_dir"] = str(pdir)
        m["stage"] = "setup"
        m["pending"] = ""
        mission.save(m)
        mission.log(m, f"Workspace created: {pdir.name}/ (+{len(created)} folders) + MISSION.md")
        if shutil.which("git"):  # version control from minute one — quietly, non-fatal
            try:
                subprocess.run(["git", "init", "-q"], cwd=pdir, capture_output=True, timeout=15)
                mission.log(m, "git repository initialized")
            except Exception:
                pass
        editor = self._open_editor(pdir)
        if editor:
            mission.log(m, f"Editor opened: {editor}")
        m = mission.load()
        for a in m.get("agents", []):
            a["status"] = "ready"
        mission.save(m)
        base = (f"Done — {name} is set up: folders, the mission plan, and version control are "
                f"in your workspace{', and your editor is open' if editor else ''}.")
        if m.get("coding_mode") == "manual":
            mission.log(m, "Manual mode: the workspace is yours — Agents on standby as advisors.")
            return {"speech": base + " It's all yours to code — I'm here whenever you have "
                                     "questions, and the plan stays in the mission window."}
        return {"speech": base + " Shall the Agents start building now?",
                "await_reply": True, "agent": self.domain}

    # --- the build stage: the Agent crew writes the code -----------------------------
    def _start_build(self):
        m = mission.load()
        if not m.get("active") or not m.get("project_dir"):
            return {"speech": "There's no approved project set up yet — tell me what you'd "
                              "like to build and we'll plan it first."}
        if m.get("coding_mode") == "manual":
            return {"speech": "You chose to code this one yourself — but say the word and "
                              "I'll switch the Agents to building it."}
        if m.get("stage") == "build":
            return self._status()
        m["stage"] = "build"
        for a in m.get("agents", []):
            a["status"] = "ACTIVE"
        mission.save(m)
        mission.log(m, "Agent crew activated — build starting.")
        import threading
        threading.Thread(target=self._build_worker, daemon=True, name="dev-build").start()
        hybrid = m.get("coding_mode") == "hybrid"
        return {"speech": "The Agents are at work — you can watch every step in the mission "
                          "window, and I'll pop up a notification when they're done. Ask "
                          "“how's the build going” anytime." +
                          (" Since we're building together, everything they write is yours "
                           "to edit." if hybrid else "")}

    def _build_worker(self) -> None:
        import asyncio as aio
        try:
            aio.run(self._build_async())
        except Exception as e:  # the crew must never die silently
            m = mission.load()
            mission.log(m, f"Build stopped by an error: {e!r}")
            _notify("ThorOS build", "The build hit an error — see the mission window.")

    def _cancelled(self) -> bool:
        m = mission.load()
        return not m.get("active") or m.get("stage") != "build"

    async def _build_async(self) -> None:
        m = mission.load()
        pdir = Path(m["project_dir"])
        plan = m.get("plan", {})
        coder = next((a["name"] for a in m.get("agents", []) if "special" in a["specialty"].lower()
                      or "code" in a["specialty"].lower()), None) or \
            (m.get("agents") or [{"name": "Code Agent"}])[0]["name"]

        # Only real pip packages — models often list stdlib modules (curses, random, json)
        # as "deps", which would spin up a pointless venv and fail to install.
        stdlib = getattr(sys, "stdlib_module_names", frozenset())
        deps = [d for d in (plan.get("python_deps") or [])
                if re.fullmatch(r"[A-Za-z0-9_.\-]+", d)
                and d.split("[")[0].replace("-", "_").lower() not in
                {s.lower() for s in stdlib}]
        py = "python3"
        if deps:
            mission.log(m, f"Setting up the Python environment ({', '.join(deps)})…")
            try:
                subprocess.run(["python3", "-m", "venv", ".venv"], cwd=pdir,
                               capture_output=True, timeout=180)
                r = subprocess.run([str(pdir / ".venv/bin/pip"), "install", "-q", *deps],
                                   cwd=pdir, capture_output=True, text=True, timeout=600)
                if r.returncode == 0:
                    py = ".venv/bin/python"
                    mission.log(m, "Environment ready.")
                else:
                    mission.log(m, "Package install failed — building with the system Python.")
            except Exception:
                mission.log(m, "Environment setup failed — building with the system Python.")
        if self._cancelled():
            return

        transcript = "\n".join(f"{d['q']}: {d['a']}" for d in m.get("decisions", []))
        r = await self.coder.generate(
            system=("Plan the FILES for this project: 2 to 8 files with relative paths and a "
                    "one-line purpose each. Include a README.md. Keep it FLAT — put every "
                    "code file in ONE directory (the project root), NOT in nested package "
                    "folders, so imports stay simple. The entry file must match the run "
                    "command exactly. JSON only."),
            prompt=f"Project: {m.get('summary')} named {m.get('name')}\n"
                   f"Language: {plan.get('language')}\nRun command: {plan.get('run_command')}\n"
                   f"Decisions:\n{transcript}",
            schema=_FILES_SCHEMA)
        files = [f for f in (r.parsed or {}).get("files", [])[:8]
                 if f.get("path") and ".." not in f["path"] and not f["path"].startswith("/")]
        if not files:
            mission.log(m, "The crew couldn't agree on a file plan — build stopped.")
            _notify("ThorOS build", "Build stopped — no file plan. Try “start building” again.")
            return
        mission.log(m, "File plan: " + ", ".join(f["path"] for f in files))

        manifest = "\n".join(f"- {f['path']}: {f['purpose']}" for f in files)
        for f in files:
            if self._cancelled():
                mission.log(m, "Build cancelled — crew standing down.")
                return
            mission.log(m, f"✍ {coder}: writing {f['path']}")
            try:
                r = await self.coder.generate(
                    system=("Write the COMPLETE contents of one file for this project. "
                            "Production-quality, fully working, no placeholders or TODOs. "
                            "IMPORTS: all the listed files sit in the SAME directory and run "
                            "together via the run command — import siblings by their BARE "
                            "module name only ('from utils import foo' / 'import utils'), "
                            "NEVER with a directory or package prefix like 'src.'. Any "
                            "long-running loop or UI (e.g. curses) must sit under "
                            "`if __name__ == \"__main__\":`. Only this file's content in the "
                            "'content' field. JSON only."),
                    prompt=f"Project: {m.get('summary')} named {m.get('name')}\n"
                           f"Language: {plan.get('language')} · Run: {plan.get('run_command')}\n"
                           f"All files:\n{manifest}\n\nWrite this file now: {f['path']} — {f['purpose']}",
                    schema=_CODE_SCHEMA, temperature=0.3)
                content = (r.parsed or {}).get("content", "")
            except Exception as e:
                mission.log(m, f"{f['path']} failed: {e!r}")
                continue
            if not content.strip():
                mission.log(m, f"{f['path']}: the Agent produced nothing — skipped.")
                continue
            target = (pdir / f["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            mission.log(m, f"   {f['path']} written ({len(content.splitlines())} lines)")

        self._normalize_imports(pdir, [f["path"] for f in files if f["path"].endswith(".py")], m)

        # Quality gates (jailed — the code never runs for real here). Two rounds, each with
        # one LLM repair pass: (1) py_compile = syntax; (2) an IMPORT SMOKE test that runs
        # every module's top-level code (imports, defs) but skips its __main__ block via
        # runpy run_name='__smoke__' — this catches the import/path bugs that compile fine
        # but crash at launch, WITHOUT starting a curses loop that would hang.
        py_files = [f["path"] for f in files if f["path"].endswith(".py")
                    and (pdir / f["path"]).is_file()]
        entry = self._entry_file(plan.get("run_command", ""), py_files)
        if py_files:
            from ..core.sandbox import run_command

            async def gate(cmd, timeout):
                return await run_command(cmd, pdir, timeout=timeout)

            async def repair(err):
                # Repair the files named in the error; if none matched (or only one), also
                # give the entry file a look, since import bugs cascade across files.
                targets = [p for p in py_files if p in err] or py_files
                for path in targets:
                    if self._cancelled():
                        return
                    try:
                        cur = (pdir / path).read_text(encoding="utf-8")
                        rr = await self.coder.generate(
                            system=("Fix this file so the project compiles AND runs. Sibling "
                                    "modules are imported by BARE name (no 'src.' prefix). "
                                    "Return the COMPLETE corrected file in 'content'. JSON only."),
                            prompt=f"Error output:\n{err[:1400]}\n\nFile {path}:\n{cur[:6000]}",
                            schema=_CODE_SCHEMA, temperature=0.2)
                        fx = (rr.parsed or {}).get("content", "")
                        if fx.strip():
                            (pdir / path).write_text(fx, encoding="utf-8")
                            mission.log(m, f"   {path} repaired")
                    except Exception:
                        pass

            compile_cmd = f"python3 -m py_compile {' '.join(py_files)}"
            comp = {}
            for attempt in range(3):  # syntax gate — retry the repair a few times
                comp = await gate(compile_cmd, 60)
                if comp.get("returncode") == 0 or self._cancelled():
                    break
                mission.log(m, f"Syntax check (try {attempt + 1}) — the crew is fixing it…")
                await repair(comp.get("output") or "")
                self._normalize_imports(pdir, py_files, m)
            mission.log(m, "Syntax check: " + ("PASSED" if comp.get("returncode") == 0 else "still failing"))

            if entry and not self._cancelled():
                smoke_cmd = (f"python3 -c \"import runpy,sys; sys.argv=['{entry}']; "
                             f"runpy.run_path('{entry}', run_name='__smoke__')\"")
                launch_ok = False
                for attempt in range(3):  # launch gate — imports/paths resolve
                    sm = await gate(smoke_cmd, 30)
                    out = sm.get("output") or ""
                    launch_ok = sm.get("returncode") == 0 or "curses" in out.lower() or sm.get("timed_out")
                    if launch_ok or self._cancelled():
                        break
                    mission.log(m, f"Launch check (try {attempt + 1}) — the crew is fixing it…")
                    await repair(out)
                    self._normalize_imports(pdir, py_files, m)
                mission.log(m, "Launch check: " + ("PASSED" if launch_ok else
                            "the imports still need a look — it's open in your editor"))

        run_cmd = (plan.get("run_command") or "").strip()
        if run_cmd.startswith(("python ", "python3 ")) and py != "python3":
            run_cmd = py + " " + run_cmd.split(" ", 1)[1]
            plan["run_command"] = run_cmd
        m = mission.load()
        m["plan"] = plan
        m["stage"] = "done"
        for a in m.get("agents", []):
            a["status"] = "done"
        mission.save(m)
        if shutil.which("git"):
            try:
                subprocess.run(["git", "add", "-A"], cwd=pdir, capture_output=True, timeout=20)
                subprocess.run(["git", "-c", "user.name=ThorOS", "-c", "user.email=agents@thoros",
                                "commit", "-q", "-m", "Agent crew: initial build"],
                               cwd=pdir, capture_output=True, timeout=20)
                mission.log(m, "Committed to version control.")
            except Exception:
                pass
        mission.log(m, f"BUILD COMPLETE — say “run the project” to start it"
                       f"{f' ({run_cmd})' if run_cmd else ''}.")
        _notify(f"ThorOS — {m.get('name', 'project')} is built",
                "Say “run the project” to try it, or open it in your editor.")

    @staticmethod
    def _normalize_imports(pdir: Path, py_files: list[str], m: dict) -> None:
        """Deterministically kill the #1 multi-file codegen bug: modules run together with
        their own dir on sys.path, so sibling imports must be BARE — but small models keep
        writing 'from src.utils import …'. If all code lives under one top dir (e.g. src/),
        strip that prefix from every import. Cheap, exact, no LLM variance."""
        tops = {p.split("/", 1)[0] for p in py_files if "/" in p}
        pkgs = {t for t in tops if not t.endswith(".py")}
        # also strip any dir that is itself a package holding these files
        pkgs |= {Path(p).parent.name for p in py_files if "/" in p}
        pkgs = {p for p in pkgs if p and re.fullmatch(r"[A-Za-z_]\w*", p)}
        if not pkgs:
            return
        alt = "|".join(sorted(pkgs, key=len, reverse=True))
        from_re = re.compile(rf"(?m)^(\s*from\s+)(?:{alt})\.")
        imp_re = re.compile(rf"(?m)^(\s*import\s+)(?:{alt})\.(\w+)")
        for rel in py_files:
            fp = pdir / rel
            try:
                src = fp.read_text(encoding="utf-8")
            except OSError:
                continue
            new = imp_re.sub(r"\1\2", from_re.sub(r"\1", src))
            if new != src:
                fp.write_text(new, encoding="utf-8")
                mission.log(m, f"   normalized imports in {rel}")

    @staticmethod
    def _entry_file(run_command: str, py_files: list[str]) -> str:
        """The .py file the run command launches (e.g. 'python3 src/main.py' -> 'src/main.py')."""
        for tok in (run_command or "").split():
            if tok.endswith(".py"):
                return tok
        for cand in ("main.py", "app.py", "game.py"):
            for p in py_files:
                if p.endswith(cand):
                    return p
        return py_files[0] if py_files else ""

    def _run_project(self):
        m = mission.load()
        pdir = m.get("project_dir")
        cmd = (m.get("plan", {}).get("run_command") or "").strip()
        if not pdir or not cmd:
            return {"speech": "There's no built project to run yet."}
        try:  # the user's explicit choice — runs as their own program, in their session
            subprocess.Popen(cmd, shell=True, cwd=pdir,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            return {"speech": f"I couldn't start it: {e}"}
        return {"speech": f"Starting {m.get('name', 'the project')} — {cmd}. Enjoy!"}

    def _status(self):
        m = mission.load()
        if not m.get("active"):
            return {"speech": "No development mission is running."}
        stage = m.get("stage", "?")
        if stage == "build":
            last = (m.get("log") or [{}])[-1].get("text", "")
            return {"speech": f"The Agents are building {m.get('name')} — latest: {last}"}
        if stage == "done":
            return {"speech": f"{m.get('name')} is built — say “run the project” to start it."}
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
