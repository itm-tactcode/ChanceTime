"""Phase 11: accounts, digest, export polish."""

from __future__ import annotations

import time
from pathlib import Path

from chancetime.monitoring.digest import build_digest, write_digest_file
from chancetime.persistence.export import (
    export_closed_csv,
    export_fills_csv,
    export_summary_csv,
)
from chancetime.persistence.store import StateStore
from chancetime.utils.accounts import (
    default_accounts,
    get_account,
    list_accounts_summary,
    load_config_for_account,
)


def test_default_accounts() -> None:
    accts = default_accounts()
    assert "paper" in accts and "live" in accts
    assert accts["paper"].paper_mode is True
    assert accts["live"].paper_mode is False


def test_get_account_and_config() -> None:
    a = get_account("paper")
    assert "paper" in a.db_path
    cfg, acct = load_config_for_account("paper", env_file=None)
    assert cfg.persistence.db_path == acct.db_path
    assert cfg.bot.paper_mode is True


def test_list_accounts() -> None:
    rows = list_accounts_summary()
    names = {r["name"] for r in rows}
    assert "paper" in names


def test_digest_and_export(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    store = StateStore(db, enabled=True)
    from chancetime.execution.engine import Fill, OrderStatus
    from chancetime.strategies.base import Side

    store.record_fill(
        Fill(
            order_id="o1",
            market_id="m1",
            side=Side.YES,
            price=0.5,
            size_usd=10.0,
            status=OrderStatus.FILLED,
            paper=True,
            ts=time.time(),
        ),
        strategy="simple_edge",
        platform="mock",
    )
    report = build_digest(store, account="test")
    assert report.fills_today >= 1
    assert "Chance Time digest" in report.text
    path = write_digest_file(report, directory=tmp_path / "digests")
    assert path.is_file()

    f = export_fills_csv(store, tmp_path / "fills.csv", book="test", year=None)
    c = export_closed_csv(store, tmp_path / "closed.csv", book="test")
    s = export_summary_csv(store, tmp_path / "sum.csv", book="test")
    assert f.is_file() and c.is_file() and s.is_file()
    text = f.read_text(encoding="utf-8")
    assert "ts_iso" in text and "tax_year" in text
    store.close()
