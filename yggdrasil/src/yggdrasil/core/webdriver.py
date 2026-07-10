"""Firefox Marionette client — read and drive the live web page, so the browser can be
operated entirely by voice: list the links, open one, read the page aloud.

Firefox has a built-in automation protocol (Marionette, port 2828) when launched with
``--marionette``. We speak its wire protocol directly (``<len>:<json>``) — no external
dependency, version-tolerant. One persistent connection is shared across commands.

This is the deep browser integration (capability-ladder Rung 2): where key-poking (xdotool)
can only scroll and go back, this can SEE the page — enumerate its links and headings, click a
specific one, and extract its readable text — which is what lets someone browse the web with
no hands.
"""
from __future__ import annotations

import json
import socket

HOST, PORT = "127.0.0.1", 2828


def available() -> bool:
    try:
        s = socket.create_connection((HOST, PORT), timeout=1.0)
        s.close()
        return True
    except OSError:
        return False


class Marionette:
    def __init__(self) -> None:
        self.sock: socket.socket | None = None
        self._mid = 0

    def _recv(self, s: socket.socket):
        buf = b""
        while b":" not in buf:
            c = s.recv(1)
            if not c:
                raise OSError("marionette connection closed")
            buf += c
        length, rest = buf.split(b":", 1)
        n = int(length)
        data = rest
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk:
                raise OSError("marionette connection closed")
            data += chunk
        return json.loads(data.decode("utf-8"))

    def _send(self, s: socket.socket, cmd: str, params: dict) -> None:
        self._mid += 1
        b = json.dumps([0, self._mid, cmd, params]).encode("utf-8")
        s.sendall(f"{len(b)}:".encode() + b)

    def _connect(self) -> None:
        s = socket.create_connection((HOST, PORT), timeout=8.0)
        self._recv(s)          # handshake: {"applicationType":"gecko","marionetteProtocol":3}
        self._mid = 0
        self._send(s, "WebDriver:NewSession", {})
        r = self._recv(s)
        # A session may already exist (a prior connection) — that's fine, commands still work.
        if isinstance(r, list) and len(r) >= 3 and r[2] and "session" not in str(r[2]).lower():
            s.close()
            raise OSError(f"marionette NewSession failed: {r[2]}")
        self.sock = s

    def execute(self, script: str, args: list | None = None):
        """Run JavaScript in the current page and return its value. Reconnects once on a dropped
        socket. ``script`` should ``return`` a value; ``args`` are exposed as ``arguments[…]``."""
        for attempt in range(2):
            try:
                if self.sock is None:
                    self._connect()
                self._send(self.sock, "WebDriver:ExecuteScript",
                           {"script": script, "args": args or []})
                r = self._recv(self.sock)
                if isinstance(r, list) and len(r) >= 4:
                    if r[2]:
                        raise RuntimeError(str(r[2])[:200])
                    val = r[3]
                    return val.get("value") if isinstance(val, dict) else val
                return None
            except (OSError, ConnectionError):
                self.sock = None
                if attempt == 1:
                    raise

    def navigate(self, url: str) -> None:
        self.execute("window.location.href = arguments[0]; return true;", [url])

    def close(self) -> None:
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = None


_client: Marionette | None = None


def client() -> Marionette:
    global _client
    if _client is None:
        _client = Marionette()
    return _client


# --- page-reading helpers (JavaScript run in the live page) ---------------------------------

# Meaningful, visible links (text 4–90 chars), de-duplicated, in document order, capped.
_LINKS_JS = r"""
const seen = new Set(); const out = [];
for (const a of document.querySelectorAll('a')) {
  if (!a.offsetParent) continue;                    // not visible
  if (!a.href || a.href.startsWith('javascript')) continue;
  let t = (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\s+/g,' ');
  if (t.length < 4 || t.length > 90) continue;
  const key = t.toLowerCase();
  if (seen.has(key)) continue;
  seen.add(key);
  out.push({text: t, href: a.href});
  if (out.length >= 40) break;
}
return out;
"""

# Clickable BUTTONS (Show more, Load more, Next…) with visible text — for expanding overviews etc.
_BUTTONS_JS = r"""
const out = [];
for (const b of document.querySelectorAll('button,[role=button],input[type=submit]')) {
  if (!b.offsetParent) continue;
  let t = (b.innerText || b.value || b.getAttribute('aria-label') || '').trim().replace(/\s+/g,' ');
  if (t && t.length <= 40) out.push(t);
  if (out.length >= 20) break;
}
return out;
"""

# The page's main readable text (prefer <main>/<article>), collapsed and length-capped.
_TEXT_JS = r"""
const pick = document.querySelector('main') || document.querySelector('article') || document.body;
let t = (pick.innerText || '').replace(/\n{2,}/g,'\n').replace(/[ \t]{2,}/g,' ').trim();
return t.slice(0, 6000);
"""


def get_links() -> list[dict]:
    return client().execute(_LINKS_JS) or []


def get_buttons() -> list[str]:
    return client().execute(_BUTTONS_JS) or []


def get_main_text() -> str:
    return client().execute(_TEXT_JS) or ""


def click_button(text: str) -> bool:
    """Click the first visible button whose text contains ``text`` (e.g. 'show more'). Returns
    whether one was found."""
    js = r"""
    const want = (arguments[0]||'').toLowerCase();
    for (const b of document.querySelectorAll('button,[role=button],input[type=submit]')) {
      if (!b.offsetParent) continue;
      const t = (b.innerText || b.value || b.getAttribute('aria-label') || '').toLowerCase();
      if (t.includes(want)) { b.click(); return true; }
    }
    return false;
    """
    return bool(client().execute(js, [text]))


def current_url() -> str:
    return client().execute("return window.location.href;") or ""
