"""Desktop background by voice — picking the right picture, and never the wrong kind of wrong.

"Make that photo my background" is a menu someone who can't use a mouse cannot otherwise reach,
so this capability is squarely on-mission. The model may CHOOSE among the user's real pictures,
but it selects by index from a list we supply and every pick is validated, so the worst case is
the wrong real picture — never a path the model invented.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yggdrasil.agents import system_agent as sa
from yggdrasil.core.bus import LocalBus
from yggdrasil.core.permissions import AuthChallenge, DefaultPolicy, PermissionManager, UserChannel


class _Ch(UserChannel):
    async def present_challenge(self, challenge: AuthChallenge) -> None:  # pragma: no cover
        pass


class _Resp:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = ""


class _LLM:
    def __init__(self, parsed):
        self._parsed = parsed
        self.asked = None

    async def generate(self, **kw):
        self.asked = kw
        return _Resp(self._parsed)


PICS = [Path(p) for p in (
    "/home/u/Pictures/sunset over the bay.jpg",
    "/home/u/Pictures/mountains.png",
    "/home/u/Downloads/IMG_20260728_113355.jpg",
    "/usr/share/backgrounds/yggdrasil/thoros.png",
)]


def _agent(llm=None):
    return sa.SystemAgent(LocalBus(), PermissionManager(DefaultPolicy(), _Ch()), llm)


def _pick(want, llm=None):
    return asyncio.run(_agent(llm)._pick_picture(want, list(PICS)))


def test_exact_stem_wins_without_the_model():
    llm = _LLM({"number": 2})           # would pick mountains if consulted
    assert _pick("mountains", llm).name == "mountains.png"
    assert llm.asked is None            # never asked — an exact name needs no reasoning


def test_unique_substring_matches():
    assert _pick("sunset").name == "sunset over the bay.jpg"


def test_model_picks_from_the_real_list():
    # "that photo from my phone" matches nothing by name; the model chooses number 3.
    assert _pick("the photo from my phone", _LLM({"number": 3})).name == "IMG_20260728_113355.jpg"


@pytest.mark.parametrize("n", [0, -1, 99, 5])
def test_out_of_range_choices_are_refused(n):
    """A number outside the list must never become an action."""
    assert _pick("something vague", _LLM({"number": n})) is None


def test_malformed_model_reply_is_refused():
    assert _pick("something vague", _LLM({})) is None
    assert _pick("something vague", _LLM(None)) is None


def test_no_model_and_no_name_match_gives_up_honestly():
    assert _pick("something vague", None) is None


def test_no_pictures_does_not_dead_end(monkeypatch):
    ag = _agent()
    monkeypatch.setattr(ag, "_pictures", lambda limit=60: [])
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(sa.shutil, "which", lambda _n: "/usr/bin/gsettings")
    out = asyncio.run(ag._wallpaper("anything"))
    assert out["assist"] is True        # escalates to the backbone rather than stopping dead
    assert "pictures" in out["speech"].lower()


def test_no_desktop_session_is_honest(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    out = asyncio.run(_agent()._wallpaper("sunset"))
    assert "signed in" in out.lower()
