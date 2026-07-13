"""Sync venue positions into local SQLite portfolio book."""

from __future__ import annotations

from typing import Any

from chancetime.persistence.store import StateStore
from chancetime.risk.portfolio import Portfolio
from chancetime.strategies.base import Side
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


def apply_venue_positions(
    store: StateStore,
    rows: list[dict[str, Any]],
    *,
    replace_platforms: set[str] | None = None,
) -> Portfolio:
    """Merge venue position snapshots into the local portfolio.

    ``replace_platforms``: drop local open positions on those platforms before
    inserting venue rows (keeps mock/paper other platforms intact).
    """
    portfolio = store.load_portfolio() if store.enabled else Portfolio()
    replace = replace_platforms or {str(r.get("platform") or "") for r in rows}

    # Drop local opens for platforms we are fully refreshing from venue
    for mid, pos in list(portfolio.positions.items()):
        if pos.platform in replace:
            del portfolio.positions[mid]

    for r in rows:
        platform = str(r.get("platform") or "")
        market_id = str(r.get("market_id") or "")
        if not platform or not market_id:
            continue
        contracts = float(r.get("contracts") or 0)
        if contracts <= 0:
            continue
        side = Side.YES if str(r.get("side") or "yes").lower() == "yes" else Side.NO
        # Prefer venue cost/notional when provided (avoid bogus 0.50 default entry)
        entry = r.get("entry_price")
        size_hint = r.get("size_usd") or r.get("notional_usd") or r.get("cost_usd")
        if entry is not None:
            entry_f = max(0.01, min(0.99, float(entry)))
        elif size_hint is not None and contracts > 0:
            entry_f = max(0.01, min(0.99, float(size_hint) / contracts))
        else:
            entry_f = 0.5
        if size_hint is not None:
            size_usd = abs(float(size_hint))
        else:
            size_usd = abs(contracts * entry_f)
        portfolio.open_position(
            market_id=market_id,
            platform=platform,
            side=side,
            size_usd=size_usd,
            entry_price=entry_f,
            strategy=str(r.get("strategy") or "venue_sync"),
            contracts=contracts,
        )

    if store.enabled:
        store.save_portfolio(portfolio)
    log.info(
        "venue_positions_synced",
        n_rows=len(rows),
        open=portfolio.open_count,
        platforms=sorted(replace),
    )
    return portfolio
