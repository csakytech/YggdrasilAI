"""Task Agent — the CLI-synthesis rung: turn a plain request into a shell command, preview it, and run
it SAFELY on the user's say-so.

The bridge for "there's no voice app for that": Linux already has a command-line tool for almost
everything, and the local model is good at picking it. Jarvis works out the command, shows exactly what
it'll do, and runs it inside a bubblewrap jail confined to the target directory (no network, nothing
else writable) — so a wrong command can't escape its blast radius. Read-only commands just run and
report; anything that changes files is previewed and waits for a spoken "run it".

If the request isn't actually a shell task (a question, or it needs a whole application), it returns
`assist:True` so the reasoning backbone takes over — this is one rung of the help ladder.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..core import sandbox
from ..core.permissions import Capability
from .base import BaseAgent
from .command_agent import _DENY

_SCHEMA = {
    "type": "object",
    "properties": {
        "feasible": {"type": "boolean"},
        "command": {"type": "string"},
        "explanation": {"type": "string"},
        "read_only": {"type": "boolean"},
        "workdir": {"type": "string"},
    },
    "required": ["feasible", "command", "explanation", "read_only"],
}
# Backstop: tokens that mean the command CHANGES something, so it's never auto-run as "read-only".
_MODIFY = re.compile(
    r"(^|\s)(rm|rmdir|mv|cp|dd|mkfs|mkdir|touch|chmod|chown|ln|truncate|tee|install|apt|apt-get|"
    r"dpkg|pip|sed\s+-i|shred|unlink)\b|>>|(?<![0-9])>", re.I)
_DENY_RE = [re.compile(p) for p in _DENY]


class TaskAgent(BaseAgent):
    domain = "task"
    module_id = "core.task"
    planner_examples = [
        'use the terminal to count the lines in my python files -> {"steps":[{"action":"task.do","argument":"count the lines in my python files"}]}',
        'figure out how to convert report.docx to pdf -> {"steps":[{"action":"task.do","argument":"convert report.docx to pdf"}]}',
    ]
    capabilities = {
        "do": Capability("do", False, "Work out a shell command for a task and preview it"),
        "confirm": Capability("confirm", False, "Run the previewed command"),
        "cancel": Capability("cancel", False, "Discard the previewed command"),
    }

    def __init__(self, bus, perms, llm=None, workspace: str | os.PathLike = ".") -> None:
        super().__init__(bus, perms)
        self.llm = llm
        self.workspace = Path(workspace)
        self._pending: dict | None = None  # {command, workdir, explanation} awaiting a spoken "run it"

    async def _execute(self, verb, params):
        arg = (params.get("argument") or "").strip()
        if verb == "do":
            return await self._do(arg)
        if verb == "confirm":
            return await self._confirm()
        if verb == "cancel":
            return {"speech": self._cancel()}
        raise ValueError(f"unhandled verb '{verb}'")

    async def _do(self, goal):
        if not goal:
            return {"speech": "What would you like me to do?"}
        if not self.llm:
            return {"speech": "", "assist": True}
        plan = await self._synthesize(goal)
        cmd = ((plan or {}).get("command") or "").strip()
        if not plan or not plan.get("feasible") or not cmd:
            return {"speech": "", "assist": True}     # not a shell task -> reasoning backbone
        if self._denied(cmd):
            return {"speech": "I won't do that — it looks destructive, so I've stopped."}
        if not sandbox.sandbox_available():
            return {"speech": f"I worked out a command for that, but I can't run it safely without the "
                              f"sandbox installed.", "assist": True}
        workdir = self._resolve_workdir((plan.get("workdir") or "").strip())
        read_only = bool(plan.get("read_only")) and not _MODIFY.search(cmd)
        expl = (plan.get("explanation") or "").strip()
        where = self._friendly_dir(workdir)
        if read_only:  # safe to just run (contained) and report
            res = await sandbox.run_command(cmd, workdir)
            return {"speech": self._report(expl, res)}
        # Modifying — preview and wait for an explicit spoken go-ahead.
        self._pending = {"command": cmd, "workdir": workdir, "explanation": expl}
        return {"speech": f"{expl} I'd run “{cmd}” in {where}, contained to that folder. "
                          f"Say “run it” to go ahead, or “cancel”."}

    async def _confirm(self):
        p = self._pending
        if not p:
            return {"speech": "There's nothing waiting to run. Tell me what you'd like done."}
        self._pending = None
        res = await sandbox.run_command(p["command"], p["workdir"])
        return {"speech": self._report(p["explanation"], res, did=True)}

    def _cancel(self):
        if not self._pending:
            return "There's nothing to cancel."
        self._pending = None
        return "Okay, I won't run it."

    # --- helpers ---
    async def _synthesize(self, goal) -> dict:
        system = (
            "You translate a user's request into ONE shell command for Debian Linux (bash). If the "
            "request is NOT an actionable shell task — it's a question, chit-chat, or it needs a full "
            "GUI application — set feasible=false. Use standard tools (coreutils, ffmpeg, imagemagick, "
            "pandoc, poppler-utils, etc.). The command runs INSIDE the working directory, so prefer "
            "RELATIVE paths. read_only=true ONLY if it makes no changes at all (no writes/deletes/"
            "installs). Put a one-sentence plain-English explanation in 'explanation' and a working "
            "directory in 'workdir' (use ~ if unsure). Output JSON only. /no_think"
        )
        try:
            resp = await self.llm.generate(system=system, prompt=goal, schema=_SCHEMA, temperature=0.1)
            return resp.parsed or {}
        except Exception:
            return {}

    @staticmethod
    def _denied(cmd: str) -> bool:
        c = cmd.lower()
        return any(p.search(c) for p in _DENY_RE)

    def _resolve_workdir(self, hint: str) -> str:
        if hint and hint not in ("~", "$HOME"):
            p = Path(os.path.expanduser(hint))
            if not p.is_absolute():
                p = Path.home() / hint
            if p.is_dir():
                return str(p)
        self.workspace.mkdir(parents=True, exist_ok=True)
        return str(self.workspace)

    @staticmethod
    def _friendly_dir(path: str) -> str:
        try:
            rel = Path(path).resolve().relative_to(Path.home())
            return "your home folder" if str(rel) == "." else f"~/{rel}"
        except ValueError:
            return path

    @staticmethod
    def _report(expl: str, res: dict, did: bool = False) -> str:
        if res.get("timed_out"):
            return "That took too long, so I stopped it."
        out = (res.get("output") or "").strip()
        rc = res.get("returncode")
        if rc not in (0, None):
            tail = out.splitlines()[-1] if out else "no details"
            return f"It didn't work — {tail}"
        if out:
            lines = out.splitlines()
            snippet = "; ".join(lines[:8])
            return ("Done. " if did else "") + (snippet[:400] + "…" if len(snippet) > 400 else snippet)
        return f"Done — {expl}" if expl else "Done."
