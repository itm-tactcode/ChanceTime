"""Pluggable trading strategies (the item bag)."""

from __future__ import annotations

from chancetime.llm.calibrate import ProbabilityCalibrator
from chancetime.llm.client import GrokClient
from chancetime.strategies.arb_cross import ArbCrossStrategy
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.strategies.complement_arb import ComplementArbStrategy
from chancetime.strategies.llm_calibrated import LLMCalibratedStrategy
from chancetime.strategies.mean_revert import MeanRevertStrategy
from chancetime.strategies.ml_edge import MLEdgeStrategy
from chancetime.strategies.news_impulse import NewsImpulseStrategy
from chancetime.strategies.research_loggers import (
    MatchQualityStrategy,
    PairGapTrackerStrategy,
    PriceBucketsStrategy,
    TteBucketsStrategy,
)
from chancetime.strategies.simple_edge import SimpleEdgeStrategy
from chancetime.utils.config import AppConfig

__all__ = [
    "ArbCrossStrategy",
    "BaseStrategy",
    "ComplementArbStrategy",
    "LLMCalibratedStrategy",
    "MLEdgeStrategy",
    "MatchQualityStrategy",
    "MeanRevertStrategy",
    "NewsImpulseStrategy",
    "PairGapTrackerStrategy",
    "PriceBucketsStrategy",
    "Side",
    "Signal",
    "SimpleEdgeStrategy",
    "TteBucketsStrategy",
    "build_strategies",
    "strategy_open_limits_from_config",
    "strategy_size_caps_from_config",
    "strategy_weights_from_config",
]

_STRATEGY_ATTRS = (
    ("simple_edge", "simple_edge"),
    ("llm_calibrated", "llm_calibrated"),
    ("arb_cross", "arb_cross"),
    ("complement_arb", "complement_arb"),
    ("mean_revert", "mean_revert"),
    ("news_impulse", "news_impulse"),
    ("ml_edge", "ml_edge"),
    ("pair_gap_tracker", "pair_gap_tracker"),
    ("tte_buckets", "tte_buckets"),
    ("price_buckets", "price_buckets"),
    ("match_quality", "match_quality"),
)


def strategy_weights_from_config(cfg: AppConfig) -> dict[str, float]:
    s = cfg.strategies
    return {name: getattr(s, attr).weight for name, attr in _STRATEGY_ATTRS}


def strategy_open_limits_from_config(cfg: AppConfig) -> dict[str, int]:
    """Per-strategy open caps. Missing/None → risk.max_open_per_strategy; 0 → unlimited."""
    default = int(cfg.risk.max_open_per_strategy)
    s = cfg.strategies
    out: dict[str, int] = {}
    for name, attr in _STRATEGY_ATTRS:
        st = getattr(s, attr)
        raw = getattr(st, "max_open", None)
        out[name] = default if raw is None else int(raw)
    return out


def strategy_size_caps_from_config(cfg: AppConfig) -> dict[str, float]:
    """Optional per-strategy max size_usd (empty if unset)."""
    s = cfg.strategies
    out: dict[str, float] = {}
    for name, attr in _STRATEGY_ATTRS:
        st = getattr(s, attr)
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
                universe=getattr(se, "universe", "broad"),
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
                universe=getattr(lc, "universe", "llm_screen"),
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
                universe=getattr(ac, "universe", "dual_list"),
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
    ca = cfg.strategies.complement_arb
    if ca.enabled:
        strategies.append(
            ComplementArbStrategy(
                enabled=ca.enabled,
                universe=getattr(ca, "universe", "short_bbo"),
                min_edge=ca.min_edge,
                fee_buffer=ca.fee_buffer,
                require_bbo=ca.require_bbo,
                min_depth_usd=ca.min_depth_usd,
                max_leg_usd=ca.max_leg_usd,
                max_pair_usd=ca.max_pair_usd,
                min_liquidity_usd=ca.min_liquidity_usd,
                size_by_depth=ca.size_by_depth,
                reject_synthetic=ca.reject_synthetic,
                max_hours_to_close=ca.max_hours_to_close,
                weight=ca.weight,
            )
        )
    mr = cfg.strategies.mean_revert
    if mr.enabled:
        strategies.append(
            MeanRevertStrategy(
                enabled=mr.enabled,
                universe=getattr(mr, "universe", "broad"),
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
                universe=getattr(ni, "universe", "llm_screen"),
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
                universe=getattr(me, "universe", "broad"),
                model_path=me.model_path,
                edge_threshold=me.edge_threshold,
                min_liquidity_usd=me.min_liquidity_usd,
                weight=me.weight,
            )
        )
    # --- Log-only research (never emit fills) ---
    pg = cfg.strategies.pair_gap_tracker
    if pg.enabled:
        strategies.append(
            PairGapTrackerStrategy(
                enabled=pg.enabled,
                universe=pg.universe,
                min_match_score=pg.min_match_score,
                fee_buffer=pg.fee_buffer,
                top_n=pg.top_n,
                log_name=pg.log_name,
                weight=pg.weight,
            )
        )
    tb = cfg.strategies.tte_buckets
    if tb.enabled:
        strategies.append(
            TteBucketsStrategy(
                enabled=tb.enabled,
                universe=tb.universe,
                max_rows=tb.max_rows,
                log_name=tb.log_name,
                weight=tb.weight,
            )
        )
    pb = cfg.strategies.price_buckets
    if pb.enabled:
        strategies.append(
            PriceBucketsStrategy(
                enabled=pb.enabled,
                universe=pb.universe,
                max_rows=pb.max_rows,
                log_name=pb.log_name,
                weight=pb.weight,
            )
        )
    mq = cfg.strategies.match_quality
    if mq.enabled:
        strategies.append(
            MatchQualityStrategy(
                enabled=mq.enabled,
                universe=mq.universe,
                min_match_score=mq.min_match_score,
                long_tte_hours=mq.long_tte_hours,
                top_n=mq.top_n,
                log_name=mq.log_name,
                weight=mq.weight,
            )
        )
    return strategies
