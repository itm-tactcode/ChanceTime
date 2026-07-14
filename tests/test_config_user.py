"""user.yaml overlay + deep_merge."""

from __future__ import annotations

from pathlib import Path

from chancetime.utils.config import deep_merge, load_config, save_user_config


def test_deep_merge_nested() -> None:
    base = {"bot": {"poll_interval_seconds": 30, "paper_mode": True}, "x": 1}
    over = {"bot": {"poll_interval_seconds": 2}, "y": 2}
    m = deep_merge(base, over)
    assert m["bot"]["poll_interval_seconds"] == 2
    assert m["bot"]["paper_mode"] is True
    assert m["x"] == 1 and m["y"] == 2


def test_user_yaml_overlay(tmp_path: Path, monkeypatch: object) -> None:
    root = Path(__file__).resolve().parents[1]
    # Write a temp user overlay via save into project config would pollute;
    # use load_config with explicit user_config path.
    user = tmp_path / "user.yaml"
    user.write_text("bot:\n  poll_interval_seconds: 7\n", encoding="utf-8")
    cfg = load_config(
        root / "config" / "default.yaml",
        env_file=None,
        user_config=user,
    )
    assert cfg.bot.poll_interval_seconds == 7.0
    assert cfg.bot.paper_mode is True


def test_save_user_config_roundtrip(tmp_path: Path) -> None:
    path = save_user_config(
        {"bot": {"poll_interval_seconds": 5}},
        root=tmp_path,
    )
    assert path == tmp_path / "config" / "user.yaml"
    text = path.read_text(encoding="utf-8")
    assert "poll_interval_seconds: 5" in text
    # second save merges
    save_user_config({"bot": {"name": "mine"}}, root=tmp_path)
    text2 = path.read_text(encoding="utf-8")
    assert "poll_interval_seconds: 5" in text2
    assert "name: mine" in text2


def test_crypto_modules_user_yaml_overlay(tmp_path: Path) -> None:
    """Path C/D knobs merge from gitignored user.yaml (private edge params)."""
    root = Path(__file__).resolve().parents[1]
    user = tmp_path / "user.yaml"
    user.write_text(
        "crypto_updown:\n"
        "  min_edge: 0.11\n"
        "  paper_strategy: true\n"
        "  snipe_min_p: 0.71\n"
        "crypto_exchange:\n"
        "  signal_size_usd: 42.0\n"
        "  trade_signals: true\n",
        encoding="utf-8",
    )
    cfg = load_config(
        root / "config" / "default.yaml",
        env_file=None,
        user_config=user,
    )
    assert cfg.crypto_updown.min_edge == 0.11
    assert cfg.crypto_updown.paper_strategy is True
    assert cfg.crypto_updown.snipe_min_p == 0.71
    # Unspecified fields keep public default.yaml baseline
    assert cfg.crypto_updown.size_usd == 5.0
    assert cfg.crypto_exchange.signal_size_usd == 42.0
    assert cfg.crypto_exchange.trade_signals is True
