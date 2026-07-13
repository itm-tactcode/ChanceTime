"""LLM-calibrated edge: fair prob from Grok, then threshold vs market.

Calls Grok only for screened candidates (liquidity + distance from 0.5 or new
markets), respects daily budget, and never places orders itself.
Optional web_search / x_search via xAI Responses API for breaking context.
"""

from __future__ import annotations

from chancetime.data_layer.models import Market
from chancetime.llm.calibrate import ProbabilityCalibrator
from chancetime.llm.client import GrokClient
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class LLMCalibratedStrategy(BaseStrategy):
    name = "llm_calibrated"

    def __init__(
        self,
        llm: GrokClient | None,
        *,
        calibrator: ProbabilityCalibrator | None = None,
        edge_threshold: float = 0.10,
        min_liquidity_usd: float = 100.0,
        min_confidence: float = 0.45,
        min_confidence_no_tools: float = 0.55,
        screen_threshold: float = 0.05,
        max_llm_calls_per_poll: int = 3,
        max_size_usd: float | None = None,
        enabled: bool = True,
        weight: float = 1.0,
        **params: object,
    ) -> None:
        super().__init__(
            edge_threshold=edge_threshold,
            min_liquidity_usd=min_liquidity_usd,
            min_confidence=min_confidence,
            screen_threshold=screen_threshold,
            max_llm_calls_per_poll=max_llm_calls_per_poll,
            enabled=enabled,
            weight=weight,
            **params,
        )
        self.llm = llm
        self.calibrator: ProbabilityCalibrator | None
        if calibrator is not None:
            self.calibrator = calibrator
        elif llm is not None:
            self.calibrator = ProbabilityCalibrator(llm)
        else:
            self.calibrator = None
        self.edge_threshold = edge_threshold
        self.min_liquidity_usd = min_liquidity_usd
        self.min_confidence = min_confidence
        self.min_confidence_no_tools = min_confidence_no_tools
        self.screen_threshold = screen_threshold
        self.max_llm_calls_per_poll = max_llm_calls_per_poll
        self.max_size_usd = max_size_usd
        self.weight = weight
        self._seen: set[str] = set()

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled or self.calibrator is None or self.llm is None:
            return []
        if not self.llm.settings.enabled:
            return []
        if self.llm.tracker.remaining() <= 0:
            log.warning("llm_calibrated_budget_exhausted")
            return []

        signals: list[Signal] = []
        calls = 0
        # Never per-market tools — use cached daily news brief only (cheap)
        tools_on = False

        for m in markets:
            if m.liquidity_usd < self.min_liquidity_usd:
                continue

            is_new = m.id not in self._seen
            interesting = abs(m.yes_price - 0.5) >= self.screen_threshold
            if not is_new and not interesting:
                continue
            if calls >= self.max_llm_calls_per_poll:
                log.info(
                    "llm_calibrated_call_cap",
                    cap=self.max_llm_calls_per_poll,
                    market_id=m.id,
                )
                break

            cal = await self.calibrator.calibrate(m, use_tools=tools_on)
            self._seen.add(m.id)
            calls += 1
            if cal is None:
                continue

            conf_floor = self.min_confidence
            if not tools_on or not cal.used_tools:
                conf_floor = max(conf_floor, self.min_confidence_no_tools)
            if cal.confidence < conf_floor:
                log.info(
                    "llm_calibrated_low_confidence",
                    market_id=m.id,
                    confidence=cal.confidence,
                    floor=conf_floor,
                    used_tools=cal.used_tools,
                )
                continue

            edge = cal.edge_vs_market
            if abs(edge) < self.edge_threshold:
                continue

            side = Side.YES if edge > 0 else Side.NO
            denom = max(self.edge_threshold * 2, 1e-9)
            strength = min(1.0, abs(edge) / denom * cal.confidence)
            size = self.max_size_usd
            sig = Signal(
                market_id=m.id,
                platform=str(m.platform),
                side=side,
                strength=strength,
                edge=edge,
                fair_prob=cal.probability,
                market_prob=m.yes_price,
                size_usd=size,
                reason=(
                    f"llm_edge={edge:.3f} thr={self.edge_threshold:.3f} "
                    f"fair={cal.probability:.2f} mkt={m.yes_price:.2f} "
                    f"conf={cal.confidence:.2f} tools={cal.used_tools} | "
                    f"{cal.reasoning[:80]}"
                ),
                metadata={
                    "strategy": self.name,
                    "llm_confidence": cal.confidence,
                    "llm_reasoning": cal.reasoning,
                    "llm_used_tools": cal.used_tools,
                    "llm_sources": cal.sources_note,
                    "yes_bid": m.yes_bid,
                    "yes_ask": m.yes_ask,
                },
            )
            signals.append(sig)
            log.info(
                "llm_calibrated_signal",
                market_id=m.id,
                side=side,
                edge=round(edge, 4),
                strength=round(strength, 3),
                used_tools=cal.used_tools,
            )

        return signals
