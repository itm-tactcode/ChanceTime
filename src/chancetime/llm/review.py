"""Post-trade review batch (async, cost-capped)."""

from __future__ import annotations

import json
from typing import Any

from chancetime.execution.engine import Fill
from chancetime.llm.client import DailyBudgetExceeded, GrokClient
from chancetime.llm.prompts import SYSTEM_POST_TRADE_REVIEW
from chancetime.llm.schemas import PostTradeReview
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


def _fill_to_dict(f: Fill) -> dict[str, Any]:
    return {
        "market_id": f.market_id,
        "side": str(f.side),
        "price": f.price,
        "size_usd": f.size_usd,
        "status": str(f.status),
        "paper": f.paper,
        "note": f.note,
    }


async def review_fills(
    llm: GrokClient,
    fills: list[Fill],
    *,
    max_fills: int = 20,
) -> PostTradeReview | None:
    """Review a batch of fills. Returns None if skipped/failed."""
    if not fills:
        log.info("review_skipped_no_fills")
        return None
    if not llm.settings.enabled:
        return None
    if llm.tracker.remaining() <= 0:
        log.warning("review_skipped_budget")
        return None

    sample = fills[-max_fills:]
    payload = json.dumps([_fill_to_dict(f) for f in sample], default=str)
    messages = [
        {"role": "system", "content": SYSTEM_POST_TRADE_REVIEW},
        {
            "role": "user",
            "content": (
                f"Review these {len(sample)} prediction-market fills (paper or live).\n"
                f"Fills JSON:\n{payload}\n\n"
                "Return JSON: summary, process_wins[], process_errors[], "
                "strategy_suggestions[], overall_grade."
            ),
        },
    ]
    try:
        review = await llm.structured(
            messages,
            PostTradeReview,
            max_tokens=400,
            prompt_summary=f"post_trade_review:n={len(sample)}",
            use_cache=False,
        )
    except DailyBudgetExceeded:
        log.warning("review_budget_exceeded")
        return None
    except Exception:
        log.exception("review_failed")
        return None

    log.info(
        "post_trade_review",
        grade=review.overall_grade,
        summary=review.summary[:200],
        n_suggestions=len(review.strategy_suggestions),
    )
    return review
