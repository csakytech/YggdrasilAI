"""Sentinel — the always-on / proactive agent pattern (see docs/MODULES.md).

A monitor exposes ``cycle() -> list[str]`` that runs a lightweight, deterministic check and
returns any alerts noticed since last time (it keeps its own previous state). ``Sentinel`` runs
that on an interval and dispatches alerts — no LLM in the hot loop; the model is only called
later to triage a finding. The Security Sentinel (the "Warden") watches failed logins and new
listening ports. Run it standalone with ``yggdrasil-warden``; a systemd service makes it
boot-persistent later.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional


def _run(cmd: list[str], timeout: int = 8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


class SecuritySentinel:
    name = "security"

    def __init__(self) -> None:
        self._prev_failed: Optional[int] = None
        self._prev_ports: Optional[set] = None

    def cycle(self) -> list[str]:
        alerts: list[str] = []

        failed = self._failed_count()
        if self._prev_failed is not None and failed > self._prev_failed:
            alerts.append(f"{failed - self._prev_failed} new failed login attempt(s) detected.")
        self._prev_failed = failed

        ports = self._ports()
        if self._prev_ports is not None:
            new = ports - self._prev_ports
            if new:
                alerts.append("New listening port(s): " + ", ".join(map(str, sorted(new))) + ".")
        self._prev_ports = ports
        return alerts

    @staticmethod
    def _failed_count() -> int:
        out = (_run(["sudo", "-n", "journalctl", "-u", "ssh.service", "--since", "-10min", "--no-pager"])
               or _run(["journalctl", "-u", "ssh.service", "--since", "-10min", "--no-pager"]))
        return out.count("Failed password") + out.count("authentication failure")

    @staticmethod
    def _ports() -> set:
        ports = set()
        for line in _run(["ss", "-tlnH"]).splitlines():
            parts = line.split()
            if len(parts) >= 4:
                p = parts[3].rsplit(":", 1)[-1]
                if p.isdigit():
                    ports.add(int(p))
        return ports


class Sentinel:
    """Runs a monitor's ``cycle()`` on an interval, dispatching any alerts."""

    def __init__(self, monitor, interval: float = 60.0,
                 on_alert: Optional[Callable[[str, str], None]] = None) -> None:
        self.monitor = monitor
        self.interval = interval
        self.on_alert = on_alert
        self.alerts: list[tuple[str, str, float]] = []  # (name, text, ts)

    async def run(self) -> None:
        await asyncio.to_thread(self.monitor.cycle)  # baseline — no alerts on first pass
        while True:
            await asyncio.sleep(self.interval)
            try:
                for text in await asyncio.to_thread(self.monitor.cycle):
                    self.alerts.append((self.monitor.name, text, time.time()))
                    self.alerts = self.alerts[-50:]
                    if self.on_alert:
                        self.on_alert(self.monitor.name, text)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # a monitor hiccup must never kill the sentinel


def _log_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "warden.log"


def main() -> None:
    """Standalone Warden: watch continuously, print + log alerts. (systemd unit later.)"""
    interval = float(os.environ.get("YGGDRASIL_WARDEN_INTERVAL", 60))
    log = _log_path()
    log.parent.mkdir(parents=True, exist_ok=True)

    def announce(name: str, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  [{name}]  {text}"
        print(line, flush=True)
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    sentinel = Sentinel(SecuritySentinel(), interval=interval, on_alert=announce)
    print(f"Warden active — watching every {interval:.0f}s. Alerts → {log}. Ctrl-C to stop.", flush=True)
    try:
        asyncio.run(sentinel.run())
    except KeyboardInterrupt:
        print("\nWarden stopped.")


if __name__ == "__main__":
    main()
