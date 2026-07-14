"""Named market universe profiles — shared fetch, per-strategy filters/queues.

Strategies declare a profile name (e.g. ``broad``, ``short_bbo``, ``dual_list``).
The bot builds each enabled profile once per poll and passes the matching
snapshot into ``generate_signals`` — strategies never open HTTP themselves.
"""

from __future__ import annotations

from typing import Any

from chancetime.data_layer.base import MarketDataClient
from chancetime.data_layer.models import Market
from chancetime.data_layer.universe import (
    DEFAULT_SHORT_QUERIES,
    expand_universe,
    filter_synthetic,
    hours_to_close,
    prioritize_by_close,
)
from chancetime.utils.logging import get_logger

log = get_logger(__name__)

# Built-in names used by default.yaml / strategy defaults
PROFILE_BROAD = "broad"
PROFILE_SHORT_BBO = "short_bbo"
PROFILE_DUAL_LIST = "dual_list"
PROFILE_LLM_SCREEN = "llm_screen"


def default_profile_specs() -> dict[str, dict[str, Any]]:
    """Reasonable built-in profiles (also mirrored in config/default.yaml)."""
    short_q = list(DEFAULT_SHORT_QUERIES)
    return {
        PROFILE_BROAD: {
            "max_markets": 150,
            "prefer_closing_within_hours": 0.0,
            "drop_beyond_prefer": False,
            "keep_unknown_close": True,
            "queries": [],
            "search_limit_per_query": 30,
            "deep_discovery": False,
            "discovery_every_polls": 0,
            "discovery_limit": 0,
        },
        PROFILE_SHORT_BBO: {
            "max_markets": 300,
            "prefer_closing_within_hours": 48.0,
            "drop_beyond_prefer": False,
            "keep_unknown_close": True,
            "queries": short_q,
            "search_limit_per_query": 40,
            "deep_discovery": False,
            "discovery_every_polls": 0,
            "discovery_limit": 0,
        },
        PROFILE_DUAL_LIST: {
            "max_markets": 250,
            # Prefer events within ~30d so 2027 finals don't dominate pairing
            "prefer_closing_within_hours": 720.0,
            "drop_beyond_prefer": True,
            "keep_unknown_close": False,
            "queries": [
                "nba",
                "mlb",
                "fed",
                "election",
                "bitcoin",
                "championship",
            ],
            "search_limit_per_query": 40,
            "deep_discovery": True,
            "discovery_every_polls": 5,
            "discovery_limit": 200,
        },
        PROFILE_LLM_SCREEN: {
            "max_markets": 40,
            "prefer_closing_within_hours": 168.0,
            "drop_beyond_prefer": False,
            "keep_unknown_close": True,
            "queries": ["fed", "election", "rate"],
            "search_limit_per_query": 20,
            "deep_discovery": False,
            "discovery_every_polls": 0,
            "discovery_limit": 0,
        },
    }


def apply_close_filter(
    markets: list[Market],
    *,
    prefer_within_hours: float,
    drop_beyond: bool,
    keep_unknown: bool,
    limit: int,
) -> list[Market]:
    """Soft prefer or hard drop by close_time."""
    if prefer_within_hours <= 0:
        return markets[:limit] if limit > 0 else markets

    if not drop_beyond:
        return prioritize_by_close(
            markets, prefer_within_hours=prefer_within_hours, limit=limit
        )

    soon: list[Market] = []
    unknown: list[Market] = []
    for m in markets:
        h = hours_to_close(m)
        if h is None:
            if keep_unknown:
                unknown.append(m)
            continue
        if 0 <= h <= prefer_within_hours:
            soon.append(m)
    # Sort soon by hours
    soon.sort(key=lambda m: hours_to_close(m) or 1e9)
    ordered = soon + unknown
    seen: set[str] = set()
    out: list[Market] = []
    for m in ordered:
        if m.venue_key in seen:
            continue
        seen.add(m.venue_key)
        out.append(m)
        if len(out) >= limit:
            break
    return out


async def build_universe_profile(
    client: MarketDataClient,
    *,
    name: str,
    max_markets: int,
    prefer_closing_within_hours: float = 0.0,
    drop_beyond_prefer: bool = False,
    keep_unknown_close: bool = True,
    queries: list[str] | None = None,
    search_limit_per_query: int = 40,
    allow_synthetic: bool = False,
) -> list[Market]:
    """Fetch + filter one named profile (no deep dual-list merge — bot does that)."""
    q = list(queries) if queries is not None else []
    if prefer_closing_within_hours > 0 or q:
        markets = await expand_universe(
            client,
            base_limit=max(1, max_markets),
            prefer_within_hours=0.0,  # apply drop/prefer ourselves for control
            short_queries=q,
            search_limit_per_query=search_limit_per_query,
            allow_synthetic=allow_synthetic,
        )
    else:
        markets = await client.list_markets(limit=max(1, max_markets))
        markets = filter_synthetic(markets, allow=allow_synthetic)

    markets = apply_close_filter(
        markets,
        prefer_within_hours=prefer_closing_within_hours,
        drop_beyond=drop_beyond_prefer,
        keep_unknown=keep_unknown_close,
        limit=max(max_markets * 3, max_markets),
    )
    soon_n = sum(
        1
        for m in markets
        if (h := hours_to_close(m)) is not None
        and prefer_closing_within_hours > 0
        and 0 <= h <= prefer_closing_within_hours
    )
    log.info(
        "universe_profile_built",
        profile=name,
        total=len(markets),
        soon=soon_n,
        prefer_hours=prefer_closing_within_hours,
        drop_beyond=drop_beyond_prefer,
        queries=len(q),
    )
    return markets


def merge_market_lists(*lists: list[Market]) -> list[Market]:
    """Union by venue_key, first occurrence wins."""
    seen: set[str] = set()
    out: list[Market] = []
    for lst in lists:
        for m in lst:
            if m.venue_key in seen:
                continue
            seen.add(m.venue_key)
            out.append(m)
    return out
