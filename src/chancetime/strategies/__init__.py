"""Pluggable trading strategies (the item bag)."""

from __future__ import annotations

from chancetime.llm.calibrate import ProbabilityCalibrator
from chancetime.llm.client import GrokClient
from chancetime.strategies.arb_cross import ArbCrossStrategy
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.strategies.llm_calibrated import LLMCalibratedStrategy
from chancetime.strategies.mean_revert import MeanRevertStrategy
from chancetime.strategies.ml_edge import MLEdgeStrategy
from chancetime.strategies.news_impulse import NewsImpulseStrategy
from chancetime.strategies.simple_edge import SimpleEdgeStrategy
from chancetime.utils.config import AppConfig

__all__ = [
    "ArbCrossStrategy",
    "BaseStrategy",
    "LLMCalibratedStrategy",
    "MLEdgeStrategy",
    "MeanRevertStrategy",
    "NewsImpulseStrategy",
    "Side",
    "Signal",
    "SimpleEdgeStrategy",
    "build_strategies",
    "strategy_open_limits_from_config",
    "strategy_size_caps_from_config",
    "strategy_weights_from_config",
]


def strategy_weights_from_config(cfg: AppConfig) -> dict[str, float]:
    s = cfg.strategies
    return {
        "simple_edge": s.simple_edge.weight,
        "llm_calibrated": s.llm_calibrated.weight,
        "arb_cross": s.arb_cross.weight,
        "mean_revert": s.mean_revert.weight,
        "news_impulse": s.news_impulse.weight,
        "ml_edge": s.ml_edge.weight,
    }


def strategy_open_limits_from_config(cfg: AppConfig) -> dict[str, int]:
    """Per-strategy open caps. Missing/None → risk.max_open_per_strategy; 0 → unlimited."""
    default = int(cfg.risk.max_open_per_strategy)
    s = cfg.strategies
    out: dict[str, int] = {}
    for name, st in (
        ("simple_edge", s.simple_edge),
        ("llm_calibrated", s.llm_calibrated),
        ("arb_cross", s.arb_cross),
        ("mean_revert", s.mean_revert),
        ("news_impulse", s.news_impulse),
        ("ml_edge", s.ml_edge),
    ):
        raw = getattr(st, "max_open", None)
        out[name] = default if raw is None else int(raw)
    return out


def strategy_size_caps_from_config(cfg: AppConfig) -> dict[str, float]:
    """Optional per-strategy max size_usd (empty if unset)."""
    s = cfg.strategies
    out: dict[str, float] = {}
    for name, st in (
        ("simple_edge", s.simple_edge),
        ("llm_calibrated", s.llm_calibrated),
        ("arb_cross", s.arb_cross),
        ("mean_revert", s.mean_revert),
        ("news_impulse", s.news_impulse),
        ("ml_edge", s.ml_edge),
    ):
        raw = getattr(st, "max_size_usd", None)
        if raw is not None and float(raw) > 0:
            out[name] = float(raw)
    return out


def build_strategies(
    cfg: AppConfig,
    *,
    llm: GrokClient | None = None,
) -> list[BaseStrategy]:
    """Instantiate enabled strategies from config.

    Pass ``llm`` when equipping cost-aware LLM strategies.
    """
    strategies: list[BaseStrategy] = []
    se = cfg.strategies.simple_edge
    if se.enabled:
        strategies.append(
            SimpleEdgeStrategy(
                enabled=se.enabled,
                edge_threshold=se.edge_threshold,
                min_liquidity_usd=se.min_liquidity_usd,
                default_fair_prob=se.default_fair_prob,
                prior_mode=se.prior_mode,
                blend_alpha=se.blend_alpha,
                history_window=se.history_window,
                min_history=se.min_history,
                weight=se.weight,
                min_yes_price=getattr(se, "min_yes_price", 0.05),
                max_yes_price=getattr(se, "max_yes_price", 0.95),
            )
        )
    lc = cfg.strategies.llm_calibrated
    if lc.enabled:
        calibrator = None
        if llm is not None:
            calibrator = ProbabilityCalibrator(
                llm,
                price_move_bust=cfg.llm.price_move_bust,
                news_context=cfg.llm.news_context,
            )
        strategies.append(
            LLMCalibratedStrategy(
                llm=llm,
                calibrator=calibrator,
                enabled=lc.enabled,
                edge_threshold=lc.edge_threshold,
                min_liquidity_usd=lc.min_liquidity_usd,
                min_confidence=lc.min_confidence,
                min_confidence_no_tools=getattr(lc, "min_confidence_no_tools", 0.55),
                screen_threshold=lc.screen_threshold,
                max_llm_calls_per_poll=lc.max_llm_calls_per_poll,
                max_size_usd=getattr(lc, "max_size_usd", None),
                weight=lc.weight,
            )
        )
    ac = cfg.strategies.arb_cross
    if ac.enabled:
        from chancetime.data_layer.arb_discovery import load_aliases

        file_aliases = load_aliases()
        strategies.append(
            ArbCrossStrategy(
                llm=llm,
                enabled=ac.enabled,
                min_spread=ac.min_spread,
                fee_buffer=ac.fee_buffer,
                min_match_score=ac.min_match_score,
                min_liquidity_usd=ac.min_liquidity_usd,
                emit_hedge_legs=ac.emit_hedge_legs,
                use_llm_match=ac.use_llm_match,
                llm_match_min_confidence=ac.llm_match_min_confidence,
                llm_match_max_each=ac.llm_match_max_each,
                llm_match_band_low=getattr(ac, "llm_match_band_low", 0.40),
                llm_bulk_fallback=getattr(ac, "llm_bulk_fallback", False),
                require_bbo=ac.require_bbo,
                use_executable_prices=ac.use_executable_prices,
                size_by_depth=ac.size_by_depth,
                max_leg_usd=ac.max_leg_usd,
                max_pair_usd=ac.max_pair_usd,
                min_depth_usd=ac.min_depth_usd,
                weight=ac.weight,
                aliases={**file_aliases, **dict(ac.aliases)},
            )
        )
    mr = cfg.strategies.mean_revert
    if mr.enabled:
        strategies.append(
            MeanRevertStrategy(
                enabled=mr.enabled,
                move_threshold=mr.move_threshold,
                min_liquidity_usd=mr.min_liquidity_usd,
                history_window=mr.history_window,
                min_history=mr.min_history,
                weight=mr.weight,
            )
        )
    ni = cfg.strategies.news_impulse
    if ni.enabled:
        strategies.append(
            NewsImpulseStrategy(
                llm=llm,
                news_context=cfg.llm.news_context or ni.news_context,
                enabled=ni.enabled,
                edge_threshold=ni.edge_threshold,
                min_liquidity_usd=ni.min_liquidity_usd,
                min_confidence=ni.min_confidence,
                max_llm_calls_per_poll=ni.max_llm_calls_per_poll,
                weight=ni.weight,
            )
        )
    me = cfg.strategies.ml_edge
    if me.enabled:
        strategies.append(
            MLEdgeStrategy(
                enabled=me.enabled,
                model_path=me.model_path,
                edge_threshold=me.edge_threshold,
                min_liquidity_usd=me.min_liquidity_usd,
                weight=me.weight,
            )
        )
    return strategies
