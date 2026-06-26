"""Research Agent (Core module): live web/data lookups, summarized by the LOCAL model.

The local LLM has a frozen knowledge cutoff, so it cannot know today's price/news/weather. This
agent FETCHES current data from the internet — free no-key APIs for exact things (crypto via
CoinGecko, weather via Open-Meteo) and a DuckDuckGo web search for open questions/news — then hands
the fetched text to the local model to summarize into a brief spoken answer. The brain stays local
and private; only the specific lookup you asked for leaves the machine.

This is the foundation for scheduled "briefings" (Routines): the same lookup, delivered on a
schedule ("...every morning at 9am").
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from ..core.permissions import Capability
from .base import BaseAgent

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/120.0 Safari/537.36")

# spoken aliases / tickers -> CoinGecko id (anything else is resolved via their /search endpoint)
_COINS = {
    "btc": "bitcoin", "bitcoin": "bitcoin", "eth": "ethereum", "ethereum": "ethereum",
    "sol": "solana", "solana": "solana", "xrp": "ripple", "ripple": "ripple",
    "doge": "dogecoin", "dogecoin": "dogecoin", "ada": "cardano", "cardano": "cardano",
    "bnb": "binancecoin", "ltc": "litecoin", "litecoin": "litecoin", "dot": "polkadot",
    "matic": "matic-network", "avax": "avalanche-2", "link": "chainlink", "chainlink": "chainlink",
    "usdt": "tether", "tether": "tether", "usdc": "usd-coin", "ton": "the-open-network",
    "trx": "tron", "tron": "tron", "shib": "shiba-inu",
}
_PRICE_RE = re.compile(r"\b(price|worth|cost|value|trading|how much)\b", re.I)
_WEATHER_RE = re.compile(r"\bweather|temperature|forecast|how (?:hot|cold|warm)\b", re.I)
# WMO weather codes -> plain words (Open-Meteo)
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers",
    81: "rain showers", 82: "violent rain showers", 95: "thunderstorms", 96: "thunderstorms with hail",
}


class ResearchAgent(BaseAgent):
    domain = "research"
    module_id = "core.research"
    planner_examples = [
        'what is the price of bitcoin -> {"steps":[{"action":"research.lookup","argument":"price of bitcoin"}]}',
        'check the price of bitcoin -> {"steps":[{"action":"research.lookup","argument":"price of bitcoin"}]}',
        'how much is ethereum worth -> {"steps":[{"action":"research.lookup","argument":"price of ethereum"}]}',
        'what is the weather in seattle -> {"steps":[{"action":"research.lookup","argument":"weather in seattle"}]}',
        'any news on tesla -> {"steps":[{"action":"research.lookup","argument":"news on tesla"}]}',
        'what is happening with the stock market -> {"steps":[{"action":"research.lookup","argument":"stock market news today"}]}',
        'look up the latest on the mars mission -> {"steps":[{"action":"research.lookup","argument":"latest news on the mars mission"}]}',
    ]
    capabilities = {
        "lookup": Capability("lookup", False, "Look up current info on the web and summarize it aloud"),
    }

    def __init__(self, bus, perms, llm=None) -> None:
        super().__init__(bus, perms)
        self.llm = llm

    async def _execute(self, verb: str, params: dict[str, Any]) -> Any:
        if verb == "lookup":
            return {"speech": await self._lookup((params.get("argument") or "").strip())}
        raise ValueError(f"unhandled verb '{verb}'")

    # ---- orchestration ----
    async def _lookup(self, query: str) -> str:
        if not query:
            return "What would you like me to look up?"
        if not self.llm:
            return "I need a language model to summarize what I find."
        bundle = await self._gather(query)
        if bundle is None:
            return "I couldn't reach the internet just now — check the connection and try again."
        if not bundle.strip():
            return f"I couldn't find current information on {query}."
        return await self._summarize(query, bundle)

    async def _gather(self, query: str) -> str | None:
        """Build a 'live data' bundle from the best source(s). None means a hard network failure."""
        q = query.lower()
        coin = self._detect_coin(q)
        if coin:
            data = await self._crypto(coin)
            if data is not None:
                news = await self._web_snippets(f"{coin} price news today", n=4)
                return data + (f"\n\nRecent headlines:\n{news}" if news else "")
        if _WEATHER_RE.search(q):
            place = self._extract_place(q)
            if place:
                w = await self._weather(place)
                if w is not None:
                    return w
        return await self._web_snippets(query, n=6)

    async def _summarize(self, query: str, bundle: str) -> str:
        from ..core.config import get_name

        system = (
            f"You are {get_name()}, a concise voice assistant. The user asked: \"{query}\". "
            "Using ONLY the live data below, answer in a brief, natural, SPOKEN style — 2 to 3 short "
            "sentences, no markdown, no bullet lists. Lead with the key fact or number. For a price, "
            "give the price and the recent trend (up/down). If headlines are included, sum up the "
            "overall mood in a phrase (e.g. 'the news is mostly positive'). Never invent anything "
            "that isn't in the data; if the data is thin, say so briefly. /no_think"
        )
        try:
            resp = await self.llm.generate(system=system, prompt=bundle, temperature=0.3)
            return resp.text.strip() or "I found some information but couldn't summarize it."
        except Exception:
            return "I found some information but had trouble summarizing it."

    # ---- sources ----
    @staticmethod
    def _detect_coin(q: str) -> str | None:
        for w in re.findall(r"[a-z0-9]+", q):
            if w in _COINS:
                return _COINS[w]
        return None

    @staticmethod
    def _extract_place(q: str) -> str:
        m = re.search(r"\b(?:in|for|at)\s+([a-z][a-z .'-]+)$", q.strip())
        return m.group(1).strip() if m else ""

    @staticmethod
    async def _get_json(url: str, timeout: float = 8.0):
        import httpx

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": _UA, "Accept": "application/json"})
            r.raise_for_status()
            return r.json()

    @staticmethod
    async def _get_text(url: str, timeout: float = 8.0) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": _UA})
            r.raise_for_status()
            return r.text

    async def _crypto(self, coin_id: str) -> str | None:
        base = "https://api.coingecko.com/api/v3"
        try:
            price = await self._get_json(
                f"{base}/simple/price?ids={coin_id}&vs_currencies=usd"
                "&include_24hr_change=true&include_market_cap=true")
            p = price.get(coin_id)
            if not p or p.get("usd") is None:
                return None
            usd = p["usd"]
            lines = [f"{coin_id.replace('-', ' ').title()} (cryptocurrency), live data:"]
            lines.append(f"Current price: ${usd:,.0f}" if usd >= 1 else f"Current price: ${usd:,.6f}")
            if p.get("usd_24h_change") is not None:
                lines.append(f"24-hour change: {p['usd_24h_change']:+.1f}%")
            try:  # 7-day trend
                chart = await self._get_json(f"{base}/coins/{coin_id}/market_chart?vs_currency=usd&days=7")
                pts = [x[1] for x in chart.get("prices", []) if isinstance(x, list) and len(x) == 2]
                if len(pts) >= 2 and pts[0]:
                    lines.append(f"7-day change: {(pts[-1] - pts[0]) / pts[0] * 100:+.1f}%")
            except Exception:
                pass
            return "\n".join(lines)
        except Exception:
            return None

    async def _weather(self, place: str) -> str | None:
        try:
            geo = await self._get_json(
                f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(place)}&count=1")
            res = geo.get("results") or []
            if not res:
                return None
            g = res[0]
            label = ", ".join(x for x in (g.get("name"), g.get("admin1"), g.get("country_code")) if x)
            w = await self._get_json(
                f"https://api.open-meteo.com/v1/forecast?latitude={g['latitude']}&longitude={g['longitude']}"
                "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
                "&temperature_unit=fahrenheit&wind_speed_unit=mph")
            cur = w.get("current", {})
            out = [f"Current weather in {label}:"]
            if cur.get("weather_code") in _WMO:
                out.append(f"Conditions: {_WMO[cur['weather_code']]}")
            if cur.get("temperature_2m") is not None:
                feels = cur.get("apparent_temperature")
                out.append(f"Temperature: {cur['temperature_2m']:.0f}°F"
                           + (f" (feels like {feels:.0f}°F)" if feels is not None else ""))
            if cur.get("wind_speed_10m") is not None:
                out.append(f"Wind: {cur['wind_speed_10m']:.0f} mph")
            return "\n".join(out) if len(out) > 1 else None
        except Exception:
            return None

    async def _web_snippets(self, query: str, n: int = 6) -> str | None:
        """Top DuckDuckGo result snippets as plain text. None = network failure; '' = no results."""
        try:
            html = await self._get_text(
                f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}")
        except Exception:
            return None
        out: list[str] = []
        for raw in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)[:n]:
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw)).strip()
            if text:
                out.append(f"- {text}")
        return "\n".join(out)
