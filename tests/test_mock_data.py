"""Mock market data client."""

from __future__ import annotations

import pytest

from chancetime.data_layer import MockMarketClient, Platform


@pytest.mark.asyncio
async def test_mock_list_markets() -> None:
    markets = await MockMarketClient().list_markets(limit=8)
    assert len(markets) == 8
    platforms = {m.platform for m in markets}
    # Dual-listed arb fixtures + classic mock singles
    assert Platform.KALSHI in platforms
    assert Platform.POLYMARKET in platforms
    assert Platform.MOCK in platforms
    assert all(0.0 <= m.yes_price <= 1.0 for m in markets)
