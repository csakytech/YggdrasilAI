"""Baseline recorder — the foundation the security sentinel will stand on.

The properties that matter are all about restraint: it records nothing unless asked, it never
raises, it stays bounded, and above all it refuses to be treated as authoritative before it has
watched long enough. Week-one false positives are how a security feature gets switched off and
never switched back on.
"""
from __future__ import annotations

import json
import time

import pytest

from yggdrasil.core import baseline


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("YGGDRASIL_BASELINE", "1")   # on, for these tests
    yield


def _store():
    return json.loads(baseline._path().read_text(encoding="utf-8"))


def test_records_nothing_when_switched_off(monkeypatch):
    monkeypatch.setenv("YGGDRASIL_BASELINE", "0")
    monkeypatch.setattr(baseline, "sample", lambda: {"ports": {"tcp/22 sshd"}, "remotes": set(),
                                                     "procs": {"firefox"}})
    assert baseline.record() is False
    assert baseline.load() == {}


def test_folds_samples_into_counts(monkeypatch):
    monkeypatch.setattr(baseline, "sample", lambda: {"ports": {"tcp/22 sshd"}, "remotes": set(),
                                                     "procs": {"firefox"}})
    assert baseline.record() is True
    assert baseline.record() is True
    s = _store()
    assert s["samples"] == 2
    assert s["ports"]["tcp/22 sshd"]["count"] == 2
    assert s["ports"]["tcp/22 sshd"]["first"] <= s["ports"]["tcp/22 sshd"]["last"]


def test_an_unobservable_machine_does_not_count_as_a_sample(monkeypatch):
    """No ss, no /proc — record nothing rather than banking an empty sample, which would
    otherwise let a blind recorder 'settle' and start being believed."""
    monkeypatch.setattr(baseline, "sample", lambda: {"ports": set(), "remotes": set(), "procs": set()})
    assert baseline.record() is False
    assert baseline.load() == {}


def test_store_stays_bounded_and_evicts_least_recently_seen(monkeypatch):
    monkeypatch.setattr(baseline, "MAX_KEYS", 10)
    monkeypatch.setattr(baseline, "sample",
                        lambda: {"ports": {f"tcp/{p} x" for p in range(50)},
                                 "remotes": set(), "procs": set()})
    baseline.record()
    assert len(_store()["ports"]) == 10


def test_novel_is_silent_until_the_baseline_has_settled(monkeypatch):
    """The cold-start guard: a young baseline must report NOTHING as new, however unfamiliar."""
    monkeypatch.setattr(baseline, "sample", lambda: {"ports": {"tcp/22 sshd"}, "remotes": set(),
                                                     "procs": set()})
    baseline.record()
    assert baseline.settled() is False
    assert baseline.novel("ports", {"tcp/4444 xmrig"}) == set()


def test_novel_reports_only_genuinely_new_things_once_settled(monkeypatch):
    monkeypatch.setattr(baseline, "sample", lambda: {"ports": {"tcp/22 sshd"}, "remotes": set(),
                                                     "procs": set()})
    baseline.record()
    aged = _store()
    aged["started"] = time.time() - (baseline.SETTLE_DAYS + 1) * 86400
    aged["samples"] = baseline.SETTLE_SAMPLES + 1
    baseline._path().write_text(json.dumps(aged), encoding="utf-8")

    assert baseline.settled() is True
    assert baseline.novel("ports", {"tcp/22 sshd"}) == set()               # known: stays quiet
    assert baseline.novel("ports", {"tcp/4444 xmrig"}) == {"tcp/4444 xmrig"}


def test_forget_erases_everything(monkeypatch):
    monkeypatch.setattr(baseline, "sample", lambda: {"ports": {"tcp/22 sshd"}, "remotes": set(),
                                                     "procs": set()})
    baseline.record()
    assert baseline.load() != {}
    baseline.forget()
    assert baseline.load() == {}


def test_recording_never_raises(monkeypatch):
    """It runs unattended on a timer; a recorder that can crash the session is worse than none."""
    def _boom():
        raise RuntimeError("no ss, no /proc, nothing")
    monkeypatch.setattr(baseline, "sample", _boom)
    assert baseline.record() is False


def test_summary_is_safe_on_an_empty_store():
    s = baseline.summary()
    assert s["samples"] == 0 and s["settled"] is False


def test_port_parsing_handles_ipv6_and_ipv4():
    assert baseline._port_of("0.0.0.0:22") == "22"
    assert baseline._port_of("[::]:8080") == "8080"
    assert baseline._port_of("*:443") == "443"
