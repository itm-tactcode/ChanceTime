"""Log-only research strategies — never emit trade signals.

Write structured JSONL under ``data/research/`` for offline backtests:
- pair_gap_tracker: dual-list fee-aware edge time series
- tte_buckets: mid/spread by hours-to-close
- price_buckets: open mids by price band (resolve later offline)
- match_quality: dual-list score + suspicious long TTE flags
"""

from __future__ import annotations

from typing import Any

from chancetime.data_layer.matching import pair_markets, split_by_platform
from chancetime.data_layer.models import Market, Platform
from chancetime.data_layer.universe import hours_to_close
from chancetime.utils.research_log import append_research, base_fields
from chancetime.strategies.base import BaseStrategy, Signal
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


def _tte_bucket(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 0:
        return "past"
    if hours <= 2:
        return "0_2h"
    if hours <= 24:
        return "2_24h"
    if hours <= 168:
        return "1_7d"
    if hours <= 720:
        return "7_30d"
    return "30d_plus"


def _price_bucket(mid: float) -> str:
    if mid < 0.05:
        return "0_05"
    if mid < 0.15:
        return "05_15"
    if mid < 0.35:
        return "15_35"
    if mid < 0.50:
        return "35_50"
    if mid < 0.65:
        return "50_65"
    if mid < 0.85:
        return "65_85"
    if mid < 0.95:
        return "85_95"
    return "95_100"


def _exec_edge(cheap: Market, rich: Market, fee_buffer: float) -> tuple[float, float, float]:
    yes_cost = cheap.yes_ask_exec()
    no_cost = rich.no_ask_exec()
    edge = 1.0 - yes_cost - no_cost - fee_buffer
    return edge, yes_cost, no_cost


class PairGapTrackerStrategy(BaseStrategy):
    """Log dual-list executable edges each poll (no fills)."""

    name = "pair_gap_tracker"

    def __init__(
        self,
        *,
        enabled: bool = True,
        universe: str = "dual_list",
        min_match_score: float = 0.72,
        fee_buffer: float = 0.03,
        top_n: int = 40,
        log_name: str = "pair_gap",
        weight: float = 0.0,
        **params: object,
    ) -> None:
        super().__init__(
            enabled=enabled,
            universe=universe,
            min_match_score=min_match_score,
            fee_buffer=fee_buffer,
            top_n=top_n,
            log_name=log_name,
            weight=weight,
            **params,
        )
        self.min_match_score = float(min_match_score)
        self.fee_buffer = float(fee_buffer)
        self.top_n = int(top_n)
        self.log_name = str(log_name)
        self.weight = float(weight)
        self._poll = 0

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled:
            return []
        self._poll += 1
        by = split_by_platform(markets)
        kalshi = [m for m in by.get(Platform.KALSHI, []) if not m.synthetic]
        pm = [m for m in by.get(Platform.POLYMARKET, []) if not m.synthetic]
        if not kalshi or not pm:
            # pure mock: allow synthetic dual list
            kalshi = by.get(Platform.KALSHI, [])
            pm = by.get(Platform.POLYMARKET, [])
        if not kalshi or not pm:
            return []

        pairs = pair_markets(kalshi, pm, min_score=self.min_match_score)
        rows: list[dict[str, Any]] = []
        ranked: list[tuple[float, dict[str, Any]]] = []
        for p in pairs:
            a, b = p.left, p.right
            if b.yes_price >= a.yes_price:
                cheap, rich = a, b
            else:
                cheap, rich = b, a
            edge, y_cost, n_cost = _exec_edge(cheap, rich, self.fee_buffer)
            mid_spread = abs(a.yes_price - b.yes_price)
            h_c = hours_to_close(cheap)
            h_r = hours_to_close(rich)
            row = {
                **base_fields(poll=self._poll, strategy=self.name),
                "pair_id": f"{cheap.venue_key}|{rich.venue_key}",
                "match_score": round(p.score, 4),
                "cheap_id": cheap.id,
                "cheap_platform": str(cheap.platform),
                "rich_id": rich.id,
                "rich_platform": str(rich.platform),
                "title_cheap": cheap.title[:120],
                "title_rich": rich.title[:120],
                "mid_cheap": round(cheap.yes_price, 4),
                "mid_rich": round(rich.yes_price, 4),
                "mid_spread": round(mid_spread, 4),
                "exec_yes_cost": round(y_cost, 4),
                "exec_no_cost": round(n_cost, 4),
                "exec_edge": round(edge, 4),
                "fee_buffer": self.fee_buffer,
                "has_bbo_cheap": cheap.has_bbo,
                "has_bbo_rich": rich.has_bbo,
                "hours_to_close_cheap": None if h_c is None else round(h_c, 3),
                "hours_to_close_rich": None if h_r is None else round(h_r, 3),
                "tte_bucket": _tte_bucket(h_c if h_c is not None else h_r),
                "depth_yes": round(cheap.depth_usd_for_yes_buy(), 2),
                "depth_no": round(rich.depth_usd_for_no_buy(), 2),
            }
            ranked.append((edge, row))
        ranked.sort(key=lambda x: -x[0])
        rows = [r for _, r in ranked[: self.top_n]]
        append_research(self.log_name, rows)
        log.info(
            "pair_gap_tracker",
            pairs=len(pairs),
            logged=len(rows),
            best_edge=round(ranked[0][0], 4) if ranked else None,
        )
        return []


class TteBucketsStrategy(BaseStrategy):
    """Log mid/spread samples bucketed by time-to-event."""

    name = "tte_buckets"

    def __init__(
        self,
        *,
        enabled: bool = True,
        universe: str = "short_bbo",
        max_rows: int = 200,
        log_name: str = "tte_buckets",
        weight: float = 0.0,
        **params: object,
    ) -> None:
        super().__init__(
            enabled=enabled,
            universe=universe,
            max_rows=max_rows,
            log_name=log_name,
            weight=weight,
            **params,
        )
        self.max_rows = int(max_rows)
        self.log_name = str(log_name)
        self.weight = float(weight)
        self._poll = 0

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled:
            return []
        self._poll += 1
        rows: list[dict[str, Any]] = []
        # Prefer markets with known close; sample rest
        scored: list[tuple[float, Market]] = []
        for m in markets:
            if m.synthetic and any(not x.synthetic for x in markets):
                continue
            h = hours_to_close(m)
            scored.append((h if h is not None else 1e9, m))
        scored.sort(key=lambda x: x[0])
        for h, m in scored[: self.max_rows]:
            spr = None
            if m.yes_bid is not None and m.yes_ask is not None:
                spr = float(m.yes_ask) - float(m.yes_bid)
            hh = hours_to_close(m)
            rows.append(
                {
                    **base_fields(poll=self._poll, strategy=self.name),
                    "market_id": m.id,
                    "platform": str(m.platform),
                    "title": m.title[:100],
                    "yes_mid": round(m.yes_price, 4),
                    "yes_bid": m.yes_bid,
                    "yes_ask": m.yes_ask,
                    "spread": None if spr is None else round(spr, 4),
                    "has_bbo": m.has_bbo,
                    "hours_to_close": None if hh is None else round(hh, 3),
                    "tte_bucket": _tte_bucket(hh),
                    "volume_usd": m.volume_usd,
                    "liquidity_usd": m.liquidity_usd,
                }
            )
        append_research(self.log_name, rows)
        buckets: dict[str, int] = {}
        for r in rows:
            buckets[r["tte_bucket"]] = buckets.get(r["tte_bucket"], 0) + 1
        log.info("tte_buckets", logged=len(rows), buckets=buckets)
        return []


class PriceBucketsStrategy(BaseStrategy):
    """Log open mids by price band for later resolve-rate studies."""

    name = "price_buckets"

    def __init__(
        self,
        *,
        enabled: bool = True,
        universe: str = "broad",
        max_rows: int = 250,
        log_name: str = "price_buckets",
        weight: float = 0.0,
        **params: object,
    ) -> None:
        super().__init__(
            enabled=enabled,
            universe=universe,
            max_rows=max_rows,
            log_name=log_name,
            weight=weight,
            **params,
        )
        self.max_rows = int(max_rows)
        self.log_name = str(log_name)
        self.weight = float(weight)
        self._poll = 0

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled:
            return []
        self._poll += 1
        rows: list[dict[str, Any]] = []
        # Prefer liquid / higher volume
        ordered = sorted(
            (m for m in markets if not (m.synthetic and any(not x.synthetic for x in markets))),
            key=lambda m: -(m.volume_usd or m.liquidity_usd or 0),
        )
        for m in ordered[: self.max_rows]:
            hh = hours_to_close(m)
            rows.append(
                {
                    **base_fields(poll=self._poll, strategy=self.name),
                    "market_id": m.id,
                    "platform": str(m.platform),
                    "title": m.title[:100],
                    "yes_mid": round(m.yes_price, 4),
                    "price_bucket": _price_bucket(m.yes_price),
                    "hours_to_close": None if hh is None else round(hh, 3),
                    "tte_bucket": _tte_bucket(hh),
                    "volume_usd": m.volume_usd,
                    "liquidity_usd": m.liquidity_usd,
                    "has_bbo": m.has_bbo,
                }
            )
        append_research(self.log_name, rows)
        bands: dict[str, int] = {}
        for r in rows:
            bands[r["price_bucket"]] = bands.get(r["price_bucket"], 0) + 1
        log.info("price_buckets", logged=len(rows), bands=bands)
        return []


class MatchQualityStrategy(BaseStrategy):
    """Log dual-list match quality + suspicious long-horizon flags."""

    name = "match_quality"

    def __init__(
        self,
        *,
        enabled: bool = True,
        universe: str = "dual_list",
        min_match_score: float = 0.55,
        long_tte_hours: float = 720.0,
        top_n: int = 60,
        log_name: str = "match_quality",
        weight: float = 0.0,
        **params: object,
    ) -> None:
        super().__init__(
            enabled=enabled,
            universe=universe,
            min_match_score=min_match_score,
            long_tte_hours=long_tte_hours,
            top_n=top_n,
            log_name=log_name,
            weight=weight,
            **params,
        )
        self.min_match_score = float(min_match_score)
        self.long_tte_hours = float(long_tte_hours)
        self.top_n = int(top_n)
        self.log_name = str(log_name)
        self.weight = float(weight)
        self._poll = 0

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled:
            return []
        self._poll += 1
        by = split_by_platform(markets)
        kalshi = by.get(Platform.KALSHI, [])
        pm = by.get(Platform.POLYMARKET, [])
        if not kalshi or not pm:
            return []
        pairs = pair_markets(kalshi, pm, min_score=self.min_match_score)
        rows: list[dict[str, Any]] = []
        for p in pairs[: self.top_n * 2]:
            a, b = p.left, p.right
            ha, hb = hours_to_close(a), hours_to_close(b)
            h = ha if ha is not None else hb
            suspicious = bool(h is not None and h > self.long_tte_hours)
            # year mismatch heuristic in titles
            years_a = {t for t in a.title.replace(",", " ").split() if t.isdigit() and len(t) == 4}
            years_b = {t for t in b.title.replace(",", " ").split() if t.isdigit() and len(t) == 4}
            year_mismatch = bool(years_a and years_b and years_a.isdisjoint(years_b))
            if b.yes_price >= a.yes_price:
                cheap, rich = a, b
            else:
                cheap, rich = b, a
            edge, _, _ = _exec_edge(cheap, rich, 0.03)
            rows.append(
                {
                    **base_fields(poll=self._poll, strategy=self.name),
                    "pair_id": f"{a.venue_key}|{b.venue_key}",
                    "match_score": round(p.score, 4),
                    "left_id": a.id,
                    "right_id": b.id,
                    "title_left": a.title[:100],
                    "title_right": b.title[:100],
                    "mid_left": round(a.yes_price, 4),
                    "mid_right": round(b.yes_price, 4),
                    "exec_edge": round(edge, 4),
                    "hours_to_close": None if h is None else round(h, 3),
                    "tte_bucket": _tte_bucket(h),
                    "suspicious_long_tte": suspicious,
                    "year_mismatch": year_mismatch,
                    "review_flag": suspicious or year_mismatch or p.score < 0.72,
                }
            )
        # Prefer flagged / high score
        rows.sort(
            key=lambda r: (
                -int(r["review_flag"]),
                -float(r["match_score"]),
                -float(r["exec_edge"]),
            )
        )
        rows = rows[: self.top_n]
        append_research(self.log_name, rows)
        n_flag = sum(1 for r in rows if r["review_flag"])
        log.info("match_quality", pairs=len(pairs), logged=len(rows), review_flags=n_flag)
        return []
