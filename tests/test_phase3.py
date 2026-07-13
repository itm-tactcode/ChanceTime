"""Phase 3: weights, TP/SL, partial fills, trailing prior."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.backtesting import BacktestEngine, CostModel, load_bars_csv
from chancetime.risk.engine import RiskEngine
from chancetime.strategies.base import Side, Signal
from chancetime.strategies.simple_edge import SimpleEdgeStrategy
from chancetime.utils.config import RiskSettings

FIXTURE = Path(__file__).resolve().parents[1] / "backtests" / "fixtures" / "sample_series.csv"


def _sig(mid: str, edge: float = 0.1, strength: float = 1.0, size: float | None = None) -> Signal:
    return Signal(
        market_id=mid,
        platform="mock",
        side=Side.YES,
        strength=strength,
        edge=edge,
        market_prob=0.4,
        size_usd=size,
    )


def test_strategy_weight_scales_size() -> None:
    risk = RiskEngine(
        RiskSettings(max_position_usd=50.0, max_open_positions=5),
        strategy_weights={"simple_edge": 0.5},
    )
    s = _sig("m1", strength=1.0)
    approved = risk.filter_signals(
        [s],
        default_size_usd=10.0,
        strategy_name_by_signal={id(s): "simple_edge"},
    )
    assert len(approved) == 1
    assert approved[0].size_usd == pytest.approx(5.0)


def test_take_profit_closes() -> None:
    risk = RiskEngine(
        RiskSettings(take_profit_pct=0.10, stop_loss_pct=None),
    )
    risk.register_fill(
        market_id="m1",
        platform="mock",
        side=Side.YES,
        size_usd=10.0,
        entry_price=0.40,
        strategy="simple_edge",
    )
    closed = risk.manage_open_positions({"m1": 0.50})  # +25%
    assert len(closed) == 1
    assert risk.portfolio.open_count == 0


def test_cost_model_partial_liquidity() -> None:
    c = CostModel(liquidity_participation=0.1, min_fill_ratio=0.5)
    assert c.clip_size_to_liquidity(10.0, 50.0) == pytest.approx(5.0)
    assert c.clip_size_to_liquidity(10.0, 10.0) is None  # cap=1 < 5 min


@pytest.mark.asyncio
async def test_trailing_mean_prior_runs() -> None:
    bars = load_bars_csv(FIXTURE)
    engine = BacktestEngine(
        starting_cash=1_000.0,
        order_size_usd=10.0,
        costs=CostModel(fee_bps=100, slippage_bps=50, liquidity_participation=1.0),
    )
    strat = SimpleEdgeStrategy(
        edge_threshold=0.02,
        prior_mode="trailing_mean",
        history_window=3,
        min_history=2,
        min_liquidity_usd=100.0,
    )
    result = await engine.run(bars, strat, params={"prior_mode": "trailing_mean"})
    assert result.starting_cash == 1_000.0
    assert len(result.equity_curve) >= 1
