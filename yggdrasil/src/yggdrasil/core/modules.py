"""Module installer — the engine behind the marketplace: install / list / remove / load agent packets.

A packet is a directory: ``manifest.toml`` + the agent code (+ optional assets). The functions here are
the shared backend; the front-ends (a hands-free VOICE flow and a GTK installer GUI) both sit on top
of them, so there is one install path, not two. ``consent_summary()`` renders "what you're approving"
for the GUI panel or the spoken consent.

SECURITY — agents currently load **in-process, with NO sandbox**. That is safe ONLY for trusted /
verified agents. Do NOT load untrusted community packets through ``load_installed`` until the sandbox
lands (bubblewrap + a mediated bus API). This is deliberately the engine for the curated/verified MVP;
``ALLOW_UNTRUSTED`` stays False so we can't accidentally ship the unsafe path.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

ALLOW_UNTRUSTED = False  # flip only once the sandbox exists; gates loading community-tier packets


def modules_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "yggdrasil" / "modules"


def _load_manifest(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def validate(m: dict) -> list[str]:
    """Problems with a manifest; empty list = valid."""
    errs: list[str] = []
    a = m.get("agent", {})
    if "." not in (a.get("id") or ""):
        errs.append("agent.id must be a namespaced 'author.name'")
    if not a.get("version"):
        errs.append("agent.version is required")
    if not (m.get("routing", {}) or {}).get("domain"):
        errs.append("routing.domain is required")
    ep = m.get("entrypoint", {}) or {}
    if not ep.get("module") or not ep.get("class"):
        errs.append("entrypoint.module and entrypoint.class are required")
    return errs


def consent_summary(m: dict) -> dict:
    """Plain-language 'what this agent can do' — shown in the GUI panel and spoken before install."""
    a = m.get("agent", {})
    perms = m.get("permissions", {}) or {}
    caps = m.get("capability", []) or []
    grants: list[str] = []
    if perms.get("filesystem_read"):
        grants.append("read files in " + ", ".join(perms["filesystem_read"]))
    if perms.get("filesystem_write"):
        grants.append("write files in " + ", ".join(perms["filesystem_write"]))
    if perms.get("network"):
        grants.append("reach the internet (" + ", ".join(perms["network"]) + ")")
    if perms.get("run_commands"):
        grants.append("run commands: " + ", ".join(perms["run_commands"]))
    if perms.get("controls_apps"):
        grants.append("control these apps: " + ", ".join(perms["controls_apps"]))
    return {
        "id": a.get("id"),
        "name": a.get("name", a.get("id")),
        "summary": a.get("summary", ""),
        "can_do": [c.get("description", c.get("name")) for c in caps],
        "dangerous": [c.get("description", c.get("name")) for c in caps if c.get("dangerous")],
        "permissions": grants or ["use only its own private storage"],
    }


def install(source: str | os.PathLike) -> dict:
    """Install a packet from a local directory. Returns the manifest; raises ValueError if invalid."""
    src = Path(source)
    mpath = src / "manifest.toml"
    if not mpath.is_file():
        raise ValueError("packet has no manifest.toml")
    m = _load_manifest(mpath)
    errs = validate(m)
    if errs:
        raise ValueError("invalid manifest: " + "; ".join(errs))
    mid = m["agent"]["id"]
    dest = modules_dir() / mid
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    _index_set(mid, {"id": mid, "name": m["agent"].get("name", mid),
                     "version": m["agent"]["version"], "domain": m["routing"]["domain"]})
    return m


def installed() -> list[dict]:
    return list(_index().values())


def remove(mid: str) -> bool:
    existed = (modules_dir() / mid).exists()
    shutil.rmtree(modules_dir() / mid, ignore_errors=True)
    _index_del(mid)
    return existed


def load_installed(bus, perms, llm=None, reserved_domains=()) -> list:
    """Import + instantiate each installed agent so the registry can register it.

    IN-PROCESS — trusted agents only (no sandbox yet). ``reserved_domains`` are taken by Core agents;
    an installed packet may NOT override them, so a community agent can't hijack 'file', 'system', etc.
    """
    out = []
    for meta in installed():
        if meta.get("domain") in reserved_domains:
            print(f"[modules] skip {meta['id']}: domain '{meta['domain']}' is reserved", file=sys.stderr)
            continue
        try:
            out.append(_load_one(meta["id"], bus, perms, llm))
        except Exception as e:  # one bad packet must not stop the assistant from starting
            print(f"[modules] failed to load {meta['id']}: {e!r}", file=sys.stderr)
    return [a for a in out if a is not None]


def _load_one(mid: str, bus, perms, llm):
    d = modules_dir() / mid
    m = _load_manifest(d / "manifest.toml")
    ep = m["entrypoint"]
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))  # let the packet import its sibling modules
    spec = importlib.util.spec_from_file_location(
        "yggmod_" + mid.replace(".", "_").replace("-", "_"), d / f"{ep['module']}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, ep["class"])
    try:
        return cls(bus, perms, llm)
    except TypeError:
        return cls(bus, perms)  # agents that don't take an llm arg


# --- installed index: modules/installed.json ---
def _index_path() -> Path:
    return modules_dir() / "installed.json"


def _index() -> dict:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_index(idx: dict) -> None:
    modules_dir().mkdir(parents=True, exist_ok=True)
    _index_path().write_text(json.dumps(idx, indent=2), encoding="utf-8")


def _index_set(mid: str, meta: dict) -> None:
    idx = _index()
    idx[mid] = meta
    _save_index(idx)


def _index_del(mid: str) -> None:
    idx = _index()
    idx.pop(mid, None)
    _save_index(idx)
