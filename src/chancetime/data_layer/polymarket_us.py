"""Polymarket US market data client.

Polymarket US (CFTC-regulated, docs at https://docs.polymarket.us) is
account-based like Kalshi — NOT the international Polygon/CLOB wallet stack.

Public market data: https://gateway.polymarket.us  (GET /v1/markets)
Authenticated trading: https://api.polymarket.us  (key required; not wired for orders yet)

Auth material (for future signed routes):
  - POLYMARKET_API_KEY — API Key ID (UUID)
  - POLYMARKET_PRIVATE_KEY_PATH — private key file
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp

from chancetime.data_layer.base import MarketDataClient
from chancetime.data_layer.matching import normalize_title
from chancetime.data_layer.models import Market, Platform
from chancetime.data_layer.timeparse import parse_close_time
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import load_text_secret, resolve_path

log = get_logger(__name__)

POLYMARKET_US_PUBLIC_BASE = "https://gateway.polymarket.us"
POLYMARKET_US_AUTH_BASE = "https://api.polymarket.us"


def _amount_value(raw: object) -> float | None:
    """Parse gateway Amount object or plain number/string."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        v = raw.get("value")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _price_01(raw: object) -> float | None:
    """Coerce price to 0-1 (handles cents or dollars)."""
    v = _amount_value(raw) if not isinstance(raw, int | float | str) else None
    if v is None:
        try:
            v = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    if v > 1.0:
        v = v / 100.0
    return max(0.0, min(1.0, v))


class PolymarketUSClient(MarketDataClient):
    """Polymarket US public markets fetcher (gateway).

    SAFETY: read-only public listing. No order placement here.
    """

    def __init__(
        self,
        *,
        api_key_id: str | None = None,
        private_key_path: str | Path | None = None,
        base_url: str = POLYMARKET_US_PUBLIC_BASE,
        enrich_bbo: bool = True,
        bbo_limit: int = 15,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.api_key_id = api_key_id
        self.private_key_path: Path | None = (
            resolve_path(private_key_path) if private_key_path else None
        )
        self.base_url = base_url.rstrip("/")
        self.auth_base_url = POLYMARKET_US_AUTH_BASE
        self.enrich_bbo = enrich_bbo
        self.bbo_limit = bbo_limit
        self._session = session
        self._owns_session = session is None
        self._private_key_pem: str | None = None

        if self.private_key_path is not None:
            if self.private_key_path.is_file():
                log.info(
                    "polymarket_us_private_key_loaded_path",
                    path=str(self.private_key_path),
                )
            else:
                log.warning(
                    "polymarket_us_private_key_missing",
                    path=str(self.private_key_path),
                )

    @property
    def credentials_configured(self) -> bool:
        return bool(
            self.api_key_id
            and self.private_key_path is not None
            and self.private_key_path.is_file()
        )

    def load_private_key_pem(self) -> str:
        if self._private_key_pem is not None:
            return self._private_key_pem
        if self.private_key_path is None:
            raise FileNotFoundError("POLYMARKET_PRIVATE_KEY_PATH is not set")
        pem = load_text_secret(self.private_key_path)
        # US keys may be shorter than Kalshi RSA; accept any non-empty PEM-like file
        if len(pem.strip()) < 20:
            raise ValueError(f"Key file looks empty: {self.private_key_path}")
        self._private_key_pem = pem
        return pem

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25),
                headers={"Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def list_markets(self, *, limit: int = 20) -> list[Market]:
        """GET /v1/markets?active=true&closed=false on gateway (paginated)."""
        session = await self._get_session()
        markets_raw = await self._paginate_markets(session, limit=limit)
        result: list[Market] = []
        for m in markets_raw[:limit]:
            try:
                result.append(self._normalize(m))
            except (KeyError, TypeError, ValueError) as exc:
                log.debug("polymarket_us_market_skip", error=str(exc))

        if self.enrich_bbo and result:
            await self._enrich_with_bbo(session, result[: min(self.bbo_limit, len(result))])

        log.info(
            "polymarket_us_markets_fetched",
            count=len(result),
            url=f"{self.base_url}/v1/markets",
            auth_configured=self.credentials_configured,
        )
        return result

    async def search_markets(self, query: str, *, limit: int = 40) -> list[Market]:
        """GET /v1/search?query=... — pull markets nested under matching events."""
        session = await self._get_session()
        url = f"{self.base_url}/v1/search"
        params: dict[str, str | int] = {"query": query, "limit": min(limit, 50)}
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning(
                        "polymarket_us_search_failed",
                        status=resp.status,
                        query=query,
                        body=body[:160],
                    )
                    return []
                payload: Any = await resp.json()
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            log.warning("polymarket_us_search_error", error=str(exc), query=query)
            return []

        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return []
        result: list[Market] = []
        seen: set[str] = set()
        for ev in events:
            if not isinstance(ev, dict):
                continue
            nested = ev.get("markets")
            if not isinstance(nested, list):
                continue
            for m in nested:
                if not isinstance(m, dict):
                    continue
                try:
                    market = self._normalize(m)
                except (KeyError, TypeError, ValueError):
                    continue
                if market.id in seen:
                    continue
                seen.add(market.id)
                result.append(market)
                if len(result) >= limit:
                    break
            if len(result) >= limit:
                break
        log.info("polymarket_us_search", query=query, count=len(result))
        return result

    async def _paginate_markets(
        self, session: aiohttp.ClientSession, *, limit: int
    ) -> list[dict[str, Any]]:
        url = f"{self.base_url}/v1/markets"
        markets_raw: list[dict[str, Any]] = []
        page_size = min(50, max(limit, 20))
        offset = 0
        while len(markets_raw) < limit:
            params: dict[str, str | int | bool] = {
                "limit": page_size,
                "offset": offset,
                "active": "true",
                "closed": "false",
            }
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning(
                            "polymarket_us_list_markets_failed",
                            status=resp.status,
                            body=body[:200],
                            offset=offset,
                        )
                        break
                    payload: Any = await resp.json()
            except (TimeoutError, aiohttp.ClientError, OSError) as exc:
                log.warning("polymarket_us_list_markets_error", error=str(exc))
                break
            batch = self._extract_market_list(payload)
            if not batch:
                break
            markets_raw.extend(batch)
            offset += len(batch)
            if len(batch) < page_size:
                break
        return markets_raw

    async def _enrich_with_bbo(self, session: aiohttp.ClientSession, markets: list[Market]) -> None:
        """Best-effort BBO mid/bid/ask update for markets that have a slug."""
        for i, m in enumerate(markets):
            updated = await self._fetch_one_bbo(session, m)
            if updated is not None:
                markets[i] = updated

    async def _fetch_one_bbo(self, session: aiohttp.ClientSession, m: Market) -> Market | None:
        slug = m.slug or m.id
        if not slug:
            return None
        url = f"{self.base_url}/v1/markets/{slug}/bbo"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data: Any = await resp.json()
            lite = data.get("marketData") or data
            if not isinstance(lite, dict):
                return None
            bid = _price_01(
                lite.get("bestBid") or lite.get("longQuote") or lite.get("bestBidQuote")
            )
            ask = _price_01(
                lite.get("bestAsk") or lite.get("shortQuote") or lite.get("bestAskQuote")
            )
            cur = _price_01(lite.get("currentPx") or lite.get("lastTradePx"))
            bid_sz = _amount_value(
                lite.get("bestBidSize") or lite.get("bidSize") or lite.get("longSize")
            )
            ask_sz = _amount_value(
                lite.get("bestAskSize") or lite.get("askSize") or lite.get("shortSize")
            )
            if bid is not None and ask is not None:
                mid = (bid + ask) / 2.0
            elif cur is not None:
                mid = cur
            else:
                return None
            mid = max(0.0, min(1.0, mid))
            oi = lite.get("openInterest")
            try:
                liq = float(oi) if oi is not None else m.liquidity_usd
            except (TypeError, ValueError):
                liq = m.liquidity_usd
            return m.model_copy(
                update={
                    "yes_price": mid,
                    "no_price": max(0.0, min(1.0, 1.0 - mid)),
                    "liquidity_usd": liq,
                    "yes_bid": bid,
                    "yes_ask": ask,
                    "yes_bid_size": bid_sz,
                    "yes_ask_size": ask_sz,
                    "has_bbo": bid is not None or ask is not None,
                }
            )
        except (TimeoutError, aiohttp.ClientError, OSError, TypeError, ValueError):
            return None

    async def enrich_bbo_markets(self, markets: list[Market]) -> list[Market]:
        """Fetch BBO for specific markets (pair-only use recommended)."""
        session = await self._get_session()
        out: list[Market] = []
        for m in markets:
            if m.platform != Platform.POLYMARKET:
                out.append(m)
                continue
            updated = await self._fetch_one_bbo(session, m)
            out.append(updated if updated is not None else m)
        return out

    @staticmethod
    def _extract_market_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [m for m in payload if isinstance(m, dict)]
        if isinstance(payload, dict):
            for key in ("markets", "data", "results", "items"):
                raw = payload.get(key)
                if isinstance(raw, list):
                    return [m for m in raw if isinstance(m, dict)]
        return []

    @staticmethod
    def _display_title(raw: dict[str, Any], mid: str) -> str:
        """Build a matchable title.

        Polymarket US often puts the *event type* in ``question``
        (e.g. "National League Champion") and the *subject* in ``title``
        (e.g. "New York Mets"). Prefer subject + event for matching.
        """
        subject = str(raw.get("title") or "").strip()
        question = str(raw.get("question") or raw.get("name") or "").strip()
        desc = str(raw.get("description") or raw.get("subtitle") or "").strip()
        if subject and question and subject.lower() not in question.lower():
            return f"{subject} - {question}"
        if subject:
            return subject
        if question:
            return question
        if desc:
            return desc.split(".")[0].strip()[:160] or mid
        return mid

    @staticmethod
    def _normalize(raw: dict[str, Any]) -> Market:
        mid = str(raw.get("id") or raw.get("market_id") or raw.get("slug") or "unknown")
        title = PolymarketUSClient._display_title(raw, mid)
        slug = raw.get("slug")
        slug_s = str(slug) if slug else None

        yes_f: float | None = None
        # Prefer BBO quotes on market object
        bid = _price_01(raw.get("bestBidQuote") or raw.get("bestBid"))
        ask = _price_01(raw.get("bestAskQuote") or raw.get("bestAsk"))
        if bid is not None and ask is not None:
            yes_f = (bid + ask) / 2.0
        # marketSides: long side is typically YES
        if yes_f is None:
            sides = raw.get("marketSides")
            if isinstance(sides, list):
                for side in sides:
                    if not isinstance(side, dict):
                        continue
                    if side.get("long") is True or str(side.get("identifier", "")).upper() in {
                        "YES",
                        "LONG",
                    }:
                        yes_f = _price_01(side.get("price") or side.get("quote"))
                        if yes_f is not None:
                            break
        if yes_f is None:
            yes_f = _price_01(raw.get("yes_price") or raw.get("last_price") or raw.get("price"))
        if yes_f is None:
            yes_f = 0.5
        yes_f = max(0.0, min(1.0, yes_f))

        vol = raw.get("volume24hr") or raw.get("volume") or 0
        try:
            volume = float(vol)
        except (TypeError, ValueError):
            volume = 0.0

        has_quote = bid is not None and ask is not None
        close = parse_close_time(
            raw.get("endDate"),
            raw.get("end_date"),
            raw.get("closeTime"),
            raw.get("close_time"),
            raw.get("expirationTime"),
            raw.get("expiration_time"),
            raw.get("resolvesAt"),
            raw.get("eventStartTime"),
        )
        return Market(
            id=mid,
            platform=Platform.POLYMARKET,
            title=title,
            description=str(raw.get("description") or raw.get("subtitle") or ""),
            yes_price=yes_f,
            no_price=max(0.0, min(1.0, 1.0 - yes_f)),
            volume_usd=volume,
            liquidity_usd=volume,  # proxy until book depth wired fully
            close_time=close,
            url=f"https://polymarket.us/market/{slug_s}" if slug_s else None,
            slug=slug_s,
            canonical_key=normalize_title(title),
            yes_bid=bid,
            yes_ask=ask,
            has_bbo=has_quote,
            raw=dict(raw),
        )
