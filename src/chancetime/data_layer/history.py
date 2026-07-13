"""Append-only market history recorder (JSONL) for Phase 10 replay/backtests.

Each line is one market snapshot at a poll/export time. Path defaults to
``data/history/markets-YYYYMMDD.jsonl`` under the project root.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from chancetime.data_layer.models import Market
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root, resolve_path

log = get_logger(__name__)


def default_history_dir() -> Path:
    return project_root() / "data" / "history"


def history_path_for_day(
    day: datetime | None = None,
    *,
    directory: str | Path | None = None,
) -> Path:
    d = day or datetime.now(tz=UTC)
    base = resolve_path(directory) if directory else default_history_dir()
    return base / f"markets-{d.strftime('%Y%m%d')}.jsonl"


@dataclass
class MarketHistoryRecorder:
    """Thread-safe enough for single-bot asyncio (sync file append)."""

    path: Path
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(
        cls,
        *,
        enabled: bool = True,
        directory: str | Path | None = None,
        filename: str | None = None,
    ) -> MarketHistoryRecorder:
        if filename:
            path = resolve_path(directory or default_history_dir()) / filename
        else:
            path = history_path_for_day(directory=directory)
        return cls(path=path, enabled=enabled)

    def record_markets(
        self,
        markets: Iterable[Market],
        *,
        source: str = "",
        poll: int | None = None,
        ts: float | None = None,
    ) -> int:
        if not self.enabled:
            return 0
        stamp = ts if ts is not None else time.time()
        iso = datetime.fromtimestamp(stamp, tz=UTC).isoformat()
        n = 0
        with self.path.open("a", encoding="utf-8") as f:
            for m in markets:
                row = market_to_history_row(m, ts=stamp, ts_iso=iso, source=source, poll=poll)
                f.write(json.dumps(row, default=str) + "\n")
                n += 1
        if n:
            log.info("history_recorded", path=str(self.path), rows=n, source=source)
        return n


def market_to_history_row(
    m: Market,
    *,
    ts: float,
    ts_iso: str,
    source: str = "",
    poll: int | None = None,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "ts_iso": ts_iso,
        "source": source,
        "poll": poll,
        "market_id": m.id,
        "platform": str(m.platform),
        "title": m.title,
        "yes_price": m.yes_price,
        "no_price": m.no_price,
        "volume_usd": m.volume_usd,
        "liquidity_usd": m.liquidity_usd,
        "yes_bid": m.yes_bid,
        "yes_ask": m.yes_ask,
        "yes_bid_size": m.yes_bid_size,
        "yes_ask_size": m.yes_ask_size,
        "has_bbo": m.has_bbo,
        "canonical_key": m.canonical_key,
        "slug": m.slug,
        "url": m.url,
    }


def load_history_jsonl(path: str | Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Load history rows (newest-last order as written)."""
    p = resolve_path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    rows: list[dict[str, Any]] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def load_bars_from_history(
    history_path: str | Path,
    *,
    market_ids: set[str] | None = None,
    platforms: set[str] | None = None,
) -> list:
    """Load JSONL history as backtest ``MarketBar`` list (multi-venue aware)."""
    from chancetime.backtesting.loader import _parse_ts
    from chancetime.backtesting.models import MarketBar, ResolveOutcome

    rows = load_history_jsonl(history_path)
    bars: list[MarketBar] = []
    for r in rows:
        mid = str(r.get("market_id", ""))
        if market_ids is not None and mid not in market_ids:
            continue
        plat = str(r.get("platform") or "history")
        if platforms is not None and plat not in platforms:
            continue
        yes = float(r.get("yes_price") or 0)
        if not 0.0 <= yes <= 1.0:
            continue
        ts_raw = r.get("ts_iso") or r.get("ts")
        if ts_raw is None:
            continue
        if isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(float(ts_raw), tz=UTC)
        else:
            ts = _parse_ts(str(ts_raw))
        yb = r.get("yes_bid")
        ya = r.get("yes_ask")
        bars.append(
            MarketBar(
                ts=ts,
                market_id=mid,
                yes_price=yes,
                liquidity_usd=float(r.get("liquidity_usd") or 0),
                title=str(r.get("title") or mid),
                platform=plat,
                resolve=ResolveOutcome.OPEN,
                yes_bid=float(yb) if yb is not None else None,
                yes_ask=float(ya) if ya is not None else None,
                yes_bid_size=float(r["yes_bid_size"])
                if r.get("yes_bid_size") is not None
                else None,
                yes_ask_size=float(r["yes_ask_size"])
                if r.get("yes_ask_size") is not None
                else None,
                has_bbo=bool(r.get("has_bbo")),
                volume_usd=float(r.get("volume_usd") or 0),
            )
        )
    bars.sort(key=lambda b: (b.ts, b.platform, b.market_id))
    return bars


def list_history_files(directory: str | Path | None = None) -> list[Path]:
    base = resolve_path(directory) if directory else default_history_dir()
    if not base.is_dir():
        return []
    return sorted(base.glob("markets-*.jsonl"))


def history_to_bars_csv(
    history_path: str | Path,
    out_csv: str | Path,
    *,
    market_ids: set[str] | None = None,
) -> Path:
    """Convert JSONL history to backtest CSV (includes BBO/depth columns)."""
    import csv

    rows = load_history_jsonl(history_path)
    out = resolve_path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ts",
        "market_id",
        "yes_price",
        "liquidity_usd",
        "title",
        "platform",
        "resolve",
        "yes_bid",
        "yes_ask",
        "yes_bid_size",
        "yes_ask_size",
        "has_bbo",
        "volume_usd",
    ]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            mid = str(r.get("market_id", ""))
            if market_ids is not None and mid not in market_ids:
                continue
            w.writerow(
                {
                    "ts": r.get("ts_iso") or r.get("ts"),
                    "market_id": mid,
                    "yes_price": r.get("yes_price"),
                    "liquidity_usd": r.get("liquidity_usd") or 0,
                    "title": r.get("title") or mid,
                    "platform": r.get("platform") or "history",
                    "resolve": "",
                    "yes_bid": r.get("yes_bid") if r.get("yes_bid") is not None else "",
                    "yes_ask": r.get("yes_ask") if r.get("yes_ask") is not None else "",
                    "yes_bid_size": r.get("yes_bid_size")
                    if r.get("yes_bid_size") is not None
                    else "",
                    "yes_ask_size": r.get("yes_ask_size")
                    if r.get("yes_ask_size") is not None
                    else "",
                    "has_bbo": 1 if r.get("has_bbo") else 0,
                    "volume_usd": r.get("volume_usd") or 0,
                }
            )
    return out
