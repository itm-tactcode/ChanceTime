"""Structured LLM outputs (Pydantic). Keep fields few to save tokens."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProbabilityCalibration(BaseModel):
    """Calibrated YES probability for a single market."""

    probability: float = Field(ge=0.0, le=1.0, description="Calibrated P(YES)")
    confidence: float = Field(ge=0.0, le=1.0, description="Self-rated confidence")
    reasoning: str = Field(default="", description="Short justification")
    edge_vs_market: float = Field(
        default=0.0,
        description="probability - market_yes (positive => YES undervalued)",
    )
    used_tools: bool = Field(
        default=False,
        description="Whether web_search / x_search informed this estimate",
    )
    sources_note: str = Field(
        default="",
        description="Brief note on search findings or 'none'",
    )


class PostTradeReview(BaseModel):
    """Batch review of closed / paper fills."""

    summary: str = ""
    process_wins: list[str] = Field(default_factory=list)
    process_errors: list[str] = Field(default_factory=list)
    strategy_suggestions: list[str] = Field(default_factory=list)
    overall_grade: str = Field(default="C", description="Letter grade A-F")
