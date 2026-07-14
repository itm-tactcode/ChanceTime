"""Same-market complement arb + synthetic isolation."""

from __future__ import annotations

import pytest

from chancetime.data_layer.mock import MockMarketClient
from chancetime.data_layer.models import Market, Platform
from chancetime.risk.engine import RiskEngine
from chancetime.strategies.arb_cross import ArbCrossStrategy
from chancetime.strategies.base import Side
from chancetime.strategies.complement_arb import ComplementArbStrategy, position_key
from chancetime.utils.config import RiskSettings


@pytest.mark.asyncio
async def test_complement_finds_mock_gap() -> None:
    markets = await MockMarketClient().list_markets(limit=50)
    strat = ComplementArbStrategy(
        enabled=True,
        min_edge=0.01,
        fee_buffer=0.02,
        require_bbo=True,
        min_depth_usd=1.0,
        reject_synthetic=False,  # pure mock feed; has_live=False still allows
    )
    sigs = await strat.generate_signals(markets)
    assert len(sigs) >= 2
    yes = [s for s in sigs if s.side == Side.YES]
    no = [s for s in sigs if s.side == Side.NO]
    assert yes and no
    assert yes[0].metadata.get("same_market_complement")
    assert yes[0].metadata.get("arb_group_id") == no[0].metadata.get("arb_group_id")
    assert yes[0].edge > 0
    # yes_ask 0.41 + no_ask 0.35 + fee 0.02 → edge ~0.22
    assert yes[0].edge >= 0.15


@pytest.mark.asyncio
async def test_complement_skips_synthetic_when_live_present() -> None:
    live = Market(
        id="KXREAL-1",
        platform=Platform.KALSHI,
        title="Real market no gap",
        yes_price=0.5,
        no_price=0.5,
        yes_bid=0.49,
        yes_ask=0.51,
        has_bbo=True,
        synthetic=False,
        liquidity_usd=10_000,
    )
    mock_gap = Market(
        id="mock-complement-gap",
        platform=Platform.MOCK,
        title="Fake gap",
        yes_price=0.5,
        no_price=0.5,
        yes_bid=0.65,
        yes_ask=0.41,
        has_bbo=True,
        synthetic=True,
        liquidity_usd=10_000,
    )
    strat = ComplementArbStrategy(enabled=True, min_edge=0.01, fee_buffer=0.02)
    sigs = await strat.generate_signals([live, mock_gap])
    assert sigs == []


@pytest.mark.asyncio
async def test_arb_cross_drops_synthetic_when_live_present() -> None:
    """Fed mock pair must not fire alongside real venues."""
    mock = await MockMarketClient().list_markets(limit=20)
    live_k = Market(
        id="KX-LIVE-A",
        platform=Platform.KALSHI,
        title="Unrelated live kalshi event alpha",
        yes_price=0.4,
        no_price=0.6,
        yes_bid=0.39,
        yes_ask=0.41,
        has_bbo=True,
        synthetic=False,
        liquidity_usd=5_000,
    )
    live_p = Market(
        id="pm-live-b",
        platform=Platform.POLYMARKET,
        title="Unrelated live polymarket event beta",
        yes_price=0.55,
        no_price=0.45,
        yes_bid=0.54,
        yes_ask=0.56,
        has_bbo=True,
        synthetic=False,
        liquidity_usd=5_000,
    )
    strat = ArbCrossStrategy(
        enabled=True,
        min_spread=0.04,
        fee_buffer=0.02,
        require_bbo=True,
        use_llm_match=False,
        min_match_score=0.72,
    )
    # Pure mock still works
    pure = await strat.generate_signals(mock)
    assert any(s.metadata.get("arb_group_id") for s in pure)

    mixed = await strat.generate_signals([*mock, live_k, live_p])
    # Fed synthetic pair dropped; unrelated lives should not pair as fed
    for s in mixed:
        assert "kalshi-fed-cut" not in s.market_id
        assert "pm-fed-cut" not in s.market_id


@pytest.mark.asyncio
async def test_complement_risk_approves_dual_same_market() -> None:
    markets = await MockMarketClient().list_markets(limit=50)
    strat = ComplementArbStrategy(min_edge=0.01, fee_buffer=0.02, min_depth_usd=1.0)
    sigs = await strat.generate_signals(markets)
    assert len(sigs) >= 2
    risk = RiskEngine(
        RiskSettings(
            max_open_positions=10,
            min_net_edge=0.0,
            min_yes_mid=0.0,
            max_yes_mid=1.0,
            max_spread=0.0,
            enforce_cash=True,
        ),
        cash_basis=1000.0,
    )
    risk.set_markets(markets)
    name_by = {id(s): "complement_arb" for s in sigs}
    approved = risk.filter_signals(sigs, default_size_usd=10.0, strategy_name_by_signal=name_by)
    assert len(approved) >= 2
    gids = {s.metadata.get("arb_group_id") for s in approved}
    assert len(gids) >= 1
    # Register both position keys without overwrite
    for s in approved[:2]:
        risk.register_fill(
            market_id=s.market_id,
            platform=s.platform,
            side=s.side,
            size_usd=float(s.size_usd or 10),
            entry_price=float(s.metadata.get("exec_price") or 0.5),
            strategy="complement_arb",
            position_key=str(s.metadata.get("position_key") or position_key(s.market_id, s.side)),
        )
    assert risk.portfolio.open_count >= 2


def test_unknown_data_source_raises() -> None:
    from chancetime.data_layer import build_data_client

    with pytest.raises(ValueError, match="Unknown data.source"):
        build_data_client("not-a-real-source")
