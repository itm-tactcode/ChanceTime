"""Probability calibration via Grok (cost-aware, cached, optional live search).

LLM output is advisory only — strategies convert it to Signals; risk/execution decide.
"""

from __future__ import annotations

from chancetime.data_layer.models import Market
from chancetime.llm.client import DailyBudgetExceeded, GrokClient
from chancetime.llm.prompts import (
    SYSTEM_PROBABILITY_CALIBRATION,
    SYSTEM_PROBABILITY_CALIBRATION_WITH_TOOLS,
    probability_calibration_user,
)
from chancetime.llm.schemas import ProbabilityCalibration
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class ProbabilityCalibrator:
    """Wrap Grok structured calibration with budget soft-fail and cache bust."""

    def __init__(
        self,
        llm: GrokClient,
        *,
        max_tokens: int = 256,
        price_move_bust: float = 0.05,
        news_context: str = "",
    ) -> None:
        self.llm = llm
        self.max_tokens = max_tokens
        self.price_move_bust = price_move_bust
        self.news_context = news_context.strip()
        self._last_yes: dict[str, float] = {}

    def _should_bust_cache(self, market: Market) -> bool:
        prev = self._last_yes.get(market.id)
        if prev is None:
            return False
        return abs(market.yes_price - prev) >= self.price_move_bust

    async def calibrate(
        self,
        market: Market,
        *,
        context: str = "",
        use_cache: bool | None = None,
        use_tools: bool | None = None,
    ) -> ProbabilityCalibration | None:
        """Return calibration or None if budget/disabled/parse failure."""
        if not self.llm.settings.enabled:
            return None
        if self.llm.tracker.remaining() <= 0:
            log.warning("calibrate_skipped_budget", market_id=market.id)
            return None

        bust = self._should_bust_cache(market)
        if use_cache is None:
            use_cache = not bust
        if bust:
            log.info(
                "calibrate_cache_bust",
                market_id=market.id,
                move=round(abs(market.yes_price - self._last_yes.get(market.id, 0)), 4),
            )

        ctx_parts = [p for p in (self.news_context, context) if p]
        combined_ctx = "\n".join(ctx_parts)

        # Default: never tools on per-market calibrate (use DailyNewsBrief cache).
        tools_wanted = use_tools
        if tools_wanted is None:
            tools_wanted = bool(
                getattr(self.llm.settings, "calibrate_with_tools", False)
            )
        if tools_wanted is True and not self.llm.allow_tool_call():
            tools_wanted = False

        tools_active = self.llm._tools_active(force_tools=tools_wanted)
        system = (
            SYSTEM_PROBABILITY_CALIBRATION_WITH_TOOLS
            if tools_active
            else SYSTEM_PROBABILITY_CALIBRATION
        )

        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": probability_calibration_user(
                    title=market.title,
                    description=market.description,
                    market_prob=market.yes_price,
                    context=combined_ctx,
                    yes_bid=market.yes_bid,
                    yes_ask=market.yes_ask,
                    platform=str(market.platform),
                    tools_hint=tools_active,
                ),
            },
        ]
        try:
            result = await self.llm.structured(
                messages,
                ProbabilityCalibration,
                max_tokens=self.max_tokens,
                use_cache=use_cache,
                prompt_summary=f"calibrate:{market.id}",
                use_tools=tools_wanted,
            )
            # Recompute edge from probability if model left it zero/wrong
            if abs(result.edge_vs_market) < 1e-9:
                result = result.model_copy(
                    update={"edge_vs_market": result.probability - market.yes_price}
                )
            self._last_yes[market.id] = market.yes_price
            log.info(
                "calibrated",
                market_id=market.id,
                fair=round(result.probability, 4),
                market=round(market.yes_price, 4),
                edge=round(result.edge_vs_market, 4),
                confidence=round(result.confidence, 3),
                used_tools=result.used_tools,
                sources_note=(result.sources_note or "")[:80],
                cache_busted=bust,
            )
            return result
        except DailyBudgetExceeded:
            log.warning("calibrate_budget_exceeded", market_id=market.id)
            return None
        except Exception:
            log.exception("calibrate_failed", market_id=market.id)
            return None
