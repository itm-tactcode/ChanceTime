"""Shared market data models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Platform(StrEnum):
    MOCK = "mock"
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class Market(BaseModel):
    """Normalized prediction market snapshot."""

    id: str
    platform: Platform
    title: str
    description: str = ""
    # Probability of YES (0-1). For binary markets. Prefer mid of bid/ask when known.
    yes_price: float = Field(ge=0.0, le=1.0)
    no_price: float = Field(ge=0.0, le=1.0)
    volume_usd: float = 0.0
    liquidity_usd: float = 0.0
    close_time: datetime | None = None
    url: str | None = None
    slug: str | None = None
    # Normalized key for cross-venue matching (filled by matcher if empty)
    canonical_key: str = ""
    # Best bid/ask for YES (0-1). When set, use for executable arb — not mid alone.
    yes_bid: float | None = Field(default=None, ge=0.0, le=1.0)
    yes_ask: float | None = Field(default=None, ge=0.0, le=1.0)
    # Size at best level (contracts when known; else notional proxy)
    yes_bid_size: float | None = Field(default=None, ge=0.0)
    yes_ask_size: float | None = Field(default=None, ge=0.0)
    has_bbo: bool = False
    raw: dict[str, object] = Field(default_factory=dict)

    @property
    def mid_prob(self) -> float:
        return self.yes_price

    @property
    def venue_key(self) -> str:
        """Stable id including platform for multi-venue books."""
        return f"{self.platform}:{self.id}"

    def yes_ask_exec(self) -> float:
        """Price to buy YES (prefer ask; fall back to mid)."""
        if self.yes_ask is not None:
            return self.yes_ask
        return self.yes_price

    def no_ask_exec(self) -> float:
        """Price to buy NO (prefer 1 - yes_bid; fall back to 1 - mid)."""
        if self.yes_bid is not None:
            return max(0.0, min(1.0, 1.0 - self.yes_bid))
        return max(0.0, min(1.0, 1.0 - self.yes_price))

    def depth_usd_for_yes_buy(self) -> float:
        """Approx notional available to lift YES ask."""
        if self.yes_ask_size is not None and self.yes_ask is not None:
            return float(self.yes_ask_size) * float(self.yes_ask)
        if self.yes_ask_size is not None:
            return float(self.yes_ask_size) * self.yes_ask_exec()
        return max(self.liquidity_usd, 0.0)

    def depth_usd_for_no_buy(self) -> float:
        """Approx notional available to lift NO ask (YES bid size via reciprocity)."""
        no_ask = self.no_ask_exec()
        if self.yes_bid_size is not None:
            return float(self.yes_bid_size) * no_ask
        return max(self.liquidity_usd, 0.0)
