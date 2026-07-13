"""Phase 20: edge-after-cost scorecard + walk-forward costs-on."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.monitoring.scorecard import build_edge_scorecard
from chancetime.persistence.store import StateStore


def test_scorecard_beat_fees(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = StateStore(db, enabled=True)
    store.record_strategy_fill("simple_edge", size_usd=100.0)
    store.record_strategy_close("simple_edge", realized_pnl=5.0)
    # fee 70 bps on 100 = 0.70 → after cost = 4.30 beat
    card = build_edge_scorecard(store, account="paper", fee_bps=70.0, min_closed_for_gate=1)
    assert card.strategies
    se = next(s for s in card.strategies if s.strategy == "simple_edge")
    assert se.beat_fees is True
    assert se.edge_after_cost > 0
    assert card.gate_ok is True
    store.close()


def test_scorecard_miss_fees(tmp_path: Path) -> None:
    db = tmp_path / "s2.db"
    store = StateStore(db, enabled=True)
    store.record_strategy_fill("mean_revert", size_usd=200.0)
    store.record_strategy_close("mean_revert", realized_pnl=0.5)
    # fees ~1.4 on 200 notional → after cost negative
    card = build_edge_scorecard(store, account="paper", fee_bps=70.0, min_closed_for_gate=1)
    mr = next(s for s in card.strategies if s.strategy == "mean_revert")
    assert mr.beat_fees is False
    assert card.gate_ok is False
    store.close()


@pytest.mark.asyncio
async def test_walk_forward_costs_on_default() -> None:
    from chancetime.backtesting.fees import CostModel
    from chancetime.backtesting.loader import load_bars_csv
    from chancetime.backtesting.walk_forward import walk_forward_simple_edge

    root = Path(__file__).resolve().parents[1]
    fixture = root / "backtests" / "fixtures" / "sample_series.csv"
    if not fixture.is_file():
        pytest.skip("no fixture")
    bars = load_bars_csv(str(fixture))
    report = await walk_forward_simple_edge(
        bars,
        n_folds=2,
        costs=CostModel(fee_bps=100.0, slippage_bps=50.0),
    )
    assert report.folds or report.mean_test_pnl == 0.0
