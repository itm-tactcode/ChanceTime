"""Event-driven backtester for binary prediction markets.

Replays MarketBar series → strategy signals → simulated fills → settle on resolve.

This is intentionally separate from live ExecutionEngine: same strategy
interfaces, no network, no PAPER_MODE side effects on real venues.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from chancetime.backtesting.fees import CostModel
from chancetime.backtesting.metrics import hit_rate, max_drawdown
from chancetime.backtesting.models import (
    BacktestResult,
    EquityPoint,
    MarketBar,
    ResolveOutcome,
    SimFill,
    SimPosition,
    SimSettlement,
)
from chancetime.data_layer.models import Market, Platform
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class BacktestEngine:
    """Replay bars and evaluate a single strategy."""

    def __init__(
        self,
        *,
        starting_cash: float = 1_000.0,
        order_size_usd: float = 10.0,
        max_open_positions: int = 10,
        costs: CostModel | None = None,
    ) -> None:
        self.starting_cash = starting_cash
        self.order_size_usd = order_size_usd
        self.max_open_positions = max_open_positions
        self.costs = costs or CostModel()

    async def run(
        self,
        bars: list[MarketBar],
        strategy: BaseStrategy,
        *,
        params: dict[str, Any] | None = None,
    ) -> BacktestResult:
        cash = self.starting_cash
        open_pos: dict[str, SimPosition] = {}
        fills: list[SimFill] = []
        settlements: list[SimSettlement] = []
        equity_curve: list[EquityPoint] = []
        fees_paid = 0.0

        # Group bars by timestamp (event clock)
        by_ts: dict[datetime, list[MarketBar]] = defaultdict(list)
        for b in bars:
            by_ts[b.ts].append(b)
        timeline = sorted(by_ts.keys())

        # Latest quote per market (for MTM / depth)
        last_yes: dict[str, float] = {}
        last_liq: dict[str, float] = {}
        last_bar: dict[str, MarketBar] = {}

        for ts in timeline:
            event_bars = by_ts[ts]

            # 1) Apply resolutions first
            for bar in event_bars:
                last_yes[bar.market_id] = bar.yes_price
                last_liq[bar.market_id] = bar.liquidity_usd
                last_bar[bar.market_id] = bar
                if bar.resolve is ResolveOutcome.OPEN:
                    continue
                if bar.market_id in open_pos:
                    pos = open_pos.pop(bar.market_id)
                    # Entry already deducted size_usd + fee. Resolution pays contracts*$1 or 0.
                    payout = self._payout(pos, bar.resolve)
                    pnl = payout - pos.size_usd - pos.fee_usd
                    cash += payout
                    settlements.append(
                        SimSettlement(
                            ts=ts,
                            market_id=pos.market_id,
                            side=pos.side,
                            entry_price=pos.entry_price,
                            size_usd=pos.size_usd,
                            fee_usd=pos.fee_usd,
                            contracts=pos.contracts,
                            outcome=bar.resolve,
                            pnl_usd=pnl,
                            reason=pos.reason,
                        )
                    )
                    log.info(
                        "bt_settle",
                        market_id=pos.market_id,
                        outcome=str(bar.resolve),
                        pnl=round(pnl, 4),
                    )

            # 2) Build market snapshots for open (non-resolving-this-tick) quotes
            markets: list[Market] = []
            for bar in event_bars:
                if bar.resolve is not ResolveOutcome.OPEN:
                    continue
                markets.append(self._bar_to_market(bar))

            if markets and strategy.enabled:
                signals = await strategy.generate_signals(markets)
                for sig in signals:
                    if sig.market_id in open_pos:
                        continue
                    if len(open_pos) >= self.max_open_positions:
                        break
                    size = sig.size_usd if sig.size_usd is not None else self.order_size_usd
                    liq = last_liq.get(sig.market_id, 0.0)
                    bar = last_bar.get(sig.market_id)
                    depth: float | None = None
                    if bar is not None:
                        if sig.side == Side.YES:
                            depth = bar.depth_usd_yes_buy()
                        elif sig.side == Side.NO:
                            depth = bar.depth_usd_no_buy()
                    clipped = self.costs.clip_size_to_depth(
                        size, depth_usd=depth, liquidity_usd=liq
                    )
                    if clipped is None:
                        log.info(
                            "bt_partial_skip",
                            market_id=sig.market_id,
                            requested=size,
                            liquidity=liq,
                            depth=depth,
                        )
                        continue
                    if clipped + 1e-9 < size:
                        log.info(
                            "bt_partial_fill",
                            market_id=sig.market_id,
                            requested=round(size, 4),
                            filled=round(clipped, 4),
                        )
                    size = clipped
                    if size + self.costs.fee_usd(size) > cash:
                        continue
                    fill = self._simulate_fill(ts, sig, size, bar=bar)
                    if fill is None:
                        continue
                    cash -= fill.size_usd + fill.fee_usd
                    fees_paid += fill.fee_usd
                    open_pos[fill.market_id] = SimPosition(
                        market_id=fill.market_id,
                        side=fill.side,
                        entry_ts=fill.ts,
                        entry_price=fill.entry_price,
                        size_usd=fill.size_usd,
                        fee_usd=fill.fee_usd,
                        contracts=fill.contracts,
                        reason=fill.reason,
                    )
                    fills.append(fill)
                    log.info(
                        "bt_fill",
                        market_id=fill.market_id,
                        side=str(fill.side),
                        price=round(fill.entry_price, 4),
                        size=fill.size_usd,
                    )

            # 3) Mark equity: cash + MTM of open positions
            mtm = sum(
                self._mtm(pos, last_yes.get(pos.market_id, pos.entry_price))
                for pos in open_pos.values()
            )
            equity_curve.append(
                EquityPoint(
                    ts=ts,
                    equity=cash + mtm,
                    cash=cash,
                    open_positions=len(open_pos),
                )
            )

        # Force-close remaining at last mid (no resolution)
        if open_pos and timeline:
            last_ts = timeline[-1]
            for mid, pos in list(open_pos.items()):
                mid_yes = last_yes.get(mid, pos.entry_price)
                value = self._mtm(pos, mid_yes)
                pnl = value - pos.size_usd - pos.fee_usd
                cash += value
                settlements.append(
                    SimSettlement(
                        ts=last_ts,
                        market_id=mid,
                        side=pos.side,
                        entry_price=pos.entry_price,
                        size_usd=pos.size_usd,
                        fee_usd=pos.fee_usd,
                        contracts=pos.contracts,
                        outcome=ResolveOutcome.OPEN,
                        pnl_usd=pnl,
                        reason=pos.reason + " | eod_mtm",
                    )
                )
            open_pos.clear()

        wins, losses, hr = hit_rate(settlements)
        realized = sum(s.pnl_usd for s in settlements)
        # cash already reflects entries/exits; cross-check with start + realized
        ending = cash

        return BacktestResult(
            strategy_name=strategy.name,
            params=params or dict(strategy.params),
            starting_cash=self.starting_cash,
            ending_cash=ending,
            realized_pnl=realized,
            n_trades=len(settlements),
            n_wins=wins,
            n_losses=losses,
            hit_rate=hr,
            max_drawdown=max_drawdown(equity_curve),
            fees_paid=fees_paid,
            fills=fills,
            settlements=settlements,
            equity_curve=equity_curve,
        )

    def _simulate_fill(
        self,
        ts: datetime,
        sig: Signal,
        size: float,
        *,
        bar: MarketBar | None = None,
    ) -> SimFill | None:
        if sig.side == Side.FLAT or sig.market_prob is None:
            return None
        mid_yes = sig.market_prob
        if sig.side == Side.YES:
            if bar is not None and (bar.yes_ask is not None or bar.has_bbo):
                px = self.costs.fill_price_from_bbo(
                    mid=mid_yes,
                    yes_bid=bar.yes_bid,
                    yes_ask=bar.yes_ask,
                    buying_yes=True,
                )
            else:
                px = self.costs.apply_slippage(mid_yes, buying=True)
        else:
            if bar is not None and (bar.yes_bid is not None or bar.has_bbo):
                px = self.costs.fill_price_from_bbo(
                    mid=mid_yes,
                    yes_bid=bar.yes_bid,
                    yes_ask=bar.yes_ask,
                    buying_yes=False,
                )
            else:
                px = self.costs.apply_slippage(1.0 - mid_yes, buying=True)
        fee = self.costs.fee_usd(size)
        contracts = size / px if px > 0 else 0.0
        return SimFill(
            ts=ts,
            market_id=sig.market_id,
            side=sig.side,
            entry_price=px,
            size_usd=size,
            fee_usd=fee,
            contracts=contracts,
            reason=sig.reason,
        )

    @staticmethod
    def _payout(pos: SimPosition, outcome: ResolveOutcome) -> float:
        """Cash received at resolution (before comparing to cost)."""
        if outcome is ResolveOutcome.YES:
            return pos.contracts if pos.side == Side.YES else 0.0
        if outcome is ResolveOutcome.NO:
            return pos.contracts if pos.side == Side.NO else 0.0
        return 0.0

    def _settle_pnl(self, pos: SimPosition, outcome: ResolveOutcome) -> float:
        """PnL = payout - size - fees."""
        return self._payout(pos, outcome) - pos.size_usd - pos.fee_usd

    def _mtm(self, pos: SimPosition, yes_mid: float) -> float:
        """Mark position to mid (exitable value of shares)."""
        yes_mid = max(0.01, min(0.99, yes_mid))
        if pos.side == Side.YES:
            return pos.contracts * yes_mid
        return pos.contracts * (1.0 - yes_mid)

    @staticmethod
    def _bar_to_market(bar: MarketBar) -> Market:
        try:
            platform = Platform(bar.platform)
        except ValueError:
            platform = Platform.MOCK
        yes = max(0.0, min(1.0, bar.yes_price))
        return Market(
            id=bar.market_id,
            platform=platform,
            title=bar.title or bar.market_id,
            yes_price=yes,
            no_price=max(0.0, min(1.0, 1.0 - yes)),
            liquidity_usd=bar.liquidity_usd,
            volume_usd=bar.volume_usd,
            yes_bid=bar.yes_bid,
            yes_ask=bar.yes_ask,
            yes_bid_size=bar.yes_bid_size,
            yes_ask_size=bar.yes_ask_size,
            has_bbo=bar.has_bbo,
        )


async def run_param_grid(
    bars: list[MarketBar],
    *,
    edge_thresholds: list[float],
    starting_cash: float = 1_000.0,
    order_size_usd: float = 10.0,
    costs: CostModel | None = None,
) -> list[BacktestResult]:
    """Run SimpleEdgeStrategy across edge_threshold values."""
    from chancetime.strategies.simple_edge import SimpleEdgeStrategy

    engine = BacktestEngine(
        starting_cash=starting_cash,
        order_size_usd=order_size_usd,
        costs=costs,
    )
    results: list[BacktestResult] = []
    for thr in edge_thresholds:
        strat = SimpleEdgeStrategy(
            edge_threshold=thr,
            min_liquidity_usd=100.0,
            default_fair_prob=0.5,
        )
        res = await engine.run(
            bars,
            strat,
            params={"edge_threshold": thr, "default_fair_prob": 0.5},
        )
        results.append(res)
    return results
