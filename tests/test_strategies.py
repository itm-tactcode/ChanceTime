"""Strategy unit tests."""

from __future__ import annotations

import pytest

from chancetime.data_layer.mock import MockMarketClient
from chancetime.strategies.base import Side
from chancetime.strategies.simple_edge import SimpleEdgeStrategy


@pytest.mark.asyncio
async def test_simple_edge_emits_signals_on_mock_markets() -> None:
    markets = await MockMarketClient().list_markets()
    strategy = SimpleEdgeStrategy(edge_threshold=0.08, min_liquidity_usd=100.0)
    signals = await strategy.generate_signals(markets)

    # Illiquid desert market filtered; liquid ones with |p-0.5|>=0.08 may signal
    assert all(s.market_id != "mock-illiquid-noise" for s in signals)
    for s in signals:
        assert s.side in (Side.YES, Side.NO)
        assert abs(s.edge) >= 0.08
        assert 0.0 <= s.strength <= 1.0


@pytest.mark.asyncio
async def test_simple_edge_disabled() -> None:
    markets = await MockMarketClient().list_markets()
    strategy = SimpleEdgeStrategy(enabled=False)
    assert await strategy.generate_signals(markets) == []
