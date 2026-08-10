"""Baseline recorder — what is NORMAL on this machine.

Foundation for the security sentinel. Every few minutes it takes a cheap snapshot of what's
running and what the machine is talking to, and folds it into a small aggregated store. It does
no analysis and raises no alarms: its whole job is to be able to answer, later, "have I ever
seen this before, and how often?"

Why aggregate rather than log a timeline: a timeline of every connection is both enormous and
far more revealing than we need. What a sentinel needs is *first seen, last seen, how many
samples* — which is exactly what "normal" means — and that fits in a few kilobytes.

WHY IT MUST START EARLY: a baseline is worthless until it has watched for weeks. A fresh install
knows nothing, so everything looks anomalous — and a security tool that cries wolf in week one
gets switched off before it's ever useful. Hence `settled()`: nothing built on top of this should
raise anything until the baseline has had time to become one.

PRIVACY. This records process names, listening ports, and the addresses this machine connects
to — enough to infer what you run and where you go. So: it is OFF unless the user turns it on,
it never leaves the machine, it stores no payloads, and `forget()` erases it completely. Same
rule as the conversation log: recording the user is the user's decision, not ours.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

# Distinct things remembered per category. Generous enough to describe a busy desktop, bounded
# so a pathological day (a port scanner, a torrent client) can't grow the file without limit.
MAX_KEYS = 400
SAMPLE_VERSION = 1
SETTLE_DAYS = 14        # below this, the baseline is still learning and must not be used to alarm
SETTLE_SAMPLES = 200


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "baseline.json"


def enabled() -> bool:
    try:
        from . import config
        return config.get_baseline()
    except Exception:
        return False


# --- observing -----------------------------------------------------------------------------

_SS_PROC = re.compile(r'users:\(\("([^"]+)"')


def _ss(args: list[str]) -> list[str]:
    try:
        r = subprocess.run(["ss", *args], capture_output=True, text=True, timeout=8)
        return r.stdout.splitlines() if r.returncode == 0 else []
    except Exception:
        return []


def _port_of(endpoint: str) -> str:
    """Last colon-separated field, so IPv6 ('[::]:22') and IPv4 ('0.0.0.0:22') both work."""
    return endpoint.rsplit(":", 1)[-1] if ":" in endpoint else ""


def listening() -> set[str]:
    """Listening sockets as 'tcp/22 sshd'. The PROCESS matters as much as the port: port 8080
    served by a dev server you started is ordinary; port 8080 served by something you've never
    heard of is the entire point of this exercise."""
    out = set()
    for line in _ss(["-tulnpH"]):
        f = line.split()
        if len(f) < 5:
            continue
        proto = f[0]
        port = _port_of(f[4])
        if not port.isdigit():
            continue
        m = _SS_PROC.search(line)
        out.add(f"{proto}/{port} {m.group(1) if m else '?'}")
    return out


def remotes() -> set[str]:
    """Established outbound peers as 'host:port proc'. Ports are kept; the host is kept as-is
    because 'which machine' is the question a sentinel has to answer. Loopback is dropped — it's
    noise, and it's the machine talking to itself."""
    out = set()
    for line in _ss(["-tunpH", "state", "established"]):
        f = line.split()
        if len(f) < 5:
            continue
        peer = f[4]
        if peer.startswith(("127.", "[::1]", "::1")):
            continue
        m = _SS_PROC.search(line)
        out.add(f"{peer} {m.group(1) if m else '?'}")
    return out


def processes() -> set[str]:
    """Process NAMES only — never command lines, which carry file paths, URLs and sometimes
    secrets. A name is enough to notice something new appearing."""
    out = set()
    try:
        for p in Path("/proc").iterdir():
            if not p.name.isdigit():
                continue
            try:
                out.add((p / "comm").read_text(encoding="utf-8", errors="replace").strip())
            except OSError:
                continue
    except OSError:
        pass
    return {n for n in out if n}


def sample() -> dict[str, set[str]]:
    return {"ports": listening(), "remotes": remotes(), "procs": processes()}


# --- remembering ---------------------------------------------------------------------------

def load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}


def _blank() -> dict:
    return {"version": SAMPLE_VERSION, "started": time.time(), "samples": 0,
            "ports": {}, "remotes": {}, "procs": {}}


def _fold(store: dict, kind: str, seen: set[str], now: float) -> None:
    """Merge one observation into the running counts. Eviction is least-recently-seen, so a
    one-off burst ages out while the things you actually use every day survive."""
    book = store.setdefault(kind, {})
    for key in seen:
        e = book.get(key)
        if e:
            e["last"] = now
            e["count"] = int(e.get("count", 0)) + 1
        else:
            book[key] = {"first": now, "last": now, "count": 1}
    if len(book) > MAX_KEYS:
        for key, _ in sorted(book.items(), key=lambda kv: kv[1].get("last", 0))[:len(book) - MAX_KEYS]:
            book.pop(key, None)


def record() -> bool:
    """Take one sample and fold it in. Never raises: this runs unattended on a timer, and a
    recorder that can crash the session is worse than no recorder."""
    if not enabled():
        return False
    try:
        now = time.time()
        store = load() or _blank()
        obs = sample()
        if not any(obs.values()):
            return False          # nothing observable (no `ss`, no /proc) — don't count the sample
        for kind, seen in obs.items():
            _fold(store, kind, seen, now)
        store["samples"] = int(store.get("samples", 0)) + 1
        store["last"] = now
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store), encoding="utf-8")
        return True
    except Exception:
        return False


# --- asking it questions (for the sentinel, later) -------------------------------------------

def settled() -> bool:
    """Has this watched long enough to be worth trusting? Nothing should raise an alarm from a
    baseline that is still learning — week-one false positives are how a security feature gets
    turned off and never turned back on."""
    s = load()
    if not s:
        return False
    age_days = (time.time() - float(s.get("started") or time.time())) / 86400.0
    return age_days >= SETTLE_DAYS and int(s.get("samples", 0)) >= SETTLE_SAMPLES


def seen(kind: str, key: str) -> dict | None:
    return (load().get(kind) or {}).get(key)


def novel(kind: str, keys: set[str]) -> set[str]:
    """Which of these has this machine never recorded before? Empty while still learning, so a
    caller cannot accidentally treat a young baseline as authoritative."""
    if not settled():
        return set()
    known = set((load().get(kind) or {}).keys())
    return set(keys) - known


def summary() -> dict:
    s = load()
    if not s:
        return {"enabled": enabled(), "samples": 0, "settled": False}
    return {
        "enabled": enabled(),
        "samples": int(s.get("samples", 0)),
        "started": s.get("started"),
        "days": round((time.time() - float(s.get("started") or time.time())) / 86400.0, 1),
        "settled": settled(),
        "ports": len(s.get("ports") or {}),
        "remotes": len(s.get("remotes") or {}),
        "procs": len(s.get("procs") or {}),
    }


def forget() -> None:
    """Erase everything recorded. Offered because a record of what you run and where you connect
    is exactly the sort of thing a user is entitled to delete on a whim."""
    try:
        _path().unlink()
    except OSError:
        pass


def main() -> None:
    """Entry point for the timer: one sample, then exit."""
    import sys
    ok = record()
    if "--summary" in sys.argv:
        print(json.dumps(summary(), indent=2))
    sys.exit(0 if ok or not enabled() else 1)


if __name__ == "__main__":
    main()
