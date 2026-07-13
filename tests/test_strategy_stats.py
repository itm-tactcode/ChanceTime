"""Phase 8 strategy performance counters."""

from __future__ import annotations

from pathlib import Path

from chancetime.persistence.store import StateStore


def test_strategy_stats_accumulate(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "s.db", enabled=True)
    store.record_signal_stats(
        generated=5,
        approved=2,
        filled=1,
        strategy_counts={"mean_revert": 3, "simple_edge": 2},
    )
    store.record_strategy_fill("mean_revert", size_usd=10.0)
    store.record_strategy_fill("mean_revert", size_usd=5.0)
    store.record_strategy_close("mean_revert", realized_pnl=1.25)

    rows = {r["strategy"]: r for r in store.list_strategy_stats()}
    assert "mean_revert" in rows
    assert int(rows["mean_revert"]["signals"]) == 3
    assert int(rows["mean_revert"]["fills"]) == 2
    assert float(rows["mean_revert"]["fill_notional_usd"]) == 15.0
    assert int(rows["mean_revert"]["closed_trades"]) == 1
    assert float(rows["mean_revert"]["realized_pnl"]) == 1.25
    assert int(rows["simple_edge"]["signals"]) == 2
    store.close()
