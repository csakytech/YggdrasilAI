"""Background jobs + honest status (v1.5.2) — Jarvis never leaves you hanging OR lies.

Driven by a real transcript: 'OBS' was misheard as 'OSB' so the install failed, then the
reasoning backbone FABRICATED 'I'm still trying to install it'. These tests pin the three
fixes: the mishearing resolves, the not-found answer is honest (no fabrication hook), the
background-jobs registry reports the TRUTH, and the status route reads it.
"""
from __future__ import annotations

import pytest

from yggdrasil.core import jobs
from yggdrasil.core.orchestrator import _JOBS_STATUS_RE, _JOBS_WINDOW_RE


# ---- the jobs registry ----

def test_job_lifecycle_and_truthful_description(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    t0 = 1000.0
    assert "not working on anything" in jobs.describe(jobs.recent(t0), t0)

    jobs.start("install-obs-studio", "Software", "Installing OBS Studio", t0)
    running = jobs.active(t0 + 30)
    assert len(running) == 1
    line = jobs.describe(jobs.recent(t0 + 90), t0 + 90)
    assert "Installing OBS Studio" in line and "running for 1 minute" in line

    jobs.update("install-obs-studio", t0 + 40, progress=45.0, detail="Unpacking…")
    assert "45%" in jobs.describe(jobs.recent(t0 + 40), t0 + 40)

    jobs.finish("install-obs-studio", t0 + 120, ok=True, detail="OBS Studio is installed")
    assert jobs.active(t0 + 130) == []                      # no longer running
    assert "finished" in jobs.describe(jobs.recent(t0 + 130), t0 + 130)  # still recent


def test_failed_job_reports_honestly(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    jobs.start("install-foo", "Software", "Installing Foo", 0.0)
    jobs.finish("install-foo", 5.0, ok=False, detail="install failed (exit 100)")
    line = jobs.describe(jobs.recent(6.0), 6.0)
    assert "didn't work" in line and "exit 100" in line


# ---- routing: status questions read the registry, not the LLM ----

@pytest.mark.parametrize("phrase", [
    "how is it going with the OBS Studio install?",
    "how's the install going",
    "what are you working on",
    "what are you doing",
    "are you still installing it",
    "are you done",
    "what's the status",
    "Jarvis, how's the download coming along",
])
def test_status_questions_route(phrase):
    assert _JOBS_STATUS_RE.match(phrase), phrase


@pytest.mark.parametrize("phrase", [
    "install obs studio",          # a command, not a status question
    "how do I install obs",        # a how-to, not status
    "what is the weather",
])
def test_status_leaves_others_alone(phrase):
    assert not _JOBS_STATUS_RE.match(phrase), phrase


@pytest.mark.parametrize("phrase", ["open the tasks window", "show the jobs list",
                                    "Jarvis, open the activity window", "bring up the tasks panel"])
def test_tasks_window_routes(phrase):
    assert _JOBS_WINDOW_RE.match(phrase), phrase


# ---- the mishearing fix ----

def test_fuzzy_known_recovers_transposition():
    from yggdrasil.agents.software_agent import SoftwareAgent
    assert SoftwareAgent._fuzzy_known("osb studio") == "obs-studio"   # letters transposed
    assert SoftwareAgent._fuzzy_known("osb") == "obs-studio"
    assert SoftwareAgent._fuzzy_known("gimp") == "gimp"               # exact still fine
    assert SoftwareAgent._fuzzy_known("banana pancakes") is None      # not a wild guess
