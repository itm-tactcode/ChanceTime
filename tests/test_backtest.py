"""Phase 1 backtester tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.backtesting import BacktestEngine, CostModel, load_bars_csv, run_param_grid
from chancetime.strategies.simple_edge import SimpleEdgeStrategy

FIXTURE = Path(__file__).resolve().parents[1] / "backtests" / "fixtures" / "sample_series.csv"


def test_load_fixture() -> None:
    bars = load_bars_csv(FIXTURE)
    assert len(bars) >= 10
    ids = {b.market_id for b in bars}
    assert {"fed-cut", "btc-100k", "turnout"} <= ids
    assert any(b.resolve.value != "open" for b in bars)


@pytest.mark.asyncio
async def test_simple_edge_backtest() -> None:
    bars = load_bars_csv(FIXTURE)
    engine = BacktestEngine(
        starting_cash=1_000.0,
        order_size_usd=10.0,
        costs=CostModel(fee_bps=100, slippage_bps=50),
    )
    strat = SimpleEdgeStrategy(edge_threshold=0.08, min_liquidity_usd=100.0)
    result = await engine.run(bars, strat)
    assert result.n_trades >= 1
    assert result.starting_cash == 1_000.0
    assert len(result.equity_curve) >= 1
    # Cash accounting: ending ≈ start + realized
    assert abs(result.ending_cash - (result.starting_cash + result.realized_pnl)) < 1e-6


@pytest.mark.asyncio
async def test_param_grid() -> None:
    bars = load_bars_csv(FIXTURE)
    results = await run_param_grid(
        bars,
        edge_thresholds=[0.05, 0.08, 0.12],
        starting_cash=1_000.0,
        order_size_usd=10.0,
    )
    assert len(results) == 3
    assert results[0].params["edge_threshold"] == 0.05
