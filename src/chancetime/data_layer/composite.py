"""Multi-venue market data client (Kalshi + Polymarket US + mock)."""

from __future__ import annotations

from chancetime.data_layer.base import MarketDataClient
from chancetime.data_layer.models import Market
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class CompositeMarketClient(MarketDataClient):
    """Fetch markets from several clients and concatenate results."""

    def __init__(self, clients: list[MarketDataClient]) -> None:
        if not clients:
            raise ValueError("CompositeMarketClient needs at least one client")
        self.clients = clients

    async def list_markets(self, *, limit: int = 20) -> list[Market]:
        # Each venue gets up to ``limit`` so deep scans are not half-starved
        per = max(1, limit)
        combined: list[Market] = []
        for client in self.clients:
            try:
                batch = await client.list_markets(limit=per)
                combined.extend(batch)
            except Exception:
                log.exception("composite_client_error", client=type(client).__name__)
        log.info("composite_markets_fetched", count=len(combined), venues=len(self.clients))
        # Soft cap: allow 2x for multi-venue before caller slices
        return combined[: max(limit * len(self.clients), limit)]

    async def close(self) -> None:
        for client in self.clients:
            await client.close()
