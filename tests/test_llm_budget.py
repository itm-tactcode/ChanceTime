"""Hard tests: budget undercount and durable spend must not recur."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.llm.client import DailyBudgetExceeded, DailySpendTracker, GrokClient
from chancetime.utils.config import LLMSettings


def test_grok45_default_prices_not_fast_tier() -> None:
    s = LLMSettings()
    # Flagship rates — $0.20/$0.50 was the Jul-2026 undercount bug
    assert s.price_input_per_1m >= 1.0
    assert s.price_output_per_1m >= 2.0


def test_estimate_matches_flagship_scale(tmp_path: Path) -> None:
    client = GrokClient(
        LLMSettings(price_input_per_1m=2.0, price_output_per_1m=6.0, daily_budget_usd=50.0),
        api_key=None,
        spend_path=tmp_path / "est.json",
    )
    # 25M input tokens @ $2/1M ≈ $50 (the incident scale)
    cost = client._estimate_cost(25_000_000, 0)
    assert 49.0 <= cost <= 51.0


def test_durable_spend_survives_reload(tmp_path: Path) -> None:
    path = tmp_path / "spend.json"
    t1 = DailySpendTracker(budget_usd=5.0, persist_path=path)
    t1.spent_usd = 3.5
    t1._save()
    t2 = DailySpendTracker(budget_usd=5.0, persist_path=path)
    assert t2.spent_usd == pytest.approx(3.5, abs=1e-6)
    assert t2.remaining() == pytest.approx(1.5, abs=1e-6)


@pytest.mark.asyncio
async def test_preflight_blocks_over_reserve(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    client = GrokClient(
        LLMSettings(
            daily_budget_usd=1.0,
            price_input_per_1m=2.0,
            price_output_per_1m=6.0,
            tools_enabled=True,
            web_search=True,
            tools_reserve_input_tokens=200_000,  # $0.40 at $2/1M
            max_tokens=100,
        ),
        api_key=None,
        spend_path=path,
    )
    client.tracker.spent_usd = 0.9
    client.tracker._save()
    with pytest.raises(DailyBudgetExceeded):
        # tools reserve would need more than $0.10 remaining
        await client.chat(
            [{"role": "user", "content": "x" * 100}],
            use_cache=False,
            use_tools=True,
        )


@pytest.mark.asyncio
async def test_budget_zero_blocks_api_path(tmp_path: Path) -> None:
    client = GrokClient(
        LLMSettings(daily_budget_usd=0.01, price_input_per_1m=2.0, price_output_per_1m=6.0),
        api_key=None,
        spend_path=tmp_path / "z.json",
    )
    client.tracker.spent_usd = 0.01
    client.tracker._save()
    with pytest.raises(DailyBudgetExceeded):
        await client.chat([{"role": "user", "content": "hi"}], use_cache=False)
