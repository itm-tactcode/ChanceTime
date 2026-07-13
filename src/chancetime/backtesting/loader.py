"""Load historical / synthetic market bars for backtests."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from chancetime.backtesting.models import MarketBar, ResolveOutcome
from chancetime.utils.paths import resolve_path


def _parse_ts(raw: str) -> datetime:
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _parse_resolve(raw: str | None) -> ResolveOutcome:
    if raw is None or str(raw).strip() == "":
        return ResolveOutcome.OPEN
    v = str(raw).strip().lower()
    if v in {"1", "yes", "y", "true"}:
        return ResolveOutcome.YES
    if v in {"0", "no", "n", "false"}:
        return ResolveOutcome.NO
    if v in {"open", "none", "nan"}:
        return ResolveOutcome.OPEN
    raise ValueError(f"Unknown resolve value: {raw!r}")


def load_bars_csv(path: str | Path) -> list[MarketBar]:
    """Load bars from CSV.

    Required columns: ``ts``, ``market_id``, ``yes_price``
    Optional: ``liquidity_usd``, ``title``, ``platform``, ``resolve``
    """
    resolved = resolve_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"Fixture not found: {resolved}")

    bars: list[MarketBar] = []
    with resolved.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "ts" not in reader.fieldnames:
            raise ValueError("CSV must have headers including ts, market_id, yes_price")
        for row in reader:
            yes = float(row["yes_price"])
            if not 0.0 <= yes <= 1.0:
                raise ValueError(f"yes_price out of range on {row}: {yes}")
            def _opt_f(key: str) -> float | None:
                raw = row.get(key)
                if raw is None or str(raw).strip() == "":
                    return None
                return float(raw)

            has_bbo = str(row.get("has_bbo") or "").strip().lower() in {
                "1",
                "true",
                "yes",
                "y",
            }
            yb, ya = _opt_f("yes_bid"), _opt_f("yes_ask")
            if yb is not None or ya is not None:
                has_bbo = True
            bars.append(
                MarketBar(
                    ts=_parse_ts(row["ts"]),
                    market_id=str(row["market_id"]).strip(),
                    yes_price=yes,
                    liquidity_usd=float(row.get("liquidity_usd") or 0),
                    title=str(row.get("title") or row["market_id"]),
                    platform=str(row.get("platform") or "fixture"),
                    resolve=_parse_resolve(row.get("resolve")),
                    yes_bid=yb,
                    yes_ask=ya,
                    yes_bid_size=_opt_f("yes_bid_size"),
                    yes_ask_size=_opt_f("yes_ask_size"),
                    has_bbo=has_bbo,
                    volume_usd=float(row.get("volume_usd") or 0),
                )
            )
    bars.sort(key=lambda b: (b.ts, b.market_id))
    return bars
