"""Phase 9: doctor, shadow mode, user knobs whitelist."""

from __future__ import annotations

from pathlib import Path

from chancetime.utils.config import BotSettings, load_config
from chancetime.utils.doctor import run_doctor
from chancetime.utils.user_knobs import (
    apply_user_overrides,
    build_knobs_snapshot,
    sanitize_user_overrides,
    snapshot_to_overrides,
)


def test_shadow_mode_default_false() -> None:
    assert BotSettings().shadow_mode is False


def test_sanitize_strips_secrets() -> None:
    raw = {
        "bot": {"poll_interval_seconds": 12, "xai_api_key": "nope"},
        "xai_api_key": "nope",
        "risk": {"max_position_usd": 9},
    }
    clean = sanitize_user_overrides(raw)
    assert "xai_api_key" not in clean
    assert clean["bot"]["poll_interval_seconds"] == 12
    assert "xai_api_key" not in clean["bot"]
    assert clean["risk"]["max_position_usd"] == 9


def test_snapshot_roundtrip(tmp_path: Path) -> None:
    snap = build_knobs_snapshot(
        {
            "bot": {"poll_interval_seconds": 11, "shadow_mode": True},
            "data": {"source": "mock"},
            "strategies": {"mean_revert": {"enabled": True, "weight": 0.5}},
        }
    )
    assert snap["poll_interval_seconds"] == 11
    assert snap["shadow_mode"] is True
    assert snap["strategies"]["mean_revert"]["enabled"] is True
    nested = snapshot_to_overrides(snap)
    result = apply_user_overrides(nested, root=tmp_path)
    assert Path(result["path"]).is_file()
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "shadow_mode: true" in text
    assert "mean_revert" in text


def test_doctor_runs() -> None:
    report = run_doctor(env_file=None)
    assert "checks" in report
    assert "summary" in report
    names = {c["name"] for c in report["checks"]}
    assert "config_load" in names


def test_default_config_has_shadow_key() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config" / "default.yaml", env_file=None)
    assert hasattr(cfg.bot, "shadow_mode")
