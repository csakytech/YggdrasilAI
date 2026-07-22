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


# ---- "did you mean?" — a mishearing is offered as a question, not a silent swap ----

@pytest.mark.asyncio
async def test_mishearing_asks_did_you_mean(monkeypatch):
    import yggdrasil.agents.software_agent as sa
    from yggdrasil.core.bus import LocalBus
    from yggdrasil.core.permissions import AuthChallenge, DefaultPolicy, PermissionManager, UserChannel

    class _Ch(UserChannel):
        async def present_challenge(self, c): pass

    ag = sa.SoftwareAgent(LocalBus(), PermissionManager(DefaultPolicy(), _Ch()))

    async def avail(pkg):        # nothing matches by exact name; obs-studio is available
        return pkg == "obs-studio"

    async def not_installed(pkg):
        return False

    async def no_near(spoken):
        return []

    monkeypatch.setattr(ag, "_available", avail)
    monkeypatch.setattr(ag, "_installed", not_installed)
    monkeypatch.setattr(ag, "_nearby", no_near)

    out = await ag._execute("install", {"argument": "OSB Studio"})
    # asks to confirm the correction rather than failing OR silently installing
    assert out.get("await_confirm")
    assert "did you mean" in out["speech"].lower()
    assert "obs studio" in out["speech"].lower()
    assert ag._pending and ag._pending["pkg"] == "obs-studio"
    # and it NEVER claims to be installing something it isn't
    assert "installing" not in out["speech"].lower()


# ---- spoken completion + announce-once (Jarvis says when a job finishes) ----

def test_spoken_completion_phrasing():
    assert jobs.spoken_completion(
        {"state": "done", "title": "Installing OBS Studio", "done_message": ""}
    ) == "OBS Studio has finished installing."
    # a job with its own message wins (the "200,000 bottles" case)
    assert jobs.spoken_completion(
        {"state": "done", "title": "Counting bottles",
         "done_message": "I have finished counting 200,000 bottles."}
    ) == "I have finished counting 200,000 bottles."
    # failures are announced honestly, not as success
    fail = jobs.spoken_completion({"state": "error", "title": "Installing Foo", "detail": "no network"})
    assert "didn't finish" in fail and "no network" in fail


def test_announce_once(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    jobs.start("j1", "Software", "Installing OBS Studio", 0.0,
               done_message="OBS Studio has finished installing.")
    jobs.finish("j1", 5.0, ok=True)
    pending = jobs.unannounced_finished(6.0)
    assert len(pending) == 1 and pending[0]["id"] == "j1"
    jobs.mark_announced("j1")
    assert jobs.unannounced_finished(6.0) == []   # never announced twice
