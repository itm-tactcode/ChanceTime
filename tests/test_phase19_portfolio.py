"""Phase 19: deploy %, clusters, time-to-event, hot-reload knobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chancetime.data_layer.models import Market, Platform
from chancetime.data_layer.timeparse import hours_until, parse_close_time
from chancetime.risk.engine import RiskEngine
from chancetime.risk.families import EventFamily, classify_family, cluster_key
from chancetime.strategies.base import Side, Signal
from chancetime.utils.config import RiskSettings


def test_cluster_key_series() -> None:
    assert cluster_key("Will CLE win?", market_id="KXNBA-27-CLE").startswith("sports:kxnba:27")
    assert "fed" in cluster_key("federal funds", market_id="KXFED-26JUL-T3.75")
    a = cluster_key("Will CLE win NBA?", market_id="KXNBA-27-CLE")
    b = cluster_key("Will GSW win NBA?", market_id="KXNBA-27-GSW")
    assert a == b  # same series/period → correlated cluster


def test_classify_family_from_ticker() -> None:
    assert classify_family("x", market_id="KXBTCMAXY-26DEC31-99999.99") == EventFamily.CRYPTO
    assert classify_family("x", market_id="KXFED-26JUL-T3.75") == EventFamily.MACRO


def test_parse_close_time_iso() -> None:
    dt = parse_close_time("2026-12-31T23:59:59Z")
    assert dt is not None
    assert dt.year == 2026
    assert hours_until(dt, now=datetime(2026, 12, 1, tzinfo=timezone.utc)) is not None


def test_deploy_cap_blocks() -> None:
    risk = RiskEngine(
        RiskSettings(
            max_deploy_pct=0.10,  # $100 of $1000
            max_position_usd=50.0,
            max_open_positions=20,
            max_family_exposure_usd=1000.0,
            max_cluster_exposure_usd=0.0,
            enforce_cash=True,
            min_net_edge=0.0,
            assumed_half_spread=0.0,
            max_spread=0.0,
        ),
        cash_basis=1000.0,
        strategy_weights={"simple_edge": 1.0},
        title_by_market={f"m{i}": f"Other market {i}" for i in range(5)},
    )
    risk.portfolio.open_position(
        market_id="m0",
        platform="mock",
        side=Side.YES,
        size_usd=90.0,
        entry_price=0.5,
        strategy="simple_edge",
    )
    sig = Signal(
        market_id="m1",
        platform="mock",
        side=Side.YES,
        strength=1.0,
        edge=0.2,
        size_usd=20.0,
        market_prob=0.4,
        reason="t",
        metadata={"strategy": "simple_edge"},
    )
    approved = risk.filter_signals(
        [sig],
        default_size_usd=20.0,
        strategy_name_by_signal={id(sig): "simple_edge"},
    )
    assert approved == []


def test_cluster_exposure_blocks_correlated() -> None:
    risk = RiskEngine(
        RiskSettings(
            max_cluster_exposure_usd=10.0,
            max_family_exposure_usd=1000.0,
            max_position_usd=50.0,
            max_deploy_pct=0.0,
            min_net_edge=0.0,
            assumed_half_spread=0.0,
            max_spread=0.0,
        ),
        cash_basis=1000.0,
        strategy_weights={"simple_edge": 1.0},
        title_by_market={
            "KXNBA-27-CLE": "Will Cleveland win the 2027 Pro Basketball Finals?",
            "KXNBA-27-GSW": "Will Golden State win the 2027 Pro Basketball Finals?",
        },
    )
    risk.portfolio.open_position(
        market_id="KXNBA-27-CLE",
        platform="kalshi",
        side=Side.YES,
        size_usd=8.0,
        entry_price=0.1,
        strategy="simple_edge",
    )
    sig = Signal(
        market_id="KXNBA-27-GSW",
        platform="kalshi",
        side=Side.YES,
        strength=1.0,
        edge=0.2,
        size_usd=5.0,
        market_prob=0.1,
        reason="t",
        metadata={"strategy": "simple_edge"},
    )
    approved = risk.filter_signals(
        [sig],
        default_size_usd=5.0,
        strategy_name_by_signal={id(sig): "simple_edge"},
    )
    assert approved == []


def test_too_far_filter() -> None:
    far = datetime.now(timezone.utc) + timedelta(days=500)
    m = Market(
        id="far1",
        platform=Platform.KALSHI,
        title="Far event",
        yes_price=0.4,
        no_price=0.6,
        close_time=far,
    )
    risk = RiskEngine(
        RiskSettings(
            max_days_to_close=30.0,
            max_family_exposure_usd=1000.0,
            max_cluster_exposure_usd=0.0,
            max_deploy_pct=0.0,
            min_net_edge=0.0,
            assumed_half_spread=0.0,
            max_spread=0.0,
            max_position_usd=50.0,
        ),
        cash_basis=1000.0,
        strategy_weights={"simple_edge": 1.0},
        title_by_market={"far1": "Far event"},
    )
    risk.set_markets([m])
    sig = Signal(
        market_id="far1",
        platform="kalshi",
        side=Side.YES,
        strength=1.0,
        edge=0.2,
        size_usd=10.0,
        market_prob=0.4,
        reason="t",
        metadata={"strategy": "simple_edge"},
    )
    approved = risk.filter_signals(
        [sig],
        default_size_usd=10.0,
        strategy_name_by_signal={id(sig): "simple_edge"},
    )
    assert approved == []


def test_hot_reload_apply_settings() -> None:
    risk = RiskEngine(RiskSettings(max_open_positions=5), cash_basis=1000.0)
    risk.apply_risk_settings(RiskSettings(max_open_positions=12, max_deploy_pct=0.5))
    assert risk.settings.max_open_positions == 12
    assert risk.max_deploy_usd() == 500.0
