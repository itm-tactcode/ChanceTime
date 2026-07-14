"""Path D paper spot book — fail closed without a real quote."""

from __future__ import annotations

from dataclasses import dataclass, field

from chancetime.crypto_exchange.models import ExchangeFill, SpotQuote
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class SpotPosition:
    asset: str
    qty: float = 0.0
    avg_price: float = 0.0
    cost_usd: float = 0.0


@dataclass
class ExchangePaperBook:
    cash: float = 1000.0
    fee_bps: float = 30.0  # 0.30% default paper fee
    positions: dict[str, SpotPosition] = field(default_factory=dict)
    fills: list[ExchangeFill] = field(default_factory=list)
    venue: str = "coinbase"

    def available_cash(self) -> float:
        return self.cash

    def exposure_usd(self, quotes: dict[str, SpotQuote]) -> float:
        total = 0.0
        for asset, pos in self.positions.items():
            if pos.qty <= 0:
                continue
            q = quotes.get(asset)
            px = q.mid if q and q.mid else pos.avg_price
            total += pos.qty * px
        return total

    def mark_equity(self, quotes: dict[str, SpotQuote]) -> float:
        return self.cash + self.exposure_usd(quotes)

    def try_buy(
        self,
        quote: SpotQuote,
        *,
        size_usd: float,
        signal_id: str | None = None,
        note: str = "",
    ) -> str | None:
        if not quote.has_price:
            return "missing_price"
        px = quote.ask if quote.ask and quote.ask > 0 else quote.mid
        if px is None or px <= 0:
            return "bad_price"
        if size_usd <= 0:
            return "bad_size"
        fee = size_usd * (self.fee_bps / 10_000.0)
        total = size_usd + fee
        if total > self.cash + 1e-9:
            return "insufficient_cash"
        qty = size_usd / px
        asset = quote.asset.upper()
        pos = self.positions.get(asset) or SpotPosition(asset=asset)
        new_qty = pos.qty + qty
        new_cost = pos.cost_usd + size_usd
        pos.avg_price = new_cost / new_qty if new_qty > 0 else 0.0
        pos.qty = new_qty
        pos.cost_usd = new_cost
        self.positions[asset] = pos
        self.cash -= total
        import time

        fill = ExchangeFill(
            asset=asset,
            side="buy",
            price=px,
            qty=qty,
            size_usd=size_usd,
            fee_usd=fee,
            venue=self.venue,
            signal_id=signal_id,
            note=note,
            ts=time.time(),
        )
        self.fills.append(fill)
        log.info(
            "exchange_paper_buy",
            asset=asset,
            price=px,
            size_usd=size_usd,
            fee=round(fee, 4),
            signal_id=signal_id,
        )
        return None

    def try_sell(
        self,
        quote: SpotQuote,
        *,
        size_usd: float | None = None,
        qty: float | None = None,
        signal_id: str | None = None,
        note: str = "",
    ) -> str | None:
        if not quote.has_price:
            return "missing_price"
        px = quote.bid if quote.bid and quote.bid > 0 else quote.mid
        if px is None or px <= 0:
            return "bad_price"
        asset = quote.asset.upper()
        pos = self.positions.get(asset)
        if not pos or pos.qty <= 0:
            return "no_position"
        if qty is None:
            if size_usd is None or size_usd <= 0:
                return "bad_size"
            qty = min(pos.qty, size_usd / px)
        if qty <= 0 or qty > pos.qty + 1e-12:
            return "bad_qty"
        proceeds = qty * px
        fee = proceeds * (self.fee_bps / 10_000.0)
        net = proceeds - fee
        # Reduce cost basis proportionally
        frac = qty / pos.qty
        pos.cost_usd *= 1.0 - frac
        pos.qty -= qty
        if pos.qty < 1e-12:
            pos.qty = 0.0
            pos.cost_usd = 0.0
            pos.avg_price = 0.0
        self.cash += net
        import time

        fill = ExchangeFill(
            asset=asset,
            side="sell",
            price=px,
            qty=qty,
            size_usd=proceeds,
            fee_usd=fee,
            venue=self.venue,
            signal_id=signal_id,
            note=note,
            ts=time.time(),
        )
        self.fills.append(fill)
        log.info(
            "exchange_paper_sell",
            asset=asset,
            price=px,
            qty=qty,
            fee=round(fee, 4),
            signal_id=signal_id,
        )
        return None
