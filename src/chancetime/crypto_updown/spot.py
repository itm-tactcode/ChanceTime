"""External spot prices — Coinbase primary; fallbacks for missing products (e.g. HYPE).

Fail-closed: returns None — never invents prices.
"""

from __future__ import annotations

import time
from typing import Any

import aiohttp

from chancetime.crypto_updown.models import SpotTick
from chancetime.utils.logging import get_logger

log = get_logger(__name__)

# Coinbase Exchange product ids
_COINBASE_PRODUCTS: dict[str, str] = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "DOGE": "DOGE-USD",
    "BNB": "BNB-USD",
}

# CoinGecko ids for assets Coinbase public may lack
_COINGECKO_IDS: dict[str, str] = {
    "HYPE": "hyperliquid",
    "BNB": "binancecoin",
}

# Kraken public pair (optional fallback)
_KRAKEN_PAIRS: dict[str, str] = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "XRP": "XRPUSD",
    "DOGE": "DOGEUSD",
}


class SpotClient:
    """Public spot ticker with multi-source fallback. Never invents prices."""

    def __init__(
        self,
        *,
        coinbase_base: str = "https://api.exchange.coinbase.com",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.coinbase_base = coinbase_base.rstrip("/")
        self._session = session
        self._owns = session is None
        self._last_ok: dict[str, SpotTick] = {}

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._owns and self._session is not None:
            await self._session.close()
            self._session = None

    async def get_price(self, asset: str) -> SpotTick | None:
        """Return live spot or None if unavailable (fail closed)."""
        a = asset.upper().strip()
        tick = await self._coinbase(a)
        if tick is None:
            tick = await self._coingecko(a)
        if tick is None:
            tick = await self._kraken(a)
        if tick is not None:
            self._last_ok[a] = tick
        return tick

    def last_ok_age(self, asset: str, now: float | None = None) -> float | None:
        """Seconds since last successful tick for asset, or None."""
        t = self._last_ok.get(asset.upper())
        if t is None:
            return None
        return (now if now is not None else time.time()) - t.ts

    async def _coinbase(self, asset: str) -> SpotTick | None:
        product = _COINBASE_PRODUCTS.get(asset)
        if not product:
            return None
        session = await self._sess()
        url = f"{self.coinbase_base}/products/{product}/ticker"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                payload: dict[str, Any] = await resp.json()
            price = float(payload["price"])
        except (TimeoutError, aiohttp.ClientError, OSError, KeyError, TypeError, ValueError):
            return None
        if price <= 0:
            return None
        return SpotTick(symbol=asset, price=price, source="coinbase_exchange", ts=time.time())

    async def _coingecko(self, asset: str) -> SpotTick | None:
        cg_id = _COINGECKO_IDS.get(asset)
        if not cg_id:
            return None
        session = await self._sess()
        url = "https://api.coingecko.com/api/v3/simple/price"
        try:
            async with session.get(
                url, params={"ids": cg_id, "vs_currencies": "usd"}
            ) as resp:
                if resp.status != 200:
                    log.debug("coingecko_status", asset=asset, status=resp.status)
                    return None
                payload = await resp.json()
            price = float(payload[cg_id]["usd"])
        except (
            TimeoutError,
            aiohttp.ClientError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            log.debug("coingecko_error", asset=asset, error=str(exc))
            return None
        if price <= 0:
            return None
        log.info("spot_fallback", asset=asset, source="coingecko", price=price)
        return SpotTick(symbol=asset, price=price, source="coingecko", ts=time.time())

    async def _kraken(self, asset: str) -> SpotTick | None:
        pair = _KRAKEN_PAIRS.get(asset)
        if not pair:
            return None
        session = await self._sess()
        url = "https://api.kraken.com/0/public/Ticker"
        try:
            async with session.get(url, params={"pair": pair}) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()
            result = payload.get("result") or {}
            # Kraken keys vary (XXBTZUSD vs XBTUSD)
            block = next(iter(result.values()), None)
            if not block:
                return None
            # c = last trade closed [price, lot]
            price = float(block["c"][0])
        except (
            TimeoutError,
            aiohttp.ClientError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
            StopIteration,
        ):
            return None
        if price <= 0:
            return None
        log.info("spot_fallback", asset=asset, source="kraken", price=price)
        return SpotTick(symbol=asset, price=price, source="kraken", ts=time.time())
