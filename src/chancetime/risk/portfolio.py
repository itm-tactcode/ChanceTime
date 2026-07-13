"""Position book with open / reduce / close and mark-to-market."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from chancetime.strategies.base import Side
from chancetime.utils.logging import get_logger

log = get_logger(__name__)

# Allow sub-cent prediction-market prices. Do NOT floor at 0.01 — that creates
# fake take-profits when entry is 0.006 and MTM is forced to 0.01.
_PRICE_EPS = 1e-6


@dataclass
class Position:
    """Open binary contract position."""

    market_id: str
    platform: str
    side: Side
    size_usd: float
    entry_price: float  # price paid per share for this side (0-1)
    contracts: float
    strategy: str = ""
    opened_ts: float = field(default_factory=time.time)
    last_mark: float | None = None  # last MTM mid (yes price)

    @property
    def notional_at_entry(self) -> float:
        return self.size_usd


@dataclass
class ClosedTrade:
    market_id: str
    side: Side
    size_usd: float
    entry_price: float
    exit_price: float
    contracts: float
    realized_pnl: float
    reason: str
    strategy: str = ""
    closed_ts: float = field(default_factory=time.time)


class Portfolio:
    """In-memory portfolio: open positions + realized PnL + closed trades."""

    def __init__(self) -> None:
        self.positions: dict[str, Position] = {}
        self.closed: list[ClosedTrade] = []
        self.realized_pnl_today: float = 0.0

    @property
    def open_count(self) -> int:
        return len(self.positions)

    @property
    def total_exposure_usd(self) -> float:
        return sum(abs(p.size_usd) for p in self.positions.values())

    def available_cash(self, cash_basis: float) -> float:
        """Spendable cash: bankroll + realized PnL − capital locked in open size.

        Mirrors a real exchange: you cannot place orders larger than free cash.
        """
        return float(cash_basis) + self.realized_pnl_today - self.total_exposure_usd

    def get(self, market_id: str) -> Position | None:
        return self.positions.get(market_id)

    def open_position(
        self,
        *,
        market_id: str,
        platform: str,
        side: Side,
        size_usd: float,
        entry_price: float,
        strategy: str = "",
        contracts: float | None = None,
    ) -> Position:
        entry_price = _clamp_contract_price(entry_price)
        if contracts is None or contracts <= 0:
            contracts = size_usd / entry_price
        pos = Position(
            market_id=market_id,
            platform=platform,
            side=side,
            size_usd=size_usd,
            entry_price=entry_price,
            contracts=contracts,
            strategy=strategy,
        )
        self.positions[market_id] = pos
        log.info(
            "position_opened",
            market_id=market_id,
            side=str(side),
            size_usd=size_usd,
            entry_price=round(entry_price, 6),
            contracts=round(contracts, 4),
            strategy=strategy,
        )
        return pos

    def reduce(
        self,
        market_id: str,
        *,
        reduce_usd: float,
        exit_yes_mid: float,
        reason: str = "reduce",
    ) -> ClosedTrade | None:
        """Reduce position by notional USD; full close if reduce_usd >= size."""
        pos = self.positions.get(market_id)
        if pos is None:
            return None
        reduce_usd = min(reduce_usd, pos.size_usd)
        if reduce_usd <= 0:
            return None
        frac = reduce_usd / pos.size_usd
        contracts_out = pos.contracts * frac
        exit_price = self._side_price(pos.side, exit_yes_mid)
        pnl = self._pnl(pos.side, pos.entry_price, exit_price, contracts_out)
        trade = ClosedTrade(
            market_id=market_id,
            side=pos.side,
            size_usd=reduce_usd,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            contracts=contracts_out,
            realized_pnl=pnl,
            reason=reason,
            strategy=pos.strategy,
        )
        self.realized_pnl_today += pnl
        self.closed.append(trade)

        if reduce_usd >= pos.size_usd - 1e-9:
            del self.positions[market_id]
            log.info(
                "position_closed",
                market_id=market_id,
                pnl=round(pnl, 4),
                reason=reason,
            )
        else:
            pos.size_usd -= reduce_usd
            pos.contracts -= contracts_out
            log.info(
                "position_reduced",
                market_id=market_id,
                remaining_usd=round(pos.size_usd, 4),
                pnl=round(pnl, 4),
                reason=reason,
            )
        return trade

    def close(
        self,
        market_id: str,
        *,
        exit_yes_mid: float,
        reason: str = "close",
    ) -> ClosedTrade | None:
        pos = self.positions.get(market_id)
        if pos is None:
            return None
        return self.reduce(
            market_id,
            reduce_usd=pos.size_usd,
            exit_yes_mid=exit_yes_mid,
            reason=reason,
        )

    def mark_to_market(self, yes_mids: dict[str, float]) -> float:
        """Update marks; return total unrealized PnL."""
        unrealized = 0.0
        for mid, pos in self.positions.items():
            yes = yes_mids.get(mid)
            if yes is None:
                continue
            pos.last_mark = yes
            exit_px = self._side_price(pos.side, yes)
            unrealized += self._pnl(pos.side, pos.entry_price, exit_px, pos.contracts)
        return unrealized

    def equity_snapshot(self, cash_basis: float, yes_mids: dict[str, float]) -> dict[str, float]:
        """Paper wealth view (PnL accounting, not a full cash ledger).

        Model:
        - ``cash_basis`` = starting bankroll (does not decrease on each buy).
        - ``realized_pnl_today`` = sum of closed-trade PnL.
        - ``unrealized_pnl`` = mark-to-market gain/loss on open contracts only
          (not full position market value).
        - ``equity`` ≈ bankroll + cumulative PnL =
          ``cash_basis + realized + unrealized``.

        Capital tied up is ``exposure_usd`` (sum of open size_usd). Approximate
        free cash for risk display: ``cash_basis + realized - exposure_usd``.
        Buying $6 does not invent $6 of equity — only price moves do.
        """
        unrealized = self.mark_to_market(yes_mids)
        exposure = self.total_exposure_usd
        # MTM value of open book ≈ cost basis + unrealized
        position_mtm = exposure + unrealized
        free_cash = self.available_cash(cash_basis)
        equity = cash_basis + self.realized_pnl_today + unrealized
        return {
            "cash_basis": cash_basis,
            "realized_pnl_today": self.realized_pnl_today,
            "unrealized_pnl": unrealized,
            "equity": equity,
            "open_positions": float(self.open_count),
            "exposure_usd": exposure,
            "position_mtm": position_mtm,
            "free_cash_approx": free_cash,
            "available_cash": free_cash,
        }

    @staticmethod
    def _side_price(side: Side, yes_mid: float) -> float:
        yes_mid = _clamp_contract_price(yes_mid)
        if side == Side.YES:
            return yes_mid
        if side == Side.NO:
            return _clamp_contract_price(1.0 - yes_mid)
        return yes_mid

    @staticmethod
    def _pnl(side: Side, entry: float, exit_px: float, contracts: float) -> float:
        # Long contracts: PnL = (exit - entry) * contracts
        return (exit_px - entry) * contracts


def _clamp_contract_price(price: float) -> float:
    """Clamp binary contract price to (0, 1) without a 1¢ floor."""
    if price != price:  # NaN
        return 0.5
    return max(_PRICE_EPS, min(1.0 - _PRICE_EPS, float(price)))
