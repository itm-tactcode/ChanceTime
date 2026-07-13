"""mean_revert strategy."""

from __future__ import annotations

import pytest

from chancetime.data_layer.models import Market, Platform
from chancetime.strategies.base import Side
from chancetime.strategies.mean_revert import MeanRevertStrategy


def _m(mid: str, yes: float, liq: float = 1000.0) -> Market:
    return Market(
        id=mid,
        platform=Platform.MOCK,
        title=f"Market {mid}",
        yes_price=yes,
        no_price=1.0 - yes,
        liquidity_usd=liq,
        volume_usd=liq,
    )


@pytest.mark.asyncio
async def test_mean_revert_fades_spike() -> None:
    strat = MeanRevertStrategy(
        enabled=True,
        move_threshold=0.05,
        min_liquidity_usd=10.0,
        history_window=5,
        min_history=3,
    )
    # Build history around 0.50
    for p in (0.48, 0.50, 0.52):
        await strat.generate_signals([_m("a", p)])
    # Spike to 0.62 → should fade with NO
    sigs = await strat.generate_signals([_m("a", 0.62)])
    assert len(sigs) >= 1
    assert sigs[0].side == Side.NO
    assert sigs[0].market_id == "a"


@pytest.mark.asyncio
async def test_mean_revert_needs_history() -> None:
    strat = MeanRevertStrategy(enabled=True, move_threshold=0.05, min_history=3)
    sigs = await strat.generate_signals([_m("b", 0.90)])
    assert sigs == []
