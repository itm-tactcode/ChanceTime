"""Pluggable strategy base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from chancetime.data_layer.models import Market


class Side(StrEnum):
    YES = "yes"
    NO = "no"
    FLAT = "flat"


class Signal(BaseModel):
    """Strategy output: intent to trade a market (not an order yet)."""

    market_id: str
    platform: str
    side: Side
    strength: float = Field(ge=0.0, le=1.0, description="Signal confidence 0-1")
    edge: float = 0.0
    fair_prob: float | None = None
    market_prob: float | None = None
    size_usd: float | None = None
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BaseStrategy(ABC):
    """Abstract strategy: generate signals from market snapshots.

    Strategies must NOT place orders. They only emit Signals for the
    risk + execution layers to filter and act on.
    """

    name: str = "base"

    def __init__(self, **params: Any) -> None:
        self.params = params
        self.enabled: bool = bool(params.get("enabled", True))
        # Named data.profiles.* key — bot builds once per poll per distinct name
        self.universe: str = str(params.get("universe") or "broad")

    @property
    def universe_name(self) -> str:
        """Market universe profile this strategy should receive."""
        return self.universe or "broad"

    @abstractmethod
    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        """Produce zero or more signals from current market data."""

    async def on_fill(self, signal: Signal, fill_price: float, size_usd: float) -> None:
        """Optional hook after a fill (paper or live)."""
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "universe": self.universe_name,
            "params": self.params,
        }
