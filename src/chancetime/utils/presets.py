"""Hardcoded recommended setting packs (Phase 14/15 readiness).

Apply via ``chancetime presets apply NAME`` → merges into config/user.yaml.
Never touches secrets.
"""

from __future__ import annotations

from typing import Any

from chancetime.utils.user_knobs import apply_user_overrides

# Each preset is a nested override dict safe for user.yaml
PRESETS: dict[str, dict[str, Any]] = {
    "conservative_paper": {
        "bot": {
            "poll_interval_seconds": 45,
            "shadow_mode": False,
            "paper_mode": True,
        },
        "data": {"source": "mock", "max_markets": 50},
        "history": {"enabled": True},
        "risk": {
            "max_position_usd": 25.0,
            "max_daily_loss_usd": 15.0,
            "max_open_positions": 5,
            "max_family_exposure_usd": 50.0,
            "take_profit_pct": 0.25,
            "stop_loss_pct": 0.20,
        },
        "execution": {"default_order_size_usd": 5.0},
        "llm": {"enabled": False, "daily_budget_usd": 1.0},
        "strategies": {
            "simple_edge": {"enabled": True, "weight": 1.0, "edge_threshold": 0.10},
            "arb_cross": {"enabled": False, "weight": 0.0},
            "mean_revert": {"enabled": False, "weight": 0.0},
            "ml_edge": {"enabled": False, "weight": 0.0},
            "llm_calibrated": {"enabled": False, "weight": 0.0},
            "news_impulse": {"enabled": False, "weight": 0.0},
        },
    },
    "research_shadow": {
        "bot": {
            "poll_interval_seconds": 30,
            "shadow_mode": True,
            "paper_mode": True,
        },
        "data": {"source": "mock", "max_markets": 100},
        "history": {"enabled": True},
        "risk": {
            "max_position_usd": 50.0,
            "max_daily_loss_usd": 25.0,
            "max_open_positions": 10,
            "max_family_exposure_usd": 100.0,
        },
        "execution": {"default_order_size_usd": 10.0},
        "llm": {"enabled": True, "daily_budget_usd": 3.0},
        "strategies": {
            "simple_edge": {"enabled": True, "weight": 1.0, "edge_threshold": 0.08},
            "arb_cross": {"enabled": True, "weight": 1.0},
            "mean_revert": {"enabled": True, "weight": 0.8},
            "ml_edge": {"enabled": True, "weight": 0.5},
            "llm_calibrated": {"enabled": False, "weight": 0.0},
            "news_impulse": {"enabled": False, "weight": 0.0},
        },
    },
    "paper_bag_active": {
        "bot": {"poll_interval_seconds": 30, "shadow_mode": False, "paper_mode": True},
        "data": {"source": "mock", "max_markets": 80},
        "history": {"enabled": True},
        "risk": {
            "max_position_usd": 40.0,
            "max_daily_loss_usd": 20.0,
            "max_open_positions": 8,
            "max_family_exposure_usd": 80.0,
        },
        "execution": {"default_order_size_usd": 8.0},
        "llm": {"enabled": False, "daily_budget_usd": 2.0},
        "strategies": {
            "simple_edge": {"enabled": True, "weight": 1.0, "edge_threshold": 0.08},
            "arb_cross": {"enabled": True, "weight": 1.0},
            "mean_revert": {"enabled": True, "weight": 0.7},
            "ml_edge": {"enabled": True, "weight": 0.6},
            "llm_calibrated": {"enabled": False, "weight": 0.0},
            "news_impulse": {"enabled": False, "weight": 0.0},
        },
    },
    "arb_focus": {
        "bot": {"poll_interval_seconds": 20, "shadow_mode": False, "paper_mode": True},
        "data": {"source": "both", "max_markets": 100},
        "history": {"enabled": True},
        "risk": {
            "max_position_usd": 30.0,
            "max_daily_loss_usd": 15.0,
            "max_open_positions": 6,
            "max_family_exposure_usd": 60.0,
        },
        "execution": {
            "default_order_size_usd": 10.0,
            "require_both_arb_legs": True,
            "max_leg_usd": 15.0,
            "max_arb_pair_usd": 30.0,
        },
        "llm": {"enabled": False, "daily_budget_usd": 1.0},
        "strategies": {
            "simple_edge": {"enabled": False, "weight": 0.0},
            "arb_cross": {
                "enabled": True,
                "weight": 1.0,
                "require_bbo": False,
                "min_spread": 0.04,
            },
            "mean_revert": {"enabled": False, "weight": 0.0},
            "ml_edge": {"enabled": False, "weight": 0.0},
            "llm_calibrated": {"enabled": False, "weight": 0.0},
            "news_impulse": {"enabled": False, "weight": 0.0},
        },
    },
    "live_micro_dry": {
        # Still paper_mode true — for reviewing live_micro-like knobs without live
        "bot": {
            "poll_interval_seconds": 60,
            "shadow_mode": False,
            "paper_mode": True,
        },
        "data": {"source": "both", "max_markets": 40},
        "history": {"enabled": True},
        "risk": {
            "max_position_usd": 10.0,
            "max_daily_loss_usd": 10.0,
            "max_open_positions": 3,
            "max_family_exposure_usd": 15.0,
        },
        "execution": {
            "default_order_size_usd": 3.0,
            "live_enabled": False,
            "max_live_order_usd": 5.0,
            "max_live_notional_session": 20.0,
            "dual_leg_live_enabled": False,
        },
        "llm": {"enabled": False, "daily_budget_usd": 1.0},
        "strategies": {
            "simple_edge": {"enabled": True, "weight": 1.0, "edge_threshold": 0.12},
            "arb_cross": {"enabled": False, "weight": 0.0},
            "mean_revert": {"enabled": False, "weight": 0.0},
            "ml_edge": {"enabled": False, "weight": 0.0},
            "llm_calibrated": {"enabled": False, "weight": 0.0},
            "news_impulse": {"enabled": False, "weight": 0.0},
        },
    },
}

PRESET_BLURBS: dict[str, str] = {
    "conservative_paper": "Small size, simple_edge only, history on, no LLM.",
    "research_shadow": "Many strategies, shadow_mode (no fills), history on.",
    "paper_bag_active": "Multi-strategy paper bag with moderate risk.",
    "arb_focus": "Cross-venue arb emphasis; dual data source.",
    "live_micro_dry": "Live-micro-like caps but paper_mode true — dry review only.",
}


def list_presets() -> list[dict[str, str]]:
    return [
        {"name": name, "blurb": PRESET_BLURBS.get(name, "")}
        for name in sorted(PRESETS)
    ]


def get_preset(name: str) -> dict[str, Any]:
    key = name.strip().lower()
    if key not in PRESETS:
        known = ", ".join(sorted(PRESETS))
        raise KeyError(f"Unknown preset {name!r}. Known: {known}")
    return PRESETS[key]


def apply_preset(name: str, *, root: Any = None) -> dict[str, Any]:
    from pathlib import Path

    preset = get_preset(name)
    result = apply_user_overrides(preset, root=root)
    return {
        "preset": name,
        "blurb": PRESET_BLURBS.get(name.strip().lower(), ""),
        **result,
    }
