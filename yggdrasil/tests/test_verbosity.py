"""Reply-verbosity setting (v1.4) — the ThorAI Settings 'spoken confirmations' control.

The rule that matters, and the whole reason the feature is safe: verbosity governs ONLY action
confirmations. Questions, not-found replies, fallbacks, and informational answers (weather,
help, research) must be spoken IN FULL at every level — a user who set 'off' still hears
"Install GIMP? Say yes or no." and "I couldn't reach the internet."
"""
from __future__ import annotations

import pytest

from yggdrasil.core.bus import Result, Status, Task
from yggdrasil.core.orchestrator import Orchestrator


class _Orch(Orchestrator):
    def __init__(self):  # bypass full wiring — we only exercise _verbosity_adjust
        pass


def _adjust(action, data, level, monkeypatch):
    from yggdrasil.core import config

    monkeypatch.setattr(config, "get_verbosity", lambda: level)
    verb = action.split(".")[-1]
    return _Orch()._verbosity_adjust(action, verb, data)


FULL = {"app.search": ("Searching the web for robots.", "Searching."),
        "app.launch": ("Opening Firefox.", "Opening."),
        "file.delete": ("Deleted report.txt.", "Done."),
        "software.confirm": ("Done — GIMP is installed. Say “open gimp”.", "Done.")}


@pytest.mark.parametrize("action,texts", FULL.items())
def test_full_keeps_the_sentence(action, texts, monkeypatch):
    full, _ = texts
    assert _adjust(action, {"speech": full}, "full", monkeypatch) == full


@pytest.mark.parametrize("action,texts", FULL.items())
def test_simple_shrinks_to_a_word(action, texts, monkeypatch):
    full, brief = texts
    assert _adjust(action, {"speech": full}, "simple", monkeypatch) == brief


@pytest.mark.parametrize("action,texts", FULL.items())
def test_off_is_silent(action, texts, monkeypatch):
    full, _ = texts
    assert _adjust(action, {"speech": full}, "off", monkeypatch) == ""


# ---- the guardrails: these are NEVER shortened, at any level ----

@pytest.mark.parametrize("level", ["full", "simple", "off"])
def test_questions_always_spoken(level, monkeypatch):
    data = {"speech": "Install GIMP? Say yes or no.", "await_confirm": True, "agent": "software"}
    assert _adjust("software.install", data, level, monkeypatch) == data["speech"]


@pytest.mark.parametrize("level", ["full", "simple", "off"])
def test_notfound_always_spoken(level, monkeypatch):
    data = {"speech": "I couldn't find that folder.", "missing": True}
    assert _adjust("file.open", data, level, monkeypatch) == data["speech"]


@pytest.mark.parametrize("level", ["full", "simple", "off"])
def test_fallback_always_spoken(level, monkeypatch):
    data = {"speech": "The install failed — no network. Try again shortly.", "assist": True}
    assert _adjust("software.install", data, level, monkeypatch) == data["speech"]


@pytest.mark.parametrize("level", ["full", "simple", "off"])
def test_informational_answers_untouched(level, monkeypatch):
    # research/weather/help/memory carry no brief and aren't confirm actions -> always full
    for action in ("research.lookup", "help.show", "memory.recall", "explain.why"):
        data = {"speech": "It's twelve degrees and cloudy in Oslo."}
        assert _adjust(action, data, level, monkeypatch) == data["speech"]


def test_config_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("YGGDRASIL_VERBOSITY", raising=False)
    from yggdrasil.core import config
    assert config.get_verbosity() == "full"
    config.set_verbosity("simple")
    assert config.get_verbosity() == "simple"
    config.set_verbosity("nonsense")
    assert config.get_verbosity() == "full"
