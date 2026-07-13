"""Phase 10: market history recorder, fees, walk-forward."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chancetime.backtesting.fees import CostModel, cost_model_for_venue
from chancetime.backtesting.loader import load_bars_csv
from chancetime.backtesting.models import MarketBar, ResolveOutcome
from chancetime.backtesting.walk_forward import split_time_folds, walk_forward_simple_edge
from chancetime.data_layer.history import (
    MarketHistoryRecorder,
    history_to_bars_csv,
    load_history_jsonl,
)
from chancetime.data_layer.models import Market, Platform


def test_record_and_convert(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    rec = MarketHistoryRecorder(path=path, enabled=True)
    markets = [
        Market(
            id="m1",
            platform=Platform.MOCK,
            title="Test",
            yes_price=0.4,
            no_price=0.6,
            liquidity_usd=100,
            yes_bid=0.39,
            yes_ask=0.41,
            yes_bid_size=50,
            yes_ask_size=40,
            has_bbo=True,
        )
    ]
    n = rec.record_markets(markets, source="mock", poll=1)
    assert n == 1
    rows = load_history_jsonl(path)
    assert rows[0]["market_id"] == "m1"
    assert rows[0]["has_bbo"] is True
    csv_path = history_to_bars_csv(path, tmp_path / "out.csv")
    bars = load_bars_csv(csv_path)
    assert len(bars) == 1
    assert bars[0].yes_ask == 0.41
    assert bars[0].depth_usd_yes_buy() is not None


def test_cost_model_venue() -> None:
    k = cost_model_for_venue("kalshi")
    assert k.fee_bps < 100
    p = cost_model_for_venue("polymarket")
    assert p.fee_bps == 0.0
    m = CostModel()
    assert m.clip_size_to_depth(10, depth_usd=20, liquidity_usd=0) is not None
    assert m.clip_size_to_depth(100, depth_usd=1, liquidity_usd=0) is None


def test_walk_forward_runs() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "backtests" / "fixtures" / "sample_series.csv"
    bars = load_bars_csv(fixture)
    report = asyncio.run(
        walk_forward_simple_edge(bars, n_folds=2, starting_cash=1000, order_size_usd=10)
    )
    assert report.folds
    assert isinstance(report.mean_test_pnl, float)


def test_depth_on_bar() -> None:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    b = MarketBar(
        ts=ts,
        market_id="x",
        yes_price=0.5,
        yes_ask=0.52,
        yes_ask_size=10,
        has_bbo=True,
        resolve=ResolveOutcome.OPEN,
    )
    assert b.depth_usd_yes_buy() == 5.2


def test_split_folds_smoke() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        MarketBar(ts=base + timedelta(hours=i), market_id="m", yes_price=0.5)
        for i in range(20)
    ]
    folds = split_time_folds(bars, n_folds=3)
    assert folds


def test_load_bars_from_history(tmp_path: Path) -> None:
    from chancetime.data_layer.history import load_bars_from_history

    path = tmp_path / "h.jsonl"
    rec = MarketHistoryRecorder(path=path, enabled=True)
    rec.record_markets(
        [
            Market(
                id="a",
                platform=Platform.KALSHI,
                title="A",
                yes_price=0.55,
                no_price=0.45,
                yes_bid=0.54,
                yes_ask=0.56,
                has_bbo=True,
            ),
            Market(
                id="b",
                platform=Platform.POLYMARKET,
                title="B",
                yes_price=0.6,
                no_price=0.4,
            ),
        ],
        source="both",
    )
    bars = load_bars_from_history(path)
    assert len(bars) == 2
    platforms = {b.platform for b in bars}
    assert "kalshi" in platforms and "polymarket" in platforms
    only_k = load_bars_from_history(path, platforms={"kalshi"})
    assert len(only_k) == 1
