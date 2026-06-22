"""Security Agent — "the Warden" (Core module).

Defensive and local-only. Deterministic OS tools do the *detecting*; the LLM only *triages and
explains* (see docs/MODULES.md). v1 capabilities are read-only — `audit` (posture + plain
summary) and `updates`. Active responses (block IP / kill process / quarantine) will arrive
later as dangerous, gated capabilities. Continuous monitoring lives in the Security Sentinel
(core/sentinel.py).
"""
from __future__ import annotations

import re
import subprocess
from typing import Any

from ..core.permissions import Capability
from .base import BaseAgent

_THINK = re.compile(r"<think>.*?</think>", re.S)


def _run(cmd: list[str], timeout: int = 8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


class SecurityAgent(BaseAgent):
    domain = "security"
    module_id = "core.security"
    planner_examples = [
        'how secure am I -> {"steps":[{"action":"security.audit","argument":""}]}',
        'run a security check -> {"steps":[{"action":"security.audit","argument":""}]}',
        'are there any security updates -> {"steps":[{"action":"security.updates","argument":""}]}',
    ]
    capabilities = {
        "audit": Capability("audit", False, "Check the system's security posture and explain it"),
        "updates": Capability("updates", False, "Check for pending security updates"),
    }

    def __init__(self, bus, perms, llm=None) -> None:
        super().__init__(bus, perms)
        self.llm = llm

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if verb == "updates":
            return {"speech": self._updates_speech()}
        if verb == "audit":
            findings = self.gather()
            if self.llm:
                return {"speech": await self._summarize(findings)}
            return {"speech": self._plain(findings)}
        raise ValueError(f"unhandled verb '{verb}'")

    # --- deterministic checks ---
    def gather(self) -> dict:
        return {
            "firewall": self._firewall(),
            "updates": self._updates(),
            "listening_ports": self._ports(),
            "failed_logins_1h": self._failed_logins(),
            "ssh_root_login": self._sshd("permitrootlogin"),
            "ssh_password_auth": self._sshd("passwordauthentication"),
        }

    @staticmethod
    def _firewall() -> str:
        out = _run(["sudo", "-n", "ufw", "status"]) or _run(["ufw", "status"])
        if "Status: active" in out:
            return "ufw active"
        if "Status: inactive" in out:
            return "ufw inactive"
        return "nftables rules present" if _run(["sudo", "-n", "nft", "list", "ruleset"]).strip() \
            else "no firewall detected"

    @staticmethod
    def _upgradable() -> tuple[int, int]:
        lines = [l for l in _run(["apt", "list", "--upgradable"]).splitlines() if "/" in l]
        return len(lines), sum("security" in l.lower() for l in lines)

    def _updates(self) -> str:
        total, sec = self._upgradable()
        return f"{total} upgradable ({sec} security)"

    def _updates_speech(self) -> str:
        total, sec = self._upgradable()
        if not total:
            return "You're up to date — no pending updates."
        s = f"{total} update{'s' if total != 1 else ''} available"
        if sec:
            s += f", including {sec} security update{'s' if sec != 1 else ''}"
        return s + "."

    @staticmethod
    def _ports() -> str:
        ports = set()
        for line in _run(["ss", "-tlnH"]).splitlines():
            parts = line.split()
            if len(parts) >= 4:
                p = parts[3].rsplit(":", 1)[-1]
                if p.isdigit():
                    ports.add(int(p))
        return ", ".join(str(p) for p in sorted(ports)) or "none"

    @staticmethod
    def _failed_logins() -> int:
        out = (_run(["sudo", "-n", "journalctl", "-u", "ssh.service", "--since", "-1h", "--no-pager"])
               or _run(["journalctl", "-u", "ssh.service", "--since", "-1h", "--no-pager"]))
        return out.count("Failed password") + out.count("authentication failure")

    @staticmethod
    def _sshd(key: str) -> str:
        out = _run(["sudo", "-n", "sshd", "-T"]) or _run(["sshd", "-T"])
        for line in out.splitlines():
            if line.lower().startswith(key + " "):
                return line.split(None, 1)[1].strip()
        return "unknown"

    def _plain(self, f: dict) -> str:
        return (f"Firewall: {f['firewall']}. Updates: {f['updates']}. "
                f"Listening ports: {f['listening_ports']}. Failed logins in the last hour: "
                f"{f['failed_logins_1h']}. SSH root login: {f['ssh_root_login']}, "
                f"password auth: {f['ssh_password_auth']}.")

    async def _summarize(self, f: dict) -> str:
        resp = await self.llm.generate(
            system="You are a concise, calm security analyst. Plain language, no markdown. /no_think",
            prompt=("Summarize this Linux machine's security posture in 2-4 short sentences for "
                    "its owner. Flag anything risky and suggest the single most useful action.\n"
                    + self._plain(f)),
            temperature=0.3,
        )
        return _THINK.sub("", resp.text).strip() or self._plain(f)
