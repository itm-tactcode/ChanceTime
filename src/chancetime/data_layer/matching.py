"""Cross-venue market matching (title normalization + scoring).

Maps Kalshi ↔ Polymarket US markets that describe the same event.
Matching is fuzzy by design; always log score and require a min threshold.

Dual-listed contracts often use different phrasing:
  Kalshi:  "Will Cleveland win the 2027 Pro Basketball Finals?"
  PM US:   "Cleveland Cavaliers - 2027 NBA Champion"
  Kalshi:  "Will Bitcoin be above $149,999.99 by Dec 31, 2026..."
  PM US:   "Above $149,999.99 - How high will Bitcoin get this year"

Structural (entity + event-family + year/level) scoring bridges those shapes
without lowering executable-edge thresholds.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from chancetime.data_layer.models import Market, Platform

_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "will",
        "be",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "and",
        "or",
        "by",
        "is",
        "are",
        "was",
        "were",
        "with",
        "from",
        "this",
        "that",
        "it",
        "as",
        "if",
        "win",
        "wins",
        "get",
        "how",
        "high",
        "low",
        "above",
        "below",
        "next",
        "year",
        "this",
    }
)

# (family_id, patterns that identify the family in a normalized title)
_EVENT_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "nba_champion",
        (
            "pro basketball finals",
            "nba champion",
            "nba finals",
            "nba championship",
            "basketball champion",
            "basketball finals",
        ),
    ),
    (
        "mlb_ws",
        (
            "world series",
            "mlb world series",
            "mlb champion",
            "baseball world series",
        ),
    ),
    (
        "mlb_nl",
        (
            "national league champion",
            "nl champion",
            "nl pennant",
            "national league pennant",
            "mlb national league",
        ),
    ),
    (
        "mlb_al",
        (
            "american league champion",
            "al champion",
            "al pennant",
            "american league pennant",
            "mlb american league",
        ),
    ),
    (
        "nfl_sb",
        (
            "super bowl",
            "nfl champion",
            "pro football champion",
            "superbowl",
        ),
    ),
    (
        "btc_year_high",
        (
            "bitcoin be above",
            "bitcoin above",
            "how high will bitcoin",
            "bitcoin get this year",
            "bitcoin price above",
        ),
    ),
    (
        "eth_year_high",
        (
            "ethereum be above",
            "ethereum above",
            "how high will ethereum",
            "ethereum get this year",
        ),
    ),
    (
        "fed_rate_level",
        (
            "federal funds rate",
            "upper bound of the federal",
            "fed funds rate",
        ),
    ),
    (
        "fed_decision",
        (
            "fed decision",
            "fomc decision",
            "rate cut at",
            "bps increase",
            "bps decrease",
            "no change",
        ),
    ),
    (
        "fed_rate_cut_any",
        (
            "federal reserve cut rates",
            "fed cut rates",
            "rate cut before",
        ),
    ),
)

# Canonical team/city aliases (token sets). Order: longer/more specific first when matching.
_ENTITY_ALIASES: tuple[tuple[str, frozenset[str]], ...] = (
    # NBA
    ("atlanta hawks", frozenset({"atlanta", "hawks"})),
    ("boston celtics", frozenset({"boston", "celtics"})),
    ("brooklyn nets", frozenset({"brooklyn", "nets"})),
    ("charlotte hornets", frozenset({"charlotte", "hornets"})),
    ("chicago bulls", frozenset({"chicago", "bulls"})),
    ("cleveland cavaliers", frozenset({"cleveland", "cavaliers", "cavs"})),
    ("dallas mavericks", frozenset({"dallas", "mavericks", "mavs"})),
    ("denver nuggets", frozenset({"denver", "nuggets"})),
    ("detroit pistons", frozenset({"detroit", "pistons"})),
    ("golden state warriors", frozenset({"golden", "state", "warriors", "gsw"})),
    ("houston rockets", frozenset({"houston", "rockets"})),
    ("indiana pacers", frozenset({"indiana", "pacers"})),
    ("la clippers", frozenset({"clippers", "lac"})),
    ("la lakers", frozenset({"lakers", "lal"})),
    ("memphis grizzlies", frozenset({"memphis", "grizzlies"})),
    ("miami heat", frozenset({"miami", "heat"})),
    ("milwaukee bucks", frozenset({"milwaukee", "bucks"})),
    ("minnesota timberwolves", frozenset({"minnesota", "timberwolves", "wolves"})),
    ("new orleans pelicans", frozenset({"orleans", "pelicans"})),
    ("new york knicks", frozenset({"knicks"})),
    ("oklahoma city thunder", frozenset({"oklahoma", "thunder", "okc"})),
    ("orlando magic", frozenset({"orlando", "magic"})),
    ("philadelphia 76ers", frozenset({"philadelphia", "76ers", "sixers", "philly"})),
    ("phoenix suns", frozenset({"phoenix", "suns"})),
    ("portland trail blazers", frozenset({"portland", "blazers", "trail"})),
    ("sacramento kings", frozenset({"sacramento", "kings"})),
    ("san antonio spurs", frozenset({"antonio", "spurs"})),
    ("toronto raptors", frozenset({"toronto", "raptors"})),
    ("utah jazz", frozenset({"utah", "jazz"})),
    ("washington wizards", frozenset({"washington", "wizards"})),
    # MLB (common dual-list)
    ("arizona diamondbacks", frozenset({"arizona", "diamondbacks", "dbacks"})),
    ("atlanta braves", frozenset({"atlanta", "braves"})),
    ("baltimore orioles", frozenset({"baltimore", "orioles"})),
    ("boston red sox", frozenset({"boston", "sox"})),  # ambiguous with white sox — city helps
    ("chicago cubs", frozenset({"cubs"})),
    ("chicago white sox", frozenset({"white", "sox"})),
    ("cincinnati reds", frozenset({"cincinnati", "reds"})),
    ("cleveland guardians", frozenset({"cleveland", "guardians"})),
    ("colorado rockies", frozenset({"colorado", "rockies"})),
    ("detroit tigers", frozenset({"detroit", "tigers"})),
    ("houston astros", frozenset({"houston", "astros"})),
    ("kansas city royals", frozenset({"kansas", "royals"})),
    ("los angeles angels", frozenset({"angels"})),
    ("los angeles dodgers", frozenset({"dodgers"})),
    ("miami marlins", frozenset({"miami", "marlins"})),
    ("milwaukee brewers", frozenset({"milwaukee", "brewers"})),
    ("minnesota twins", frozenset({"minnesota", "twins"})),
    ("new york mets", frozenset({"mets"})),
    ("new york yankees", frozenset({"yankees"})),
    ("athletics", frozenset({"athletics", "oakland", "as"})),
    ("philadelphia phillies", frozenset({"philadelphia", "phillies"})),
    ("pittsburgh pirates", frozenset({"pittsburgh", "pirates"})),
    ("san diego padres", frozenset({"diego", "padres"})),
    ("san francisco giants", frozenset({"francisco", "giants"})),
    ("seattle mariners", frozenset({"seattle", "mariners"})),
    ("st louis cardinals", frozenset({"louis", "cardinals", "stl"})),
    ("tampa bay rays", frozenset({"tampa", "rays"})),
    ("texas rangers", frozenset({"texas", "rangers"})),
    ("toronto blue jays", frozenset({"toronto", "jays"})),
    ("washington nationals", frozenset({"nationals", "nats"})),
    # Crypto assets
    ("bitcoin", frozenset({"bitcoin", "btc"})),
    ("ethereum", frozenset({"ethereum", "eth"})),
)

# Kalshi short city forms that need disambiguation via ticker or trailing letter
_KALSHI_LA_BASKETBALL = re.compile(
    r"los angeles\s+([lc])\b|los angeles\s+(lakers|clippers)\b",
    re.I,
)
_YEAR_RE = re.compile(r"\b(20[2-3]\d)\b")
_MONEY_RE = re.compile(r"\b(\d{4,}(?:\.\d+)?)\b")
_TICKER_TEAM_RE = re.compile(r"KX[A-Z]+-\d{2}-([A-Z]{2,3})\b")


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, drop stopwords -> canonical key.

    Also normalizes currency/number variants so e.g. ``$100,000`` and
    ``100000 USD`` collapse to the same token sequence.
    """
    t = unicodedata.normalize("NFKD", title)
    t = t.encode("ascii", "ignore").decode("ascii")
    t = t.lower()
    # Currency symbol -> word token before punctuation strip
    t = t.replace("$", " usd ")
    # 100,000 / 1,000,000 -> 100000 / 1000000
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    # Spaced digit groups left by punctuation strip: "100 000" -> "100000"
    t = re.sub(r"(?<=\d)\s+(?=\d)", "", t)
    # Alias common currency spellings to usd
    t = re.sub(r"\b(dollars?|usdc?)\b", "usd", t)
    tokens = [w for w in t.split() if w and w not in _STOP]
    return " ".join(tokens)


def ensure_canonical_key(market: Market) -> Market:
    if market.canonical_key:
        return market
    return market.model_copy(update={"canonical_key": normalize_title(market.title)})


def _raw_lower(title: str) -> str:
    t = unicodedata.normalize("NFKD", title)
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    t = t.replace("$", " ")
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    t = re.sub(r"[^a-z0-9\s.\-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def detect_event_family(title: str) -> str | None:
    """Return a coarse event-family id if the title matches a known dual-list pattern."""
    raw = _raw_lower(title)
    for family, patterns in _EVENT_FAMILIES:
        for pat in patterns:
            if pat in raw:
                return family
    return None


def extract_years(title: str) -> set[str]:
    return set(_YEAR_RE.findall(title))


def extract_money_levels(title: str) -> set[int]:
    """Integer USD ladder levels (e.g. 149999 from $149,999.99)."""
    raw = _raw_lower(title)
    out: set[int] = set()
    for m in _MONEY_RE.findall(raw):
        try:
            val = float(m)
        except ValueError:
            continue
        if val >= 1000:
            out.add(int(round(val)))
    return out


# Shared city tokens alone must not match across sports franchises
_CITY_TOKENS = frozenset(
    {
        "atlanta",
        "boston",
        "brooklyn",
        "charlotte",
        "chicago",
        "cleveland",
        "dallas",
        "denver",
        "detroit",
        "houston",
        "indiana",
        "memphis",
        "miami",
        "milwaukee",
        "minnesota",
        "orlando",
        "philadelphia",
        "phoenix",
        "portland",
        "sacramento",
        "toronto",
        "utah",
        "washington",
        "arizona",
        "baltimore",
        "cincinnati",
        "colorado",
        "kansas",
        "oakland",
        "pittsburgh",
        "seattle",
        "texas",
        "tampa",
    }
)

_NBA_CITY_MAP: dict[str, str] = {
    "atlanta": "atlanta hawks",
    "boston": "boston celtics",
    "brooklyn": "brooklyn nets",
    "charlotte": "charlotte hornets",
    "chicago": "chicago bulls",
    "cleveland": "cleveland cavaliers",
    "dallas": "dallas mavericks",
    "denver": "denver nuggets",
    "detroit": "detroit pistons",
    "houston": "houston rockets",
    "indiana": "indiana pacers",
    "memphis": "memphis grizzlies",
    "miami": "miami heat",
    "milwaukee": "milwaukee bucks",
    "minnesota": "minnesota timberwolves",
    "orlando": "orlando magic",
    "philadelphia": "philadelphia 76ers",
    "phoenix": "phoenix suns",
    "portland": "portland trail blazers",
    "sacramento": "sacramento kings",
    "toronto": "toronto raptors",
    "utah": "utah jazz",
    "washington": "washington wizards",
    "golden state": "golden state warriors",
    "oklahoma city": "oklahoma city thunder",
    "san antonio": "san antonio spurs",
    "new orleans": "new orleans pelicans",
    "new york": "new york knicks",
    "la lakers": "la lakers",
    "la clippers": "la clippers",
}

_NBA_TICKER_MAP: dict[str, str] = {
    "ATL": "atlanta hawks",
    "BOS": "boston celtics",
    "BKN": "brooklyn nets",
    "CHA": "charlotte hornets",
    "CHI": "chicago bulls",
    "CLE": "cleveland cavaliers",
    "DAL": "dallas mavericks",
    "DEN": "denver nuggets",
    "DET": "detroit pistons",
    "GSW": "golden state warriors",
    "HOU": "houston rockets",
    "IND": "indiana pacers",
    "LAC": "la clippers",
    "LAL": "la lakers",
    "MEM": "memphis grizzlies",
    "MIA": "miami heat",
    "MIL": "milwaukee bucks",
    "MIN": "minnesota timberwolves",
    "NOP": "new orleans pelicans",
    "NYK": "new york knicks",
    "OKC": "oklahoma city thunder",
    "ORL": "orlando magic",
    "PHI": "philadelphia 76ers",
    "PHX": "phoenix suns",
    "POR": "portland trail blazers",
    "SAC": "sacramento kings",
    "SAS": "san antonio spurs",
    "TOR": "toronto raptors",
    "UTA": "utah jazz",
    "WAS": "washington wizards",
}


def extract_entities(title: str, *, market_id: str = "") -> set[str]:
    """Canonical entity keys present in the title (teams, assets)."""
    raw = _raw_lower(title)
    tokens = set(raw.split())

    # Kalshi LA basketball disambiguation
    la = _KALSHI_LA_BASKETBALL.search(raw)
    if la:
        g = (la.group(1) or la.group(2) or "").lower()
        if g in {"l", "lakers"}:
            return {"la lakers"}
        if g in {"c", "clippers"}:
            return {"la clippers"}

    # Ticker suffix e.g. KXNBA-27-CLE (authoritative when present)
    if market_id:
        m = _TICKER_TEAM_RE.search(market_id.upper())
        if m:
            code = m.group(1)
            if code in _NBA_TICKER_MAP:
                return {_NBA_TICKER_MAP[code]}

    found: set[str] = set()
    for key, aliases in _ENTITY_ALIASES:
        if key in {"bitcoin", "ethereum"}:
            continue
        # Full name / multi-word key in title
        if key in raw:
            found.add(key)
            continue
        # Distinctive nicknames only (not bare shared city tokens)
        distinctive = {a for a in aliases if a not in _CITY_TOKENS and len(a) >= 3}
        if any(a in tokens or a in raw for a in distinctive):
            found.add(key)

    # Multi-word city phrases for NBA-style Kalshi titles without nicknames
    nbaish = (
        "pro basketball" in raw
        or "nba" in raw
        or ("finals" in raw and "football" not in raw and "world series" not in raw)
    )
    if nbaish:
        for city, ent in sorted(_NBA_CITY_MAP.items(), key=lambda x: -len(x[0])):
            if city in raw:
                found.add(ent)
                break

    if "bitcoin" in raw or re.search(r"\bbtc\b", raw):
        found.add("bitcoin")
    if "ethereum" in raw or re.search(r"\beth\b", raw):
        found.add("ethereum")

    return found


def structural_similarity(
    title_a: str,
    title_b: str,
    *,
    id_a: str = "",
    id_b: str = "",
) -> float:
    """0-1 structural same-event score (entity + family + year/level).

    Returns 0 when families conflict or entities conflict — never invents
    dual listings across different contract types (e.g. Fed level vs Fed decision).
    """
    fam_a = detect_event_family(title_a)
    fam_b = detect_event_family(title_b)
    if fam_a is None or fam_b is None:
        return 0.0
    if fam_a != fam_b:
        return 0.0

    ents_a = extract_entities(title_a, market_id=id_a)
    ents_b = extract_entities(title_b, market_id=id_b)
    years_a = extract_years(title_a)
    years_b = extract_years(title_b)
    money_a = extract_money_levels(title_a)
    money_b = extract_money_levels(title_b)

    score = 0.55  # shared family baseline

    # Entities (teams / assets)
    if ents_a and ents_b:
        if ents_a & ents_b:
            score += 0.30
        else:
            # Conflict: different teams same family → not the same contract
            return 0.0
    elif fam_a in {"btc_year_high", "eth_year_high", "fed_rate_level", "fed_decision", "fed_rate_cut_any"}:
        # asset/event implied by family
        score += 0.10

    # Year alignment
    if years_a and years_b:
        if years_a & years_b:
            score += 0.10
        else:
            score -= 0.15

    # Ladder / strike level (crypto thresholds, fed bounds)
    if money_a and money_b:
        if money_a & money_b:
            score += 0.15
        else:
            # Same family different strike → different market
            if fam_a in {"btc_year_high", "eth_year_high", "fed_rate_level"}:
                return 0.0
            score -= 0.05

    return max(0.0, min(1.0, score))


def title_similarity(
    a: str,
    b: str,
    *,
    id_a: str = "",
    id_b: str = "",
) -> float:
    """0-1 similarity on normalized titles (token Jaccard + sequence + structural)."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jacc = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    base = 0.55 * jacc + 0.45 * seq
    structural = structural_similarity(a, b, id_a=id_a, id_b=id_b)
    # Prefer structural when dual-list shapes differ but are the same event
    return max(base, structural)


@dataclass(frozen=True)
class MarketPair:
    """Two markets believed to be the same event on different venues."""

    left: Market
    right: Market
    score: float

    @property
    def yes_spread(self) -> float:
        """right.yes - left.yes (positive => left YES cheaper)."""
        return self.right.yes_price - self.left.yes_price


def pair_markets(
    markets_a: list[Market],
    markets_b: list[Market],
    *,
    min_score: float = 0.72,
    aliases: dict[str, str] | None = None,
) -> list[MarketPair]:
    """Greedy best-match pairs across two venue lists.

    ``aliases`` maps canonical_key (or raw id) on A → canonical_key/id on B.
    """
    aliases = aliases or {}
    a_list = [ensure_canonical_key(m) for m in markets_a]
    b_list = [ensure_canonical_key(m) for m in markets_b]
    used_b: set[str] = set()
    pairs: list[MarketPair] = []

    # Explicit aliases first
    b_by_key = {m.canonical_key: m for m in b_list}
    b_by_id = {m.id: m for m in b_list}
    for ma in a_list:
        alias = aliases.get(ma.canonical_key) or aliases.get(ma.id)
        if not alias:
            continue
        mb = b_by_key.get(alias) or b_by_id.get(alias)
        if mb is None or mb.venue_key in used_b:
            continue
        pairs.append(MarketPair(left=ma, right=mb, score=1.0))
        used_b.add(mb.venue_key)

    for ma in a_list:
        if any(p.left.venue_key == ma.venue_key for p in pairs):
            continue
        best: MarketPair | None = None
        for mb in b_list:
            if mb.venue_key in used_b:
                continue
            score = title_similarity(ma.title, mb.title, id_a=ma.id, id_b=mb.id)
            if score < min_score:
                continue
            if best is None or score > best.score:
                best = MarketPair(left=ma, right=mb, score=score)
        if best is not None:
            pairs.append(best)
            used_b.add(best.right.venue_key)

    pairs.sort(key=lambda p: p.score, reverse=True)
    return pairs


@dataclass(frozen=True)
class MatchCandidate:
    """Fuzzy mid-band candidate for optional LLM adjudication."""

    left: Market
    right: Market
    score: float


def find_borderline_candidates(
    markets_a: list[Market],
    markets_b: list[Market],
    *,
    score_low: float = 0.40,
    score_high: float = 0.72,
    exclude_left: set[str] | None = None,
    exclude_right: set[str] | None = None,
    max_candidates: int = 24,
) -> list[MatchCandidate]:
    """Greedy best mid-band matches for LLM review.

    Returns pairs with ``score_low <= score < score_high`` that were not
    already claimed (``exclude_*`` venue_keys). One left / one right each.
    Sorted by score descending, capped at ``max_candidates``.
    """
    if score_low >= score_high or max_candidates <= 0:
        return []
    a_list = [ensure_canonical_key(m) for m in markets_a]
    b_list = [ensure_canonical_key(m) for m in markets_b]
    used_a = set(exclude_left or ())
    used_b = set(exclude_right or ())
    candidates: list[MatchCandidate] = []

    for ma in a_list:
        if ma.venue_key in used_a:
            continue
        best: MatchCandidate | None = None
        for mb in b_list:
            if mb.venue_key in used_b:
                continue
            score = title_similarity(ma.title, mb.title, id_a=ma.id, id_b=mb.id)
            if score < score_low or score >= score_high:
                continue
            if best is None or score > best.score:
                best = MatchCandidate(left=ma, right=mb, score=score)
        if best is not None:
            candidates.append(best)
            used_a.add(best.left.venue_key)
            used_b.add(best.right.venue_key)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:max_candidates]


def split_by_platform(markets: list[Market]) -> dict[Platform, list[Market]]:
    out: dict[Platform, list[Market]] = {}
    for m in markets:
        out.setdefault(m.platform, []).append(m)
    return out
