"""Prompt templates for Grok LLM calls.

Keep prompts short to control cost. Prefer structured JSON outputs.
"""

from __future__ import annotations

SYSTEM_PROBABILITY_CALIBRATION = """You are a careful probability calibrator for prediction markets.
Given a market question and any context, output a calibrated probability between 0 and 1.
Be honest about uncertainty. Prefer conservative (closer to 0.5) estimates when evidence is weak.
Respond ONLY with valid JSON matching the schema provided."""

SYSTEM_PROBABILITY_CALIBRATION_WITH_TOOLS = """You are a careful probability calibrator for US prediction markets (Kalshi / Polymarket US).

You have server-side tools: web_search and x_search. USE THEM when they could change the probability:
- Breaking news, elections, sports, Fed, crypto, legal outcomes, anything time-sensitive
- Confirm whether the market question is still open / what the resolution criteria imply
- Cross-check recent X posts and reputable web sources for new information

Do NOT invent headlines. If tools find nothing useful, say so and stay near the market price with lower confidence.

You are advisory only — you do not place trades. Coded risk/execution will apply fees, spreads, and size caps.

Output ONLY valid JSON matching the schema (no markdown outside JSON). Include brief reasoning that cites what tools found (or that they found nothing)."""

SYSTEM_POST_TRADE_REVIEW = """You are a trading coach reviewing closed prediction-market trades.
Identify process errors, good decisions, and concrete strategy improvements.
Respond ONLY with valid JSON matching the schema provided."""


def probability_calibration_user(
    *,
    title: str,
    description: str,
    market_prob: float,
    context: str = "",
    yes_bid: float | None = None,
    yes_ask: float | None = None,
    platform: str = "",
    tools_hint: bool = False,
) -> str:
    bbo = ""
    if yes_bid is not None or yes_ask is not None:
        bbo = (
            f"YES bid: {yes_bid if yes_bid is not None else 'n/a'}  "
            f"YES ask: {yes_ask if yes_ask is not None else 'n/a'}\n"
        )
    tool_line = ""
    if tools_hint:
        tool_line = (
            "\nUse web_search and/or x_search if current events could move this market. "
            "Then return JSON only.\n"
        )
    return (
        f"Market: {title}\n"
        f"Platform: {platform or 'unknown'}\n"
        f"Description: {description}\n"
        f"Current market-implied probability (YES mid): {market_prob:.4f}\n"
        f"{bbo}"
        f"Additional context:\n{context or '(none)'}\n"
        f"{tool_line}\n"
        "Return JSON: "
        '{"probability": <float 0-1>, "confidence": <float 0-1>, '
        '"reasoning": "<short>", "edge_vs_market": <float>, '
        '"used_tools": <bool>, "sources_note": "<what search found or none>"}'
    )
