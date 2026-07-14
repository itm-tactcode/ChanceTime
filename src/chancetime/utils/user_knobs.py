"""Whitelist merge for config/user.yaml (desktop + dashboard write path).

Never accepts secret keys.
"""

from __future__ import annotations

from typing import Any

from chancetime.utils.config import deep_merge, save_user_config, user_config_path
from chancetime.utils.paths import project_root

# Keys we refuse even if nested (defense in depth)
_SECRET_FRAGMENTS = (
    "api_key",
    "secret",
    "password",
    "token",
    "private_key",
    "pem",
    "xai_",
    "telegram",
)


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in _SECRET_FRAGMENTS)


def sanitize_user_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only non-secret ops knobs; drop unknown top-level sections lightly."""
    allowed_top = {
        "bot",
        "risk",
        "data",
        "history",
        "strategies",
        "execution",
        "llm",
        "alerts",
        "logging",
        "persistence",
        "dashboard",
    }
    out: dict[str, Any] = {}
    for top, val in raw.items():
        if _is_secret_key(str(top)):
            continue
        if top not in allowed_top:
            continue
        if isinstance(val, dict):
            out[top] = _sanitize_map(val)
        else:
            out[top] = val
    return out


def _sanitize_map(d: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for k, v in d.items():
        if _is_secret_key(str(k)):
            continue
        if isinstance(v, dict):
            cleaned[k] = _sanitize_map(v)
        else:
            cleaned[k] = v
    return cleaned


def apply_user_overrides(
    overrides: dict[str, Any],
    *,
    root: Any = None,
) -> dict[str, Any]:
    """Sanitize, merge into user.yaml, return {path, written}."""
    clean = sanitize_user_overrides(overrides)
    if not clean:
        raise ValueError("no allowed knobs in payload (secrets stripped / empty)")
    path = save_user_config(clean, root=root)
    return {"path": str(path), "written": clean}


def load_user_overrides_file(*, root: Any = None) -> dict[str, Any]:
    import yaml

    r = root or project_root()
    path = user_config_path(root=r)
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def snapshot_user_knobs(*, root: Any = None) -> dict[str, Any]:
    """Effective ops knobs from full config load (default.yaml + user.yaml).

    Prefer this over hard-coded UI defaults so desktop/suggest match the bot.
    Does not apply process env secrets beyond what load_config does.
    """
    from chancetime.utils.config import load_config

    cfg = load_config(user_config=True)
    s = cfg.strategies
    return {
        "poll_interval_seconds": cfg.bot.poll_interval_seconds,
        "shadow_mode": cfg.bot.shadow_mode,
        "hot_reload_risk": bool(getattr(cfg.bot, "hot_reload_risk", False)),
        "data_source": cfg.data.source,
        "max_markets": cfg.data.max_markets,
        "discovery_every_polls": int(getattr(cfg.data, "discovery_every_polls", 0) or 0),
        "discovery_limit": int(getattr(cfg.data, "discovery_limit", 150) or 150),
        "history_enabled": cfg.history.enabled,
        "max_position_usd": cfg.risk.max_position_usd,
        "max_daily_loss_usd": cfg.risk.max_daily_loss_usd,
        "max_open_positions": cfg.risk.max_open_positions,
        "max_family_exposure_usd": cfg.risk.max_family_exposure_usd,
        "max_cluster_exposure_usd": float(getattr(cfg.risk, "max_cluster_exposure_usd", 0) or 0),
        "max_deploy_pct": float(getattr(cfg.risk, "max_deploy_pct", 0) or 0),
        "min_hours_to_close": float(getattr(cfg.risk, "min_hours_to_close", 0) or 0),
        "max_days_to_close": float(getattr(cfg.risk, "max_days_to_close", 0) or 0),
        "take_profit_pct": cfg.risk.take_profit_pct,
        "stop_loss_pct": cfg.risk.stop_loss_pct,
        "enforce_cash": cfg.risk.enforce_cash,
        "min_net_edge": cfg.risk.min_net_edge,
        "max_open_per_strategy": cfg.risk.max_open_per_strategy,
        "max_spread": float(getattr(cfg.risk, "max_spread", 0.06) or 0),
        "default_order_size_usd": cfg.execution.default_order_size_usd,
        "llm_enabled": cfg.llm.enabled,
        "llm_daily_budget_usd": cfg.llm.daily_budget_usd,
        "llm_calibrated_max_calls": int(getattr(s.llm_calibrated, "max_llm_calls_per_poll", 2) or 2),
        "strategies": {
            "simple_edge": {
                "enabled": s.simple_edge.enabled,
                "weight": s.simple_edge.weight,
                "edge_threshold": s.simple_edge.edge_threshold,
                "max_open": s.simple_edge.max_open
                if s.simple_edge.max_open is not None
                else cfg.risk.max_open_per_strategy,
            },
            "arb_cross": {
                "enabled": s.arb_cross.enabled,
                "weight": s.arb_cross.weight,
                "max_open": s.arb_cross.max_open
                if s.arb_cross.max_open is not None
                else cfg.risk.max_open_per_strategy,
            },
            "complement_arb": {
                "enabled": s.complement_arb.enabled,
                "weight": s.complement_arb.weight,
                "max_open": s.complement_arb.max_open
                if s.complement_arb.max_open is not None
                else cfg.risk.max_open_per_strategy,
            },
            "mean_revert": {
                "enabled": s.mean_revert.enabled,
                "weight": s.mean_revert.weight,
                "max_open": s.mean_revert.max_open
                if s.mean_revert.max_open is not None
                else cfg.risk.max_open_per_strategy,
            },
            "ml_edge": {
                "enabled": s.ml_edge.enabled,
                "weight": s.ml_edge.weight,
                "max_open": s.ml_edge.max_open
                if s.ml_edge.max_open is not None
                else cfg.risk.max_open_per_strategy,
            },
            "llm_calibrated": {
                "enabled": s.llm_calibrated.enabled,
                "weight": s.llm_calibrated.weight,
                "max_open": s.llm_calibrated.max_open
                if s.llm_calibrated.max_open is not None
                else cfg.risk.max_open_per_strategy,
                "max_llm_calls_per_poll": int(
                    getattr(s.llm_calibrated, "max_llm_calls_per_poll", 2) or 2
                ),
            },
            "news_impulse": {
                "enabled": s.news_impulse.enabled,
                "weight": s.news_impulse.weight,
                "max_open": s.news_impulse.max_open
                if s.news_impulse.max_open is not None
                else cfg.risk.max_open_per_strategy,
            },
        },
    }


def build_knobs_snapshot(
    overrides: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flat-ish snapshot for UIs (effective config, optionally re-merged with overrides).

    When ``overrides`` is None, loads user.yaml via ``load_config`` so max_open etc.
    match the running bot — never stale hard-coded 10 from .env comments.
    """
    if overrides is None and defaults is None:
        try:
            return snapshot_user_knobs()
        except Exception:
            pass
    # Fallback hard defaults only if config load fails
    base: dict[str, Any] = {
        "poll_interval_seconds": 30,
        "shadow_mode": False,
        "data_source": "mock",
        "max_markets": 100,
        "history_enabled": False,
        "max_position_usd": 50.0,
        "max_daily_loss_usd": 25.0,
        "max_open_positions": 10,
        "max_family_exposure_usd": 100.0,
        "max_cluster_exposure_usd": 0.0,
        "max_deploy_pct": 0.0,
        "min_hours_to_close": 0.0,
        "max_days_to_close": 0.0,
        "hot_reload_risk": False,
        "discovery_every_polls": 5,
        "discovery_limit": 150,
        "take_profit_pct": 0.30,
        "stop_loss_pct": 0.25,
        "max_spread": 0.06,
        "default_order_size_usd": 10.0,
        "llm_enabled": True,
        "llm_daily_budget_usd": 5.0,
        "llm_calibrated_max_calls": 2,
        "strategies": {
            "simple_edge": {"enabled": True, "weight": 1.0, "edge_threshold": 0.08},
            "arb_cross": {"enabled": True, "weight": 1.0},
            "mean_revert": {"enabled": False, "weight": 1.0},
            "ml_edge": {"enabled": False, "weight": 1.0},
            "llm_calibrated": {
                "enabled": False,
                "weight": 1.0,
                "max_llm_calls_per_poll": 2,
            },
            "news_impulse": {"enabled": False, "weight": 1.0},
        },
    }
    try:
        base = deep_merge(base, snapshot_user_knobs())
    except Exception:
        pass
    if defaults:
        base = deep_merge(base, defaults)
    if overrides:
        # Map nested user.yaml into flat snapshot
        bot = overrides.get("bot") or {}
        risk = overrides.get("risk") or {}
        data = overrides.get("data") or {}
        hist = overrides.get("history") or {}
        exe = overrides.get("execution") or {}
        llm = overrides.get("llm") or {}
        if "poll_interval_seconds" in bot:
            base["poll_interval_seconds"] = bot["poll_interval_seconds"]
        if "shadow_mode" in bot:
            base["shadow_mode"] = bool(bot["shadow_mode"])
        if "hot_reload_risk" in bot:
            base["hot_reload_risk"] = bool(bot["hot_reload_risk"])
        if "source" in data:
            base["data_source"] = data["source"]
        if "max_markets" in data:
            base["max_markets"] = data["max_markets"]
        if "discovery_every_polls" in data:
            base["discovery_every_polls"] = int(data["discovery_every_polls"])
        if "discovery_limit" in data:
            base["discovery_limit"] = int(data["discovery_limit"])
        if "enabled" in hist:
            base["history_enabled"] = bool(hist["enabled"])
        for k in (
            "max_position_usd",
            "max_daily_loss_usd",
            "max_open_positions",
            "max_family_exposure_usd",
            "max_cluster_exposure_usd",
            "max_deploy_pct",
            "min_hours_to_close",
            "max_days_to_close",
            "take_profit_pct",
            "stop_loss_pct",
            "enforce_cash",
            "min_net_edge",
            "max_open_per_strategy",
            "assumed_half_spread",
            "assumed_fee",
            "max_spread",
        ):
            if k in risk:
                base[k] = risk[k]
        if "default_order_size_usd" in exe:
            base["default_order_size_usd"] = exe["default_order_size_usd"]
        if "enabled" in llm:
            base["llm_enabled"] = llm["enabled"]
        if "daily_budget_usd" in llm:
            base["llm_daily_budget_usd"] = llm["daily_budget_usd"]
        strats = overrides.get("strategies") or {}
        for name, st in strats.items():
            if not isinstance(st, dict):
                continue
            slot = base["strategies"].setdefault(name, {"enabled": False, "weight": 1.0})
            if "enabled" in st:
                slot["enabled"] = bool(st["enabled"])
            if "weight" in st:
                slot["weight"] = float(st["weight"])
            if "edge_threshold" in st:
                slot["edge_threshold"] = float(st["edge_threshold"])
            if "max_open" in st:
                slot["max_open"] = int(st["max_open"]) if st["max_open"] is not None else None
            if "max_llm_calls_per_poll" in st:
                slot["max_llm_calls_per_poll"] = int(st["max_llm_calls_per_poll"])
                if name == "llm_calibrated":
                    base["llm_calibrated_max_calls"] = int(st["max_llm_calls_per_poll"])
    return base


def snapshot_to_overrides(snap: dict[str, Any]) -> dict[str, Any]:
    """Convert UI snapshot back to nested user.yaml overrides."""
    strats_in = snap.get("strategies") or {}
    strategies: dict[str, Any] = {}
    for name, st in strats_in.items():
        if not isinstance(st, dict):
            continue
        entry: dict[str, Any] = {}
        if "enabled" in st:
            entry["enabled"] = bool(st["enabled"])
        if "weight" in st:
            entry["weight"] = float(st["weight"])
        if "edge_threshold" in st:
            entry["edge_threshold"] = float(st["edge_threshold"])
        if "max_open" in st and st["max_open"] is not None and st["max_open"] != "":
            entry["max_open"] = int(st["max_open"])
        if "max_llm_calls_per_poll" in st and st["max_llm_calls_per_poll"] is not None:
            entry["max_llm_calls_per_poll"] = int(st["max_llm_calls_per_poll"])
        if entry:
            strategies[name] = entry
    # Top-level LLM max-calls field maps onto llm_calibrated
    if snap.get("llm_calibrated_max_calls") is not None:
        lc = strategies.setdefault("llm_calibrated", {})
        lc["max_llm_calls_per_poll"] = int(snap["llm_calibrated_max_calls"])
    risk: dict[str, Any] = {
        "max_position_usd": float(snap.get("max_position_usd", 50)),
        "max_daily_loss_usd": float(snap.get("max_daily_loss_usd", 25)),
        "max_open_positions": int(snap.get("max_open_positions", 10)),
        "max_family_exposure_usd": float(snap.get("max_family_exposure_usd", 100)),
        "take_profit_pct": float(snap.get("take_profit_pct", 0.3))
        if snap.get("take_profit_pct") is not None
        else None,
        "stop_loss_pct": float(snap.get("stop_loss_pct", 0.25))
        if snap.get("stop_loss_pct") is not None
        else None,
    }
    if snap.get("max_open_per_strategy") is not None:
        risk["max_open_per_strategy"] = int(snap["max_open_per_strategy"])
    for k, cast in (
        ("max_cluster_exposure_usd", float),
        ("max_deploy_pct", float),
        ("min_hours_to_close", float),
        ("max_days_to_close", float),
        ("max_spread", float),
        ("min_net_edge", float),
    ):
        if snap.get(k) is not None and snap.get(k) != "":
            risk[k] = cast(snap[k])
    data: dict[str, Any] = {
        "source": str(snap.get("data_source", "mock")),
        "max_markets": int(snap.get("max_markets", 100)),
    }
    if snap.get("discovery_every_polls") is not None:
        data["discovery_every_polls"] = int(snap["discovery_every_polls"])
    if snap.get("discovery_limit") is not None:
        data["discovery_limit"] = int(snap["discovery_limit"])
    return {
        "bot": {
            "poll_interval_seconds": float(snap.get("poll_interval_seconds", 30)),
            "shadow_mode": bool(snap.get("shadow_mode", False)),
            "hot_reload_risk": bool(snap.get("hot_reload_risk", False)),
        },
        "data": data,
        "history": {
            "enabled": bool(snap.get("history_enabled", False)),
        },
        "risk": risk,
        "execution": {
            "default_order_size_usd": float(snap.get("default_order_size_usd", 10)),
        },
        "llm": {
            "enabled": bool(snap.get("llm_enabled", True)),
            "daily_budget_usd": float(snap.get("llm_daily_budget_usd", 5)),
        },
        "strategies": strategies,
    }
