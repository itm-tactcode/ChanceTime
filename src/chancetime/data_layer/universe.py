"""Expanded / short-horizon market universe helpers.

Page-1 open books on Kalshi are sports-heavy. Complement arb and short-dated
scans need extra search queries and close-time prioritization — without LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from chancetime.data_layer.base import MarketDataClient
from chancetime.data_layer.models import Market
from chancetime.utils.logging import get_logger

log = get_logger(__name__)

# Default queries to surface crypto / short windows (venue-dependent coverage)
DEFAULT_SHORT_QUERIES: tuple[str, ...] = (
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "crypto",
    "up or down",
    "15 min",
    "5 min",
    "hourly",
)


def hours_to_close(m: Market, *, now: datetime | None = None) -> float | None:
    if m.close_time is None:
        return None
    now = now or datetime.now(UTC)
    ct = m.close_time
    if ct.tzinfo is None:
        ct = ct.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return (ct - now).total_seconds() / 3600.0


def filter_synthetic(markets: list[Market], *, allow: bool) -> list[Market]:
    """Drop mock fixtures when running against live sources."""
    if allow:
        return markets
    real = [m for m in markets if not m.synthetic]
    dropped = len(markets) - len(real)
    if dropped:
        log.warning(
            "synthetic_markets_dropped",
            dropped=dropped,
            kept=len(real),
            msg="Mock fixtures excluded from live data.source feed",
        )
    return real


def prioritize_by_close(
    markets: list[Market],
    *,
    prefer_within_hours: float,
    limit: int,
) -> list[Market]:
    """Prefer markets closing soon; keep others to fill remaining slots."""
    if prefer_within_hours <= 0 or limit <= 0:
        return markets[:limit] if limit else markets

    now = datetime.now(UTC)
    soon: list[tuple[float, Market]] = []
    later: list[Market] = []
    unknown: list[Market] = []
    for m in markets:
        h = hours_to_close(m, now=now)
        if h is None:
            unknown.append(m)
        elif 0 <= h <= prefer_within_hours:
            soon.append((h, m))
        else:
            later.append(m)
    soon.sort(key=lambda x: x[0])
    ordered = [m for _, m in soon] + later + unknown
    # Dedupe by venue_key preserving order
    seen: set[str] = set()
    out: list[Market] = []
    for m in ordered:
        k = m.venue_key
        if k in seen:
            continue
        seen.add(k)
        out.append(m)
        if len(out) >= limit:
            break
    return out


async def expand_universe(
    client: MarketDataClient,
    *,
    base_limit: int,
    prefer_within_hours: float = 0.0,
    short_queries: list[str] | None = None,
    search_limit_per_query: int = 40,
    allow_synthetic: bool = False,
) -> list[Market]:
    """List markets + optional search enrichment, then prioritize by close time.

    Works with single venue or composite clients that expose ``list_markets``
    and optionally ``search_markets`` on children.
    """
    base = await client.list_markets(limit=max(1, base_limit))
    by_key: dict[str, Market] = {m.venue_key: m for m in base}

    queries = short_queries if short_queries is not None else list(DEFAULT_SHORT_QUERIES)
    if queries:
        await _search_into(client, queries, search_limit_per_query, by_key)

    markets = list(by_key.values())
    markets = filter_synthetic(markets, allow=allow_synthetic)
    soft_cap = max(base_limit * 3, base_limit)
    if prefer_within_hours > 0:
        markets = prioritize_by_close(
            markets, prefer_within_hours=prefer_within_hours, limit=soft_cap
        )
    else:
        markets = markets[:soft_cap]

    soon_n = 0
    if prefer_within_hours > 0:
        for m in markets:
            h = hours_to_close(m)
            if h is not None and 0 <= h <= prefer_within_hours:
                soon_n += 1
    log.info(
        "universe_expanded",
        total=len(markets),
        base=len(base),
        soon=soon_n,
        prefer_hours=prefer_within_hours,
        queries=len(queries),
    )
    return markets


async def _search_into(
    client: MarketDataClient,
    queries: list[str],
    limit_per: int,
    by_key: dict[str, Market],
) -> None:
    """Run search on client or each composite child."""
    children: list[Any] = (
        list(client.clients or []) if hasattr(client, "clients") else [client]
    )

    for child in children:
        search = getattr(child, "search_markets", None)
        if search is None:
            continue
        for q in queries:
            q = (q or "").strip()
            if not q:
                continue
            try:
                found = await search(q, limit=limit_per)
            except Exception:
                log.exception("universe_search_failed", query=q, client=type(child).__name__)
                continue
            for m in found:
                by_key.setdefault(m.venue_key, m)
