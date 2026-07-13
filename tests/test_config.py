"""Config loading tests."""

from __future__ import annotations

from pathlib import Path

from chancetime.utils.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def test_load_default_config() -> None:
    # Ignore local config/user.yaml so presets/smoke tests don't break CI
    cfg = load_config(ROOT / "config" / "default.yaml", env_file=None, user_config=False)
    assert cfg.bot.paper_mode is True
    assert cfg.risk.max_position_usd == 50.0
    assert cfg.data.source == "mock"
    assert cfg.strategies.simple_edge.edge_threshold == 0.08
    assert cfg.llm.daily_budget_usd == 5.0


def test_paper_mode_property() -> None:
    cfg = load_config(ROOT / "config" / "default.yaml", env_file=None, user_config=False)
    assert cfg.paper_mode is cfg.bot.paper_mode
