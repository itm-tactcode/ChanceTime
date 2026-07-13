"""Daily digest: P&L, fills, positions, optional Telegram send (Phase 11)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chancetime.persistence.store import StateStore
from chancetime.utils.paths import project_root, resolve_path


@dataclass
class DigestReport:
    account: str
    book_path: str
    day: str
    open_positions: int
    fills_today: int
    closed_today: int
    realized_pnl_today: float
    equity: float | None
    unrealized_pnl: float | None
    exposure_usd: float | None
    fill_notional_today: float
    text: str


def _day_bounds(day: datetime | None = None) -> tuple[float, float, str]:
    d = day or datetime.now(tz=UTC)
    start = datetime(d.year, d.month, d.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp(), start.strftime("%Y-%m-%d")


def build_digest(
    store: StateStore,
    *,
    account: str = "paper",
    day: datetime | None = None,
) -> DigestReport:
    """Build a human-readable daily digest from SQLite book."""
    t0, t1, day_s = _day_bounds(day)
    summary = store.summary() if store.enabled else {}
    fills = store.list_fills(limit=5_000) if store.enabled else []
    closed = store.list_closed(limit=5_000) if store.enabled else []

    fills_today = [f for f in fills if t0 <= float(f.get("ts") or 0) < t1]
    closed_today = [c for c in closed if t0 <= float(c.get("closed_ts") or 0) < t1]
    notional = sum(float(f.get("size_usd") or 0) for f in fills_today)
    realized_closed = sum(float(c.get("realized_pnl") or 0) for c in closed_today)
    # Prefer meta/summary realized when present
    realized = float(summary.get("realized_pnl_today") or realized_closed or 0)

    le = summary.get("last_equity") or {}
    equity = le.get("equity")
    unreal = le.get("unrealized_pnl")
    exposure = le.get("exposure_usd")
    open_n = int(summary.get("open_positions") or 0)

    lines = [
        f"Chance Time digest — {day_s}",
        f"account={account}  book={store.path if store.enabled else 'n/a'}",
        f"open_positions={open_n}",
        f"fills_today={len(fills_today)}  notional=${notional:.2f}",
        f"closed_today={len(closed_today)}  realized≈${realized:.2f}",
    ]
    if equity is not None:
        lines.append(
            f"equity=${float(equity):.2f}  unrealized=${float(unreal or 0):.2f}  "
            f"exposure=${float(exposure or 0):.2f}"
        )
    if fills_today:
        lines.append("recent fills:")
        for f in fills_today[:8]:
            lines.append(
                f"  · {f.get('market_id')} {f.get('side')} "
                f"@ {float(f.get('price') or 0):.3f} ${float(f.get('size_usd') or 0):.2f} "
                f"[{f.get('strategy') or '—'}] paper={f.get('paper')}"
            )
    if closed_today:
        lines.append("closed today:")
        for c in closed_today[:8]:
            lines.append(
                f"  · {c.get('market_id')} pnl=${float(c.get('realized_pnl') or 0):.2f} "
                f"({c.get('reason')})"
            )
    # Phase 20: edge-after-cost scorecard block
    try:
        from chancetime.monitoring.scorecard import build_edge_scorecard

        card = build_edge_scorecard(store, account=account)
        lines.append("")
        lines.extend(card.summary_lines())
    except Exception:
        pass
    text = "\n".join(lines)
    return DigestReport(
        account=account,
        book_path=str(store.path) if store.enabled else "",
        day=day_s,
        open_positions=open_n,
        fills_today=len(fills_today),
        closed_today=len(closed_today),
        realized_pnl_today=realized,
        equity=float(equity) if equity is not None else None,
        unrealized_pnl=float(unreal) if unreal is not None else None,
        exposure_usd=float(exposure) if exposure is not None else None,
        fill_notional_today=notional,
        text=text,
    )


def write_digest_file(report: DigestReport, directory: str | Path = "data/digests") -> Path:
    base = resolve_path(directory)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"digest-{report.account}-{report.day}.txt"
    path.write_text(report.text + "\n", encoding="utf-8")
    return path


async def send_digest(
    report: DigestReport,
    *,
    telegram_bot_token: str | None,
    telegram_chat_id: str | None,
) -> bool:
    """Send digest via Telegram if credentials present. Returns True if sent."""
    from chancetime.monitoring.alerts import build_alerter

    alerter = build_alerter(
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
    )
    await alerter.send(report.text, level="info")
    # True if more than log sink — crude but fine
    return bool(telegram_bot_token and telegram_chat_id)


def digest_to_dict(report: DigestReport) -> dict[str, Any]:
    return {
        "account": report.account,
        "book_path": report.book_path,
        "day": report.day,
        "open_positions": report.open_positions,
        "fills_today": report.fills_today,
        "closed_today": report.closed_today,
        "realized_pnl_today": report.realized_pnl_today,
        "equity": report.equity,
        "unrealized_pnl": report.unrealized_pnl,
        "exposure_usd": report.exposure_usd,
        "fill_notional_today": report.fill_notional_today,
        "text": report.text,
        "ts": time.time(),
    }
