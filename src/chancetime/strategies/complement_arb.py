"""Same-market complement arb: buy YES + buy NO when ask sum < 1 after fees.

No LLM. Pure BBO math — suitable for tight poll loops.

Signals are dual-leg with ``arb_group_id`` so execution does both-or-neither.
Position keys use ``{market_id}::{side}`` so both legs can sit open until
resolution (or paper MTM).
"""

from __future__ import annotations

import uuid

from chancetime.data_layer.models import Market
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


def position_key(market_id: str, side: Side | str) -> str:
    """Stable portfolio key allowing YES and NO open on the same market."""
    s = side.value if isinstance(side, Side) else str(side).lower()
    return f"{market_id}::{s}"


def bare_market_id(key: str) -> str:
    """Strip ``::yes`` / ``::no`` suffix if present."""
    if "::" in key:
        return key.rsplit("::", 1)[0]
    return key


class ComplementArbStrategy(BaseStrategy):
    """Scan each market for executable YES ask + NO ask < 1 - fee_buffer."""

    name = "complement_arb"

    def __init__(
        self,
        *,
        min_edge: float = 0.01,
        fee_buffer: float = 0.02,
        require_bbo: bool = True,
        min_depth_usd: float = 5.0,
        max_leg_usd: float = 20.0,
        max_pair_usd: float = 40.0,
        min_liquidity_usd: float = 0.0,
        size_by_depth: bool = True,
        reject_synthetic: bool = True,
        max_hours_to_close: float = 0.0,
        enabled: bool = True,
        weight: float = 1.0,
        **params: object,
    ) -> None:
        super().__init__(
            min_edge=min_edge,
            fee_buffer=fee_buffer,
            require_bbo=require_bbo,
            min_depth_usd=min_depth_usd,
            max_leg_usd=max_leg_usd,
            max_pair_usd=max_pair_usd,
            min_liquidity_usd=min_liquidity_usd,
            size_by_depth=size_by_depth,
            reject_synthetic=reject_synthetic,
            max_hours_to_close=max_hours_to_close,
            enabled=enabled,
            weight=weight,
            **params,
        )
        self.min_edge = float(min_edge)
        self.fee_buffer = float(fee_buffer)
        self.require_bbo = bool(require_bbo)
        self.min_depth_usd = float(min_depth_usd)
        self.max_leg_usd = float(max_leg_usd)
        self.max_pair_usd = float(max_pair_usd)
        self.min_liquidity_usd = float(min_liquidity_usd)
        self.size_by_depth = bool(size_by_depth)
        self.reject_synthetic = bool(reject_synthetic)
        self.max_hours_to_close = float(max_hours_to_close)
        self.weight = float(weight)
        # Last poll diagnostics (for logs / tests)
        self.last_scan_count = 0
        self.last_gap_count = 0

    def _leg_size(self, m: Market) -> float | None:
        if not self.size_by_depth:
            leg = min(self.max_leg_usd, self.max_pair_usd / 2.0)
            return leg if leg >= self.min_depth_usd else None
        yd = m.depth_usd_for_yes_buy()
        nd = m.depth_usd_for_no_buy()
        if yd <= 0:
            yd = self.max_leg_usd
        if nd <= 0:
            nd = self.max_leg_usd
        per = min(yd, nd, self.max_leg_usd, self.max_pair_usd / 2.0)
        if per < self.min_depth_usd:
            return None
        return round(per, 4)

    def _hours_to_close(self, m: Market) -> float | None:
        if m.close_time is None:
            return None
        from datetime import UTC, datetime

        ct = m.close_time
        if ct.tzinfo is None:
            ct = ct.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return (ct - now).total_seconds() / 3600.0

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled:
            return []

        signals: list[Signal] = []
        scanned = 0
        gaps = 0
        skip_syn = 0
        skip_bbo = 0
        skip_edge = 0
        skip_depth = 0
        skip_liq = 0
        skip_tte = 0

        # Never mix mock fixtures into a live feed; pure-mock sessions keep fixtures.
        has_live = any(not m.synthetic for m in markets)
        for m in markets:
            if m.synthetic and has_live:
                skip_syn += 1
                continue
            scanned += 1
            if self.require_bbo and not m.has_bbo:
                skip_bbo += 1
                continue
            if self.min_liquidity_usd > 0:
                depth = max(m.liquidity_usd, m.volume_usd)
                if 0 < depth < self.min_liquidity_usd:
                    skip_liq += 1
                    continue
            if self.max_hours_to_close > 0:
                h = self._hours_to_close(m)
                if h is None or h < 0 or h > self.max_hours_to_close:
                    skip_tte += 1
                    continue

            yes_cost = m.yes_ask_exec()
            no_cost = m.no_ask_exec()
            edge = 1.0 - yes_cost - no_cost - self.fee_buffer
            if edge + 1e-9 < self.min_edge:
                skip_edge += 1
                continue

            leg = self._leg_size(m)
            if leg is None:
                skip_depth += 1
                continue

            gaps += 1
            group_id = f"comp-{uuid.uuid4().hex[:12]}"
            strength = min(1.0, max(edge, 0.0) / max(self.min_edge * 2, 1e-9))
            total = yes_cost + no_cost

            base_meta = {
                "strategy": self.name,
                "arb_group_id": group_id,
                "same_market_complement": True,
                "exec_edge": edge,
                "exec_yes_cost": yes_cost,
                "exec_no_cost": no_cost,
                "exec_total_cost": total,
                "fee_buffer": self.fee_buffer,
            }

            signals.append(
                Signal(
                    market_id=m.id,
                    platform=str(m.platform),
                    side=Side.YES,
                    strength=strength,
                    edge=edge,
                    fair_prob=1.0 - no_cost,
                    market_prob=yes_cost,
                    size_usd=leg,
                    reason=(
                        f"complement YES@ask={yes_cost:.3f}+NO@ask={no_cost:.3f}"
                        f"={total:.3f} edge={edge:.3f} | {m.title[:48]}"
                    ),
                    metadata={
                        **base_meta,
                        "arb_leg": "complement_yes",
                        "position_key": position_key(m.id, Side.YES),
                        "exec_price": yes_cost,
                    },
                )
            )
            signals.append(
                Signal(
                    market_id=m.id,
                    platform=str(m.platform),
                    side=Side.NO,
                    strength=strength * 0.99,
                    edge=edge,
                    fair_prob=yes_cost,
                    market_prob=1.0 - no_cost,
                    size_usd=leg,
                    reason=(
                        f"complement NO@ask={no_cost:.3f} hedge edge={edge:.3f} "
                        f"group={group_id}"
                    ),
                    metadata={
                        **base_meta,
                        "arb_leg": "complement_no",
                        "position_key": position_key(m.id, Side.NO),
                        "exec_price": no_cost,
                    },
                )
            )
            log.info(
                "complement_arb_signal",
                market=m.venue_key,
                edge=round(edge, 4),
                yes_ask=round(yes_cost, 4),
                no_ask=round(no_cost, 4),
                leg_usd=leg,
                group=group_id,
                synthetic=m.synthetic,
            )

        self.last_scan_count = scanned
        self.last_gap_count = gaps
        if skip_syn or skip_bbo or skip_edge or skip_depth or gaps:
            log.info(
                "complement_arb_scan",
                scanned=scanned,
                gaps=gaps,
                signals=len(signals),
                skip_synthetic=skip_syn,
                skip_bbo=skip_bbo,
                skip_edge=skip_edge,
                skip_depth=skip_depth,
                skip_liq=skip_liq,
                skip_tte=skip_tte,
            )
        return signals
