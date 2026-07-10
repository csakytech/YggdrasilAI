"""Web-search context — lets the assistant page through results it opened ("next page",
"go to page 4"). The Apps agent records a search here when it opens one; the Browser agent
reads it to build the URL for another results page (we can't read the live browser URL
without a deeper integration, so we track the search we launched)."""
from __future__ import annotations

import urllib.parse

_STATE = {"engine": "google", "query": "", "page": 1}


def set_search(query: str, engine: str = "google") -> None:
    _STATE.update(engine=engine, query=(query or "").strip(), page=1)


def get() -> dict:
    return dict(_STATE)


def set_page(n: int) -> None:
    _STATE["page"] = max(1, int(n))


def page_url(page: int) -> str | None:
    """The URL for a given results page of the current search, or None if no search is active."""
    q = _STATE["query"]
    if not q:
        return None
    page = max(1, int(page))
    qs = urllib.parse.quote(q)
    engine = _STATE["engine"]
    if engine == "bing":
        return f"https://www.bing.com/search?q={qs}&first={(page - 1) * 10 + 1}"
    if engine == "duckduckgo":
        # DDG's HTML results paginate by POST/JS; page 1 is the best we can build by URL.
        return f"https://duckduckgo.com/?q={qs}"
    # google (default): &start = 0, 10, 20, …
    base = f"https://www.google.com/search?q={qs}"
    return base + (f"&start={(page - 1) * 10}" if page > 1 else "")
