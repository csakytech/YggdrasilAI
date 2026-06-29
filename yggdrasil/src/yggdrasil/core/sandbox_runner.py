"""Runs INSIDE the bubblewrap jail. Loads ONE agent packet and serves execute requests over a narrow
stdin/stdout line protocol (one JSON object per line).

This process has NO host objects (a stub bus/perms, no real permission manager, no llm), NO network,
and the agent's own directory is the only writable path on the filesystem. So untrusted packet code
runs HERE, contained — never in the orchestrator. The host gates dangerous capabilities before it ever
sends us a request, so we just run the verb and hand back the result.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tomllib
from pathlib import Path

# Keep a clean channel for the protocol: the real stdout is ours; anything the agent prints goes to
# stderr (captured by the host as logs) so stray prints can't corrupt the JSON stream.
_PROTO = os.fdopen(os.dup(1), "w", buffering=1)
sys.stdout = sys.stderr


def _emit(obj) -> None:
    _PROTO.write(json.dumps(obj) + "\n")
    _PROTO.flush()


class _StubBus:
    async def subscribe(self, *a, **k): ...
    async def publish(self, *a, **k): ...


class _StubPerms:
    async def check(self, *a, **k):  # the host already authorized; never consulted here
        raise RuntimeError("sandboxed agents do not run the permission gate")


def _load_agent(packet_dir: Path):
    manifest = tomllib.load(open(packet_dir / "manifest.toml", "rb"))
    ep = manifest["entrypoint"]
    sys.path.insert(0, str(packet_dir))  # allow the packet's sibling imports
    spec = importlib.util.spec_from_file_location("sandboxed_packet", packet_dir / f"{ep['module']}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, ep["class"])
    try:
        return cls(_StubBus(), _StubPerms(), None)
    except TypeError:
        return cls(_StubBus(), _StubPerms())


def main() -> None:
    packet_dir = Path(sys.argv[1])
    try:
        agent = _load_agent(packet_dir)
    except Exception as e:  # noqa: BLE001
        _emit({"type": "fatal", "error": repr(e)})
        return
    _emit({"type": "ready"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        try:
            data = asyncio.run(agent._execute(req["verb"], req.get("params") or {}))
            _emit({"id": rid, "ok": True, "result": data})
        except Exception as e:  # noqa: BLE001
            _emit({"id": rid, "ok": False, "error": repr(e)})


if __name__ == "__main__":
    main()
