"""Deep cross-venue arb discovery: paginate, dual search, persist aliases.

Open-book first pages rarely overlap (Kalshi sports props vs PM futures).
Real dual listings live under series filters (Kalshi) and search (PM US).
Discovery pulls both venues with the **same query set** so matching has a
chance at same-event pairs — edge thresholds stay with the strategy.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chancetime.data_layer.kalshi import KalshiClient
from chancetime.data_layer.matching import MarketPair
from chancetime.data_layer.models import Market
from chancetime.data_layer.polymarket_us import PolymarketUSClient
from chancetime.llm.client import GrokClient
from chancetime.llm.match_venues import hybrid_pair_markets
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root, resolve_path

log = get_logger(__name__)

# Seed queries aimed at categories both venues often list
DEFAULT_DISCOVERY_QUERIES: tuple[str, ...] = (
    "world series",
    "national league",
    "american league",
    "super bowl",
    "nba champion",
    "nba finals",
    "nfl",
    "fed rate",
    "interest rate",
    "rate cut",
    "bitcoin",
    "ethereum",
    "election",
    "president",
    "recession",
    "inflation",
    "all-star",
    "mvp",
)

# Direct Kalshi series enrichment (works even when title scan fails)
CORE_KALSHI_SERIES: tuple[str, ...] = (
    "KXNBA",
    "KXMLBWS",
    "KXWSAL",
    "KXWSNL",
    "KXFED",
    "KXRATECUT",
    "KXBTCMAXY",
    "KXBTCD",
    "KXBTC",
    "KXETHMAXY",
    "KXETHD",
)


@dataclass
class DiscoveryResult:
    kalshi: list[Market]
    polymarket: list[Market]
    pairs: list[MarketPair]
    queries_used: list[str] = field(default_factory=list)
    aliases_saved: int = 0


def load_aliases(path: str | Path = "config/arb_aliases.json") -> dict[str, str]:
    p = resolve_path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("arb_aliases_load_failed", error=str(exc))
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def save_aliases(
    aliases: dict[str, str],
    path: str | Path = "config/arb_aliases.json",
    *,
    merge: bool = True,
) -> Path:
    p = resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load_aliases(p) if merge else {}
    existing.update(aliases)
    p.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info("arb_aliases_saved", path=str(p), count=len(existing))
    return p


def pairs_to_aliases(pairs: list[MarketPair], *, min_score: float = 0.85) -> dict[str, str]:
    """Persist high-confidence pairs as kalshi_id -> polymarket_id."""
    out: dict[str, str] = {}
    for p in pairs:
        if p.score < min_score:
            continue
        out[p.left.id] = p.right.id
        # also store reverse for convenience
        out[f"pm:{p.right.id}"] = p.left.id
    return out


async def deep_discover(
    kalshi: KalshiClient,
    polymarket: PolymarketUSClient,
    *,
    limit_per_venue: int = 250,
    queries: list[str] | None = None,
    min_score: float = 0.62,
    aliases: dict[str, str] | None = None,
    llm: GrokClient | None = None,
    use_llm_match: bool = False,
    llm_match_min_confidence: float = 0.75,
    llm_match_max_each: int = 40,
    llm_match_band_low: float = 0.40,
    llm_bulk_fallback: bool = False,
    search_limit: int = 40,
) -> DiscoveryResult:
    """Fetch deep open books + dual-venue search, then pair.

    Both venues are search-enriched with the same queries. Kalshi also gets
    direct series pulls for known dual-list series (NBA champ, BTC ladders, Fed).
    """
    queries = list(queries) if queries is not None else list(DEFAULT_DISCOVERY_QUERIES)
    aliases = {**(load_aliases()), **(aliases or {})}

    k_markets = await kalshi.list_markets(limit=limit_per_venue)
    p_markets = await polymarket.list_markets(limit=limit_per_venue)

    seen_k = {m.id for m in k_markets}
    seen_pm = {m.id for m in p_markets}

    def _merge_k(batch: list[Market]) -> None:
        for m in batch:
            if m.id not in seen_k:
                k_markets.append(m)
                seen_k.add(m.id)

    def _merge_pm(batch: list[Market]) -> None:
        for m in batch:
            if m.id not in seen_pm:
                p_markets.append(m)
                seen_pm.add(m.id)

    # Dual-venue same-query enrichment (critical for catalog surface mismatch)
    for q in queries:
        found_pm = await polymarket.search_markets(q, limit=search_limit)
        _merge_pm(found_pm)
        found_k = await kalshi.search_markets(q, limit=search_limit)
        _merge_k(found_k)
        # Kalshi rate-limits aggressive series+list scans
        await asyncio.sleep(0.12)

    # Direct series pull — independent of title tokens on open book
    for series in CORE_KALSHI_SERIES:
        found_k = await kalshi.search_markets(series, limit=search_limit)
        _merge_k(found_k)
        await asyncio.sleep(0.12)

    log.info(
        "arb_discovery_pool",
        kalshi=len(k_markets),
        polymarket=len(p_markets),
        queries=len(queries),
        series=len(CORE_KALSHI_SERIES),
    )

    # Prefer focused pools for LLM band when catalogs are huge
    k_for_match = k_markets
    p_for_match = p_markets
    if use_llm_match and llm is not None:
        k_focus = _focus_by_queries(k_markets, queries, max_n=max(llm_match_max_each * 3, 60))
        p_focus = _focus_by_queries(p_markets, queries, max_n=max(llm_match_max_each * 3, 60))
        if k_focus:
            k_for_match = k_focus
        if p_focus:
            p_for_match = p_focus

    pairs = await hybrid_pair_markets(
        llm,
        k_for_match,
        p_for_match,
        min_score=min_score,
        aliases=aliases,
        use_llm=use_llm_match,
        llm_band_low=llm_match_band_low,
        llm_min_confidence=llm_match_min_confidence,
        llm_max_candidates=llm_match_max_each,
        llm_bulk_fallback=llm_bulk_fallback,
    )
    # If we narrowed for LLM, also merge high-confidence fuzzy on full pool
    if k_for_match is not k_markets or p_for_match is not p_markets:
        from chancetime.data_layer.matching import pair_markets
        from chancetime.llm.match_venues import merge_pairs

        full_fuzzy = pair_markets(
            k_markets, p_markets, min_score=min_score, aliases=aliases
        )
        pairs = merge_pairs(full_fuzzy, pairs)

    log.info(
        "arb_discovery_pairs",
        pairs=len(pairs),
        min_score=min_score,
        top_score=round(pairs[0].score, 3) if pairs else 0.0,
        llm=use_llm_match,
    )

    return DiscoveryResult(
        kalshi=k_markets,
        polymarket=p_markets,
        pairs=pairs,
        queries_used=queries,
    )


def _focus_by_queries(markets: list[Market], queries: list[str], *, max_n: int) -> list[Market]:
    tokens = [t.lower() for q in queries for t in q.split() if len(t) > 2]
    if not tokens:
        return markets[:max_n]
    scored: list[tuple[int, Market]] = []
    for m in markets:
        title = m.title.lower()
        hit = sum(1 for t in tokens if t in title)
        if hit:
            scored.append((hit, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:max_n]]


def discovery_summary(result: DiscoveryResult) -> dict[str, Any]:
    spreads = sorted(
        (
            {
                "score": round(p.score, 3),
                "spread": round(p.yes_spread, 4),
                "kalshi_id": p.left.id,
                "pm_id": p.right.id,
                "kalshi_yes": round(p.left.yes_price, 4),
                "pm_yes": round(p.right.yes_price, 4),
                "kalshi_title": p.left.title[:80],
                "pm_title": p.right.title[:80],
            }
            for p in result.pairs
        ),
        key=lambda x: abs(float(x["spread"])),  # type: ignore[arg-type]
        reverse=True,
    )
    return {
        "kalshi_count": len(result.kalshi),
        "polymarket_count": len(result.polymarket),
        "pair_count": len(result.pairs),
        "queries": result.queries_used,
        "top_spreads": spreads[:15],
        "alias_file": str(project_root() / "config" / "arb_aliases.json"),
    }
