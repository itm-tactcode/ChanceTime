"""News impulse: optional Grok read on markets when news_context is set.

Only fires when ``news_context`` is non-empty and LLM budget allows.
Uses a short structured prompt: fair shift vs current mid after headline.
"""

from __future__ import annotations

from chancetime.data_layer.models import Market
from chancetime.llm.client import GrokClient
from chancetime.llm.schemas import ProbabilityCalibration
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class NewsImpulseStrategy(BaseStrategy):
    name = "news_impulse"

    def __init__(
        self,
        *,
        llm: GrokClient | None = None,
        news_context: str = "",
        edge_threshold: float = 0.06,
        min_liquidity_usd: float = 100.0,
        min_confidence: float = 0.4,
        max_llm_calls_per_poll: int = 2,
        enabled: bool = False,
        weight: float = 1.0,
        **params: object,
    ) -> None:
        super().__init__(
            edge_threshold=edge_threshold,
            min_liquidity_usd=min_liquidity_usd,
            min_confidence=min_confidence,
            max_llm_calls_per_poll=max_llm_calls_per_poll,
            enabled=enabled,
            weight=weight,
            **params,
        )
        self.llm = llm
        self.news_context = (news_context or "").strip()
        self.edge_threshold = edge_threshold
        self.min_liquidity_usd = min_liquidity_usd
        self.min_confidence = min_confidence
        self.max_llm_calls_per_poll = max(0, max_llm_calls_per_poll)
        self.weight = weight

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled or not self.news_context:
            return []
        if self.llm is None or self.max_llm_calls_per_poll <= 0:
            log.info("news_impulse_skip", reason="no_llm_or_budget_calls")
            return []

        # Prefer liquid markets whose title loosely relates to news tokens
        tokens = {t for t in self.news_context.lower().split() if len(t) > 3}
        candidates = [
            m
            for m in markets
            if m.liquidity_usd >= self.min_liquidity_usd
            and (not tokens or any(t in m.title.lower() for t in tokens))
        ]
        if not candidates:
            candidates = [m for m in markets if m.liquidity_usd >= self.min_liquidity_usd][
                : self.max_llm_calls_per_poll
            ]
        candidates = candidates[: self.max_llm_calls_per_poll]

        signals: list[Signal] = []
        for m in candidates:
            prompt = (
                f"News context:\n{self.news_context[:1200]}\n\n"
                f"Market: {m.title}\nCurrent YES mid: {m.yes_price:.3f}\n"
                "Given the news only, estimate a calibrated YES probability 0-1 "
                "and confidence."
            )
            try:
                cal = await self.llm.structured(
                    [{"role": "user", "content": prompt}],
                    ProbabilityCalibration,
                    prompt_summary=f"news_impulse:{m.id[:40]}",
                )
            except Exception:
                log.exception("news_impulse_llm_failed", market_id=m.id)
                continue
            if cal is None:
                continue
            fair = float(cal.probability)
            conf = float(cal.confidence or 0.5)
            if conf < self.min_confidence:
                continue
            edge = fair - m.yes_price
            if abs(edge) < self.edge_threshold:
                continue
            side = Side.YES if edge > 0 else Side.NO
            strength = min(1.0, abs(edge) / max(self.edge_threshold * 2, 1e-9) * conf)
            signals.append(
                Signal(
                    market_id=m.id,
                    platform=str(m.platform),
                    side=side,
                    strength=strength,
                    edge=edge,
                    fair_prob=fair,
                    market_prob=m.yes_price,
                    reason=(
                        f"news_impulse edge={edge:.3f} fair={fair:.2f} "
                        f"mkt={m.yes_price:.2f} conf={conf:.2f}"
                    ),
                    metadata={"strategy": self.name, "confidence": conf},
                )
            )
            log.info(
                "news_impulse_signal",
                market_id=m.id,
                edge=round(edge, 4),
                fair=round(fair, 4),
            )
        return signals
