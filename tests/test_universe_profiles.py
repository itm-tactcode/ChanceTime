"""Per-strategy universe profiles."""

from __future__ import annotations

import pytest

from chancetime.data_layer.mock import MockMarketClient
from chancetime.data_layer.profiles import (
    PROFILE_BROAD,
    PROFILE_SHORT_BBO,
    apply_close_filter,
    build_universe_profile,
    merge_market_lists,
)
from chancetime.data_layer.models import Market, Platform
from chancetime.strategies import build_strategies
from chancetime.utils.config import load_config


@pytest.mark.asyncio
async def test_build_broad_and_short_profiles() -> None:
    client = MockMarketClient()
    broad = await build_universe_profile(
        client,
        name=PROFILE_BROAD,
        max_markets=20,
        prefer_closing_within_hours=0.0,
        allow_synthetic=True,
    )
    short = await build_universe_profile(
        client,
        name=PROFILE_SHORT_BBO,
        max_markets=20,
        prefer_closing_within_hours=48.0,
        drop_beyond_prefer=False,
        queries=[],
        allow_synthetic=True,
    )
    assert len(broad) >= 1
    assert len(short) >= 1


def test_drop_beyond_close_filter() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    near = Market(
        id="near",
        platform=Platform.KALSHI,
        title="near",
        yes_price=0.5,
        no_price=0.5,
        close_time=now + timedelta(hours=10),
    )
    far = Market(
        id="far",
        platform=Platform.KALSHI,
        title="far 2027",
        yes_price=0.5,
        no_price=0.5,
        close_time=now + timedelta(days=400),
    )
    unk = Market(
        id="unk",
        platform=Platform.KALSHI,
        title="unk",
        yes_price=0.5,
        no_price=0.5,
        close_time=None,
    )
    kept = apply_close_filter(
        [near, far, unk],
        prefer_within_hours=48.0,
        drop_beyond=True,
        keep_unknown=False,
        limit=10,
    )
    ids = {m.id for m in kept}
    assert "near" in ids
    assert "far" not in ids
    assert "unk" not in ids


def test_merge_dedupes() -> None:
    a = Market(id="x", platform=Platform.KALSHI, title="t", yes_price=0.4, no_price=0.6)
    b = Market(id="x", platform=Platform.KALSHI, title="t2", yes_price=0.5, no_price=0.5)
    c = Market(id="y", platform=Platform.POLYMARKET, title="u", yes_price=0.5, no_price=0.5)
    m = merge_market_lists([a], [b, c])
    assert len(m) == 2
    assert m[0].title == "t"


def test_strategy_default_universes() -> None:
    cfg = load_config("config/default.yaml")
    # Force enable a few for construction
    cfg.strategies.simple_edge.enabled = True
    cfg.strategies.arb_cross.enabled = True
    cfg.strategies.complement_arb.enabled = True
    cfg.strategies.llm_calibrated.enabled = True
    strats = build_strategies(cfg, llm=None)
    by = {s.name: s.universe_name for s in strats}
    assert by.get("simple_edge") == "broad"
    assert by.get("arb_cross") == "dual_list"
    assert by.get("complement_arb") == "short_bbo"
    assert by.get("llm_calibrated") == "llm_screen"
