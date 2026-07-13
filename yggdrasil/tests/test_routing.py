"""Deterministic routes added from live QA findings (v1.2 RC)."""
from __future__ import annotations

import pytest

from yggdrasil.core.orchestrator import _OPEN_AND_SEARCH_RE


@pytest.mark.parametrize("phrase, query", [
    ("open google and search for thoros", "thoros"),
    ("Jarvis, open google and search for ThorOS", "ThorOS"),
    ("can you open google and look up electric bikes", "electric bikes"),
    ("go to google and search self driving cars", "self driving cars"),
    ("open the browser and search for the weather in oslo", "the weather in oslo"),
    ("open firefox and google quantum computing", "quantum computing"),
])
def test_open_and_search_is_one_search(phrase, query):
    m = _OPEN_AND_SEARCH_RE.match(phrase)
    assert m and m.group(1) == query


@pytest.mark.parametrize("phrase", [
    "open google.com",                      # plain browse — no search half
    "open google",                          # just the site
    "search for cats",                      # plain search — the app agent's normal route
    "open the terminal and run ls",         # different app, different intent
    "open google maps",                     # an app/site name containing 'google'
])
def test_open_and_search_leaves_others_alone(phrase):
    assert not _OPEN_AND_SEARCH_RE.match(phrase)


def test_open_in_firefox_executes(monkeypatch):
    """Regression: rc3 shipped a NameError (self in a @staticmethod) inside _open_in_firefox —
    every browse/search failed with a friendly apology. Exercise the real code path with the
    process spawn stubbed out."""
    from yggdrasil.agents import app_agent

    calls = []
    monkeypatch.setattr(app_agent.subprocess, "Popen",
                        lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(app_agent.AppsAgent, "_firefox_process", staticmethod(lambda: False))
    app_agent.AppsAgent._open_in_firefox("https://example.com")
    assert calls and calls[0][-1] == "https://example.com" and "--marionette" in calls[0]


def test_open_in_firefox_waits_for_starting_instance(monkeypatch):
    from yggdrasil.agents import app_agent

    calls = []
    windows = iter([False, False, True])  # firefox process exists; window appears on 3rd poll
    monkeypatch.setattr(app_agent.subprocess, "Popen",
                        lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(app_agent.AppsAgent, "_firefox_process", staticmethod(lambda: True))
    monkeypatch.setattr(app_agent.AppsAgent, "_firefox_window_up",
                        staticmethod(lambda: next(windows, True)))
    monkeypatch.setattr(app_agent.time, "sleep", lambda s: None)
    app_agent.AppsAgent._open_in_firefox("https://example.com")
    assert calls  # the URL was still sent after the wait


def test_default_search_engine_is_captcha_free(monkeypatch, tmp_path):
    """Google CAPTCHAs marionette-flagged browsers — a hard wall for hands-free users. The
    default engine must never be google unless the user explicitly chose it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("YGGDRASIL_SEARCH_ENGINE", raising=False)
    from yggdrasil.core import config
    assert config.get_search_engine() == "duckduckgo"
    config.set_search_engine("google")
    assert config.get_search_engine() == "google"
    config.set_search_engine("something-weird")
    assert config.get_search_engine() == "duckduckgo"


def test_chat_pref_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from yggdrasil.core import config
    assert config.get_chat_pref() == ("assistant", "")     # sane default
    config.set_chat_pref("chat", "qwen3:14b")
    assert config.get_chat_pref() == ("chat", "qwen3:14b")
    config.set_chat_pref("bogus-mode", "")
    assert config.get_chat_pref()[0] == "assistant"        # invalid mode falls back


def test_settings_route():
    from yggdrasil.core.orchestrator import _SETTINGS_RE
    for p in ("open thorai settings", "open the assistant settings", "show voice settings",
              "Jarvis, open thor ai settings", "bring up jarvis settings", "open thoros settings"):
        assert _SETTINGS_RE.match(p), p
    for p in ("open settings", "open system settings", "open the settings app",
              "change my settings for the terminal"):
        assert not _SETTINGS_RE.match(p), p
