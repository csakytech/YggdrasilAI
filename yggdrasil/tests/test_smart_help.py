"""Smart Help — the context snapshot and the "do number 3" menu parser.

Guards two things that matter for 1.0: the help card resolves to the right context, and picking a
command by number (`_help_run_index`) never swallows a real command. That last property is
safety-critical — "select 4" and "open number 5" are BROWSER commands and must fall through to
normal routing, not be re-interpreted as help-menu picks.
"""
from __future__ import annotations

import pytest

from yggdrasil.core import context
from yggdrasil.core.orchestrator import _help_run_index


@pytest.mark.parametrize("phrase, expected", [
    ("do number 3", 3), ("number 3", 3), ("number three", 3), ("option 2", 2),
    ("run number 1", 1), ("the second option", 2), ("the third one", 3),
    ("do the first one", 1), ("run the last one", -1), ("do 3", 3), ("run three", 3),
    ("do the 2nd one", 2), ("number 10", 10), ("go with number 4", 4),
])
def test_number_pick_resolves(phrase, expected):
    assert _help_run_index(phrase) == expected


@pytest.mark.parametrize("phrase", [
    "select 4",          # browser: open link 4 — must NOT be hijacked
    "open number 5",     # browser: open link 5
    "run the project",   # dev command
    "click", "read the links", "scroll down", "go back", "make it bold",
    "cancel development", "set it up", "what's the weather", "do the dishes",
])
def test_real_commands_fall_through(phrase):
    assert _help_run_index(phrase) is None


def test_dev_mission_card_wins_and_is_stage_aware(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    from yggdrasil.core import mission
    m = mission.start("")
    m["stage"] = "interview"
    m["summary"] = "a stock ticker app"
    m["pending"] = "What language should we use?"
    mission.save(m)
    # even with a window "focused", an active mission wins
    monkeypatch.setattr(context.focus, "active_window", lambda: ("Firefox", "browser"))
    snap = context.snapshot()
    assert snap["where"] == "development"
    assert any("What language" in v for v in snap["vital"])   # shows the current question
    assert all("run" not in c or c["run"] for c in snap["commands"])


def test_files_card_has_no_runnable_command(monkeypatch, tmp_path):
    """Safety: nothing on the Files card can be fired by number — so 'delete the drafts
    folder' can never be triggered by 'do number 6'."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))  # no active mission
    monkeypatch.setattr(context.focus, "active_window", lambda: ("Files", "application"))
    snap = context.snapshot()
    assert snap["where"] == "files"
    assert [c for c in snap["commands"] if c.get("run")] == []


def test_unknown_app_never_dead_ends(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(context.focus, "active_window", lambda: ("GNU Image Manipulation Program", "application"))
    snap = context.snapshot()
    assert snap["where"] == "app"
    assert "GNU Image" in snap["title"]          # names where you are
    assert len(snap["commands"]) > 0             # still offers the universal commands
