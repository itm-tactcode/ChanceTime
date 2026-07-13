"""Offline ml_edge training from fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.data_layer.models import Market, Platform
from chancetime.ml.train import train_ml_edge_from_csv
from chancetime.strategies.ml_edge import MLEdgeStrategy


@pytest.mark.asyncio
async def test_train_ml_edge_fixture(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    root = Path(__file__).resolve().parents[1]
    fixture = root / "backtests" / "fixtures" / "sample_series.csv"
    out = tmp_path / "ml_edge.joblib"
    result = train_ml_edge_from_csv(fixture, out_path=out)
    assert out.is_file()
    assert result.n_samples >= 6
    assert 0.0 <= result.train_accuracy <= 1.0

    strat = MLEdgeStrategy(enabled=True, model_path=str(out), min_liquidity_usd=0.0)
    m = Market(
        id="fed-cut",
        platform=Platform.MOCK,
        title="Fed cut",
        yes_price=0.42,
        no_price=0.58,
        liquidity_usd=5000,
        volume_usd=5000,
    )
    sigs = await strat.generate_signals([m])
    assert isinstance(sigs, list)
