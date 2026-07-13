"""Persist live fills into SQLite for dashboard."""

from __future__ import annotations

from pathlib import Path

from chancetime.execution.live_kalshi import LiveOrderResult
from chancetime.persistence.live_book import persist_live_result
from chancetime.persistence.store import StateStore
from chancetime.strategies.base import Side


def test_persist_filled_live_result(tmp_path: Path) -> None:
    db = tmp_path / "book.db"
    store = StateStore(db, enabled=True)
    res = LiveOrderResult(
        ok=True,
        venue="kalshi",
        order_id="ord-live-1",
        client_order_id="c1",
        status="filled",
        price=0.397,
        size_usd=4.764,
        contracts=12.0,
        raw={"fill_count": "12.00"},
        note="kalshi accepted fill_count=12.0 remaining=0.0 avg_px=0.397",
    )
    fill = persist_live_result(
        store,
        res,
        market_id="KXMENWORLDCUP-26-FR",
        platform="kalshi",
        side=Side.YES,
    )
    assert fill is not None
    assert fill.paper is False
    summary = store.summary()
    assert summary["fills_total"] == 1
    assert summary["open_positions"] == 1
    positions = store.list_positions()
    assert positions[0]["market_id"] == "KXMENWORLDCUP-26-FR"
    store.close()


def test_skip_unfilled(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "u.db", enabled=True)
    res = LiveOrderResult(
        ok=True,
        venue="polymarket",
        order_id="x",
        client_order_id="c",
        status="canceled_unfilled",
        price=0.41,
        size_usd=5.0,
        contracts=12.0,
        raw={},
        note="state=EXPIRED cum=0",
    )
    fill = persist_live_result(store, res, market_id="slug", platform="polymarket", side=Side.YES)
    assert fill is not None  # fill row recorded
    assert store.summary()["open_positions"] == 0
    store.close()
