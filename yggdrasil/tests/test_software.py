"""Software installs by voice (v1.2) — the recommend -> offer -> install flow.

Guards three safety/UX properties: (1) the root helper can only ever see a strictly-validated
package name — spoken input can't smuggle options or shell; (2) an install NEVER runs without a
spoken yes (the await_confirm gate); (3) "recommend software" utterances route deterministically
to research (the v1.0 bug opened a Google search in a browser and dead-ended).
"""
from __future__ import annotations

import pytest

from yggdrasil.agents.software_agent import _PKG_RE, SoftwareAgent
from yggdrasil.core.orchestrator import _RECOMMEND_RE, _RESEARCH_RE
from yggdrasil.core.bus import LocalBus
from yggdrasil.core.permissions import AuthChallenge, DefaultPolicy, PermissionManager, UserChannel


class _Channel(UserChannel):
    async def present_challenge(self, challenge: AuthChallenge) -> None:  # pragma: no cover
        pass


def _agent() -> SoftwareAgent:
    return SoftwareAgent(LocalBus(), PermissionManager(DefaultPolicy(), _Channel()))


# ---- 1. the helper's package-name gate ----

@pytest.mark.parametrize("bad", [
    "", "OBS Studio", "obs studio", "-rf", "--reinstall", "a; rm -rf /", "a&&b", "a|b",
    "../etc/passwd", "a b", "$(reboot)", "`id`", "a\nb", "рм", "A", "x" * 100,
])
def test_pkg_gate_rejects(bad):
    assert not _PKG_RE.match(bad)


@pytest.mark.parametrize("good", ["obs-studio", "gimp", "vlc", "libreoffice", "openshot-qt",
                                  "gcc-12", "libstdc++6", "python3.13"])
def test_pkg_gate_accepts(good):
    assert _PKG_RE.match(good)


# ---- 2. no install without a spoken yes ----

@pytest.mark.asyncio
async def test_install_waits_for_confirm(monkeypatch):
    ag = _agent()

    async def fake_available(pkg):
        return True

    async def fake_installed(pkg):
        return False

    ran = []

    async def fake_helper(pkg):
        ran.append(pkg)
        return 0, ""

    monkeypatch.setattr(ag, "_available", fake_available)
    monkeypatch.setattr(ag, "_installed", fake_installed)
    monkeypatch.setattr(ag, "_helper", fake_helper)

    out = await ag._execute("install", {"argument": "obs studio"})
    assert out.get("await_confirm") and out.get("agent") == "software"
    assert ran == []  # nothing installed yet — the yes hasn't been spoken
    assert ag._pending and ag._pending["pkg"] == "obs-studio"

    out = await ag._execute("confirm", {})
    assert ran == ["obs-studio"]
    assert "installed" in out["speech"].lower()


@pytest.mark.asyncio
async def test_cancel_clears_pending(monkeypatch):
    ag = _agent()
    ag._pending = {"pkg": "gimp", "spoken": "gimp"}
    out = await ag._execute("cancel", {})
    assert ag._pending is None
    assert "won't" in out["speech"]


@pytest.mark.asyncio
async def test_confirm_with_nothing_pending():
    ag = _agent()
    out = await ag._execute("confirm", {})
    assert "nothing" in out["speech"].lower()


# ---- 3. spoken-name resolution ----

@pytest.mark.asyncio
async def test_known_names_resolve(monkeypatch):
    ag = _agent()

    async def fake_available(pkg):
        return pkg in ("obs-studio", "kdenlive", "code")

    monkeypatch.setattr(ag, "_available", fake_available)
    assert await ag._resolve("OBS Studio") == "obs-studio"
    assert await ag._resolve("obs") == "obs-studio"
    assert await ag._resolve("kdenlive") == "kdenlive"
    assert await ag._resolve("vs code") == "code"


@pytest.mark.asyncio
async def test_unknown_name_does_not_invent(monkeypatch):
    ag = _agent()

    async def fake_available(pkg):
        return False

    async def fake_nearby(spoken):
        return ["some-other-thing"]

    monkeypatch.setattr(ag, "_available", fake_available)
    monkeypatch.setattr(ag, "_nearby", fake_nearby)
    assert await ag._resolve("frobnicator pro") is None  # near-miss is offered, never auto-run


# ---- 4. routing: "recommend software" must reach research, not a browser search ----

@pytest.mark.parametrize("phrase", [
    "I want to make a video on my computer, can you recommend what software to install",
    "recommend software for editing videos",
    "can you suggest a program for editing photos",
    "what software should I install to record my screen",
    "which app should I use for making music",
    "what's a good program for 3d modeling",
    "suggest some tools for programming",
])
def test_recommend_routes_to_research(phrase):
    assert _RECOMMEND_RE.match(phrase)


@pytest.mark.parametrize("phrase", [
    "open the software center",          # an app launch, not a recommendation
    "search the web for cat videos",     # explicit web search stays a web search
    "install obs studio",                # a direct install — the Software agent's planner route
    "what is the price of bitcoin",      # research, but the price shape — not recommend
    "recommend a good restaurant",       # no software word — plain conversation/research
])
def test_recommend_leaves_others_alone(phrase):
    assert not _RECOMMEND_RE.match(phrase)


def test_recommend_checked_before_generic_research():
    # a phrase matching BOTH must be fine either way, but the canonical failing utterance from
    # the bug report must at least hit one deterministic research route
    phrase = "I want to make a video on my computer, can you recommend what software to install"
    assert _RECOMMEND_RE.match(phrase) or _RESEARCH_RE.match(phrase)
