"""LLM-assisted cross-venue market matching (opt-in, cost-capped).

Two modes:
1. **Band adjudication** (preferred): fuzzy finds mid-band candidates
   (e.g. score 0.40–0.72); a tiny Grok call judges same-event yes/no.
2. **Bulk list match** (legacy / scan-arb --llm-match): send two title lists.

Never trust LLM for execution without a confidence floor. Results are cached.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from chancetime.data_layer.matching import (
    MarketPair,
    MatchCandidate,
    ensure_canonical_key,
    find_borderline_candidates,
)
from chancetime.data_layer.models import Market
from chancetime.llm.client import DailyBudgetExceeded, GrokClient
from chancetime.utils.logging import get_logger

log = get_logger(__name__)

SYSTEM_MATCH = (
    "You match prediction-market events across two exchanges "
    "(Kalshi and Polymarket US).\n"
    "Given two lists of markets (id + title), return ONLY pairs that are "
    "clearly the SAME underlying event/outcome.\n"
    "Do not force pairs. Prefer precision over recall. If unsure, omit.\n"
    "Respond with JSON only matching the schema."
)

SYSTEM_ADJUDICATE = (
    "You judge whether Kalshi and Polymarket US market titles are the "
    "SAME underlying event/outcome (dual listing).\n"
    "Rules:\n"
    "- same team/asset + same event type + same year/strike → same\n"
    "- different team, different strike, or different event type → not same\n"
    "- Fed rate *level* vs Fed *decision (bps)* are different contracts\n"
    "- Prefer precision; if unsure, same_event=false\n"
    "Respond with JSON only."
)


class VenueMatch(BaseModel):
    kalshi_id: str
    polymarket_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class VenueMatchBatch(BaseModel):
    pairs: list[VenueMatch] = Field(default_factory=list)


class AdjudicationVerdict(BaseModel):
    index: int
    same_event: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class AdjudicationBatch(BaseModel):
    verdicts: list[AdjudicationVerdict] = Field(default_factory=list)


async def llm_adjudicate_candidates(
    llm: GrokClient,
    candidates: list[MatchCandidate],
    *,
    min_confidence: float = 0.75,
    max_candidates: int = 24,
) -> list[MarketPair]:
    """Low-token yes/no on mid-band fuzzy candidates only.

    Prompt is intentionally small: index + two short titles + fuzzy score.
    """
    if not candidates:
        return []
    if not llm.settings.enabled or llm.tracker.remaining() <= 0:
        log.warning("llm_adjudicate_skipped_budget_or_disabled")
        return []

    batch = candidates[:max_candidates]
    rows = [
        {
            "i": i,
            "k_id": c.left.id,
            "k": c.left.title[:100],
            "p_id": c.right.id,
            "p": c.right.title[:100],
            "fuzzy": round(c.score, 3),
        }
        for i, c in enumerate(batch)
    ]
    messages = [
        {"role": "system", "content": SYSTEM_ADJUDICATE},
        {
            "role": "user",
            "content": (
                "For each candidate, is it the same dual-listed event?\n"
                f"{json.dumps(rows, separators=(',', ':'))}\n\n"
                'Return JSON: {"verdicts":[{"index":0,"same_event":true|'
                'false,"confidence":0-1,"reason":"short"}]}'
            ),
        },
    ]
    # ~80–150 tokens out for a dozen yes/no rows
    max_tokens = min(500, 80 + 28 * len(batch))
    try:
        result = await llm.structured(
            messages,
            AdjudicationBatch,
            max_tokens=max_tokens,
            prompt_summary=f"venue_adjudicate:n={len(batch)}",
            use_cache=True,
        )
    except DailyBudgetExceeded:
        log.warning("llm_adjudicate_budget_exceeded")
        return []
    except Exception:
        log.exception("llm_adjudicate_failed")
        return []

    pairs: list[MarketPair] = []
    used_right: set[str] = set()
    for v in result.verdicts:
        if not v.same_event or v.confidence < min_confidence:
            continue
        if v.index < 0 or v.index >= len(batch):
            continue
        cand = batch[v.index]
        if cand.right.venue_key in used_right:
            continue
        # Blend: keep LLM confidence but never below fuzzy (evidence it was mid-band)
        score = max(float(v.confidence), float(cand.score))
        pairs.append(MarketPair(left=cand.left, right=cand.right, score=score))
        used_right.add(cand.right.venue_key)
        log.info(
            "llm_adjudicate_pair",
            kalshi=cand.left.id,
            polymarket=cand.right.id,
            fuzzy=round(cand.score, 3),
            confidence=round(v.confidence, 3),
            reason=(v.reason or "")[:120],
        )
    log.info(
        "llm_adjudicate_done",
        candidates=len(batch),
        verdicts=len(result.verdicts),
        kept=len(pairs),
    )
    return pairs


async def llm_pair_markets(
    llm: GrokClient,
    kalshi: list[Market],
    polymarket: list[Market],
    *,
    max_each: int = 30,
    min_confidence: float = 0.75,
) -> list[MarketPair]:
    """Ask Grok to pair markets from two lists (heavier; prefer adjudicate)."""
    if not llm.settings.enabled or llm.tracker.remaining() <= 0:
        log.warning("llm_match_skipped_budget_or_disabled")
        return []
    if not kalshi or not polymarket:
        return []

    k_list = [ensure_canonical_key(m) for m in kalshi[:max_each]]
    p_list = [ensure_canonical_key(m) for m in polymarket[:max_each]]
    k_by_id = {m.id: m for m in k_list}
    p_by_id = {m.id: m for m in p_list}

    payload = {
        "kalshi": [{"id": m.id, "title": m.title, "yes": round(m.yes_price, 3)} for m in k_list],
        "polymarket_us": [
            {"id": m.id, "title": m.title, "yes": round(m.yes_price, 3)} for m in p_list
        ],
    }
    messages = [
        {"role": "system", "content": SYSTEM_MATCH},
        {
            "role": "user",
            "content": (
                "Match dual-listed events between Kalshi and Polymarket US.\n"
                f"Markets JSON:\n{json.dumps(payload)}\n\n"
                'Return JSON: {"pairs": [{"kalshi_id", "polymarket_id", '
                '"confidence" (0-1), "reason"}]}'
            ),
        },
    ]
    try:
        batch = await llm.structured(
            messages,
            VenueMatchBatch,
            max_tokens=800,
            prompt_summary=f"venue_match:k={len(k_list)}:p={len(p_list)}",
            use_cache=True,
        )
    except DailyBudgetExceeded:
        log.warning("llm_match_budget_exceeded")
        return []
    except Exception:
        log.exception("llm_match_failed")
        return []

    pairs: list[MarketPair] = []
    used_p: set[str] = set()
    for prop in batch.pairs:
        if prop.confidence < min_confidence:
            continue
        left = k_by_id.get(prop.kalshi_id)
        right = p_by_id.get(prop.polymarket_id)
        if left is None or right is None:
            continue
        if right.venue_key in used_p:
            continue
        pairs.append(MarketPair(left=left, right=right, score=prop.confidence))
        used_p.add(right.venue_key)
        log.info(
            "llm_match_pair",
            kalshi=left.id,
            polymarket=right.id,
            confidence=round(prop.confidence, 3),
            reason=prop.reason[:120],
        )
    log.info("llm_match_done", proposed=len(batch.pairs), kept=len(pairs))
    return pairs


async def hybrid_pair_markets(
    llm: GrokClient | None,
    kalshi: list[Market],
    polymarket: list[Market],
    *,
    min_score: float = 0.72,
    aliases: dict[str, str] | None = None,
    use_llm: bool = False,
    llm_band_low: float = 0.40,
    llm_min_confidence: float = 0.75,
    llm_max_candidates: int = 24,
    llm_bulk_fallback: bool = False,
) -> list[MarketPair]:
    """Fuzzy auto-pairs above ``min_score``; optional LLM on mid-band only.

    Band is ``[llm_band_low, min_score)``. Bulk list match is off by default
    (expensive); enable ``llm_bulk_fallback`` only when the band is empty and
    you still want a full-list Grok pass.
    """
    from chancetime.data_layer.matching import pair_markets

    fuzzy = pair_markets(
        kalshi,
        polymarket,
        min_score=min_score,
        aliases=aliases,
    )
    if not use_llm or llm is None:
        return fuzzy

    used_left = {p.left.venue_key for p in fuzzy}
    used_right = {p.right.venue_key for p in fuzzy}
    candidates = find_borderline_candidates(
        kalshi,
        polymarket,
        score_low=llm_band_low,
        score_high=min_score,
        exclude_left=used_left,
        exclude_right=used_right,
        max_candidates=llm_max_candidates,
    )
    llm_pairs: list[MarketPair] = []
    if candidates:
        llm_pairs = await llm_adjudicate_candidates(
            llm,
            candidates,
            min_confidence=llm_min_confidence,
            max_candidates=llm_max_candidates,
        )
    elif llm_bulk_fallback:
        # Rare path: no mid-band candidates but still want Grok list match
        llm_pairs = await llm_pair_markets(
            llm,
            kalshi,
            polymarket,
            max_each=llm_max_candidates,
            min_confidence=llm_min_confidence,
        )

    return merge_pairs(fuzzy, llm_pairs)


def merge_pairs(
    fuzzy: list[MarketPair],
    llm_pairs: list[MarketPair],
) -> list[MarketPair]:
    """Union pairs; prefer higher score; one right market only once."""
    by_left: dict[str, MarketPair] = {}
    used_right: set[str] = set()
    for p in sorted([*fuzzy, *llm_pairs], key=lambda x: x.score, reverse=True):
        lk, rk = p.left.venue_key, p.right.venue_key
        if lk in by_left or rk in used_right:
            continue
        by_left[lk] = p
        used_right.add(rk)
    return sorted(by_left.values(), key=lambda x: x.score, reverse=True)
