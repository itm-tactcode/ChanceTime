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


def test_slug_window_end() -> None:
    from datetime import UTC, datetime

    from chancetime.crypto_updown.gamma import _end_from_slug

    dt = _end_from_slug("btc-updown-5m-1784145600")
    assert dt is not None
    assert dt == datetime.fromtimestamp(1784145600, tz=UTC)
    assert _end_from_slug("not-a-slug") is None


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
