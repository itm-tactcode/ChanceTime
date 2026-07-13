"""Fee and slippage models for prediction-market simulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


VenueName = Literal["default", "kalshi", "polymarket", "mock", "fixture", "history"]


@dataclass(frozen=True)
class CostModel:
    """Proportional costs + liquidity / L2-depth limited partial fills.

    - ``fee_bps``: commission as basis points of notional (entry).
    - ``slippage_bps``: adverse price move on entry (YES buy pays more, etc.).
    - ``liquidity_participation``: max fraction of displayed liquidity we take.
    - ``min_fill_ratio``: drop order if partial size < this fraction of request.
    - ``use_depth_size``: when bar has bid/ask sizes, cap by depth notional.
    """

    fee_bps: float = 100.0  # 1% default (conservative for PM venues)
    slippage_bps: float = 50.0  # 0.5%
    liquidity_participation: float = 0.25
    min_fill_ratio: float = 0.25
    use_depth_size: bool = True
    venue: str = "default"

    def fee_usd(self, notional: float) -> float:
        return abs(notional) * (self.fee_bps / 10_000.0)

    def apply_slippage(self, mid_price: float, *, buying: bool) -> float:
        """Return fill price in [0.01, 0.99] after slippage."""
        slip = self.slippage_bps / 10_000.0
        px = mid_price + slip if buying else mid_price - slip
        return max(0.01, min(0.99, px))

    def fill_price_from_bbo(
        self,
        *,
        mid: float,
        yes_bid: float | None,
        yes_ask: float | None,
        buying_yes: bool,
    ) -> float:
        """Prefer BBO for entry; fall back to mid ± slippage."""
        if buying_yes and yes_ask is not None:
            return max(0.01, min(0.99, float(yes_ask)))
        if not buying_yes and yes_bid is not None:
            # Buying NO ≈ paying 1 - yes_bid
            return max(0.01, min(0.99, 1.0 - float(yes_bid)))
        return self.apply_slippage(mid, buying=True)

    def clip_size_to_liquidity(self, size_usd: float, liquidity_usd: float) -> float | None:
        """Return fillable size or None if below min fill ratio."""
        if liquidity_usd <= 0:
            # No liquidity info → allow full size (legacy fixtures)
            return size_usd
        cap = liquidity_usd * max(0.0, self.liquidity_participation)
        filled = min(size_usd, cap)
        if filled < size_usd * self.min_fill_ratio:
            return None
        return filled

    def clip_size_to_depth(
        self,
        size_usd: float,
        *,
        depth_usd: float | None,
        liquidity_usd: float = 0.0,
    ) -> float | None:
        """Prefer L2 depth notional; else liquidity; else full size."""
        if self.use_depth_size and depth_usd is not None and depth_usd > 0:
            cap = depth_usd * max(0.0, self.liquidity_participation)
            filled = min(size_usd, cap)
            if filled < size_usd * self.min_fill_ratio:
                return None
            return filled
        return self.clip_size_to_liquidity(size_usd, liquidity_usd)


def cost_model_for_venue(venue: str, *, slippage_bps: float | None = None) -> CostModel:
    """Rough fee schedules for US PM venues (conservative; update as venues change).

    Kalshi: trading fees are contract/price dependent; we approximate with bps.
    Polymarket US: similarly simplified for simulation.
    """
    v = (venue or "default").lower().strip()
    # Approximate entry fees as % of notional
    table: dict[str, float] = {
        "kalshi": 70.0,  # ~0.7% effective (varies)
        "polymarket": 0.0,  # often no taker fee on international; US TBD — 0 + slip
        "mock": 50.0,
        "fixture": 100.0,
        "history": 80.0,
        "default": 100.0,
    }
    fee = table.get(v, table["default"])
    slip = 40.0 if v == "kalshi" else 50.0
    if slippage_bps is not None:
        slip = slippage_bps
    return CostModel(fee_bps=fee, slippage_bps=slip, venue=v)
