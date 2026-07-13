"""Phase 8: families + cold strategies."""

from __future__ import annotations

from pathlib import Path

from chancetime.persistence.store import StateStore
from chancetime.risk.cold import cold_strategies_from_store
from chancetime.risk.engine import RiskEngine
from chancetime.risk.families import EventFamily, classify_family
from chancetime.strategies.base import Side, Signal
from chancetime.utils.config import RiskSettings


def test_classify_family() -> None:
    assert classify_family("Will France win the World Cup?") == EventFamily.SPORTS
    assert classify_family("Will the Fed cut rates?") == EventFamily.MACRO
    assert classify_family("Bitcoin above 100k?") == EventFamily.CRYPTO
    assert classify_family("Who wins the election?") == EventFamily.POLITICS


def test_cold_strategy_detection(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "c.db", enabled=True)
    store.record_strategy_fill("mean_revert", size_usd=10.0)
    for _ in range(5):
        store.record_strategy_fill("mean_revert", size_usd=5.0)
    store.record_strategy_close("mean_revert", realized_pnl=-12.0)
    settings = RiskSettings(cold_min_fills=5, cold_max_realized_pnl=-10.0)
    cold = cold_strategies_from_store(store, settings)
    assert "mean_revert" in cold
    store.close()


def test_family_exposure_blocks() -> None:
    risk = RiskEngine(
        RiskSettings(max_family_exposure_usd=10.0, max_position_usd=50.0),
        strategy_weights={"simple_edge": 1.0},
        title_by_market={"m1": "World Cup final", "m2": "NBA finals game"},
    )
    # Open 8 already in sports family
    risk.portfolio.open_position(
        market_id="m1",
        platform="mock",
        side=Side.YES,
        size_usd=8.0,
        entry_price=0.5,
        strategy="simple_edge",
    )
    sig = Signal(
        market_id="m2",
        platform="mock",
        side=Side.YES,
        strength=1.0,
        edge=0.2,
        size_usd=5.0,
        market_prob=0.4,
        reason="t",
        metadata={"strategy": "simple_edge"},
    )
    approved = risk.filter_signals(
        [sig],
        default_size_usd=5.0,
        strategy_name_by_signal={id(sig): "simple_edge"},
    )
    assert approved == []
