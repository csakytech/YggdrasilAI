"""Persistent memory: facts the user asks Yggdrasil to remember.

Stored as JSON under the user's config dir and loaded into the LLM's context each turn, so
Yggdrasil "knows" the user across sessions — fully local, no cloud. This is the per-user
long-term store; the multi-agent-team phase will add shared/working memory on top.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# A new fact about the user's name should replace the old one, not pile up alongside it.
_NAME_FACT = re.compile(r"\b(name|call me)\b", re.I)


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "yggdrasil" / "memory.json"


class MemoryStore:
    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else default_path()
        self.facts: list[str] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.facts = [str(f) for f in data.get("facts", [])]
        except (OSError, json.JSONDecodeError):
            self.facts = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"facts": self.facts}, indent=2), encoding="utf-8")

    def remember(self, fact: str) -> str:
        fact = (fact or "").strip()
        if not fact:
            return fact
        # A new name declaration supersedes any prior one (so we don't keep both "name is Joe"
        # and "name is Michael" and then waffle about which is right).
        if _NAME_FACT.search(fact):
            self.facts = [f for f in self.facts if not _NAME_FACT.search(f)]
        if fact not in self.facts:
            self.facts.append(fact)
        self._save()
        return fact

    def forget(self, query: str) -> list[str]:
        q = (query or "").strip().lower()
        removed = [f for f in self.facts if q and q in f.lower()]
        if removed:
            self.facts = [f for f in self.facts if f not in removed]
            self._save()
        return removed

    def recall(self) -> list[str]:
        return list(self.facts)

    def context(self) -> str:
        """Render the facts for injection into an LLM system prompt."""
        return "\n".join(f"- {f}" for f in self.facts)
