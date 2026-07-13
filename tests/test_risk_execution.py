"""Risk + paper execution tests."""

from __future__ import annotations

import pytest

from chancetime.execution.engine import ExecutionEngine, OrderStatus
from chancetime.risk.engine import RiskEngine
from chancetime.strategies.base import Side, Signal
from chancetime.utils.config import ExecutionSettings, RiskSettings


def _sig(market_id: str = "m1", size: float | None = None) -> Signal:
    return Signal(
        market_id=market_id,
        platform="mock",
        side=Side.YES,
        strength=0.5,
        edge=0.1,
        market_prob=0.4,
        size_usd=size,
    )


def test_risk_filters_oversize_and_duplicates() -> None:
    risk = RiskEngine(RiskSettings(max_position_usd=20.0, max_open_positions=2))
    approved = risk.filter_signals(
        [_sig("a", 50.0), _sig("b", 10.0), _sig("c", 10.0), _sig("d", 10.0)],
        default_size_usd=10.0,
    )
    assert all(s.market_id != "a" for s in approved)
    assert len(approved) <= 2


def test_risk_dedupes_same_market_keeps_stronger() -> None:
    risk = RiskEngine(RiskSettings(max_position_usd=50.0, max_open_positions=5))
    weak = _sig("same")
    weak.edge = 0.05
    weak.strength = 0.2
    strong = _sig("same")
    strong.edge = 0.2
    strong.strength = 1.0
    approved = risk.filter_signals([weak, strong], default_size_usd=10.0)
    assert len(approved) == 1
    assert approved[0].edge == 0.2


@pytest.mark.asyncio
async def test_paper_execution_simulates_fill() -> None:
    eng = ExecutionEngine(ExecutionSettings(paper_slippage_bps=50), paper_mode=True)
    fill = await eng.execute(_sig())
    assert fill.paper is True
    assert fill.status == OrderStatus.SIMULATED
    assert fill.price > 0


@pytest.mark.asyncio
async def test_live_mode_rejects_until_implemented() -> None:
    eng = ExecutionEngine(ExecutionSettings(), paper_mode=False)
    fill = await eng.execute(_sig())
    assert fill.status == OrderStatus.REJECTED
    assert fill.paper is False
