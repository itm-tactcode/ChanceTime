"""LLM client tests (offline / mock path)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.llm.client import DailyBudgetExceeded, GrokClient
from chancetime.utils.config import LLMSettings


@pytest.mark.asyncio
async def test_mock_chat_without_api_key(tmp_path: Path) -> None:
    client = GrokClient(
        LLMSettings(daily_budget_usd=5.0),
        api_key=None,
        spend_path=tmp_path / "spend.json",
    )
    text = await client.chat(
        [{"role": "user", "content": "Market probability question?"}],
        use_cache=False,
    )
    assert "probability" in text or "mock" in text.lower() or text.startswith("{")
    summary = client.spend_summary()
    assert summary["n_calls"] == 1
    assert summary["spent_usd"] >= 0


@pytest.mark.asyncio
async def test_budget_exceeded(tmp_path: Path) -> None:
    client = GrokClient(
        LLMSettings(daily_budget_usd=0.0),
        api_key=None,
        spend_path=tmp_path / "spend.json",
    )
    # First call may still run then exceed; force spent over budget
    client.tracker.spent_usd = 1.0
    with pytest.raises(DailyBudgetExceeded):
        await client.chat([{"role": "user", "content": "hi"}], use_cache=False)


@pytest.mark.asyncio
async def test_cache_hit(tmp_path: Path) -> None:
    client = GrokClient(LLMSettings(), api_key=None, spend_path=tmp_path / "spend.json")
    msgs = [{"role": "user", "content": "cached market probability"}]
    a = await client.chat(msgs, use_cache=True)
    b = await client.chat(msgs, use_cache=True)
    assert a == b
    assert any(c.cached for c in client.tracker.calls)
