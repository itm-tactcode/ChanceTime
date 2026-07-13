"""Phase 4: matching + arb_cross strategy."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.data_layer.matching import normalize_title, pair_markets, title_similarity
from chancetime.data_layer.mock import MockMarketClient
from chancetime.data_layer.models import Platform
from chancetime.strategies.arb_cross import ArbCrossStrategy
from chancetime.strategies.base import Side


def test_normalize_title() -> None:
    a = normalize_title("Will the Fed cut rates at the next meeting?")
    b = normalize_title("Will Fed cut rates at next meeting")
    assert a == b
    assert "will" not in a.split()


def test_title_similarity_high_for_near_duplicates() -> None:
    s = title_similarity(
        "Will Bitcoin exceed $100,000 by year end?",
        "Will Bitcoin exceed 100000 USD by year end?",
    )
    assert s >= 0.72


def test_structural_nba_champion_pair() -> None:
    """Dual-list shape: Kalshi sentence vs PM subject-question title."""
    k = "Will Cleveland win the 2027 Pro Basketball Finals?"
    p = "Cleveland Cavaliers - 2027 NBA Champion"
    s = title_similarity(k, p, id_a="KXNBA-27-CLE")
    assert s >= 0.72


def test_structural_btc_ladder_pair() -> None:
    k = "Will Bitcoin be above $149,999.99 by Dec 31, 2026 at 12:00 PM ET?"
    p = "Above $149,999.99 - How high will Bitcoin get this year"
    s = title_similarity(k, p)
    assert s >= 0.72


def test_structural_rejects_different_teams() -> None:
    k = "Will Cleveland win the 2027 Pro Basketball Finals?"
    p = "Miami Heat - 2027 NBA Champion"
    s = title_similarity(k, p, id_a="KXNBA-27-CLE")
    assert s < 0.55


def test_structural_rejects_fed_level_vs_decision() -> None:
    """Different contract types must not pair as arb legs."""
    k = "Will the upper bound of the federal funds rate be above 3.75%?"
    p = "25 bps Decrease - Fed Decision in July"
    s = title_similarity(k, p)
    assert s < 0.55


def test_borderline_candidates_band() -> None:
    from chancetime.data_layer.matching import find_borderline_candidates
    from chancetime.data_layer.models import Market, Platform

    # Mid similarity: shares tokens but below auto-accept 0.72
    left = Market(
        id="k1",
        platform=Platform.KALSHI,
        title="Mets National League 2026",
        yes_price=0.2,
        no_price=0.8,
    )
    right = Market(
        id="p1",
        platform=Platform.POLYMARKET,
        title="New York Mets - National League Champion 2026",
        yes_price=0.25,
        no_price=0.75,
    )
    s = title_similarity(left.title, right.title)
    assert 0.40 <= s < 0.72
    cands = find_borderline_candidates(
        [left],
        [right],
        score_low=0.40,
        score_high=0.72,
        max_candidates=5,
    )
    assert len(cands) == 1
    assert cands[0].left.id == "k1"


@pytest.mark.asyncio
async def test_hybrid_llm_adjudicates_mid_band(tmp_path: Path) -> None:
    from chancetime.data_layer.models import Market, Platform
    from chancetime.llm.client import GrokClient
    from chancetime.llm.match_venues import hybrid_pair_markets
    from chancetime.utils.config import LLMSettings

    left = Market(
        id="k-mets",
        platform=Platform.KALSHI,
        title="Mets National League 2026",
        yes_price=0.2,
        no_price=0.8,
    )
    right = Market(
        id="p-mets",
        platform=Platform.POLYMARKET,
        title="New York Mets - National League Champion 2026",
        yes_price=0.25,
        no_price=0.75,
    )
    # Auto-accept high (0.72); mid-band [0.40, 0.72) → mock LLM accepts index 0
    # Isolate durable spend ledger so a local exhausted budget cannot skip adjudication
    llm = GrokClient(
        LLMSettings(enabled=True, daily_budget_usd=5.0),
        api_key=None,
        spend_path=tmp_path / "spend.json",
    )
    pairs = await hybrid_pair_markets(
        llm,
        [left],
        [right],
        min_score=0.72,
        use_llm=True,
        llm_band_low=0.40,
        llm_min_confidence=0.75,
        llm_max_candidates=8,
    )
    assert len(pairs) >= 1
    assert pairs[0].left.id == "k-mets"
    assert pairs[0].right.id == "p-mets"


@pytest.mark.asyncio
async def test_pair_fed_cut_across_venues() -> None:
    markets = await MockMarketClient().list_markets(limit=20)
    kalshi = [m for m in markets if m.platform == Platform.KALSHI]
    pm = [m for m in markets if m.platform == Platform.POLYMARKET]
    pairs = pair_markets(kalshi, pm, min_score=0.72)
    assert len(pairs) >= 1
    titles = {(p.left.id, p.right.id) for p in pairs}
    assert any("fed" in a and "fed" in b for a, b in titles)


@pytest.mark.asyncio
async def test_arb_cross_emits_legs_on_spread() -> None:
    markets = await MockMarketClient().list_markets(limit=20)
    strat = ArbCrossStrategy(
        enabled=True,
        min_spread=0.04,
        fee_buffer=0.02,
        min_match_score=0.70,
        min_liquidity_usd=100.0,
        emit_hedge_legs=True,
    )
    sigs = await strat.generate_signals(markets)
    assert len(sigs) >= 2  # cheap YES + rich NO for Fed pair (executable edge clears thr)
    sides = {s.side for s in sigs}
    assert Side.YES in sides
    assert Side.NO in sides
    # Cheap leg should be Kalshi fed
    yes_sigs = [s for s in sigs if s.side == Side.YES]
    assert any(s.market_id == "kalshi-fed-cut" for s in yes_sigs)


@pytest.mark.asyncio
async def test_arb_no_signal_when_spread_small() -> None:
    markets = await MockMarketClient().list_markets(limit=20)
    strat = ArbCrossStrategy(
        enabled=True,
        min_spread=0.50,  # impossible
        fee_buffer=0.0,
        min_match_score=0.70,
        min_liquidity_usd=100.0,
    )
    sigs = await strat.generate_signals(markets)
    assert sigs == []


def test_polymarket_display_title_uses_subject() -> None:
    from chancetime.data_layer.polymarket_us import PolymarketUSClient

    raw = {
        "id": "1",
        "question": "National League Champion",
        "title": "New York Mets",
        "description": "Will New York Mets win the 2026 National League pennant?",
        "slug": "tec-mlb-nlchamp-2026-09-27-nym",
    }
    m = PolymarketUSClient._normalize(raw)
    assert "Mets" in m.title
    assert "National League" in m.title


def test_kalshi_parlay_filter() -> None:
    from chancetime.data_layer.kalshi import KalshiClient

    assert KalshiClient._looks_like_parlay(
        "yes Spain advances,yes Lamine Yamal: 1+,yes Kylian Mbappe: 1+",
        "KXMVE123",
    )
    # Do not treat arbitrary tickers containing letters M-V-E as parlays
    assert not KalshiClient._looks_like_parlay(
        "Will the Fed cut rates at the next meeting?",
        "KXMOVE-FED-CUT",
    )
    assert not KalshiClient._looks_like_parlay(
        "Will the Fed cut rates at the next meeting?",
        "FED-RATE-CUT",
    )
