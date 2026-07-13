"""Persist live fills into SQLite so the dashboard can show them."""

from __future__ import annotations

from chancetime.execution.engine import Fill, OrderStatus
from chancetime.execution.live_kalshi import LiveOrderResult
from chancetime.persistence.store import StateStore
from chancetime.strategies.base import Side
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


def persist_live_result(
    store: StateStore,
    result: LiveOrderResult,
    *,
    market_id: str,
    platform: str,
    side: Side,
    strategy: str = "live_smoke",
) -> Fill | None:
    """Write a live order result to fills (+ open position if filled).

    Returns the stored Fill, or None if store disabled / nothing to record.
    """
    if not store.enabled or not result.ok:
        return None

    status = _map_status(result.status)
    # Skip pure rejects / zero-fill cancels for positions; still log canceled as fill row
    fill = Fill(
        order_id=result.order_id or result.client_order_id,
        market_id=market_id,
        side=side,
        price=result.price,
        size_usd=result.size_usd,
        status=status,
        paper=False,
        note=result.note[:240],
        venue=result.venue or platform,
        contracts=result.contracts,
        raw=result.raw if isinstance(result.raw, dict) else {},
    )
    store.record_fill(fill, strategy=strategy, platform=platform)

    if status in {OrderStatus.FILLED, OrderStatus.SUBMITTED} and _looks_filled(result):
        portfolio = store.load_portfolio()
        # Avoid clobbering if already open on same market_id; simple open if missing
        if market_id not in portfolio.positions:
            entry = result.price if result.price > 0 else 0.5
            notional = result.size_usd
            # Prefer contracts * price when available
            if result.contracts > 0 and entry > 0:
                notional = result.contracts * entry
            portfolio.open_position(
                market_id=market_id,
                platform=platform,
                side=side,
                size_usd=notional,
                entry_price=entry,
                strategy=strategy,
            )
            store.save_portfolio(portfolio)
            log.info(
                "live_book_position_opened",
                market_id=market_id,
                platform=platform,
                size_usd=round(notional, 4),
                entry=round(entry, 4),
            )
        else:
            store.save_portfolio(portfolio)

        # Equity tick for chart. cash_basis left 0 — free cash is venue-sourced
        # via dashboard /api/balances (not paper ledger). Don't invent a bankroll.
        snap = portfolio.equity_snapshot(
            cash_basis=0.0,
            yes_mids={},
        )
        # Mark free_cash as unknown so UIs show "—" instead of false $0
        snap["free_cash_approx"] = None  # type: ignore[assignment]
        snap["available_cash"] = None  # type: ignore[assignment]
        store.record_equity(
            snap,
            poll_count=0,
            paper=False,
            extra={"source": strategy, "cash_model": "venue"},
        )

    log.info(
        "live_book_fill_stored",
        order_id=fill.order_id,
        market_id=market_id,
        status=str(status),
        size_usd=fill.size_usd,
    )
    return fill


def _map_status(raw: str) -> OrderStatus:
    s = (raw or "").lower()
    if s == "filled":
        return OrderStatus.FILLED
    if s in {"canceled_unfilled", "rejected"}:
        return OrderStatus.REJECTED
    if s == "submitted":
        return OrderStatus.SUBMITTED
    return OrderStatus.SUBMITTED


def _looks_filled(result: LiveOrderResult) -> bool:
    if result.status == "filled":
        return True
    note = (result.note or "").lower()
    if "fill_count=" in note:
        # kalshi note: fill_count=12.0
        try:
            part = note.split("fill_count=", 1)[1].split()[0]
            return float(part) > 0
        except (IndexError, ValueError):
            pass
    if "filled_qty=" in note:
        try:
            part = note.split("filled_qty=", 1)[1].split()[0]
            return float(part) > 0
        except (IndexError, ValueError):
            pass
    return False
