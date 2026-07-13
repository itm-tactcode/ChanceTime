"""Abstract market data client."""

from __future__ import annotations

from abc import ABC, abstractmethod

from chancetime.data_layer.models import Market


class MarketDataClient(ABC):
    """Fetch normalized market snapshots from a venue."""

    @abstractmethod
    async def list_markets(self, *, limit: int = 20) -> list[Market]:
        """Return active / open markets (best-effort)."""

    async def close(self) -> None:
        """Release resources (HTTP sessions, etc.)."""
        return None
