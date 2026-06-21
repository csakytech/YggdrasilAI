"""Phase-0 spine tests: the safe path, the dangerous (authorization) path, and sandboxing.

Run with:  pytest   (from the yggdrasil/ directory, after `pip install -e .[dev]`)
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from yggdrasil.agents.file_agent import FileAgent
from yggdrasil.core.bus import LocalBus, Status, Task
from yggdrasil.core.permissions import AuthChallenge, DefaultPolicy, PermissionManager, UserChannel


class CapturingChannel(UserChannel):
    """Records the challenge so the test can read the code, like a user would."""

    def __init__(self) -> None:
        self.last: AuthChallenge | None = None

    async def present_challenge(self, challenge: AuthChallenge) -> None:
        self.last = challenge


def test_create_folder_is_safe(tmp_path: Path) -> None:
    async def run() -> None:
        bus = LocalBus()
        perms = PermissionManager(DefaultPolicy(), CapturingChannel())
        agent = FileAgent(bus, perms, sandbox_root=tmp_path)
        await agent.start()

        result = await bus.request("file", Task(action="file.create_folder",
                                                agent="file", params={"path": "Crypto Research"}))
        assert result.status is Status.OK
        assert (tmp_path / "Crypto Research").is_dir()

    asyncio.run(run())


def test_delete_requires_authorization(tmp_path: Path) -> None:
    async def run() -> None:
        bus = LocalBus()
        channel = CapturingChannel()
        perms = PermissionManager(DefaultPolicy(), channel)
        agent = FileAgent(bus, perms, sandbox_root=tmp_path)
        await agent.start()
        (tmp_path / "old").mkdir()

        # First attempt: parked awaiting authorization, NOT executed.
        parked = await bus.request("file", Task(action="file.delete", agent="file",
                                                params={"path": "old"}))
        assert parked.status is Status.AWAITING_AUTH
        assert (tmp_path / "old").is_dir()  # still there
        assert channel.last is not None

        # Wrong code is rejected.
        assert perms.verify(channel.last.challenge_id, "000000") is None

        # Re-challenge and approve with the real code.
        parked2 = await bus.request("file", Task(action="file.delete", agent="file",
                                                 params={"path": "old"}))
        token = perms.verify(channel.last.challenge_id, channel.last.code)
        assert token is not None

        done = await bus.request("file", Task(action="file.delete", agent="file",
                                              params={"path": "old"}, auth_token=token))
        assert done.status is Status.OK
        assert not (tmp_path / "old").exists()

    asyncio.run(run())


def test_sandbox_escape_is_blocked(tmp_path: Path) -> None:
    async def run() -> None:
        bus = LocalBus()
        perms = PermissionManager(DefaultPolicy(), CapturingChannel())
        agent = FileAgent(bus, perms, sandbox_root=tmp_path)
        await agent.start()

        result = await bus.request("file", Task(action="file.create_folder", agent="file",
                                                params={"path": "../escape"}))
        assert result.status is Status.ERROR  # PermissionError -> isolated ERROR result
        assert not (tmp_path.parent / "escape").exists()

    asyncio.run(run())
