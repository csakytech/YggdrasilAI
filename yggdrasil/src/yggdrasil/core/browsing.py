"""Web-search context — lets the assistant page through results it opened ("next page",
"go to page 4"). The Apps agent records a search here when it opens one; the Browser agent
reads it to build the URL for another results page (we can't read the live browser URL
without a deeper integration, so we track the search we launched).

Persisted to a small JSON file so it survives an assistant restart and is shared correctly
even if agents run in separate processes.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path

_DEFAULT = {"engine": "google", "query": "", "page": 1}


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "yggdrasil" / "search.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return {**_DEFAULT, **d} if isinstance(d, dict) else dict(_DEFAULT)
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT)


def _save(d: dict) -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def set_search(query: str, engine: str = "google") -> None:
    _save({"engine": engine, "query": (query or "").strip(), "page": 1})


def get() -> dict:
    return _load()


def set_page(n: int) -> None:
    d = _load()
    d["page"] = max(1, int(n))
    _save(d)


def page_url(page: int) -> str | None:
    """The URL for a given results page of the current search, or None if no search is active."""
    d = _load()
    q = d.get("query", "")
    if not q:
        return None
    page = max(1, int(page))
    qs = urllib.parse.quote(q)
    engine = d.get("engine", "google")
    if engine == "bing":
        return f"https://www.bing.com/search?q={qs}&first={(page - 1) * 10 + 1}"
    if engine == "duckduckgo":
        return f"https://duckduckgo.com/?q={qs}"  # DDG paginates via JS; page 1 best-effort
    base = f"https://www.google.com/search?q={qs}"
    return base + (f"&start={(page - 1) * 10}" if page > 1 else "")
