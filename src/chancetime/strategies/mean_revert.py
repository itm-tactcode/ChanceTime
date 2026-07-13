"""Mean-reversion: fade short-horizon yes_price moves vs local history.

If mid jumps more than ``move_threshold`` vs trailing mean, trade the fade
(buy YES after a dump, buy NO after a spike). Needs a few polls of history.
"""

from __future__ import annotations

from collections import defaultdict, deque

from chancetime.data_layer.models import Market
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class MeanRevertStrategy(BaseStrategy):
    name = "mean_revert"

    def __init__(
        self,
        *,
        move_threshold: float = 0.06,
        min_liquidity_usd: float = 100.0,
        history_window: int = 8,
        min_history: int = 3,
        enabled: bool = True,
        weight: float = 1.0,
        **params: object,
    ) -> None:
        super().__init__(
            move_threshold=move_threshold,
            min_liquidity_usd=min_liquidity_usd,
            history_window=history_window,
            min_history=min_history,
            enabled=enabled,
            weight=weight,
            **params,
        )
        self.move_threshold = move_threshold
        self.min_liquidity_usd = min_liquidity_usd
        self.history_window = max(3, history_window)
        self.min_history = max(2, min_history)
        self.weight = weight
        self._history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.history_window)
        )

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled:
            return []

        signals: list[Signal] = []
        for m in markets:
            if m.liquidity_usd < self.min_liquidity_usd:
                continue
            hist = self._history[m.id]
            market_p = m.yes_price
            if len(hist) >= self.min_history:
                mean = sum(hist) / len(hist)
                move = market_p - mean  # + = spiked up
                if abs(move) >= self.move_threshold:
                    # Fade: spike up → buy NO; dump → buy YES
                    side = Side.NO if move > 0 else Side.YES
                    edge = -move  # signed edge toward mean
                    strength = min(1.0, abs(move) / max(self.move_threshold * 2, 1e-9))
                    signals.append(
                        Signal(
                            market_id=m.id,
                            platform=str(m.platform),
                            side=side,
                            strength=strength,
                            edge=edge,
                            fair_prob=mean,
                            market_prob=market_p,
                            reason=(
                                f"mean_revert move={move:+.3f} mean={mean:.3f} "
                                f"mkt={market_p:.3f} thr={self.move_threshold:.3f}"
                            ),
                            metadata={
                                "strategy": self.name,
                                "move": move,
                                "trailing_mean": mean,
                            },
                        )
                    )
                    log.info(
                        "mean_revert_signal",
                        market_id=m.id,
                        side=str(side),
                        move=round(move, 4),
                        mean=round(mean, 4),
                    )
            hist.append(market_p)
        return signals
