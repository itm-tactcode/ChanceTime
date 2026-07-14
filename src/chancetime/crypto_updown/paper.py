"""Paper inventory for dual-side Up/Down (fail-closed).

Accounting:
- Buy: cash -= size_usd + fee; hold contracts = (size-fee)/ask
- MTM equity: cash + sum(contracts * mid)
- Settle: cash += contracts * 1.0 if win else 0; drop position

Without settlement, open positions only MTM — they do not "disappear" back to 1000.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chancetime.crypto_updown.models import UpDownMarket
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class PaperPosition:
    market_slug: str
    side: str  # up | down
    size_usd: float  # notional spent (pre-fee accounting bag)
    entry_price: float
    contracts: float
    fees_paid: float = 0.0


@dataclass
class CryptoPaperBook:
    cash: float = 1000.0
    fee_bps: float = 50.0
    positions: dict[tuple[str, str], PaperPosition] = field(default_factory=dict)
    fills: int = 0
    realized_pnl: float = 0.0
    settles: int = 0

    @property
    def cost_basis_usd(self) -> float:
        return sum(p.size_usd for p in self.positions.values())

    def exposure_mtm(self, markets: list[UpDownMarket]) -> float:
        by_slug = {m.slug: m for m in markets}
        total = 0.0
        for (slug, side), pos in self.positions.items():
            m = by_slug.get(slug)
            px = pos.entry_price
            if m is not None:
                book = m.up if side == "up" else m.down
                if book is not None and book.mid is not None and book.mid > 0:
                    px = float(book.mid)
            total += pos.contracts * px
        return total

    def available_cash(self) -> float:
        return self.cash

    def try_buy(
        self,
        market: UpDownMarket,
        *,
        side: str,
        size_usd: float,
    ) -> str | None:
        """Buy one side at ask. Deducts cash. Returns error reason or None."""
        side = side.lower()
        book = market.up if side == "up" else market.down
        if book is None:
            return "missing_outcome"
        if not book.has_bbo or book.best_ask is None:
            return "missing_bbo"  # fail closed — never invent
        if size_usd <= 0:
            return "bad_size"
        fee = size_usd * (self.fee_bps / 10_000.0)
        total_cost = size_usd + fee
        if total_cost > self.cash + 1e-9:
            return "insufficient_cash"
        if book.best_ask <= 0:
            return "bad_ask"
        # Contracts bought with the size_usd (fee paid on top in cash)
        contracts = size_usd / book.best_ask
        key = (market.slug, side)
        prev = self.positions.get(key)
        if prev:
            total_c = prev.contracts + contracts
            total_usd = prev.size_usd + size_usd
            avg = (
                prev.entry_price * prev.contracts + book.best_ask * contracts
            ) / total_c
            self.positions[key] = PaperPosition(
                market_slug=market.slug,
                side=side,
                size_usd=total_usd,
                entry_price=avg,
                contracts=total_c,
                fees_paid=prev.fees_paid + fee,
            )
        else:
            self.positions[key] = PaperPosition(
                market_slug=market.slug,
                side=side,
                size_usd=size_usd,
                entry_price=book.best_ask,
                contracts=contracts,
                fees_paid=fee,
            )
        self.cash -= total_cost
        self.fills += 1
        log.info(
            "crypto_paper_fill",
            slug=market.slug,
            side=side,
            price=book.best_ask,
            size_usd=size_usd,
            fee=round(fee, 4),
            cash=round(self.cash, 4),
        )
        return None

    def mark_equity(self, markets: list[UpDownMarket]) -> float:
        """cash + MTM of open contracts (not cost basis)."""
        return self.cash + self.exposure_mtm(markets)

    def settle_market(
        self,
        slug: str,
        *,
        resolved_up: bool,
    ) -> list[dict]:
        """Resolve all sides for slug: winners pay $1/contract, losers $0."""
        results: list[dict] = []
        keys = [k for k in self.positions if k[0] == slug]
        for key in keys:
            pos = self.positions.pop(key)
            side = pos.side
            won = (side == "up" and resolved_up) or (side == "down" and not resolved_up)
            payout = pos.contracts * (1.0 if won else 0.0)
            # PnL vs capital spent on this bag (size + fees allocated)
            spent = pos.size_usd + pos.fees_paid
            pnl = payout - spent
            self.cash += payout
            self.realized_pnl += pnl
            self.settles += 1
            row = {
                "slug": slug,
                "side": side,
                "contracts": pos.contracts,
                "entry_price": pos.entry_price,
                "size_usd": pos.size_usd,
                "fees_paid": pos.fees_paid,
                "payout": payout,
                "pnl": pnl,
                "won": won,
                "resolved_up": resolved_up,
            }
            results.append(row)
            log.info("crypto_paper_settle", **row, cash=round(self.cash, 4))
        return results
