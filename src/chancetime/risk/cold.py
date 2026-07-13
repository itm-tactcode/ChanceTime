"""Detect cold strategies from cumulative stats (Phase 8)."""

from __future__ import annotations

from chancetime.persistence.store import StateStore
from chancetime.utils.config import RiskSettings
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


def cold_strategies_from_store(
    store: StateStore,
    settings: RiskSettings,
) -> set[str]:
    """Return strategy names that should be auto-disabled this session.

    Rule (when cold_min_fills > 0):
      fills >= cold_min_fills AND realized_pnl <= cold_max_realized_pnl
    → mark cold (skip new entries).
    """
    if settings.cold_min_fills <= 0:
        return set()
    cold: set[str] = set()
    for row in store.list_strategy_stats():
        name = str(row.get("strategy") or "")
        fills = int(row.get("fills") or 0)
        pnl = float(row.get("realized_pnl") or 0.0)
        if fills >= settings.cold_min_fills and pnl <= settings.cold_max_realized_pnl:
            cold.add(name)
            log.warning(
                "strategy_marked_cold",
                strategy=name,
                fills=fills,
                realized_pnl=round(pnl, 4),
                threshold=settings.cold_max_realized_pnl,
            )
    return cold
