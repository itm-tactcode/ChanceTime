"""Path D crypto exchange + C→D signals unit tests."""

from __future__ import annotations

import time

from chancetime.crypto_exchange.models import SpotQuote
from chancetime.crypto_exchange.paper import ExchangePaperBook
from chancetime.modules import list_modules
from chancetime.modules.signals import (
    ImpliedDirectionSignal,
    build_direction_from_book,
    load_latest_signals,
    publish_signals,
)


def test_modules_has_exchange_paper() -> None:
    mods = {m["id"]: m for m in list_modules()}
    assert mods["crypto_exchange"]["status"] == "paper_only"
    assert "crypto_exchange_paper.db" in mods["crypto_exchange"]["db_keys"]


def test_paper_buy_fail_closed() -> None:
    book = ExchangePaperBook(cash=1000.0, fee_bps=0.0)
    bad = SpotQuote(asset="BTC", product_id="BTC-USD", source="test", ts=time.time())
    assert book.try_buy(bad, size_usd=10) == "missing_price"


def test_paper_buy_and_mark() -> None:
    book = ExchangePaperBook(cash=1000.0, fee_bps=0.0)
    q = SpotQuote(
        asset="BTC",
        product_id="BTC-USD",
        bid=100.0,
        ask=101.0,
        last=100.5,
        source="test",
        ts=time.time(),
    )
    assert book.try_buy(q, size_usd=101.0) is None
    assert book.cash < 1000.0
    assert "BTC" in book.positions
    eq = book.mark_equity({"BTC": q})
    assert eq > 0


def test_buy_does_not_inflate_equity() -> None:
    """After buying $10, equity ≈ start − fees (not start + $10)."""
    book = ExchangePaperBook(cash=1000.0, fee_bps=30.0)  # 0.30%
    q = SpotQuote(
        asset="BTC",
        product_id="BTC-USD",
        bid=100.0,
        ask=100.0,
        last=100.0,
        source="test",
        ts=time.time(),
    )
    assert book.try_buy(q, size_usd=10.0) is None
    # cash 1000 - 10 - 0.03 fee
    assert abs(book.cash - 989.97) < 1e-6
    # MTM position ≈ 10 at same price
    eq = book.mark_equity({"BTC": q})
    assert abs(eq - 999.97) < 1e-6
    # Must NOT look like +$10 profit
    assert eq < 1000.0


def test_store_cash_survives_reload(tmp_path) -> None:
    from chancetime.crypto_exchange.store import ExchangePaperStore

    db = tmp_path / "ex.db"
    s = ExchangePaperStore(db, starting_cash=1000.0)
    s.record_fill(
        asset="BTC",
        side="buy",
        price=100.0,
        qty=0.1,
        size_usd=10.0,
        fee_usd=0.03,
        venue="test",
        cash_after=989.97,
    )
    s.upsert_position(asset="BTC", qty=0.1, avg_price=100.0, cost_usd=10.0)
    s.snapshot_equity(
        cash=989.97, equity=999.97, exposure_usd=10.0, open_positions=1
    )
    s.close()

    s2 = ExchangePaperStore(db, starting_cash=1000.0)
    assert abs(s2.get_cash() - 989.97) < 1e-6
    assert abs(s2.last_cash() - 989.97) < 1e-6
    pos = s2.load_positions()
    assert len(pos) == 1
    # corrupt cash while positions open → repair on open
    s2.set_cash(1000.0)
    s2.close()
    s3 = ExchangePaperStore(db, starting_cash=1000.0)
    assert abs(s3.get_cash() - 989.97) < 1e-6
    s3.close()


def test_direction_signal_from_book() -> None:
    sig = build_direction_from_book(
        asset="BTC",
        slug="btc-test",
        up_mid=0.72,
        down_mid=0.28,
        up_ask=0.73,
        down_ask=0.29,
        spot=65000.0,
        seconds_remaining=90.0,
        window_end_ts=time.time() + 90,
        complete_set_sum=1.02,
        reference_price=64000.0,  # spot above ref → agrees with up
        edge_threshold=0.08,
    )
    assert sig is not None
    assert sig.direction == "up"
    assert sig.confidence >= 0.55
    assert sig.reference_price == 64000.0
    assert "spot_agrees_ref" in sig.note
    assert sig.is_actionable(min_confidence=0.5)


def test_publish_and_load_signals(tmp_path, monkeypatch) -> None:
    import chancetime.modules.signals as sigmod

    monkeypatch.setattr(sigmod, "signals_dir", lambda: tmp_path)
    s = ImpliedDirectionSignal(
        asset="ETH",
        direction="up",
        p_up=0.7,
        confidence=0.8,
        spot=2000.0,
    )
    publish_signals([s])
    loaded = load_latest_signals(max_age_sec=60)
    assert len(loaded) == 1
    assert loaded[0].asset == "ETH"


def test_hub_includes_exchange() -> None:
    from chancetime.crypto_updown.hub import combined_portfolio

    snap = combined_portfolio()
    assert "crypto_exchange" in snap["books"]
    assert any(m["id"] == "crypto_exchange" for m in snap["modules"])
