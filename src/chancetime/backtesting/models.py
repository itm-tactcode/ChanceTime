"""Backtest data structures for binary prediction markets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from chancetime.strategies.base import Side


class ResolveOutcome(StrEnum):
    YES = "yes"
    NO = "no"
    OPEN = "open"


@dataclass(frozen=True)
class MarketBar:
    """One snapshot of a market at a timestamp."""

    ts: datetime
    market_id: str
    yes_price: float
    liquidity_usd: float = 0.0
    title: str = ""
    platform: str = "fixture"
    # If set on a bar, market resolves at this timestamp (settles open positions).
    resolve: ResolveOutcome = ResolveOutcome.OPEN
    # Optional L2 / BBO (Phase 10)
    yes_bid: float | None = None
    yes_ask: float | None = None
    yes_bid_size: float | None = None
    yes_ask_size: float | None = None
    has_bbo: bool = False
    volume_usd: float = 0.0

    def depth_usd_yes_buy(self) -> float | None:
        if self.yes_ask_size is not None:
            px = self.yes_ask if self.yes_ask is not None else self.yes_price
            return float(self.yes_ask_size) * float(px)
        return None

    def depth_usd_no_buy(self) -> float | None:
        if self.yes_bid_size is not None:
            no_px = (
                (1.0 - float(self.yes_bid))
                if self.yes_bid is not None
                else (1.0 - self.yes_price)
            )
            return float(self.yes_bid_size) * no_px
        return None


@dataclass
class SimFill:
    ts: datetime
    market_id: str
    side: Side
    entry_price: float  # price paid per share (0-1) for that side
    size_usd: float  # notional spent before fees
    fee_usd: float
    contracts: float
    reason: str = ""


@dataclass
class SimPosition:
    market_id: str
    side: Side
    entry_ts: datetime
    entry_price: float
    size_usd: float
    fee_usd: float
    contracts: float
    reason: str = ""


@dataclass
class SimSettlement:
    ts: datetime
    market_id: str
    side: Side
    entry_price: float
    size_usd: float
    fee_usd: float
    contracts: float
    outcome: ResolveOutcome
    pnl_usd: float
    reason: str = ""


@dataclass
class EquityPoint:
    ts: datetime
    equity: float
    cash: float
    open_positions: int


@dataclass
class BacktestResult:
    """Summary of a single backtest run."""

    strategy_name: str
    params: dict[str, object]
    starting_cash: float
    ending_cash: float
    realized_pnl: float
    n_trades: int
    n_wins: int
    n_losses: int
    hit_rate: float
    max_drawdown: float
    fees_paid: float
    fills: list[SimFill] = field(default_factory=list)
    settlements: list[SimSettlement] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        return [
            f"strategy={self.strategy_name} params={self.params}",
            f"pnl=${self.realized_pnl:.2f}  start=${self.starting_cash:.2f}  "
            f"end=${self.ending_cash:.2f}",
            f"trades={self.n_trades}  wins={self.n_wins}  losses={self.n_losses}  "
            f"hit_rate={self.hit_rate:.1%}",
            f"max_drawdown={self.max_drawdown:.1%}  fees=${self.fees_paid:.2f}",
        ]
