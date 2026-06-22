"""Permission manager + authorization model.

The AI never touches the OS directly; this is the ONLY component that authorizes OS-affecting
actions. But authorization must not be monotonous — like a desktop, routine work in your own
space shouldn't nag you. So:

- Most capabilities are **safe** and never prompt.
- **Dangerous** ones (e.g. delete) prompt for a short code — 4 digits by default.
- After one approval, the agent gets a **session grant** (sudo-style timeout): no re-asking for
  a few minutes. So deleting 100 files is one prompt, not a hundred.
- **Autonomous mode** ("stop asking") skips prompts entirely; "be careful again" restores them.

Modes: `guarded` (default — gate dangerous, honor grants), `autonomous` (never prompt),
`paranoid` (gate dangerous, ignore grants — always ask). Env overrides: YGGDRASIL_TRUST,
YGGDRASIL_AUTH_DIGITS, YGGDRASIL_GRANT_TTL. See docs/ARCHITECTURE.md (ADR-0004).
"""
from __future__ import annotations

import hmac
import os
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
    code: str
    action: str
    summary: str
    expires_at: float
    agent: str = ""
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
    """Safe capabilities allowed; dangerous ones require authorization (subject to the
    manager's mode + session grants)."""

    def __init__(self, blocked_agents: Optional[set[str]] = None) -> None:
        self.blocked_agents = blocked_agents or set()

    def evaluate(self, task: Task, cap: Capability, agent: str) -> PolicyAction:
        if agent in self.blocked_agents:
            return PolicyAction.DENY
        return PolicyAction.CHALLENGE if cap.dangerous else PolicyAction.ALLOW


class UserChannel(ABC):
    @abstractmethod
    async def present_challenge(self, challenge: AuthChallenge) -> None: ...


class PermissionManager:
    def __init__(
        self,
        policy: Policy,
        ui: UserChannel,
        challenge_ttl_s: float = 90.0,
        token_ttl_s: float = 30.0,
        code_digits: int | None = None,
        mode: str | None = None,
        grant_ttl_s: float | None = None,
    ) -> None:
        self.policy = policy
        self.ui = ui
        self.challenge_ttl_s = challenge_ttl_s
        self.token_ttl_s = token_ttl_s
        self.code_digits = int(code_digits or os.environ.get("YGGDRASIL_AUTH_DIGITS", 4))
        self.mode = mode or os.environ.get("YGGDRASIL_TRUST", "guarded")
        self.grant_ttl_s = float(grant_ttl_s if grant_ttl_s is not None
                                 else os.environ.get("YGGDRASIL_GRANT_TTL", 300))
        self._pending: dict[str, AuthChallenge] = {}
        self._tokens: dict[str, tuple[str, float]] = {}  # token -> (action, expiry)
        self._grants: dict[str, float] = {}              # agent -> grant expiry

    def set_mode(self, mode: str) -> bool:
        """Toggle the trust mode at runtime ("autonomous", "guarded", "paranoid")."""
        if mode not in ("guarded", "autonomous", "paranoid"):
            return False
        self.mode = mode
        if mode != "autonomous":
            self._grants.clear()  # returning to caution re-arms the prompts
        return True

    def _has_grant(self, agent: str) -> bool:
        return now() < self._grants.get(agent, 0.0)

    async def check(self, task: Task, cap: Capability, agent: str) -> Decision:
        # Fast path: a valid, action-bound token from a prior challenge.
        if task.auth_token and self._consume_token(task.auth_token, task.action):
            return Decision(Status.OK)

        decision = self.policy.evaluate(task, cap, agent)
        if decision is PolicyAction.ALLOW:
            return Decision(Status.OK)
        if decision is PolicyAction.DENY:
            return Decision(Status.DENIED, reason="blocked by policy")

        # decision is CHALLENGE (a dangerous capability). Decide whether to actually ask.
        if self.mode == "autonomous":
            return Decision(Status.OK)
        if self.mode != "paranoid" and self._has_grant(agent):
            return Decision(Status.OK)

        ch = AuthChallenge(
            challenge_id=new_id(),
            code=f"{secrets.randbelow(10 ** self.code_digits):0{self.code_digits}d}",
            action=task.action,
            summary=self._summarize(task),
            expires_at=now() + self.challenge_ttl_s,
            agent=agent,
        )
        self._pending[ch.challenge_id] = ch
        await self.ui.present_challenge(ch)
        return Decision(Status.AWAITING_AUTH, challenge=ch)

    def verify(self, challenge_id: str, spoken_code: str) -> Optional[str]:
        """User said 'Authorize <code>'. Returns a one-time auth_token, or None. On success the
        agent also earns a session grant so it won't ask again for a while."""
        ch = self._pending.get(challenge_id)
        if ch is None:
            return None
        if now() > ch.expires_at:
            self._pending.pop(challenge_id, None)
            return None
        ch.attempts_left -= 1
        if not hmac.compare_digest(spoken_code.strip(), ch.code):
            if ch.attempts_left <= 0:
                self._pending.pop(challenge_id, None)
            return None
        self._pending.pop(challenge_id, None)
        if self.grant_ttl_s > 0:
            self._grants[ch.agent] = now() + self.grant_ttl_s
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
