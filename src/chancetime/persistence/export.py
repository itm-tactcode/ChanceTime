"""Export fills / closed trades for tax-ish bookkeeping (CSV).

Phase 11: ISO timestamps, book name, year filter, summary file.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chancetime.persistence.store import StateStore
from chancetime.utils.paths import project_root, resolve_path


def _resolve_out(path: str | Path) -> Path:
    out = resolve_path(path) if not Path(path).is_absolute() else Path(path)
    if not out.is_absolute():
        out = project_root() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _iso_ts(ts: float | int | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return str(ts)


def _in_year(ts: float | int | None, year: int | None) -> bool:
    if year is None or ts is None:
        return True
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC).year == year
    except (TypeError, ValueError, OSError):
        return True


def export_fills_csv(
    store: StateStore,
    path: str | Path,
    *,
    limit: int = 10_000,
    book: str = "",
    year: int | None = None,
) -> Path:
    """Write fills table to CSV. Returns resolved path."""
    out = _resolve_out(path)
    rows = store.list_fills(limit=limit)
    if year is not None:
        rows = [r for r in rows if _in_year(r.get("ts"), year)]
    fields = [
        "book",
        "order_id",
        "ts",
        "ts_iso",
        "market_id",
        "platform",
        "side",
        "price",
        "size_usd",
        "status",
        "paper",
        "strategy",
        "arb_group_id",
        "note",
        "tax_year",
    ]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            ts = r.get("ts")
            year_v = ""
            try:
                year_v = datetime.fromtimestamp(float(ts), tz=UTC).year if ts else ""
            except (TypeError, ValueError, OSError):
                year_v = ""
            w.writerow(
                {
                    "book": book,
                    "order_id": r.get("order_id", ""),
                    "ts": ts if ts is not None else "",
                    "ts_iso": _iso_ts(ts),  # type: ignore[arg-type]
                    "market_id": r.get("market_id", ""),
                    "platform": r.get("platform", ""),
                    "side": r.get("side", ""),
                    "price": r.get("price", ""),
                    "size_usd": r.get("size_usd", ""),
                    "status": r.get("status", ""),
                    "paper": r.get("paper", ""),
                    "strategy": r.get("strategy", ""),
                    "arb_group_id": r.get("arb_group_id", ""),
                    "note": r.get("note", ""),
                    "tax_year": year_v,
                }
            )
    return out


def export_closed_csv(
    store: StateStore,
    path: str | Path,
    *,
    limit: int = 10_000,
    book: str = "",
    year: int | None = None,
) -> Path:
    out = _resolve_out(path)
    rows = store.list_closed(limit=limit)
    if year is not None:
        rows = [r for r in rows if _in_year(r.get("closed_ts"), year)]
    fields = [
        "book",
        "id",
        "closed_ts",
        "closed_ts_iso",
        "market_id",
        "side",
        "size_usd",
        "entry_price",
        "exit_price",
        "contracts",
        "realized_pnl",
        "reason",
        "strategy",
        "tax_year",
        # Rough 8949-ish aids (not tax advice)
        "proceeds",
        "cost_basis",
        "gain_loss",
    ]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            ts = r.get("closed_ts")
            year_v = ""
            try:
                year_v = datetime.fromtimestamp(float(ts), tz=UTC).year if ts else ""
            except (TypeError, ValueError, OSError):
                year_v = ""
            contracts = float(r.get("contracts") or 0)
            exit_px = float(r.get("exit_price") or 0)
            entry_px = float(r.get("entry_price") or 0)
            # Binary PM: proceeds ≈ contracts * exit_price (or 1/0 at resolve)
            proceeds = contracts * exit_px
            cost = float(r.get("size_usd") or 0)
            pnl = float(r.get("realized_pnl") or 0)
            w.writerow(
                {
                    "book": book,
                    "id": r.get("id", ""),
                    "closed_ts": ts if ts is not None else "",
                    "closed_ts_iso": _iso_ts(ts),  # type: ignore[arg-type]
                    "market_id": r.get("market_id", ""),
                    "side": r.get("side", ""),
                    "size_usd": r.get("size_usd", ""),
                    "entry_price": entry_px,
                    "exit_price": exit_px,
                    "contracts": contracts,
                    "realized_pnl": pnl,
                    "reason": r.get("reason", ""),
                    "strategy": r.get("strategy", ""),
                    "tax_year": year_v,
                    "proceeds": round(proceeds, 4),
                    "cost_basis": round(cost, 4),
                    "gain_loss": round(pnl, 4),
                }
            )
    return out


def export_summary_csv(
    store: StateStore,
    path: str | Path,
    *,
    book: str = "",
    year: int | None = None,
) -> Path:
    """One-row summary for the book (optional year filter on closed PnL)."""
    out = _resolve_out(path)
    summary = store.summary()
    closed = store.list_closed(limit=50_000)
    if year is not None:
        closed = [c for c in closed if _in_year(c.get("closed_ts"), year)]
    realized = sum(float(c.get("realized_pnl") or 0) for c in closed)
    fills = store.list_fills(limit=50_000)
    if year is not None:
        fills = [f for f in fills if _in_year(f.get("ts"), year)]
    row: dict[str, Any] = {
        "book": book,
        "db_path": str(store.path),
        "tax_year": year or "",
        "open_positions": summary.get("open_positions", 0),
        "fills_exported": len(fills),
        "closed_exported": len(closed),
        "realized_pnl_closed": round(realized, 4),
        "realized_pnl_meta": summary.get("realized_pnl_today", ""),
        "exported_at_iso": datetime.now(tz=UTC).isoformat(),
        "disclaimer": "Not tax advice. Verify with a tax professional.",
    }
    fields = list(row.keys())
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow(row)
    return out
