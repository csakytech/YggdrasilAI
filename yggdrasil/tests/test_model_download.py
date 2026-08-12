"""Downloading a model — especially a HuggingFace one — must succeed when possible and fail
honestly when not.

The live failure: "download Dolphin3-Cyber-8B from huggingface" -> Jarvis said "downloading now"
-> nothing downloaded, no explanation. Root causes: the pull target was fabricated from a bare
name Ollama's registry doesn't have, AND a correctly typed hf.co path was mangled (lowercased,
slashes stripped) to nothing. These pin the tag parsing and the "ask for the full path" guard.
"""
from __future__ import annotations

import asyncio

import pytest

from yggdrasil.agents.model_agent import ModelAgent
from yggdrasil.core.orchestrator import _model_route


# --- routing: "download X" must reach the model agent, not the shell ------------------------

def test_a_full_hf_path_routes_to_the_model_agent_not_a_shell_command():
    # It used to fall through to the planner -> command.run -> an authorization challenge.
    verb, prms = _model_route("download hf.co/RavichandranJ/Dolphin3-Cyber-8B-GGUF:Q4_K_M")
    assert verb == "pull"
    assert prms["argument"] == "hf.co/RavichandranJ/Dolphin3-Cyber-8B-GGUF:Q4_K_M"


def test_a_bare_tag_routes_to_pull():
    assert _model_route("download qwen2.5-coder:7b") == ("pull", {"argument": "qwen2.5-coder:7b"})


def test_full_path_wins_over_an_adjective_in_the_same_sentence():
    verb, prms = _model_route(
        "download the dolphin model from hf.co/RavichandranJ/Dolphin3-Cyber-8B-GGUF:Q4_K_M")
    assert verb == "pull"
    assert prms["argument"].startswith("hf.co/RavichandranJ/")


# --- find-by-name on HuggingFace: name -> search, path -> pull -------------------------------

@pytest.mark.parametrize("phrase,name", [
    ("download Dolphin3 Cyber 8B LLM from HuggingFace.co", "Dolphin3 Cyber 8B"),
    ("find the dolphin cyber model on huggingface", "dolphin cyber"),
    ("search huggingface for dolphin3 cyber", "dolphin3 cyber"),
])
def test_a_name_plus_huggingface_routes_to_find_not_pull_or_browser(phrase, name):
    assert _model_route(phrase) == ("find", {"argument": name})


def test_an_explicit_path_still_pulls_not_finds():
    # A full path shouldn't be sent to search — it's already resolved.
    verb, _ = _model_route("download hf.co/Owner/Repo-GGUF:Q4_K_M from huggingface")
    assert verb == "pull"


# --- the search helper resolves a name to a repo + quant ------------------------------------

def test_search_hf_gguf_never_raises_offline(monkeypatch):
    from yggdrasil.core import models
    # Force the network call to fail -> [] not an exception.
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))
    assert models.search_hf_gguf("anything") == []


def test_pick_quant_prefers_q4_k_m():
    from yggdrasil.core.models import _pick_quant
    assert _pick_quant(["Q2_K", "Q4_K_M", "Q8_0"]) == "Q4_K_M"
    assert _pick_quant(["Q6_K"]) == "Q6_K"          # take what's there if the preferred isn't
    assert _pick_quant([]) is None


# --- _as_tag: what Ollama is actually told to pull -------------------------------------------

def test_full_hf_path_is_preserved_verbatim():
    # Owner case (RavichandranJ) and quant case (Q4_K_M) are significant — must NOT be lowercased.
    tag = ModelAgent._as_tag("hf.co/RavichandranJ/Dolphin3-Cyber-8B-GGUF:Q4_K_M")
    assert tag == "hf.co/RavichandranJ/Dolphin3-Cyber-8B-GGUF:Q4_K_M"


def test_huggingface_host_is_normalised_to_hf_co():
    tag = ModelAgent._as_tag("huggingface.co/Owner/Repo-Name:Q4_K_M")
    assert tag == "hf.co/Owner/Repo-Name:Q4_K_M"


def test_hf_path_is_extracted_from_a_sentence():
    tag = ModelAgent._as_tag("download hf.co/owner/Repo-Name:Q4_K_M please")
    assert tag == "hf.co/owner/Repo-Name:Q4_K_M"


def test_ordinary_ollama_tags_still_work():
    assert ModelAgent._as_tag("qwen2.5-coder:7b") == "qwen2.5-coder:7b"
    assert ModelAgent._as_tag("qwen coder") == "qwen2.5-coder:7b"
    assert ModelAgent._as_tag("llama3.2") == "llama3.2"      # space-free literal is fine


def test_a_spaced_description_is_not_mashed_into_a_fake_tag():
    # The live bug: "Dolphin3 Cyber 8B" -> "dolphin3cyber8b" -> 404. A spoken description that
    # isn't a known name and isn't an hf.co path can't become a tag — return None so the caller
    # asks for the exact name instead of promising a doomed download.
    assert ModelAgent._as_tag("Dolphin3 Cyber 8B") is None
    assert ModelAgent._as_tag("Dolphin3 Cyber 8B LLM") is None


# --- _pull: the HuggingFace-without-a-path guard ---------------------------------------------

class _StubModels:
    async def installed(self):
        return []

    def start_pull(self, *a, **k):
        self.pulled = getattr(self, "pulled", []) + [a[0] if a else k.get("model")]


def _agent():
    ag = ModelAgent.__new__(ModelAgent)   # skip __init__ (needs bus/perms)
    ag.models = _StubModels()
    ag._staged = None
    ag.domain = "model"
    return ag


def test_hugging_face_without_a_path_asks_for_it_instead_of_faking_a_download():
    ag = _agent()
    out = asyncio.run(ag._pull("Dolphin3-Cyber-8B from huggingface"))
    assert "full address" in out["speech"].lower() or "hf.co/owner" in out["speech"].lower()
    assert not out.get("await_confirm")          # nothing staged — we didn't pretend
    assert ag._staged is None


def test_a_full_hf_path_stages_a_real_download():
    ag = _agent()
    out = asyncio.run(ag._pull("hf.co/RavichandranJ/Dolphin3-Cyber-8B-GGUF:Q4_K_M"))
    assert out.get("await_confirm")
    assert ag._staged["model"] == "hf.co/RavichandranJ/Dolphin3-Cyber-8B-GGUF:Q4_K_M"


def test_unparseable_name_is_honest_not_a_fabricated_tag():
    ag = _agent()
    out = asyncio.run(ag._pull("that cool model everyone's talking about"))
    assert not out.get("await_confirm")
    assert ag._staged is None
