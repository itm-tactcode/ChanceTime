"""Mock market data for paper-mode development without exchange keys."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from chancetime.data_layer.base import MarketDataClient
from chancetime.data_layer.matching import normalize_title
from chancetime.data_layer.models import Market, Platform


def _m(
    *,
    id: str,
    platform: Platform,
    title: str,
    yes: float,
    liquidity: float,
    volume: float = 10_000.0,
    description: str = "",
    days: int = 30,
    spread: float = 0.02,
    bid_size: float = 500.0,
    ask_size: float = 500.0,
    with_bbo: bool = True,
) -> Market:
    now = datetime.now(UTC)
    half = max(0.0, spread / 2.0)
    yes_bid = max(0.0, min(1.0, yes - half)) if with_bbo else None
    yes_ask = max(0.0, min(1.0, yes + half)) if with_bbo else None
    return Market(
        id=id,
        platform=platform,
        title=title,
        description=description or title,
        yes_price=yes,
        no_price=max(0.0, min(1.0, 1.0 - yes)),
        volume_usd=volume,
        liquidity_usd=liquidity,
        close_time=now + timedelta(days=days),
        slug=id,
        canonical_key=normalize_title(title),
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        yes_bid_size=bid_size if with_bbo else None,
        yes_ask_size=ask_size if with_bbo else None,
        has_bbo=with_bbo,
    )


class MockMarketClient(MarketDataClient):
    """Sample markets including dual-listed arb pairs for Phase 4 tests.

    ``list_markets`` advances an internal tick so multi-poll paper runs can
    exercise ``mean_revert`` (mid jumps on selected markets every few polls).
    """

    def __init__(self) -> None:
        self._tick = 0

    async def list_markets(self, *, limit: int = 20) -> list[Market]:
        t = self._tick
        self._tick += 1
        # Spike turnout on tick >= 3 so mean_revert has history then a move
        turnout = 0.33
        if t >= 3:
            turnout = 0.33 + min(0.12, 0.04 * (t - 2))  # 0.37, 0.41, 0.45...
        turnout = max(0.05, min(0.95, turnout))

        samples = [
            # Cross-venue pair: Fed cut (wide mid + tight books → clear executable edge)
            # mid 0.38 vs 0.50; ±1¢ BBO → YES ask 0.39 + NO ask 0.51 = 0.90 before fees
            _m(
                id="kalshi-fed-cut",
                platform=Platform.KALSHI,
                title="Will the Fed cut rates at the next meeting?",
                yes=0.38,
                liquidity=40_000.0,
                volume=125_000.0,
                description="Resolves YES if the FOMC cuts the target range.",
                spread=0.02,
            ),
            _m(
                id="pm-fed-cut",
                platform=Platform.POLYMARKET,
                title="Will the Fed cut rates at the next meeting?",
                yes=0.50,
                liquidity=35_000.0,
                volume=90_000.0,
                description="Same event on Polymarket US (mock).",
                spread=0.02,
            ),
            # Cross-venue pair: BTC (smaller spread)
            _m(
                id="kalshi-btc-100k",
                platform=Platform.KALSHI,
                title="Will Bitcoin exceed $100,000 by year end?",
                yes=0.61,
                liquidity=80_000.0,
                volume=500_000.0,
            ),
            _m(
                id="pm-btc-100k",
                platform=Platform.POLYMARKET,
                title="Will Bitcoin exceed 100000 USD by year end?",
                yes=0.63,
                liquidity=70_000.0,
                volume=400_000.0,
            ),
            # Single-venue / classic simple_edge + mean_revert drift
            _m(
                id="mock-election-turnout",
                platform=Platform.MOCK,
                title="US midterm turnout above 50%?",
                yes=turnout,
                liquidity=5_000.0,
                volume=20_000.0,
                days=90,
            ),
            _m(
                id="mock-illiquid-noise",
                platform=Platform.MOCK,
                title="Will it rain in a random desert tomorrow?",
                yes=0.05,
                liquidity=20.0,
                volume=50.0,
                days=1,
            ),
            # Backward-compatible ids used by older tests
            _m(
                id="mock-fed-cut-2026",
                platform=Platform.MOCK,
                title="Will the Fed cut rates at the next meeting?",
                yes=0.42,
                liquidity=40_000.0,
                volume=125_000.0,
            ),
            _m(
                id="mock-btc-100k",
                platform=Platform.MOCK,
                title="Will Bitcoin exceed $100,000 by year end?",
                yes=0.61,
                liquidity=80_000.0,
                volume=500_000.0,
                days=180,
            ),
        ]
        return samples[:limit]
