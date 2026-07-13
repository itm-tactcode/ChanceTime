"""Pair-only BBO enrichment for cross-venue arb.

Only refresh bid/ask on markets that already matched as dual listings —
avoids hammering every open market with orderbook calls.
"""

from __future__ import annotations

from typing import Protocol

from chancetime.data_layer.matching import MarketPair
from chancetime.data_layer.models import Market, Platform
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class BboEnrichable(Protocol):
    async def enrich_bbo_markets(self, markets: list[Market]) -> list[Market]:
        """Return markets with has_bbo / bid-ask updated where possible."""
        ...


async def enrich_pairs_bbo(
    pairs: list[MarketPair],
    *,
    kalshi: BboEnrichable | None = None,
    polymarket: BboEnrichable | None = None,
) -> list[MarketPair]:
    """Fetch BBO only for legs in ``pairs``; return pairs with refreshed markets."""
    if not pairs:
        return pairs

    by_plat: dict[Platform, dict[str, Market]] = {}
    for p in pairs:
        for m in (p.left, p.right):
            by_plat.setdefault(m.platform, {})[m.venue_key] = m

    updated: dict[str, Market] = {}

    k_list = list(by_plat.get(Platform.KALSHI, {}).values())
    if kalshi is not None and k_list:
        enriched = await kalshi.enrich_bbo_markets(k_list)
        for m in enriched:
            updated[m.venue_key] = m

    p_list = list(by_plat.get(Platform.POLYMARKET, {}).values())
    if polymarket is not None and p_list:
        enriched = await polymarket.enrich_bbo_markets(p_list)
        for m in enriched:
            updated[m.venue_key] = m

    out: list[MarketPair] = []
    for p in pairs:
        left = updated.get(p.left.venue_key, p.left)
        right = updated.get(p.right.venue_key, p.right)
        out.append(MarketPair(left=left, right=right, score=p.score))

    n_bbo = sum(1 for p in out if p.left.has_bbo) + sum(1 for p in out if p.right.has_bbo)
    log.info(
        "pairs_bbo_enriched",
        pairs=len(out),
        legs_with_bbo=n_bbo,
        kalshi_legs=len(k_list),
        polymarket_legs=len(p_list),
    )
    return out


def apply_bbo_to_market_list(markets: list[Market], pairs: list[MarketPair]) -> list[Market]:
    """Replace markets in ``markets`` with BBO-updated legs from pairs (by venue_key)."""
    by_key = {m.venue_key: m for m in markets}
    for p in pairs:
        by_key[p.left.venue_key] = p.left
        by_key[p.right.venue_key] = p.right
    return list(by_key.values())
