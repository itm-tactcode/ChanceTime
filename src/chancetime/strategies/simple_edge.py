"""Simple edge strategy: trade when |market_prob - fair_prob| exceeds threshold.

Priors:
  - ``static``: fixed ``default_fair_prob`` (default 0.5)
  - ``trailing_mean``: fade moves vs each market's own recent mean (ex-current)
  - ``blend``: mix static prior with trailing mean (resolution-aware soft prior)
"""

from __future__ import annotations

from collections import defaultdict, deque

from chancetime.data_layer.models import Market
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class SimpleEdgeStrategy(BaseStrategy):
    name = "simple_edge"

    def __init__(
        self,
        *,
        edge_threshold: float = 0.08,
        min_liquidity_usd: float = 100.0,
        default_fair_prob: float = 0.5,
        prior_mode: str = "blend",  # static | trailing_mean | blend
        blend_alpha: float = 0.5,  # weight on trailing mean when prior_mode=blend
        history_window: int = 5,
        min_history: int = 3,
        enabled: bool = True,
        weight: float = 1.0,
        min_yes_price: float = 0.05,
        max_yes_price: float = 0.95,
        **params: object,
    ) -> None:
        super().__init__(
            edge_threshold=edge_threshold,
            min_liquidity_usd=min_liquidity_usd,
            default_fair_prob=default_fair_prob,
            prior_mode=prior_mode,
            blend_alpha=blend_alpha,
            history_window=history_window,
            min_history=min_history,
            enabled=enabled,
            weight=weight,
            min_yes_price=min_yes_price,
            max_yes_price=max_yes_price,
            **params,
        )
        self.edge_threshold = edge_threshold
        self.min_liquidity_usd = min_liquidity_usd
        self.default_fair_prob = default_fair_prob
        self.prior_mode = prior_mode.lower().strip()
        self.blend_alpha = max(0.0, min(1.0, blend_alpha))
        self.history_window = max(2, history_window)
        self.min_history = max(2, min_history)
        self.weight = weight
        self.min_yes_price = max(0.0, min_yes_price)
        self.max_yes_price = min(1.0, max_yes_price)
        self._history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.history_window)
        )

    def _fair_prob(self, market_id: str, market_p: float) -> float:
        hist = self._history[market_id]
        static = self.default_fair_prob
        if len(hist) < self.min_history:
            return static
        trail = sum(hist) / len(hist)
        if self.prior_mode == "trailing_mean":
            return trail
        if self.prior_mode == "blend":
            a = self.blend_alpha
            return a * trail + (1.0 - a) * static
        return static

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled:
            return []

        signals: list[Signal] = []
        for m in markets:
            if m.liquidity_usd < self.min_liquidity_usd:
                continue

            market_p = m.yes_price
            # Static 0.5 prior invents huge "edges" on 1¢ longshots — skip by default
            if market_p < self.min_yes_price or market_p > self.max_yes_price:
                self._history[m.id].append(market_p)
                continue

            hist = self._history[m.id]
            # CRITICAL: do not trade on pure static 0.5 before we have a trailing
            # mean. Cold-start blend was filling every 5–6¢ YES as "edge=0.44".
            if len(hist) < self.min_history:
                hist.append(market_p)
                continue

            fair = self._fair_prob(m.id, market_p)
            # Update history after computing fair so trailing uses past only
            hist.append(market_p)

            edge = fair - market_p  # positive => YES undervalued vs prior

            if abs(edge) < self.edge_threshold:
                continue

            denom = max(self.edge_threshold * 2, 1e-9)
            side = Side.YES if edge > 0 else Side.NO
            strength = min(1.0, abs(edge) / denom)
            sig = Signal(
                market_id=m.id,
                platform=str(m.platform),
                side=side,
                strength=strength,
                edge=edge,
                fair_prob=fair,
                market_prob=market_p,
                reason=(
                    f"edge={edge:.3f} thr={self.edge_threshold:.3f} "
                    f"fair={fair:.2f} mkt={market_p:.2f} prior={self.prior_mode}"
                ),
                metadata={"strategy": self.name, "prior_mode": self.prior_mode},
            )
            signals.append(sig)
            log.info(
                "simple_edge_signal",
                market_id=m.id,
                side=side,
                edge=round(edge, 4),
                strength=round(strength, 3),
                prior_mode=self.prior_mode,
            )
        return signals
