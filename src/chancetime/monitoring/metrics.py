"""Lightweight metrics helpers for logs + SQLite equity series.

No Prometheus dependency — JSON-friendly snapshots for dashboard / digests.
"""

from __future__ import annotations

from typing import Any

from chancetime.persistence.store import StateStore
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


def build_poll_metrics(
    *,
    snap: dict[str, float],
    generated: int,
    approved: int,
    filled: int,
    llm_spent: float | None = None,
    llm_remaining: float | None = None,
    strategies: list[str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "equity": round(float(snap.get("equity", 0.0)), 4),
        "realized_pnl_today": round(float(snap.get("realized_pnl_today", 0.0)), 4),
        "unrealized_pnl": round(float(snap.get("unrealized_pnl", 0.0)), 4),
        "exposure_usd": round(float(snap.get("exposure_usd", 0.0)), 2),
        "open_positions": int(snap.get("open_positions", 0)),
        "signals_generated": generated,
        "signals_approved": approved,
        "fills": filled,
        "llm_spent_usd": llm_spent,
        "llm_remaining_usd": llm_remaining,
        "strategies": strategies or [],
    }
    if "free_cash_approx" in snap:
        out["free_cash_approx"] = round(float(snap["free_cash_approx"]), 4)
    if "position_mtm" in snap:
        out["position_mtm"] = round(float(snap["position_mtm"]), 4)
    return out


def log_and_store_poll(
    store: StateStore | None,
    *,
    snap: dict[str, float],
    poll_count: int,
    paper: bool,
    generated: int,
    approved: int,
    filled: int,
    llm_spent: float | None = None,
    llm_remaining: float | None = None,
    strategies: list[str] | None = None,
    strategy_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    metrics = build_poll_metrics(
        snap=snap,
        generated=generated,
        approved=approved,
        filled=filled,
        llm_spent=llm_spent,
        llm_remaining=llm_remaining,
        strategies=strategies,
    )
    log.info("metrics_poll", **metrics)
    if store is not None and store.enabled:
        extra: dict[str, Any] = {
            "llm_spent_usd": llm_spent,
            "llm_remaining_usd": llm_remaining,
            "fills": filled,
        }
        for key in ("free_cash_approx", "position_mtm"):
            if key in snap:
                extra[key] = round(float(snap[key]), 4)
        store.record_equity(
            snap,
            poll_count=poll_count,
            paper=paper,
            extra=extra,
        )
        store.record_signal_stats(
            generated=generated,
            approved=approved,
            filled=filled,
            strategy_counts=strategy_counts,
        )
    return metrics
