"""Path C crypto Up/Down — unit tests with mocks (no live network required for pure logic)."""

from __future__ import annotations

from chancetime.crypto_updown.clob import ClobPublicClient
from chancetime.crypto_updown.hub import combined_portfolio
from chancetime.crypto_updown.models import OutcomeBook, UpDownMarket
from chancetime.crypto_updown.paper import CryptoPaperBook
from chancetime.dashboard.app import create_app
from chancetime.modules import list_modules


def test_modules_registry_has_crypto() -> None:
    by_id = {m["id"]: m for m in list_modules()}
    assert "us_venues" in by_id
    assert "crypto_updown" in by_id
    assert "crypto_exchange" in by_id
    assert "alpaca" in by_id
    assert by_id["crypto_exchange"]["status"] == "paper_only"
    assert by_id["alpaca"]["status"] == "planned"


def test_combined_portfolio_shape() -> None:
    snap = combined_portfolio()
    assert "modules" in snap
    assert "books" in snap
    assert "us_venues" in snap["books"]
    assert "crypto_updown" in snap["books"]
    assert "combined_equity" in snap
    assert "note" in snap


def test_dashboard_modules_and_hub() -> None:
    app = create_app()
    from fastapi.testclient import TestClient

    try:
        client = TestClient(app)
    except Exception:
        import pytest

        pytest.importorskip("httpx")
        client = TestClient(app)

    r = client.get("/api/modules")
    assert r.status_code == 200
    body = r.json()
    assert any(m["id"] == "crypto_updown" for m in body["modules"])

    h = client.get("/api/hub")
    assert h.status_code == 200
    hub = h.json()
    assert "books" in hub
    assert "modules" in hub


def test_complete_set_sum() -> None:
    m = UpDownMarket(
        condition_id="c1",
        slug="btc-updown-test",
        question="Bitcoin Up or Down",
        asset="BTC",
        up=OutcomeBook(
            token_id="t1",
            outcome="Up",
            best_bid=0.4,
            best_ask=0.42,
            has_bbo=True,
            mid=0.41,
        ),
        down=OutcomeBook(
            token_id="t2",
            outcome="Down",
            best_bid=0.5,
            best_ask=0.52,
            has_bbo=True,
            mid=0.51,
        ),
    )
    assert abs((m.complete_set_ask_sum() or 0) - 0.94) < 1e-9


def test_paper_fail_closed_missing_bbo() -> None:
    book = CryptoPaperBook(cash=1000.0)
    m = UpDownMarket(
        condition_id="c1",
        slug="x",
        question="Bitcoin Up or Down",
        asset="BTC",
        up=OutcomeBook(token_id="t1", outcome="Up", mid=0.5, has_bbo=False),
        down=OutcomeBook(token_id="t2", outcome="Down", mid=0.5, has_bbo=False),
    )
    assert book.try_buy(m, side="up", size_usd=10) == "missing_bbo"


def test_paper_buy_with_bbo() -> None:
    book = CryptoPaperBook(cash=1000.0, fee_bps=0.0)
    m = UpDownMarket(
        condition_id="c1",
        slug="x",
        question="Bitcoin Up or Down",
        asset="BTC",
        up=OutcomeBook(
            token_id="t1",
            outcome="Up",
            best_ask=0.4,
            best_bid=0.38,
            has_bbo=True,
            mid=0.39,
        ),
        down=OutcomeBook(
            token_id="t2",
            outcome="Down",
            best_ask=0.55,
            best_bid=0.53,
            has_bbo=True,
            mid=0.54,
        ),
    )
    assert book.try_buy(m, side="up", size_usd=10) is None
    assert book.fills == 1
    assert ("x", "up") in book.positions
    # Cash must drop by size (fee 0)
    assert abs(book.cash - 990.0) < 1e-9
    # Equity ≈ cash + MTM contracts*mid = 990 + (10/0.4)*0.39 = 990 + 9.75
    eq = book.mark_equity([m])
    assert abs(eq - (990.0 + 25.0 * 0.39)) < 1e-6
    assert eq < 1000.0


def test_paper_settle_on_resolution() -> None:
    book = CryptoPaperBook(cash=1000.0, fee_bps=0.0)
    m = UpDownMarket(
        condition_id="c1",
        slug="btc-updown-5m-1",
        question="Bitcoin Up or Down",
        asset="BTC",
        up=OutcomeBook(
            token_id="t1",
            outcome="Up",
            best_ask=0.5,
            best_bid=0.49,
            has_bbo=True,
            mid=0.5,
        ),
        down=OutcomeBook(
            token_id="t2",
            outcome="Down",
            best_ask=0.5,
            best_bid=0.49,
            has_bbo=True,
            mid=0.5,
        ),
    )
    assert book.try_buy(m, side="up", size_usd=10) is None
    assert abs(book.cash - 990.0) < 1e-9
    # Up wins: 20 contracts * $1 = $20 payout
    settles = book.settle_market("btc-updown-5m-1", resolved_up=True)
    assert len(settles) == 1
    assert settles[0]["won"] is True
    assert abs(book.cash - 1010.0) < 1e-6  # 990 + 20
    assert not book.positions


def test_slug_window_bounds() -> None:
    from datetime import UTC, datetime

    from chancetime.crypto_updown.gamma import (
        _end_from_slug,
        _start_from_slug,
        asset_from_slug,
        resolved_up_from_event,
        window_bounds_from_slug,
    )

    start = 1784145600
    assert _start_from_slug(f"btc-updown-5m-{start}") == datetime.fromtimestamp(
        start, tz=UTC
    )
    # end = start + 5m
    assert _end_from_slug(f"btc-updown-5m-{start}") == datetime.fromtimestamp(
        start + 300, tz=UTC
    )
    assert window_bounds_from_slug(f"btc-updown-5m-{start}") == (
        float(start),
        float(start + 300),
    )
    assert window_bounds_from_slug(f"eth-updown-15m-{start}") == (
        float(start),
        float(start + 900),
    )
    assert window_bounds_from_slug("not-a-slug") is None
    assert asset_from_slug("btc-updown-5m-1") == "BTC"
    assert asset_from_slug("hype-updown-5m-1") == "HYPE"

    closed_up = {
        "closed": True,
        "markets": [
            {
                "closed": True,
                "outcomes": '["Up", "Down"]',
                "outcomePrices": '["1", "0"]',
            }
        ],
    }
    closed_down = {
        "closed": True,
        "markets": [
            {
                "closed": True,
                "outcomes": '["Up", "Down"]',
                "outcomePrices": '["0", "1"]',
            }
        ],
    }
    assert resolved_up_from_event(closed_up) is True
    assert resolved_up_from_event(closed_down) is False
    assert resolved_up_from_event({"closed": False, "markets": []}) is None


def test_window_refs_persist_and_reload(tmp_path) -> None:
    from chancetime.crypto_updown.store import CryptoPaperStore

    db = tmp_path / "crypto_paper.db"
    s = CryptoPaperStore(db, starting_cash=1000.0)
    s.upsert_window_ref(
        market_slug="btc-updown-5m-1784064600",
        asset="BTC",
        ref_price=64000.0,
        ref_quality="near_open",
        start_ts=1784064600.0,
        end_ts=1784064900.0,
    )
    rows = s.load_window_refs()
    assert len(rows) == 1
    assert rows[0]["ref_price"] == 64000.0
    s.delete_window_ref("btc-updown-5m-1784064600")
    assert s.load_window_refs() == []
    s.close()


def test_restart_reconcile_settles_expired_position(tmp_path) -> None:
    """Offline expiry: open position + Gamma closed outcome → settle on check."""
    import asyncio

    from chancetime.crypto_updown.bot import CryptoUpDownBot
    from chancetime.crypto_updown.paper import PaperPosition
    from chancetime.crypto_updown.store import CryptoPaperStore

    start = 1_700_000_000  # fixed past epoch
    slug = f"btc-updown-5m-{start}"
    db = tmp_path / "crypto_paper.db"
    store = CryptoPaperStore(db, starting_cash=1000.0)
    # Simulate spent cash + open Up bag (entry 0.50 → 10 contracts on $5)
    store.set_cash(994.975)
    store.upsert_position(
        market_slug=slug,
        side="up",
        size_usd=5.0,
        entry_price=0.5,
        contracts=10.0,
        fees_paid=0.025,
    )
    store.close()

    bot = CryptoUpDownBot(db_path=str(db), cash=1000.0, enrich_bbo=False)
    # Position loaded from store
    assert (slug, "up") in bot.book.positions
    assert bot.book.cash < 1000.0

    async def fake_event(_s: str):
        return {
            "closed": True,
            "markets": [
                {
                    "closed": True,
                    "outcomes": '["Up", "Down"]',
                    "outcomePrices": '["1", "0"]',
                }
            ],
        }

    bot.gamma.fetch_event_by_slug = fake_event  # type: ignore[method-assign]

    async def run() -> None:
        res = await bot._check_resolutions([], {})
        assert any(r["slug"] == slug for r in res)
        assert (slug, "up") not in bot.book.positions
        # 10 contracts * $1 payout
        assert bot.book.cash > 994.0
        assert bot.store.summary()["settlements_total"] >= 1

    try:
        asyncio.run(run())
    finally:
        asyncio.run(bot.close())


def test_clob_apply_book() -> None:
    ob = OutcomeBook(token_id="t", outcome="Up", mid=0.5)
    payload = {
        "bids": [{"price": "0.40", "size": "10"}],
        "asks": [{"price": "0.45", "size": "12"}],
    }
    out = ClobPublicClient.apply_book(ob, payload)
    assert out.has_bbo
    assert out.best_bid == 0.4
    assert out.best_ask == 0.45


def test_scan_implied_direction() -> None:
    from chancetime.crypto_updown.strategies import scan_implied_direction

    m = UpDownMarket(
        condition_id="c1",
        slug="btc-updown-test",
        question="Bitcoin Up or Down",
        asset="BTC",
        up=OutcomeBook(
            token_id="t1",
            outcome="Up",
            best_bid=0.7,
            best_ask=0.72,
            has_bbo=True,
            mid=0.71,
        ),
        down=OutcomeBook(
            token_id="t2",
            outcome="Down",
            best_bid=0.25,
            best_ask=0.28,
            has_bbo=True,
            mid=0.26,
        ),
    )
    sigs = scan_implied_direction([m], {"BTC": 65000.0})
    assert len(sigs) == 1
    assert sigs[0].direction == "up"
