"""Normalized models for crypto Up/Down windows."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SpotTick(BaseModel):
    symbol: str
    price: float
    source: str
    ts: float


class OutcomeBook(BaseModel):
    """One outcome token (Up or Down)."""

    token_id: str
    outcome: str  # Up | Down | Yes | No
    mid: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    has_bbo: bool = False


class UpDownMarket(BaseModel):
    """A single time-window binary market."""

    condition_id: str
    slug: str
    question: str
    asset: str  # BTC, ETH, ...
    window_start: datetime | None = None
    window_end: datetime | None = None
    up: OutcomeBook | None = None
    down: OutcomeBook | None = None
    volume: float = 0.0
    raw: dict = Field(default_factory=dict)

    def complete_set_ask_sum(self) -> float | None:
        """Sum of asks to buy both sides (None if missing BBO)."""
        if not self.up or not self.down:
            return None
        if self.up.best_ask is None or self.down.best_ask is None:
            return None
        return float(self.up.best_ask) + float(self.down.best_ask)

    def seconds_remaining(self, now: float | None = None) -> float | None:
        import time
        from datetime import UTC

        if self.window_end is None:
            return None
        end = self.window_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        t = now if now is not None else time.time()
        return end.timestamp() - t
