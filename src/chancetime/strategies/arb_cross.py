"""Cross-platform arb scanner: Kalshi ↔ Polymarket US.

Finds title-matched market pairs and emits hedge legs when the
*executable* edge (buy YES ask on cheap + buy NO ask on rich) clears
fee buffer + threshold. Prefer BBO over mid; size by min available depth.

Matching: fuzzy titles first; optional LLM assist when enabled and budget allows.

SAFETY: signals only — never places orders. Execution remains paper-gated.
"""

from __future__ import annotations

import uuid

from chancetime.data_layer.matching import MarketPair, split_by_platform
from chancetime.data_layer.models import Market, Platform
from chancetime.llm.client import GrokClient
from chancetime.llm.match_venues import hybrid_pair_markets
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class ArbCrossStrategy(BaseStrategy):
    """Scan for cross-venue YES price gaps between Kalshi and Polymarket US."""

    name = "arb_cross"

    def __init__(
        self,
        *,
        llm: GrokClient | None = None,
        min_spread: float = 0.04,
        fee_buffer: float = 0.02,
        min_match_score: float = 0.72,
        min_liquidity_usd: float = 0.0,
        emit_hedge_legs: bool = True,
        use_llm_match: bool = False,
        llm_match_min_confidence: float = 0.75,
        llm_match_max_each: int = 30,
        llm_match_band_low: float = 0.40,
        llm_bulk_fallback: bool = False,
        require_bbo: bool = False,
        use_executable_prices: bool = True,
        size_by_depth: bool = True,
        max_leg_usd: float = 25.0,
        max_pair_usd: float = 40.0,
        min_depth_usd: float = 5.0,
        enabled: bool = True,
        weight: float = 1.0,
        aliases: dict[str, str] | None = None,
        **params: object,
    ) -> None:
        super().__init__(
            min_spread=min_spread,
            fee_buffer=fee_buffer,
            min_match_score=min_match_score,
            min_liquidity_usd=min_liquidity_usd,
            emit_hedge_legs=emit_hedge_legs,
            use_llm_match=use_llm_match,
            llm_match_band_low=llm_match_band_low,
            require_bbo=require_bbo,
            use_executable_prices=use_executable_prices,
            size_by_depth=size_by_depth,
            max_leg_usd=max_leg_usd,
            max_pair_usd=max_pair_usd,
            min_depth_usd=min_depth_usd,
            enabled=enabled,
            weight=weight,
            **params,
        )
        self.llm = llm
        self.min_spread = min_spread
        self.fee_buffer = fee_buffer
        self.min_match_score = min_match_score
        self.min_liquidity_usd = min_liquidity_usd
        self.emit_hedge_legs = emit_hedge_legs
        self.use_llm_match = use_llm_match
        self.llm_match_min_confidence = llm_match_min_confidence
        self.llm_match_max_each = llm_match_max_each
        self.llm_match_band_low = llm_match_band_low
        self.llm_bulk_fallback = llm_bulk_fallback
        self.require_bbo = require_bbo
        self.use_executable_prices = use_executable_prices
        self.size_by_depth = size_by_depth
        self.max_leg_usd = max_leg_usd
        self.max_pair_usd = max_pair_usd
        self.min_depth_usd = min_depth_usd
        self.weight = weight
        self.aliases = aliases or {}
        self.last_pairs: list[MarketPair] = []

    async def _build_pairs(self, kalshi: list[Market], pm: list[Market]) -> list[MarketPair]:
        """Fuzzy high scores + optional low-token LLM on mid-band only."""
        return await hybrid_pair_markets(
            self.llm,
            kalshi,
            pm,
            min_score=self.min_match_score,
            aliases=self.aliases,
            use_llm=self.use_llm_match,
            llm_band_low=self.llm_match_band_low,
            llm_min_confidence=self.llm_match_min_confidence,
            llm_max_candidates=self.llm_match_max_each,
            llm_bulk_fallback=self.llm_bulk_fallback,
        )

    @staticmethod
    def _depth(m: Market) -> float:
        return max(m.liquidity_usd, m.volume_usd)

    def _placeholder_mid(self, m: Market) -> bool:
        """Empty book often surfaces as exactly 0.50 with no depth."""
        if m.has_bbo and m.yes_bid is not None and m.yes_ask is not None:
            return False
        return self._depth(m) <= 0 and abs(m.yes_price - 0.5) < 1e-6

    def _too_thin(self, m: Market) -> bool:
        if self.min_liquidity_usd <= 0:
            return False
        d = self._depth(m)
        if d <= 0:
            return False  # unknown depth — do not reject
        return d < self.min_liquidity_usd

    def _pair_size_usd(self, cheap: Market, rich: Market) -> float | None:
        """Min available depth across legs, hard-capped."""
        if not self.size_by_depth:
            leg = min(self.max_leg_usd, self.max_pair_usd / 2.0)
            return leg if leg >= self.min_depth_usd else None

        cheap_d = cheap.depth_usd_for_yes_buy()
        rich_d = rich.depth_usd_for_no_buy()
        # Unknown depth (0) → allow default cap rather than force skip
        if cheap_d <= 0:
            cheap_d = self.max_leg_usd
        if rich_d <= 0:
            rich_d = self.max_leg_usd

        per_leg = min(cheap_d, rich_d, self.max_leg_usd, self.max_pair_usd / 2.0)
        if per_leg < self.min_depth_usd:
            return None
        return round(per_leg, 4)

    def _executable_edge(self, cheap: Market, rich: Market) -> tuple[float, float, float]:
        """Return (edge, cheap_yes_cost, rich_no_cost).

        Edge = 1 - (YES ask cheap + NO ask rich) - fee_buffer.
        Positive edge ⇒ theoretical locked profit if both legs fill at those prices.
        """
        if self.use_executable_prices:
            yes_cost = cheap.yes_ask_exec()
            no_cost = rich.no_ask_exec()
        else:
            yes_cost = cheap.yes_price
            no_cost = 1.0 - rich.yes_price
        edge = 1.0 - yes_cost - no_cost - self.fee_buffer
        return edge, yes_cost, no_cost

    def _mid_spread(self, a: Market, b: Market) -> float:
        return abs(b.yes_price - a.yes_price)

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled:
            return []

        # Never mix mock fixtures with live books. Pure-synthetic feeds (source=mock)
        # still work for tests; mixed feed drops synthetic legs.
        real = [m for m in markets if not m.synthetic]
        synthetic = [m for m in markets if m.synthetic]
        if real and synthetic:
            log.warning(
                "arb_cross_dropped_synthetic",
                dropped=len(synthetic),
                kept=len(real),
                msg="Mock markets excluded while live data present",
            )
            markets = real
        elif synthetic and not real:
            markets = synthetic  # mock-only session
        else:
            markets = real

        by_plat = split_by_platform(markets)
        kalshi = by_plat.get(Platform.KALSHI, [])
        pm = by_plat.get(Platform.POLYMARKET, [])
        mock = by_plat.get(Platform.MOCK, [])
        # Legacy: Platform.MOCK ids only (synthetic dual-list uses KALSHI/POLYMARKET platforms)
        if mock and (not kalshi or not pm):
            kalshi = kalshi or [m for m in mock if m.id.startswith("kalshi-") or "kalshi" in m.id]
            pm = pm or [m for m in mock if m.id.startswith("pm-") or "poly" in m.id]

        if not kalshi or not pm:
            log.info(
                "arb_cross_skip_venues",
                kalshi=len(kalshi),
                polymarket=len(pm),
                mock=len(mock),
            )
            return []

        # Discovery may stash pairs on last_pairs; always intersect with *current*
        # universe so mock fed-cut pairs cannot survive into a live feed.
        live_mode = any(not m.synthetic for m in markets)
        id_set = {m.id for m in markets}
        pairs: list[MarketPair] = []
        if self.last_pairs:
            for p in self.last_pairs:
                if p.left.id not in id_set or p.right.id not in id_set:
                    continue
                if live_mode and (p.left.synthetic or p.right.synthetic):
                    continue
                pairs.append(p)
        if not pairs:
            pairs = await self._build_pairs(kalshi, pm)
            self.last_pairs = pairs
        log.info(
            "arb_cross_pairs",
            count=len(pairs),
            min_score=self.min_match_score,
            llm_match=self.use_llm_match,
        )

        threshold = self.min_spread
        signals: list[Signal] = []
        skipped_placeholder = 0
        skipped_thin = 0
        skipped_bbo = 0
        skipped_exec = 0
        skipped_depth = 0

        for pair in pairs:
            a, b = pair.left, pair.right
            if self._placeholder_mid(a) or self._placeholder_mid(b):
                skipped_placeholder += 1
                continue
            if self._too_thin(a) or self._too_thin(b):
                skipped_thin += 1
                continue
            if self.require_bbo and (not a.has_bbo or not b.has_bbo):
                skipped_bbo += 1
                continue

            # Direction by mid (which venue is cheaper YES)
            if b.yes_price >= a.yes_price:
                cheap, rich = a, b
            else:
                cheap, rich = b, a

            edge, yes_cost, no_cost = self._executable_edge(cheap, rich)
            mid_spread = self._mid_spread(a, b)
            # Require executable edge (tiny epsilon for float mid arithmetic)
            if edge + 1e-9 < threshold:
                skipped_exec += 1
                continue

            leg_size = self._pair_size_usd(cheap, rich)
            if leg_size is None:
                skipped_depth += 1
                continue

            strength = min(1.0, max(edge, 0.0) / max(threshold * 2, 1e-9))
            group_id = f"arb-{uuid.uuid4().hex[:12]}"
            total_cost = yes_cost + no_cost

            signals.append(
                Signal(
                    market_id=cheap.id,
                    platform=str(cheap.platform),
                    side=Side.YES,
                    strength=strength,
                    edge=edge,
                    fair_prob=1.0 - no_cost,  # implied fair from other leg
                    market_prob=yes_cost,  # use executable price for paper fill
                    size_usd=leg_size,
                    reason=(
                        f"arb YES @ask={yes_cost:.3f} on {cheap.platform} vs NO @ask={no_cost:.3f} "
                        f"on {rich.platform} edge={edge:.3f} mid_spread={mid_spread:.3f} "
                        f"match={pair.score:.2f} | {cheap.title[:50]}"
                    ),
                    metadata={
                        "strategy": self.name,
                        "arb_pair": rich.venue_key,
                        "arb_group_id": group_id,
                        "arb_leg": "cheap_yes",
                        "match_score": pair.score,
                        "spread": mid_spread,
                        "exec_edge": edge,
                        "exec_yes_cost": yes_cost,
                        "exec_no_cost": no_cost,
                        "exec_total_cost": total_cost,
                        "leg": "cheap_yes",
                        "size_mode": "depth" if self.size_by_depth else "fixed",
                    },
                )
            )
            if self.emit_hedge_legs:
                signals.append(
                    Signal(
                        market_id=rich.id,
                        platform=str(rich.platform),
                        side=Side.NO,
                        strength=strength * 0.95,
                        edge=edge,
                        fair_prob=yes_cost,
                        market_prob=1.0 - no_cost,  # YES mid equiv; exec uses no via side
                        size_usd=leg_size,
                        reason=(
                            f"arb hedge NO @ask={no_cost:.3f} on {rich.platform} "
                            f"edge={edge:.3f} match={pair.score:.2f} group={group_id}"
                        ),
                        metadata={
                            "strategy": self.name,
                            "arb_pair": cheap.venue_key,
                            "arb_group_id": group_id,
                            "arb_leg": "rich_no",
                            "match_score": pair.score,
                            "spread": mid_spread,
                            "exec_edge": edge,
                            "exec_yes_cost": yes_cost,
                            "exec_no_cost": no_cost,
                            "exec_total_cost": total_cost,
                            "leg": "rich_no",
                            "size_mode": "depth" if self.size_by_depth else "fixed",
                            # Paper fill for NO uses market_prob as YES mid; override price hint
                            "exec_price": no_cost,
                        },
                    )
                )
            log.info(
                "arb_cross_signal",
                cheap=cheap.venue_key,
                rich=rich.venue_key,
                edge=round(edge, 4),
                mid_spread=round(mid_spread, 4),
                yes_cost=round(yes_cost, 4),
                no_cost=round(no_cost, 4),
                leg_usd=leg_size,
                match=round(pair.score, 3),
                group=group_id,
            )

        if any(
            (
                skipped_placeholder,
                skipped_thin,
                skipped_bbo,
                skipped_exec,
                skipped_depth,
            )
        ):
            log.info(
                "arb_cross_skips",
                placeholder_mid=skipped_placeholder,
                thin_book=skipped_thin,
                missing_bbo=skipped_bbo,
                exec_edge=skipped_exec,
                thin_depth=skipped_depth,
                signals=len(signals),
            )
        return signals
