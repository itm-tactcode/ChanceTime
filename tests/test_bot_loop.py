"""Integration: single paper poll with mock data."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.main import Bot
from chancetime.utils.config import load_config


@pytest.mark.asyncio
async def test_single_poll_paper_mode() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config" / "default.yaml", env_file=None)
    cfg.bot.paper_mode = True
    cfg.data.source = "mock"
    cfg.llm.enabled = True
    cfg.llm.call_on_every_poll = False

    bot = Bot(cfg)
    await bot.run(max_polls=1)

    assert bot.poll_count == 1
    # At least some paper fills expected from simple_edge on mock data
    assert bot.execution.paper_mode is True
