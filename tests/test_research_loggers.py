"""Log-only research strategies write JSONL and emit no signals."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.data_layer.mock import MockMarketClient
from chancetime.strategies.research_loggers import (
    MatchQualityStrategy,
    PairGapTrackerStrategy,
    PriceBucketsStrategy,
    TteBucketsStrategy,
)


@pytest.mark.asyncio
async def test_research_loggers_write_and_no_signals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    # research_log uses project_root — point CHANCETIME or write under tmp via monkeypatch
    import chancetime.utils.research_log as rl

    monkeypatch.setattr(rl, "research_dir", lambda directory=None: tmp_path / "research")
    monkeypatch.setattr(
        rl,
        "research_path",
        lambda name, directory=None: (tmp_path / "research" / f"{name}-test.jsonl"),
    )

    markets = await MockMarketClient().list_markets(limit=50)
    for Strat, kwargs in (
        (PairGapTrackerStrategy, {"log_name": "pair_gap"}),
        (TteBucketsStrategy, {"log_name": "tte_buckets"}),
        (PriceBucketsStrategy, {"log_name": "price_buckets"}),
        (MatchQualityStrategy, {"log_name": "match_quality", "min_match_score": 0.5}),
    ):
        s = Strat(enabled=True, **kwargs)
        sigs = await s.generate_signals(markets)
        assert sigs == []
    written = list((tmp_path / "research").glob("*.jsonl"))
    assert len(written) >= 3
    for p in written:
        assert p.read_text().strip()
