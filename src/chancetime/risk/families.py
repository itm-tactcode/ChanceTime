"""Event-family + correlation cluster tagging for exposure budgets.

Phase 8: coarse families (sports / macro / crypto / politics / other).
Phase 19: tighter ``cluster_key`` so correlated legs (same series, same
championship event) share a budget — not only loose keyword bags.
"""

from __future__ import annotations

import re
from enum import StrEnum

from chancetime.data_layer.models import Market


class EventFamily(StrEnum):
    SPORTS = "sports"
    MACRO = "macro"
    CRYPTO = "crypto"
    POLITICS = "politics"
    OTHER = "other"


_SPORTS = (
    "nfl",
    "nba",
    "mlb",
    "nhl",
    "soccer",
    "football",
    "basketball",
    "baseball",
    "tennis",
    "ufc",
    "mma",
    "world cup",
    "fifa",
    "olympics",
    "match",
    "vs.",
    " vs ",
    "championship",
    "playoff",
    "super bowl",
    "world series",
    "stanley cup",
    "premier league",
    "serie a",
)
_MACRO = (
    "fed",
    "fomc",
    "rate cut",
    "rate hike",
    "interest rate",
    "federal funds",
    "cpi",
    "inflation",
    "gdp",
    "unemployment",
    "recession",
    "treasury",
)
_CRYPTO = (
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "crypto",
    "solana",
    "token",
)
_POLITICS = (
    "election",
    "president",
    "senate",
    "congress",
    "vote",
    "mayor",
    "governor",
    "parliament",
    "prime minister",
)

# Kalshi series prefix → coarse family (more reliable than title alone)
_SERIES_FAMILY: tuple[tuple[str, EventFamily], ...] = (
    ("KXNBA", EventFamily.SPORTS),
    ("KXNFL", EventFamily.SPORTS),
    ("KXMLB", EventFamily.SPORTS),
    ("KXNHL", EventFamily.SPORTS),
    ("KXSB", EventFamily.SPORTS),
    ("KXWS", EventFamily.SPORTS),
    ("KXEPL", EventFamily.SPORTS),
    ("KXATP", EventFamily.SPORTS),
    ("KXWTA", EventFamily.SPORTS),
    ("KXITF", EventFamily.SPORTS),
    ("KXFED", EventFamily.MACRO),
    ("KXRATE", EventFamily.MACRO),
    ("FED", EventFamily.MACRO),
    ("CPI", EventFamily.MACRO),
    ("KXBTC", EventFamily.CRYPTO),
    ("KXETH", EventFamily.CRYPTO),
    ("BTC", EventFamily.CRYPTO),
    ("ETH", EventFamily.CRYPTO),
    ("KXPRES", EventFamily.POLITICS),
    ("KXGOV", EventFamily.POLITICS),
)

_TICKER_SERIES_RE = re.compile(r"^([A-Z]{2,}[A-Z0-9]*?)(?:-\d|$)")
_YEAR_RE = re.compile(r"\b(20[2-3]\d)\b")


def classify_family(title: str, *, market_id: str = "") -> EventFamily:
    mid = (market_id or "").upper()
    for prefix, fam in _SERIES_FAMILY:
        if mid.startswith(prefix):
            return fam
    blob = f"{title} {market_id}".lower()
    if any(k in blob for k in _SPORTS):
        return EventFamily.SPORTS
    if any(k in blob for k in _MACRO):
        return EventFamily.MACRO
    if any(k in blob for k in _CRYPTO):
        return EventFamily.CRYPTO
    if any(k in blob for k in _POLITICS):
        return EventFamily.POLITICS
    return EventFamily.OTHER


def market_family(market: Market) -> EventFamily:
    return classify_family(market.title, market_id=market.id)


def series_prefix(market_id: str) -> str | None:
    """Extract series-like prefix from a ticker (KXNBA-27-CLE → KXNBA)."""
    mid = (market_id or "").strip().upper()
    if not mid or mid.isdigit():
        return None
    # Strip trailing team/strike segments: KXNBA-27-CLE, KXFED-26JUL-T3.75
    parts = mid.split("-")
    if parts and parts[0].startswith("KX"):
        return parts[0]
    if parts and len(parts[0]) >= 3 and parts[0].isalpha():
        return parts[0]
    m = _TICKER_SERIES_RE.match(mid)
    return m.group(1) if m else None


def cluster_key(title: str, *, market_id: str = "") -> str:
    """Correlation cluster id for tighter exposure than family alone.

    Examples:
      KXNBA-27-CLE  → sports:kxnba:27
      KXFED-26JUL-T3.75 → macro:kxfed:26jul
      PM "Miami Heat - 2027 NBA Champion" → sports:nba:2027
    """
    fam = classify_family(title, market_id=market_id)
    mid = (market_id or "").strip()
    mid_u = mid.upper()
    title_l = (title or "").lower()

    # Kalshi-style: SERIES-PERIOD-...
    parts = mid_u.split("-") if mid_u and not mid_u.isdigit() else []
    if len(parts) >= 2 and (parts[0].startswith("KX") or len(parts[0]) >= 3):
        series = parts[0].lower()
        period = parts[1].lower()
        # Strike ladder (BTCMAXY-26DEC31-99999) → series + period only
        return f"{fam.value}:{series}:{period}"

    # Title-based cluster for Polymarket / free-form
    years = _YEAR_RE.findall(title)
    year = years[0] if years else "na"
    if fam == EventFamily.SPORTS:
        for tag, keys in (
            ("nba", ("nba", "basketball finals", "pro basketball")),
            ("nfl", ("nfl", "super bowl", "pro football")),
            ("mlb", ("mlb", "world series", "national league", "american league")),
            ("nhl", ("nhl", "stanley cup")),
            ("tennis", ("tennis", "open championship", "wimbledon", "itf", "atp", "wta")),
        ):
            if any(k in title_l for k in keys):
                return f"sports:{tag}:{year}"
        return f"sports:other:{year}"
    if fam == EventFamily.MACRO:
        if any(k in title_l for k in ("fed", "fomc", "rate cut", "rate hike", "federal funds")):
            return f"macro:fed:{year}"
        if any(k in title_l for k in ("cpi", "inflation")):
            return f"macro:inflation:{year}"
        return f"macro:other:{year}"
    if fam == EventFamily.CRYPTO:
        if "bitcoin" in title_l or "btc" in title_l:
            return f"crypto:btc:{year}"
        if "ethereum" in title_l or re.search(r"\beth\b", title_l):
            return f"crypto:eth:{year}"
        return f"crypto:other:{year}"
    if fam == EventFamily.POLITICS:
        return f"politics:default:{year}"
    return f"other:default:{year}"


def market_cluster(market: Market) -> str:
    return cluster_key(market.title, market_id=market.id)
