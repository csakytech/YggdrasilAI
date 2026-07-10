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

# Everything CLICKABLE and visible — links, buttons, submit inputs — numbered in document
# order (content area first). Each element is tagged data-ygg-n so we can CLICK it later
# (element.click() — works for buttons and JS links, not just hrefs). arguments[0] = also
# draw numbered badges (the Handsfree-for-Web pattern: say "click", see numbers, "select 4").
# The tag numbers == badge numbers == spoken numbers, always.
_CLICKABLES_JS = r"""
const draw = arguments[0];
const SEL = 'a[href], button, [role=button], input[type=submit], input[type=button], summary';
const root = document.querySelector('#search, #rso, main, [role=main], #content, #mw-content-text, article') || document.body;
document.querySelectorAll('.ygg-badge').forEach(e => e.remove());
document.querySelectorAll('[data-ygg-n]').forEach(e => e.removeAttribute('data-ygg-n'));
let items = []; const seenEl = new Set(); const seenLinkText = new Set();
const collect = (scope) => {
  for (const el of scope.querySelectorAll(SEL)) {
    if (items.length >= 60) return;
    if (seenEl.has(el) || !el.offsetParent) continue;
    const isLink = el.tagName === 'A';
    if (isLink && (!el.href || el.href.startsWith('javascript'))) continue;
    let t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g,' ');
    if (t.length < 2 || t.length > 90) continue;
    if (isLink) {                       // dedupe repeated link TEXT; buttons may repeat
      const key = t.toLowerCase();
      if (seenLinkText.has(key)) continue;
      seenLinkText.add(key);
    }
    seenEl.add(el);
    items.push({el, text: t, kind: isLink ? 'link' : 'button', href: isLink ? el.href : ''});
  }
};
collect(root);
if (items.length < 5 && root !== document.body) collect(document.body);
const sx = window.scrollX, sy = window.scrollY;
items.forEach((it, i) => {
  it.el.setAttribute('data-ygg-n', String(i + 1));
  if (!draw) return;
  const r = it.el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return;
  const b = document.createElement('div');
  b.className = 'ygg-badge';
  b.textContent = String(i + 1);
  b.setAttribute('style',
    'position:absolute!important;z-index:2147483647!important;background:#f0c040!important;'
    + 'color:#000!important;font:bold 12px/1.3 sans-serif!important;padding:0 5px!important;'
    + 'border-radius:8px!important;box-shadow:0 1px 3px rgba(0,0,0,.6)!important;'
    + 'pointer-events:none!important;white-space:nowrap!important;'
    + 'left:' + (r.left + sx - 2) + 'px;top:' + (r.top + sy - 9) + 'px;');
  document.body.appendChild(b);
});
return items.map((o, i) => ({n: i + 1, text: o.text, kind: o.kind, href: o.href}));
"""

_CLICK_N_JS = r"""
const el = document.querySelector('[data-ygg-n="' + arguments[0] + '"]');
if (!el) return {ok: false, text: ''};
const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g,' ');
el.scrollIntoView({block: 'center'});
el.click();
return {ok: true, text: t.slice(0, 80)};
"""

_HIDE_BADGES_JS = "document.querySelectorAll('.ygg-badge').forEach(e => e.remove()); return true;"

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


def get_clickables(badge: bool = False) -> list[dict]:
    """Enumerate everything clickable ({n, text, kind, href}), tagging each element so
    ``click_number`` can act on it. If ``badge``, paint matching numbered badges on the page."""
    return client().execute(_CLICKABLES_JS, [badge]) or []


def get_links(badge: bool = False) -> list[dict]:
    """Just the links from the clickable enumeration (numbers preserved)."""
    return [c for c in get_clickables(badge) if c.get("kind") == "link"]


def click_number(n: int) -> dict:
    """Click clickable number ``n`` (from the last enumeration). {ok, text}."""
    r = client().execute(_CLICK_N_JS, [str(int(n))])
    return r if isinstance(r, dict) else {"ok": False, "text": ""}


def hide_badges() -> None:
    try:
        client().execute(_HIDE_BADGES_JS)
    except Exception:
        pass


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
