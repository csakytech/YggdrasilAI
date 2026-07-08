"""Mission state — the living "Development Plan" behind Development Mode.

One JSON file (single writer: the Dev agent inside the assistant; readers: the Mission
window, which polls it) holding everything about the current project mission: the goal,
the interview decisions, the pending question, the proposed plan, the Agent roster, and a
running log. Same decoupled-plain-file pattern as activity.json / schedule.json — the user
owns their data, processes stay separate, and the window survives assistant restarts.

Once a project folder exists, ``render_markdown()`` is also written there as MISSION.md —
the plan doubles as the project's own documentation.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STAGES = ("interview", "proposal", "setup", "build", "done")


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "mission.json"


def load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(m: dict) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(m, indent=2), encoding="utf-8")
    except OSError:
        pass


def active() -> bool:
    return bool(load().get("active"))


def start(goal: str) -> dict:
    m = {
        "active": True,
        "stage": "interview",
        "goal": goal.strip(),
        "summary": "",
        "name": "",
        "coding_mode": "",          # manual | hybrid | full
        "decisions": [],             # [{"q": ..., "a": ...}]
        "pending": "",               # the question awaiting an answer
        "plan": {},                  # the proposal (language/editor/folders/agents/tests/…)
        "agents": [],                # [{"name", "specialty", "status"}]
        "project_dir": "",
        "log": [],
        "started": time.time(),
    }
    save(m)
    return m


def log(m: dict, text: str) -> None:
    m.setdefault("log", []).append({"ts": time.time(), "text": text})
    del m["log"][:-40]  # keep the tail
    save(m)


def decide(m: dict, question: str, answer: str) -> None:
    m.setdefault("decisions", []).append({"q": question, "a": answer})
    m["pending"] = ""
    save(m)


def ask(m: dict, question: str) -> None:
    m["pending"] = question
    save(m)


def cancel() -> None:
    m = load()
    if m:
        m["active"] = False
        m["stage"] = "done"
        m["pending"] = ""
        log(m, "Mission cancelled.")


def render_markdown(m: dict) -> str:
    """MISSION.md — the plan as project documentation."""
    plan = m.get("plan") or {}
    lines = [f"# Development Mission — {m.get('name') or m.get('summary') or 'project'}",
             "",
             f"**Goal:** {m.get('goal', '')}",
             f"**Coding mode:** {m.get('coding_mode') or '—'}",
             ""]
    if m.get("decisions"):
        lines.append("## Decisions")
        for d in m["decisions"]:
            lines.append(f"- **{d['q']}** — {d['a']}")
        lines.append("")
    if plan:
        lines += [f"## Plan",
                  f"- **Language:** {plan.get('language', '')} — {plan.get('why_language', '')}",
                  f"- **Editor:** {plan.get('editor', '')}",
                  "- **Folders:** " + ", ".join(plan.get("folders", [])),
                  ""]
        if m.get("agents"):
            lines.append("## Agents")
            for a in m["agents"]:
                lines.append(f"- **{a['name']}** — {a['specialty']} ({a.get('status', 'planned')})")
            lines.append("")
        if plan.get("test_stages"):
            lines.append("## Test stages")
            for i, t in enumerate(plan["test_stages"], 1):
                lines.append(f"{i}. {t}")
            lines.append("")
    return "\n".join(lines)
