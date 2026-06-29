"""Out-of-process sandbox for UNTRUSTED agents (bubblewrap + a narrow stdin/stdout RPC).

You cannot safely sandbox untrusted Python inside the host interpreter (import os, open, ... can't be
fenced off). So an untrusted packet's code never runs in the orchestrator: ``SandboxedAgent`` registers
like any agent — but its domain / capabilities / planner_examples come from the MANIFEST (data, not by
importing the code) — and its ``_execute`` ships the call to a child process locked down by bubblewrap:

  * read-only system + Python; the agent's own directory is the ONLY writable path
  * NO network (``--unshare-net``) unless the manifest declares network
  * fresh user/pid/ipc/uts namespaces, dies with the parent

The permission gate still runs host-side in ``BaseAgent.handle`` (inherited), so a dangerous verb is
authorized BEFORE the sandbox is asked to act. This is what makes it safe to flip ``ALLOW_UNTRUSTED``.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import yggdrasil

from ..agents.base import BaseAgent
from .permissions import Capability

_PKG_SRC = str(Path(yggdrasil.__file__).resolve().parents[1])   # dir containing the 'yggdrasil' package
_RUNNER = str(Path(__file__).resolve().with_name("sandbox_runner.py"))


def sandbox_available() -> bool:
    """True if we can actually contain an agent (bubblewrap present). If not, untrusted agents are
    REFUSED — never silently downgraded to in-process."""
    return shutil.which("bwrap") is not None


def _bwrap_argv(packet_dir: Path, allow_net: bool) -> list[str]:
    argv = [
        "bwrap",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/sbin", "/sbin",
        "--ro-bind-try", "/etc/ssl", "/etc/ssl",
        "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--ro-bind-try", sys.base_prefix, sys.base_prefix,   # base Python stdlib (if outside /usr)
        "--ro-bind-try", sys.prefix, sys.prefix,             # the venv (site-packages + .pth)
        "--ro-bind-try", _PKG_SRC, _PKG_SRC,                 # the yggdrasil package + the runner
        "--bind", str(packet_dir), str(packet_dir),          # the ONLY writable host path
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin",
        "--setenv", "HOME", str(packet_dir),
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--chdir", str(packet_dir),
        "--unshare-user", "--unshare-ipc", "--unshare-pid", "--unshare-uts", "--unshare-cgroup",
        "--new-session", "--die-with-parent",
    ]
    if not allow_net:
        argv += ["--unshare-net"]
    argv += [sys.executable, "-I", _RUNNER, str(packet_dir)]
    return argv


class SandboxedAgent(BaseAgent):
    """Registers like a normal agent; runs the packet's code in a bubblewrap jail via a child process."""

    def __init__(self, bus, perms, packet_dir, manifest: dict, llm=None, timeout: float = 25.0) -> None:
        super().__init__(bus, perms)
        self.packet_dir = Path(packet_dir)
        self.domain = manifest["routing"]["domain"]
        self.module_id = manifest["agent"]["id"]
        self.planner_examples = list((manifest.get("routing") or {}).get("planner_examples", []))
        self.capabilities = {
            c["name"]: Capability(c["name"], bool(c.get("dangerous")), c.get("description", ""))
            for c in (manifest.get("capability") or [])
        }
        self._allow_net = bool((manifest.get("permissions") or {}).get("network"))
        self._timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._rid = 0

    async def _ensure(self) -> None:
        if self._proc and self._proc.returncode is None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *_bwrap_argv(self.packet_dir, self._allow_net),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        )
        line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=self._timeout)
        msg = json.loads(line.decode() or "{}")
        if msg.get("type") == "fatal":
            raise RuntimeError(f"sandboxed agent failed to load: {msg.get('error')}")
        if msg.get("type") != "ready":
            raise RuntimeError("sandboxed agent did not start cleanly")

    async def _execute(self, verb, params):
        async with self._lock:                       # one request in flight per child
            await self._ensure()
            self._rid += 1
            self._proc.stdin.write((json.dumps({"id": self._rid, "verb": verb, "params": params}) + "\n").encode())
            await self._proc.stdin.drain()
            try:
                line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=self._timeout)
            except asyncio.TimeoutError:
                await self._kill()
                raise RuntimeError("sandboxed agent timed out")
            if not line:
                await self._kill()
                raise RuntimeError("sandboxed agent exited unexpectedly")
            resp = json.loads(line.decode())
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "sandboxed agent error"))
            return resp.get("result")

    async def _kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        self._proc = None
