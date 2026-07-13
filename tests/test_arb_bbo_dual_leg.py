"""Pair BBO, depth sizing, dual-leg paper arb hard caps."""

from __future__ import annotations

import pytest

from chancetime.data_layer.kalshi import KalshiClient
from chancetime.data_layer.matching import MarketPair
from chancetime.data_layer.mock import MockMarketClient
from chancetime.data_layer.models import Market, Platform
from chancetime.execution.engine import ExecutionEngine, OrderStatus
from chancetime.risk.engine import RiskEngine
from chancetime.strategies.arb_cross import ArbCrossStrategy
from chancetime.strategies.base import Side, Signal
from chancetime.utils.config import ExecutionSettings, RiskSettings


def test_kalshi_orderbook_bbo_parse() -> None:
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.40", "10.00"], ["0.42", "13.00"]],
            "no_dollars": [["0.50", "5.00"], ["0.56", "17.00"]],
        }
    }
    bbo = KalshiClient._bbo_from_orderbook(payload)
    assert bbo["yes_bid"] == pytest.approx(0.42)
    assert bbo["yes_bid_size"] == pytest.approx(13.0)
    # YES ask = 1 - best NO bid
    assert bbo["yes_ask"] == pytest.approx(0.44)
    assert bbo["yes_ask_size"] == pytest.approx(17.0)


@pytest.mark.asyncio
async def test_arb_uses_executable_edge_and_group_id() -> None:
    markets = await MockMarketClient().list_markets(limit=20)
    strat = ArbCrossStrategy(
        enabled=True,
        min_spread=0.03,
        fee_buffer=0.02,
        min_match_score=0.70,
        min_liquidity_usd=0.0,
        emit_hedge_legs=True,
        require_bbo=True,
        use_executable_prices=True,
        size_by_depth=True,
        max_leg_usd=20.0,
        max_pair_usd=40.0,
        min_depth_usd=1.0,
    )
    sigs = await strat.generate_signals(markets)
    assert len(sigs) >= 2
    groups = {s.metadata.get("arb_group_id") for s in sigs}
    assert None not in groups
    assert len(groups) >= 1
    # Fed pair should fire; both legs same group + sized
    yes = [s for s in sigs if s.side == Side.YES and s.market_id == "kalshi-fed-cut"]
    assert yes
    assert yes[0].size_usd is not None
    assert yes[0].size_usd <= 20.0
    assert yes[0].metadata.get("exec_edge") is not None


@pytest.mark.asyncio
async def test_require_bbo_skips_without_quotes() -> None:
    m1 = Market(
        id="k1",
        platform=Platform.KALSHI,
        title="Will Foo happen?",
        yes_price=0.30,
        no_price=0.70,
        has_bbo=False,
    )
    m2 = Market(
        id="p1",
        platform=Platform.POLYMARKET,
        title="Will Foo happen?",
        yes_price=0.55,
        no_price=0.45,
        has_bbo=False,
    )
    strat = ArbCrossStrategy(
        enabled=True,
        min_spread=0.01,
        fee_buffer=0.0,
        min_match_score=0.5,
        require_bbo=True,
        emit_hedge_legs=True,
    )
    strat.last_pairs = [MarketPair(left=m1, right=m2, score=0.99)]
    sigs = await strat.generate_signals([m1, m2])
    assert sigs == []


@pytest.mark.asyncio
async def test_dual_leg_paper_atomic_and_caps() -> None:
    eng = ExecutionEngine(
        ExecutionSettings(
            max_arb_pairs_per_poll=1,
            max_arb_pair_usd=20.0,
            max_arb_notional_per_poll=100.0,
            max_leg_usd=15.0,
            require_both_arb_legs=True,
        ),
        paper_mode=True,
    )
    eng.begin_poll()
    g1 = "arb-group-1"
    legs = [
        Signal(
            market_id="a",
            platform="kalshi",
            side=Side.YES,
            strength=1.0,
            edge=0.05,
            market_prob=0.4,
            size_usd=30.0,
            reason="cheap",
            metadata={"arb_group_id": g1, "strategy": "arb_cross"},
        ),
        Signal(
            market_id="b",
            platform="polymarket",
            side=Side.NO,
            strength=0.9,
            edge=0.05,
            market_prob=0.55,
            size_usd=30.0,
            reason="rich",
            metadata={
                "arb_group_id": g1,
                "strategy": "arb_cross",
                "exec_price": 0.48,
            },
        ),
    ]
    fills = await eng.execute_signals(legs)
    assert len(fills) == 2
    assert all(f.status == OrderStatus.SIMULATED for f in fills)
    assert sum(f.size_usd for f in fills) <= 20.0 + 1e-6
    # Second group blocked by max_arb_pairs_per_poll=1
    g2 = "arb-group-2"
    more = [
        Signal(
            market_id="c",
            platform="kalshi",
            side=Side.YES,
            strength=1.0,
            edge=0.05,
            market_prob=0.4,
            size_usd=5.0,
            reason="c",
            metadata={"arb_group_id": g2},
        ),
        Signal(
            market_id="d",
            platform="polymarket",
            side=Side.NO,
            strength=1.0,
            edge=0.05,
            market_prob=0.5,
            size_usd=5.0,
            reason="d",
            metadata={"arb_group_id": g2},
        ),
    ]
    fills2 = await eng.execute_signals(more)
    assert all(f.status == OrderStatus.REJECTED for f in fills2)


@pytest.mark.asyncio
async def test_risk_approves_arb_group_together() -> None:
    risk = RiskEngine(RiskSettings(max_open_positions=10, max_position_usd=50.0))
    gid = "arb-xyz"
    sigs = [
        Signal(
            market_id="k",
            platform="kalshi",
            side=Side.YES,
            strength=0.8,
            edge=0.06,
            size_usd=10.0,
            reason="a",
            metadata={"arb_group_id": gid, "arb_leg": "cheap_yes", "strategy": "arb_cross"},
        ),
        Signal(
            market_id="p",
            platform="polymarket",
            side=Side.NO,
            strength=0.8,
            edge=0.06,
            size_usd=10.0,
            reason="b",
            metadata={"arb_group_id": gid, "arb_leg": "rich_no", "strategy": "arb_cross"},
        ),
    ]
    approved = risk.filter_signals(
        sigs,
        default_size_usd=10.0,
        strategy_name_by_signal={id(s): "arb_cross" for s in sigs},
    )
    assert len(approved) == 2
    assert {s.market_id for s in approved} == {"k", "p"}


@pytest.mark.asyncio
async def test_incomplete_arb_group_rejected() -> None:
    eng = ExecutionEngine(
        ExecutionSettings(require_both_arb_legs=True),
        paper_mode=True,
    )
    eng.begin_poll()
    one = Signal(
        market_id="only",
        platform="kalshi",
        side=Side.YES,
        strength=1.0,
        edge=0.1,
        size_usd=5.0,
        reason="solo",
        metadata={"arb_group_id": "lonely"},
    )
    fills = await eng.execute_signals([one])
    assert len(fills) == 1
    assert fills[0].status == OrderStatus.REJECTED
