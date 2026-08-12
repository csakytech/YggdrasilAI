"""Models screen — the data logic behind picking which brain fills which seat.

GUI-free: role_options() computes what the screen shows, apply_choice() persists a pick. The
properties that matter are that "Default" round-trips to an UNBOUND role (not a binding to the
literal word "default"), that a real model binds, and that a binding to a since-removed model is
shown rather than silently dropped — a broken role the user can see and fix beats one that
vanishes.
"""
from __future__ import annotations

import pytest

from yggdrasil.core.models import (
    DEFAULT_CHOICE, ROLES, ModelManager, apply_choice, role_options)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return ModelManager("qwen3:14b")


INSTALLED = ["qwen3:14b", "qwen2.5-coder:14b", "hf.co/x/Dolphin3-Cyber-8B-GGUF:Q4_K_M"]


def test_every_role_gets_a_row_defaulting_to_default(manager):
    rows = role_options(INSTALLED, manager.bindings(), "qwen3:14b")
    assert [r["role"] for r in rows] == list(ROLES)
    for r in rows:
        assert r["current"] == DEFAULT_CHOICE            # nothing bound yet
        assert r["choices"][0][0] == DEFAULT_CHOICE      # Default is always the first option


def test_default_round_trips_to_an_unbound_role(manager):
    apply_choice(manager, "coder", "qwen2.5-coder:14b")
    assert manager.bindings()["coder"] == "qwen2.5-coder:14b"
    # Choosing Default must UNBIND, not bind the literal word.
    apply_choice(manager, "coder", DEFAULT_CHOICE)
    assert manager.bindings()["coder"] is None
    assert "default" not in (manager._raw().get("roles") or {}).get("coder", "")


def test_binding_a_specialty_model_sticks_and_shows_as_current(manager):
    dolphin = "hf.co/x/Dolphin3-Cyber-8B-GGUF:Q4_K_M"
    apply_choice(manager, "coder", dolphin)
    apply_choice(manager, "reasoner", dolphin)          # the real pentest setup: both seats
    rows = {r["role"]: r for r in role_options(INSTALLED, manager.bindings(), "qwen3:14b")}
    assert rows["coder"]["current"] == dolphin
    assert rows["reasoner"]["current"] == dolphin
    assert rows["planner"]["current"] == DEFAULT_CHOICE  # untouched


def test_a_binding_to_a_removed_model_is_shown_not_hidden(manager):
    apply_choice(manager, "writer", "some-model-i-deleted:latest")
    rows = {r["role"]: r for r in role_options(INSTALLED, manager.bindings(), "qwen3:14b")}
    writer = rows["writer"]
    assert writer["current"] == "some-model-i-deleted:latest"
    labels = [label for _v, label in writer["choices"]]
    assert any("not installed" in x for x in labels)     # visible, so the user can re-point it


def test_planner_and_vision_carry_a_warning(manager):
    rows = {r["role"]: r for r in role_options(INSTALLED, manager.bindings(), "qwen3:14b")}
    assert rows["planner"]["warning"]                    # routing: a weak model misroutes
    assert rows["vision"]["warning"]                     # must be multimodal
    assert not rows["writer"]["warning"]                 # low-stakes seat, no nag


def test_list_installed_sync_never_raises(monkeypatch):
    from yggdrasil.core import models
    # No server reachable -> [] not an exception (the screen then says "is Ollama running?").
    assert models.list_installed_sync("http://127.0.0.1:1") == []
