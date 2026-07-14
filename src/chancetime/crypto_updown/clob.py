"""CLOB public market data (no trading keys).

https://docs.polymarket.com/trading/orderbook
https://clob.polymarket.com
"""

from __future__ import annotations

from typing import Any

import aiohttp

from chancetime.crypto_updown.models import OutcomeBook, UpDownMarket
from chancetime.utils.logging import get_logger

log = get_logger(__name__)

CLOB_BASE = "https://clob.polymarket.com"


class ClobPublicClient:
    """Read-only order books / midpoints."""

    def __init__(
        self,
        *,
        base_url: str = CLOB_BASE,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns = session is None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._owns and self._session is not None:
            await self._session.close()
            self._session = None

    async def get_book(self, token_id: str) -> dict[str, Any] | None:
        session = await self._sess()
        url = f"{self.base_url}/book"
        try:
            async with session.get(url, params={"token_id": token_id}) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            log.debug("clob_book_error", token=token_id[:16], error=str(exc))
            return None

    @staticmethod
    def apply_book(book: OutcomeBook, payload: dict[str, Any] | None) -> OutcomeBook:
        if not payload:
            return book
        bids = payload.get("bids") or []
        asks = payload.get("asks") or []
        best_bid = best_ask = None
        bid_sz = ask_sz = None
        try:
            if bids:
                # CLOB often sorts bids descending already; take max price
                best = max(bids, key=lambda x: float(x.get("price", 0)))
                best_bid = float(best["price"])
                bid_sz = float(best.get("size") or 0)
            if asks:
                best = min(asks, key=lambda x: float(x.get("price", 1)))
                best_ask = float(best["price"])
                ask_sz = float(best.get("size") or 0)
        except (TypeError, ValueError, KeyError):
            return book
        mid = book.mid
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        elif best_ask is not None:
            mid = best_ask
        elif best_bid is not None:
            mid = best_bid
        return book.model_copy(
            update={
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_size": bid_sz,
                "ask_size": ask_sz,
                "mid": mid,
                "has_bbo": best_bid is not None and best_ask is not None,
            }
        )

    async def enrich_market(self, market: UpDownMarket) -> UpDownMarket:
        """Attach BBO to up/down tokens. Fail partial OK; never invent."""
        up, down = market.up, market.down
        if up is not None:
            raw = await self.get_book(up.token_id)
            up = self.apply_book(up, raw)
        if down is not None:
            raw = await self.get_book(down.token_id)
            down = self.apply_book(down, raw)
        return market.model_copy(update={"up": up, "down": down})
