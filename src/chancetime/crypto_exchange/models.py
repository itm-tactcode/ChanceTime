"""Path D models — spot quotes and paper inventory."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Side = Literal["buy", "sell"]


class SpotQuote(BaseModel):
    asset: str
    product_id: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    source: str
    ts: float

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last

    @property
    def has_price(self) -> bool:
        return self.mid is not None and self.mid > 0


class PaperSpotPosition(BaseModel):
    asset: str
    qty: float  # base units
    avg_price: float
    cost_usd: float


class ExchangeFill(BaseModel):
    asset: str
    side: Side
    price: float
    qty: float
    size_usd: float
    fee_usd: float
    venue: str
    signal_id: str | None = None
    note: str = ""
    ts: float = Field(default=0.0)
