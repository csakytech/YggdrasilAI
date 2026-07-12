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
