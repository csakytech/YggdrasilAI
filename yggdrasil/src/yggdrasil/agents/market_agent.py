"""Market agent — browse the marketplace and install agents BY VOICE.

The accessibility-critical front-end: a user who can't touch a mouse installs new capabilities
hands-free. It's a thin layer over ``core.modules`` (the SAME engine the GUI installer uses), so there
is one install path. Anything that runs new code (install) or is destructive (remove) requires an
explicit spoken "yes, install/remove it" — you hear what the agent can do first, then confirm out loud.
"""
from __future__ import annotations

import sys

from ..core import modules
from ..core.permissions import Capability
from .base import BaseAgent


class MarketAgent(BaseAgent):
    domain = "market"
    module_id = "core.market"

    planner_examples = [
        'what agents are available -> {"steps":[{"action":"market.search","argument":""}]}',
        'find an agent for images -> {"steps":[{"action":"market.search","argument":"images"}]}',
        'install the notes agent -> {"steps":[{"action":"market.install","argument":"notes"}]}',
        'yes install it -> {"steps":[{"action":"market.confirm","argument":""}]}',
        'what agents do i have -> {"steps":[{"action":"market.installed","argument":""}]}',
        'remove the notes agent -> {"steps":[{"action":"market.remove","argument":"notes"}]}',
    ]

    capabilities = {
        "search": Capability("search", False, "Browse agents in the marketplace"),
        "install": Capability("install", False, "Install a marketplace agent (asks you to confirm first)"),
        "confirm": Capability("confirm", False, "Confirm the pending install or removal"),
        "cancel": Capability("cancel", False, "Cancel the pending install or removal"),
        "installed": Capability("installed", False, "List the agents you've installed"),
        "remove": Capability("remove", False, "Remove an installed agent (asks you to confirm first)"),
    }

    def __init__(self, bus, perms, llm=None) -> None:
        super().__init__(bus, perms)
        self.llm = llm
        self._pending: dict | None = None   # {"op": "install"|"remove", ...} awaiting a spoken "yes"
        self.on_change = None               # async hook the host sets to hot-load/refresh after a change

    async def _execute(self, verb, params):
        arg = (params.get("argument") or "").strip()
        if verb == "search":
            return {"speech": self._search(arg)}
        if verb == "install":
            return {"speech": self._stage_install(arg)}
        if verb == "remove":
            return {"speech": self._stage_remove(arg)}
        if verb == "cancel":
            return {"speech": self._cancel()}
        if verb == "installed":
            return {"speech": self._list_installed()}
        if verb == "confirm":
            return {"speech": await self._confirm()}
        raise ValueError(f"unhandled verb '{verb}'")

    # --- browse ---
    def _search(self, query):
        try:
            entries = modules.search_registry(query)
        except Exception:
            return "I couldn't reach the marketplace right now. Check your connection and try again."
        if not entries:
            where = f" for {query}" if query else ""
            return f"I didn't find any agents{where} in the marketplace yet."
        head = "Here's what I found: " if query else "Agents in the marketplace: "
        items = [f"{e.get('name', e['id'])} — {e.get('summary', '')} ({e.get('tier', 'community')})"
                 for e in entries[:5]]
        tail = f" To install one, say, for example, “install the {self._short_name(entries[0])} agent”."
        return head + "; ".join(items) + "." + tail

    # --- install: stage + speak consent, then wait for a spoken "yes" ---
    def _stage_install(self, query):
        if not query:
            return "Which agent would you like to install? You can ask what's available first."
        try:
            entries = modules.search_registry(query)
        except Exception:
            return "I couldn't reach the marketplace right now."
        if not entries:
            return f"I couldn't find a '{query}' agent in the marketplace."
        e = entries[0]
        self._pending = {"op": "install", "entry": e}
        consent = e.get("consent", {}) or {}
        perms = consent.get("permissions") or ["use only its own private storage"]
        speech = f"{e.get('name', e['id'])} — {e.get('summary', '')}. It can {self._join(perms)}."
        dangerous = consent.get("dangerous") or []
        if dangerous:
            speech += f" Heads up: it can also {self._join(dangerous)}."
        speech += f" This is a {e.get('tier', 'community')} agent. To go ahead, say: yes, install it."
        return speech

    # --- remove: stage + confirm ---
    def _stage_remove(self, query):
        match = self._find_installed(query)
        if not match:
            return f"You don't have a '{query}' agent installed."
        self._pending = {"op": "remove", "id": match["id"], "name": match.get("name", match["id"])}
        return f"Remove the {match.get('name', match['id'])} agent? Say: yes, remove it."

    def _cancel(self):
        if not self._pending:
            return "There's nothing to cancel."
        op = self._pending["op"]
        self._pending = None
        return f"Okay, I won't {op} it."

    async def _confirm(self):
        p = self._pending
        if not p:
            return "There's nothing waiting to confirm. Say, for example, “install the notes agent”."
        self._pending = None
        if p["op"] == "install":
            e = p["entry"]
            try:
                m = modules.install_from_registry(e)
            except Exception as ex:  # noqa: BLE001
                return f"Sorry, I couldn't install {e.get('name', e['id'])}. {ex}"
            await self._fire_change()
            name = (m.get("agent") or {}).get("name") or e.get("name", e["id"])
            return f"Installed {name}. It's ready now — give it a try."
        if p["op"] == "remove":
            modules.remove(p["id"])
            await self._fire_change()
            return f"Removed the {p['name']} agent."
        return "Nothing to do."

    def _list_installed(self):
        items = modules.installed()
        if not items:
            return "You haven't installed any marketplace agents yet."
        return ("You have these agents installed: "
                + "; ".join(i.get("name", i["id"]) for i in items) + ".")

    # --- helpers ---
    async def _fire_change(self):
        if self.on_change:
            try:
                await self.on_change()
            except Exception as e:  # a refresh failure must not break the confirmation reply
                print(f"[market] reload after change failed: {e!r}", file=sys.stderr)

    def _find_installed(self, query):
        q = query.lower().strip()
        for i in modules.installed():
            if q and (q in i.get("name", "").lower() or q in i.get("id", "").lower()):
                return i
        return None

    @staticmethod
    def _short_name(entry):
        return (entry.get("name") or entry.get("id", "")).lower()

    @staticmethod
    def _join(items):
        items = list(items)
        if len(items) <= 1:
            return items[0] if items else ""
        return ", ".join(items[:-1]) + ", and " + items[-1]
