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
