"""Public / optional auth price feeds for Path D (paper-first).

Default feed: Coinbase Exchange public ticker (same as Path C spot).
Robinhood: optional — requires API credentials for private endpoints;
when missing, we still price via Coinbase and label venue preference.
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol

import aiohttp

from chancetime.crypto_exchange.models import SpotQuote
from chancetime.utils.logging import get_logger

log = get_logger(__name__)

# Coinbase product ids
COINBASE_PRODUCTS: dict[str, str] = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "DOGE": "DOGE-USD",
    "BNB": "BNB-USD",
}

DEFAULT_WATCHLIST = ("BTC", "ETH", "SOL", "XRP", "DOGE")


class PriceVenue(Protocol):
    name: str

    async def get_quote(self, asset: str) -> SpotQuote | None: ...
    async def close(self) -> None: ...


class CoinbasePublicVenue:
    """Unauthenticated Coinbase Exchange ticker + optional book top."""

    name = "coinbase"

    def __init__(
        self,
        *,
        base_url: str = "https://api.exchange.coinbase.com",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns = session is None

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

    async def get_quote(self, asset: str) -> SpotQuote | None:
        product = COINBASE_PRODUCTS.get(asset.upper().strip())
        if not product:
            log.warning("coinbase_unknown_asset", asset=asset)
            return None
        session = await self._sess()
        ticker_url = f"{self.base_url}/products/{product}/ticker"
        book_url = f"{self.base_url}/products/{product}/book"
        last = bid = ask = None
        try:
            async with session.get(ticker_url) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning(
                        "coinbase_ticker_failed",
                        asset=asset,
                        status=resp.status,
                        body=body[:100],
                    )
                    return None
                payload: dict[str, Any] = await resp.json()
                last = float(payload["price"])
        except (TimeoutError, aiohttp.ClientError, OSError, KeyError, TypeError, ValueError) as exc:
            log.warning("coinbase_ticker_error", asset=asset, error=str(exc))
            return None
        # Best effort BBO
        try:
            async with session.get(book_url, params={"level": "1"}) as resp:
                if resp.status == 200:
                    book = await resp.json()
                    bids = book.get("bids") or []
                    asks = book.get("asks") or []
                    if bids:
                        bid = float(bids[0][0])
                    if asks:
                        ask = float(asks[0][0])
        except (TimeoutError, aiohttp.ClientError, OSError, TypeError, ValueError, IndexError):
            pass
        if last is None or last <= 0:
            return None
        return SpotQuote(
            asset=asset.upper(),
            product_id=product,
            bid=bid,
            ask=ask,
            last=last,
            source="coinbase_exchange",
            ts=time.time(),
        )


class RobinhoodVenue:
    """Robinhood Crypto API stub.

    Official docs: https://docs.robinhood.com/
    Without credentials, ``get_quote`` returns None (fail closed — do not invent).
    Paper bots should fall back to CoinbasePublicVenue for pricing.
    """

    name = "robinhood"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("ROBINHOOD_CRYPTO_API_KEY") or ""
        self._session = session
        self._owns = session is None
        self._configured = bool(self.api_key.strip())

    @property
    def configured(self) -> bool:
        return self._configured

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

    async def get_quote(self, asset: str) -> SpotQuote | None:
        if not self._configured:
            log.debug("robinhood_skip_no_credentials", asset=asset)
            return None
        # Full signed auth is account-specific; keep fail-closed until wired.
        log.warning(
            "robinhood_auth_not_wired",
            asset=asset,
            msg="Credentials present but signed RH client not implemented yet — use Coinbase feed",
        )
        return None


def make_price_venue(name: str = "coinbase") -> PriceVenue:
    n = name.lower().strip()
    if n in {"robinhood", "rh"}:
        return RobinhoodVenue()
    return CoinbasePublicVenue()
