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


def test_delete_requires_confirmation(tmp_path: Path) -> None:
    """Destructive ops never fire on the first request — they park behind a spoken yes/no
    confirmation (showing the RESOLVED name, since a fuzzy match can be wrong), and only run
    after an explicit 'confirm'. Saying 'no' (cancel) leaves everything untouched."""
    async def run() -> None:
        bus = LocalBus()
        perms = PermissionManager(DefaultPolicy(), CapturingChannel())
        agent = FileAgent(bus, perms, sandbox_root=tmp_path)
        await agent.start()
        (tmp_path / "old").mkdir()

        # First attempt: parked awaiting a yes/no confirmation, NOT executed.
        parked = await bus.request("file", Task(action="file.delete", agent="file",
                                                params={"path": "old"}))
        assert parked.status is Status.OK
        assert parked.data.get("await_confirm") is True
        assert parked.data.get("agent") == "file"          # the next yes/no routes back here
        assert "old" in parked.data.get("speech", "")       # confirms the resolved name aloud
        assert (tmp_path / "old").is_dir()                  # still there

        # Saying "no" (cancel) leaves it untouched.
        await bus.request("file", Task(action="file.cancel", agent="file", params={}))
        assert (tmp_path / "old").is_dir()

        # Ask again, then confirm ("yes") — only now is it deleted.
        again = await bus.request("file", Task(action="file.delete", agent="file",
                                               params={"path": "old"}))
        assert again.data.get("await_confirm") is True
        done = await bus.request("file", Task(action="file.confirm", agent="file", params={}))
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
