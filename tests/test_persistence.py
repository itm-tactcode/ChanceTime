"""SQLite StateStore + portfolio restore."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.execution.engine import Fill, OrderStatus
from chancetime.persistence.store import StateStore
from chancetime.risk.portfolio import ClosedTrade, Portfolio
from chancetime.strategies.base import Side


def test_portfolio_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    store = StateStore(db, enabled=True)
    p = Portfolio()
    p.open_position(
        market_id="m1",
        platform="mock",
        side=Side.YES,
        size_usd=10.0,
        entry_price=0.4,
        strategy="simple_edge",
    )
    p.realized_pnl_today = 1.25
    store.save_portfolio(p)
    store.close()

    store2 = StateStore(db, enabled=True)
    loaded = store2.load_portfolio()
    assert loaded.open_count == 1
    pos = loaded.get("m1")
    assert pos is not None
    assert pos.side == Side.YES
    assert pos.size_usd == pytest.approx(10.0)
    assert loaded.realized_pnl_today == pytest.approx(1.25)
    store2.close()


def test_fills_and_equity(tmp_path: Path) -> None:
    db = tmp_path / "t2.db"
    store = StateStore(db, enabled=True)
    fill = Fill(
        order_id="oid-1",
        market_id="m1",
        side=Side.YES,
        price=0.41,
        size_usd=10.0,
        status=OrderStatus.SIMULATED,
        paper=True,
        note="got item",
        arb_group_id=None,
    )
    store.record_fill(fill, strategy="simple_edge", platform="mock")
    store.record_equity(
        {
            "cash_basis": 1000.0,
            "realized_pnl_today": 0.0,
            "unrealized_pnl": 0.5,
            "equity": 1000.5,
            "open_positions": 1.0,
            "exposure_usd": 10.0,
        },
        poll_count=1,
        paper=True,
    )
    trade = ClosedTrade(
        market_id="m2",
        side=Side.NO,
        size_usd=5.0,
        entry_price=0.5,
        exit_price=0.55,
        contracts=10.0,
        realized_pnl=0.5,
        reason="take_profit",
        strategy="simple_edge",
    )
    store.append_closed_trade(trade)

    summary = store.summary()
    assert summary["fills_total"] == 1
    assert summary["closed_trades"] == 1
    assert summary["last_equity"] is not None
    assert len(store.list_fills()) == 1
    assert len(store.equity_series()) == 1
    store.close()


def test_disabled_store_noop(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "nope.db", enabled=False)
    p = Portfolio()
    store.save_portfolio(p)
    assert store.summary() == {"enabled": False}
    store.close()


@pytest.mark.asyncio
async def test_bot_persists_after_poll(tmp_path: Path) -> None:
    from chancetime.main import Bot
    from chancetime.utils.config import load_config

    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config" / "default.yaml", env_file=None)
    cfg.bot.paper_mode = True
    cfg.data.source = "mock"
    cfg.llm.enabled = False
    cfg.persistence.enabled = True
    cfg.persistence.db_path = str(tmp_path / "bot.db")

    bot = Bot(cfg)
    await bot.run(max_polls=1)
    assert bot.poll_count == 1

    store = StateStore(cfg.persistence.db_path, enabled=True)
    summary = store.summary()
    assert summary["enabled"] is True
    # Equity snapshot written each poll
    assert summary["last_equity"] is not None
    store.close()
