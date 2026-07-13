"""Mini-game slogan helpers."""

from __future__ import annotations

import pytest

from chancetime.execution.engine import ExecutionEngine, OrderStatus
from chancetime.flair import GOT_ITEM, MISS, fill_slogan, miss_slogan
from chancetime.strategies.base import Side, Signal
from chancetime.utils.config import ExecutionSettings


def test_slogans() -> None:
    assert GOT_ITEM == "got item"
    assert MISS == "miss"
    assert fill_slogan(paper=True) == "got item (paper)"
    assert fill_slogan(paper=False) == "got item"
    assert miss_slogan() == "miss"
    assert "live" in miss_slogan(reason="live not implemented")


@pytest.mark.asyncio
async def test_paper_fill_is_got_item() -> None:
    eng = ExecutionEngine(ExecutionSettings(), paper_mode=True)
    fill = await eng.execute(
        Signal(
            market_id="m1",
            platform="mock",
            side=Side.YES,
            strength=0.5,
            market_prob=0.4,
        )
    )
    assert fill.status == OrderStatus.SIMULATED
    assert GOT_ITEM in fill.note


@pytest.mark.asyncio
async def test_live_reject_is_miss() -> None:
    eng = ExecutionEngine(ExecutionSettings(), paper_mode=False)
    fill = await eng.execute(
        Signal(
            market_id="m1",
            platform="mock",
            side=Side.YES,
            strength=0.5,
            market_prob=0.4,
        )
    )
    assert fill.status == OrderStatus.REJECTED
    assert MISS in fill.note
