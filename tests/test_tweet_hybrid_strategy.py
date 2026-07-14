"""Path C tweet hybrid strategy — unit tests (no network)."""

from __future__ import annotations

from chancetime.crypto_updown.models import OutcomeBook, UpDownMarket
from chancetime.crypto_updown.paper import CryptoPaperBook
from chancetime.crypto_updown.strategies import (
    TweetHybridStrategy,
    TweetStrategyConfig,
    model_p_up,
)


def _mkt(
    *,
    up_mid: float = 0.45,
    down_mid: float = 0.55,
    up_ask: float = 0.46,
    down_ask: float = 0.56,
    up_bid: float = 0.44,
    down_bid: float = 0.54,
) -> UpDownMarket:
    return UpDownMarket(
        condition_id="c1",
        slug="btc-test",
        question="Bitcoin Up or Down",
        asset="BTC",
        up=OutcomeBook(
            token_id="u",
            outcome="Up",
            mid=up_mid,
            best_ask=up_ask,
            best_bid=up_bid,
            has_bbo=True,
        ),
        down=OutcomeBook(
            token_id="d",
            outcome="Down",
            mid=down_mid,
            best_ask=down_ask,
            best_bid=down_bid,
            has_bbo=True,
        ),
    )


def test_model_p_up_moves_with_spot() -> None:
    p_up = model_p_up(
        spot=101.0,
        ref=100.0,
        vol=0.001,
        seconds_remaining=30.0,
        window_seconds=300.0,
    )
    p_dn = model_p_up(
        spot=99.0,
        ref=100.0,
        vol=0.001,
        seconds_remaining=30.0,
        window_seconds=300.0,
    )
    assert p_up > 0.5
    assert p_dn < 0.5


def test_mispricing_buys_undervalued_up() -> None:
    """Model thinks Up more than market mid → buy up."""
    strat = TweetHybridStrategy(
        TweetStrategyConfig(min_edge=0.05, size_usd=5.0, complete_set_max_sum=0.90)
    )
    # Feed vol samples
    for px in [100.0, 100.2, 100.5, 101.0, 101.5]:
        strat.note_spot("BTC", px)
    book = CryptoPaperBook(cash=1000.0, fee_bps=0.0)
    m = _mkt(up_mid=0.40, down_mid=0.60, up_ask=0.41, down_ask=0.61, up_bid=0.39, down_bid=0.59)
    # strong up spot vs ref
    ev = strat.evaluate_market(m, spot=102.0, ref=100.0)
    assert ev["model_p_up"] is not None
    assert ev["model_p_up"] > 0.5
    acts = strat.decide_actions(book, m, ev)
    buys = [a for a in acts if a.get("action") == "paper_buy" and a.get("phase") == "mispricing"]
    assert any(a.get("side") == "up" for a in buys)
    # Cash must decrease after real fills
    assert book.cash < 1000.0


def test_fail_closed_no_spot() -> None:
    strat = TweetHybridStrategy()
    book = CryptoPaperBook(cash=1000.0)
    m = _mkt()
    ev = strat.evaluate_market(m, spot=None, ref=100.0)
    acts = strat.decide_actions(book, m, ev)
    assert acts[0]["reason"] == "no_spot"
    assert book.fills == 0


def test_run_poll_shadow_no_fills() -> None:
    strat = TweetHybridStrategy()
    book = CryptoPaperBook(cash=1000.0)
    m = _mkt()
    res = strat.run_poll(
        book,
        [m],
        {"BTC": 101.0},
        {"btc-test": 100.0},
        execute=False,
    )
    assert res.evaluations
    assert book.fills == 0
    assert any(a.get("action") == "shadow" for a in res.actions)
