"""System-info library (v1.4.1) — "what's my local IP" answered by commands, not the LLM.

Live bug that drove this: "Jarvis, what is my local IP" routed to the top-running-programs
answer. These tests pin the deterministic route, the topic classifier (order matters:
'external ip' contains 'ip'), and that answers come from real system facts.
"""
from __future__ import annotations

import pytest

from yggdrasil.agents.system_agent import SystemAgent
from yggdrasil.core.orchestrator import _SYSINFO_RE


# ---- routing: the exact utterances from the report, plus natural variants ----

@pytest.mark.parametrize("phrase", [
    "what is my local IP",
    "Jarvis, what is my local IP",
    "whats my local ip",
    "can you find out the external ip of this computer",
    "how much memory does this system have",
    "how much ram do we have",
    "what cpu is in this machine",
    "what processor does this computer have",
    "what graphics card do I have",
    "what is the hostname",
    "what's the name of this computer",
    "check the battery",
    "what kernel is this running",
    "which operating system is this",
    "tell me my ip address",
])
def test_sysinfo_routes(phrase):
    assert _SYSINFO_RE.match(phrase), phrase


@pytest.mark.parametrize("phrase", [
    "search for local ip tutorials",          # web search, not a machine question
    "google how to find my ip",
    "open the memory settings",
    "look up the best cpu 2026",
    "what is the price of bitcoin",           # research stays research
    "remind me to check the battery tomorrow",
    "write a poem about memory",
])
def test_sysinfo_leaves_others_alone(phrase):
    assert not _SYSINFO_RE.match(phrase), phrase


# ---- topic classification (order matters: 'external ip' contains 'ip') ----

@pytest.mark.parametrize("question, topic", [
    ("what is my local ip", "local_ip"),
    ("find out the external ip of this computer", "external_ip"),
    ("what's my public ip", "external_ip"),
    ("my ip address", "local_ip"),
    ("how much memory does this system have", "memory"),
    ("how much ram", "memory"),
    ("what cpu is this", "cpu"),
    ("how many cores does it have", "cpu"),
    ("what graphics card do i have", "gpu"),
    ("what's the hostname", "hostname"),
    ("name of this computer", "hostname"),
    ("what kernel", "kernel"),
    ("which operating system is this", "os"),
    ("battery level", "battery"),
    ("how long has the system been running", "uptime"),
    ("how much disk space", "disk"),
])
def test_classify(question, topic):
    assert SystemAgent.classify(question) == topic


# ---- answers come from real facts ----

def test_local_ip_is_a_real_address():
    out = SystemAgent._local_ip()
    import re
    m = re.search(r"(\d{1,3}\.){3}\d{1,3}", out)
    # on a networked machine we get an address; offline we get the honest failure message
    assert m or "network" in out


def test_cpu_mentions_threads():
    out = SystemAgent._cpu()
    assert "thread" in out


def test_disk_no_longer_crashes():
    # regression: system.disk referenced shutil without importing it since the agent shipped
    out = SystemAgent._disk()
    assert "gigabytes" in out


def test_os_mentions_thoros():
    assert "ThorOS" in SystemAgent._os()
