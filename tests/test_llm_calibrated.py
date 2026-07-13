"""Phase 2: LLM calibration strategy (mock, no live API required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.data_layer.mock import MockMarketClient
from chancetime.llm.calibrate import ProbabilityCalibrator
from chancetime.llm.client import GrokClient
from chancetime.llm.schemas import ProbabilityCalibration
from chancetime.strategies.llm_calibrated import LLMCalibratedStrategy
from chancetime.utils.config import LLMSettings


@pytest.mark.asyncio
async def test_calibrator_mock(tmp_path: Path) -> None:
    llm = GrokClient(
        LLMSettings(daily_budget_usd=5.0),
        api_key=None,
        spend_path=tmp_path / "spend.json",
    )
    markets = await MockMarketClient().list_markets()
    cal = await ProbabilityCalibrator(llm).calibrate(markets[0])
    assert cal is not None
    assert isinstance(cal, ProbabilityCalibration)
    assert 0.0 <= cal.probability <= 1.0


@pytest.mark.asyncio
async def test_llm_calibrated_strategy_emits_or_skips(tmp_path: Path) -> None:
    llm = GrokClient(
        LLMSettings(daily_budget_usd=5.0, max_tokens=128),
        api_key=None,
        spend_path=tmp_path / "spend.json",
    )
    # Mock always returns ~0.5 fair → edge ~0 vs most markets; force low threshold
    strat = LLMCalibratedStrategy(
        llm,
        edge_threshold=0.0,  # accept any non-zero edge after recompute
        min_confidence=0.0,
        min_confidence_no_tools=0.0,
        screen_threshold=0.0,
        max_llm_calls_per_poll=2,
        min_liquidity_usd=100.0,
    )
    markets = await MockMarketClient().list_markets()
    signals = await strat.generate_signals(markets)
    # Mock fair=0.5; markets away from 0.5 should produce signals when thr=0
    assert isinstance(signals, list)
    assert llm.spend_summary()["n_calls"] >= 1
    assert llm.spend_summary()["n_calls"] <= 2


@pytest.mark.asyncio
async def test_budget_blocks_calibration(tmp_path: Path) -> None:
    llm = GrokClient(
        LLMSettings(daily_budget_usd=0.0),
        api_key=None,
        spend_path=tmp_path / "spend.json",
    )
    llm.tracker.spent_usd = 1.0
    markets = await MockMarketClient().list_markets()
    cal = await ProbabilityCalibrator(llm).calibrate(markets[0])
    assert cal is None
