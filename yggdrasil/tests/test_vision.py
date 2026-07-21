"""Screen vision (v1.5) — the read-only 'what am I looking at' rung.

Pins the routing (sight questions reach the Vision agent, not file-reading or web), the
capture-then-describe flow with the model stubbed, and the graceful paths when there's no
display or the vision model isn't downloaded yet.
"""
from __future__ import annotations

import pytest

from yggdrasil.core.orchestrator import _VISION_RE


@pytest.mark.parametrize("phrase", [
    "what am I looking at",
    "what is on my screen",
    "Jarvis, what's on the screen",
    "read the screen",
    "read the screen aloud",
    "describe my screen",
    "can you see what this is",
    "what does this error say",
    "what does this say",
    "look at the screen",
    "what's on screen right now",
])
def test_vision_routes(phrase):
    assert _VISION_RE.match(phrase), phrase


@pytest.mark.parametrize("phrase", [
    "read the file report.txt",           # Documents agent, not sight
    "read me the document",
    "open my screen settings",
    "what is the price of bitcoin",       # research
    "read the webpage",                   # browser
    "take a screenshot",                  # capture-to-file, a different (future) verb
])
def test_vision_leaves_others_alone(phrase):
    assert not _VISION_RE.match(phrase), phrase


# ---- capture + describe flow ----

class _VLM:
    model = "qwen2.5vl:3b"

    def __init__(self, answer="A code editor is open with a Python file."):
        self.answer = answer
        self.seen = None

    async def describe_image(self, *, system, prompt, image_b64, temperature=0.2):
        self.seen = {"prompt": prompt, "image": image_b64}
        from yggdrasil.core.llm import LLMResponse
        return LLMResponse(text=self.answer)


class _Models:
    def __init__(self, installed):
        self._installed = installed
        self.pulled = []

    async def installed(self):
        return [{"name": n} for n in self._installed]

    def start_pull(self, model, on_done=None):
        self.pulled.append(model)


def _agent(vlm, models, monkeypatch, has_screen=True, img="ZmFrZQ=="):
    import yggdrasil.agents.vision_agent as va
    from yggdrasil.core.bus import LocalBus
    from yggdrasil.core.permissions import AuthChallenge, DefaultPolicy, PermissionManager, UserChannel

    class _Ch(UserChannel):
        async def present_challenge(self, challenge: AuthChallenge) -> None:  # pragma: no cover
            pass

    monkeypatch.setattr(va.screen, "available", lambda: has_screen)
    monkeypatch.setattr(va.screen, "capture_b64", lambda: img if has_screen else None)
    return va.VisionAgent(LocalBus(), PermissionManager(DefaultPolicy(), _Ch()), vlm, models)


@pytest.mark.asyncio
async def test_look_describes_the_capture(monkeypatch):
    vlm = _VLM()
    ag = _agent(vlm, _Models(["qwen2.5vl:3b", "qwen3:14b"]), monkeypatch)
    out = await ag._execute("look", {"argument": "what am I looking at"})
    assert out["speech"] == "A code editor is open with a Python file."
    assert vlm.seen["image"] == "ZmFrZQ=="  # the screenshot was actually sent


@pytest.mark.asyncio
async def test_missing_vlm_offers_download_not_error(monkeypatch):
    models = _Models(["qwen3:14b"])  # text model only, no VLM
    ag = _agent(_VLM(), models, monkeypatch)
    out = await ag._execute("look", {"argument": ""})
    assert "download" in out["speech"].lower()
    assert models.pulled == ["qwen2.5vl:3b"]  # kicked off the pull


@pytest.mark.asyncio
async def test_no_display_is_honest(monkeypatch):
    ag = _agent(_VLM(), _Models(["qwen2.5vl:3b"]), monkeypatch, has_screen=False)
    out = await ag._execute("look", {"argument": ""})
    assert "signed in" in out["speech"].lower()


@pytest.mark.asyncio
async def test_no_vision_model_configured(monkeypatch):
    ag = _agent(None, None, monkeypatch)  # vision_llm is None
    out = await ag._execute("look", {"argument": ""})
    assert "vision model" in out["speech"].lower()


def test_vision_role_resolves_to_a_vlm_not_the_text_default(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from yggdrasil.core.models import VISION_DEFAULT, ModelManager
    mm = ModelManager("qwen3:14b")  # text default
    assert mm.resolved("vision") == VISION_DEFAULT      # never the text model
    assert mm.resolved("reasoner") == "qwen3:14b"       # other roles still fall back to default
    mm.bind("vision", "llava:7b")
    assert mm.resolved("vision") == "llava:7b"           # explicit binding wins


# ---- click by sight (v1.5.1 control rung) ----

from yggdrasil.core.orchestrator import _VCLICK_RE, _VSCROLL_RE


@pytest.mark.parametrize("phrase, target", [
    ("click the Watch Demo button", "Watch Demo"),
    ("Jarvis, click the blue subscribe button", "blue subscribe"),
    ("press the X to close it", "X to close it"),
    ("click on the login link", "login"),
    ("tap the menu icon", "menu"),
    ("hit the play button", "play"),
])
def test_click_routes_and_extracts_target(phrase, target):
    m = _VCLICK_RE.match(phrase)
    assert m and target.lower() in m.group(1).lower().strip()


@pytest.mark.parametrize("phrase", [
    "select 4",            # browser link-number, not vision
    "click 3",             # browser link-number
    "click link 5",
    "press enter",         # keystroke, not a visual click
    "hit escape",
    "press the space bar",
])
def test_click_leaves_numbers_and_keys_alone(phrase):
    assert not _VCLICK_RE.match(phrase)


@pytest.mark.parametrize("phrase", ["scroll down", "scroll up", "scroll to the bottom",
                                    "Jarvis, scroll down", "please scroll back up", "scroll a lot"])
def test_scroll_routes(phrase):
    assert _VSCROLL_RE.match(phrase)


@pytest.mark.asyncio
async def test_click_grounds_and_clicks(monkeypatch):
    import yggdrasil.agents.vision_agent as va

    class _VLM:
        model = "qwen2.5vl:3b"
        async def describe_image(self, *, system, prompt, image_b64, temperature=0.2):
            from yggdrasil.core.llm import LLMResponse
            return LLMResponse(text='{"found": true, "x_pct": 50, "y_pct": 25, "label": "Watch Demo"}')

    clicks = []
    monkeypatch.setattr(va.screen, "available", lambda: True)
    monkeypatch.setattr(va.screen, "capture_b64", lambda: "img")
    monkeypatch.setattr(va.screen, "geometry", lambda: (1920, 1080))
    monkeypatch.setattr(va.screen, "click_at", lambda x, y, button=1: clicks.append((x, y)) or True)

    from yggdrasil.core.bus import LocalBus
    from yggdrasil.core.permissions import AuthChallenge, DefaultPolicy, PermissionManager, UserChannel
    class _Ch(UserChannel):
        async def present_challenge(self, c): pass
    ag = va.VisionAgent(LocalBus(), PermissionManager(DefaultPolicy(), _Ch()), _VLM(),
                        _Models(["qwen2.5vl:3b"]))
    out = await ag._execute("click", {"argument": "the Watch Demo button"})
    assert clicks == [(960, 270)]  # 50% of 1920, 25% of 1080 — grounded, not guessed
    assert "watch demo" in out["speech"].lower()


@pytest.mark.asyncio
async def test_click_not_found_is_honest(monkeypatch):
    import yggdrasil.agents.vision_agent as va

    class _VLM:
        model = "qwen2.5vl:3b"
        async def describe_image(self, *, system, prompt, image_b64, temperature=0.2):
            from yggdrasil.core.llm import LLMResponse
            return LLMResponse(text='{"found": false, "label": ""}')

    clicked = []
    monkeypatch.setattr(va.screen, "available", lambda: True)
    monkeypatch.setattr(va.screen, "capture_b64", lambda: "img")
    monkeypatch.setattr(va.screen, "geometry", lambda: (1920, 1080))
    monkeypatch.setattr(va.screen, "click_at", lambda x, y, button=1: clicked.append(1) or True)

    from yggdrasil.core.bus import LocalBus
    from yggdrasil.core.permissions import AuthChallenge, DefaultPolicy, PermissionManager, UserChannel
    class _Ch(UserChannel):
        async def present_challenge(self, c): pass
    ag = va.VisionAgent(LocalBus(), PermissionManager(DefaultPolicy(), _Ch()), _VLM(),
                        _Models(["qwen2.5vl:3b"]))
    out = await ag._execute("click", {"argument": "the nonexistent widget"})
    assert clicked == []  # never clicks when the model didn't find it — no blind clicking
    assert "couldn't find" in out["speech"].lower()
