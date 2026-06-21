"""Permission manager + authorization-code challenge.

The AI never touches the OS directly. Every action passes through an agent, and this
``PermissionManager`` is the ONLY component that authorizes OS-affecting actions.
Dangerous capabilities trigger a single-use, time-limited code the user must speak or
type ("Authorize 710628"). See docs/ARCHITECTURE.md (ADR-0004).
"""
from __future__ import annotations

import hmac
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .bus import Status, Task, new_id, now


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    dangerous: bool = False
    description: str = ""


@dataclass(slots=True)
class AuthChallenge:
    challenge_id: str
    code: str  # 6-digit; what the user must repeat back
    action: str
    summary: str  # human-readable, e.g. "file.delete old.txt"
    expires_at: float
    attempts_left: int = 3


@dataclass(slots=True)
class Decision:
    status: Status  # OK (allow) / DENIED / AWAITING_AUTH
    reason: str = ""
    challenge: Optional[AuthChallenge] = None


class PolicyAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    CHALLENGE = "challenge"


class Policy(ABC):
    @abstractmethod
    def evaluate(self, task: Task, cap: Capability, agent: str) -> PolicyAction: ...


class DefaultPolicy(Policy):
    """Safe capabilities allowed; dangerous ones require an authorization challenge."""

    def __init__(self, blocked_agents: Optional[set[str]] = None) -> None:
        self.blocked_agents = blocked_agents or set()

    def evaluate(self, task: Task, cap: Capability, agent: str) -> PolicyAction:
        if agent in self.blocked_agents:
            return PolicyAction.DENY
        return PolicyAction.CHALLENGE if cap.dangerous else PolicyAction.ALLOW


class UserChannel(ABC):
    """How the permission manager reaches the user to present a challenge."""

    @abstractmethod
    async def present_challenge(self, challenge: AuthChallenge) -> None: ...


class PermissionManager:
    def __init__(
        self,
        policy: Policy,
        ui: UserChannel,
        challenge_ttl_s: float = 90.0,
        token_ttl_s: float = 30.0,
    ) -> None:
        self.policy = policy
        self.ui = ui
        self.challenge_ttl_s = challenge_ttl_s
        self.token_ttl_s = token_ttl_s
        self._pending: dict[str, AuthChallenge] = {}
        self._tokens: dict[str, tuple[str, float]] = {}  # token -> (action, expiry)

    async def check(self, task: Task, cap: Capability, agent: str) -> Decision:
        # Fast path: task already carries a valid, action-bound token.
        if task.auth_token and self._consume_token(task.auth_token, task.action):
            return Decision(Status.OK)

        action = self.policy.evaluate(task, cap, agent)
        if action is PolicyAction.ALLOW:
            return Decision(Status.OK)
        if action is PolicyAction.DENY:
            return Decision(Status.DENIED, reason="blocked by policy")

        ch = AuthChallenge(
            challenge_id=new_id(),
            code=f"{secrets.randbelow(1_000_000):06d}",
            action=task.action,
            summary=self._summarize(task),
            expires_at=now() + self.challenge_ttl_s,
        )
        self._pending[ch.challenge_id] = ch
        await self.ui.present_challenge(ch)
        return Decision(Status.AWAITING_AUTH, challenge=ch)

    def verify(self, challenge_id: str, spoken_code: str) -> Optional[str]:
        """User said 'Authorize <code>'. Returns a one-time auth_token, or None."""
        ch = self._pending.get(challenge_id)
        if ch is None:
            return None
        if now() > ch.expires_at:
            self._pending.pop(challenge_id, None)
            return None
        ch.attempts_left -= 1
        if not hmac.compare_digest(spoken_code.strip(), ch.code):
            if ch.attempts_left <= 0:
                self._pending.pop(challenge_id, None)  # brute-force guard
            return None
        self._pending.pop(challenge_id, None)  # single use
        token = secrets.token_hex(16)
        self._tokens[token] = (ch.action, now() + self.token_ttl_s)
        return token

    def _consume_token(self, token: str, action: str) -> bool:
        entry = self._tokens.pop(token, None)
        if entry is None:
            return False
        bound_action, expiry = entry
        return bound_action == action and now() <= expiry

    @staticmethod
    def _summarize(task: Task) -> str:
        target = task.params.get("path") or task.params.get("target") or ""
        return f"{task.action} {target}".strip()
