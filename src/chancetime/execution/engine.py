"""Order execution — paper by default; live path hard-capped (Phase 6).

SAFETY:
- Default paper_mode=True.
- Live requires paper_mode=False AND live_enabled AND human risk ack.
- Per-order / session notional + contract caps.
- Dual-leg arb live: both legs or neither (same group); legging risk remains.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from chancetime.backtesting.fees import CostModel, cost_model_for_venue
from chancetime.data_layer.models import Market
from chancetime.flair import fill_slogan, miss_slogan
from chancetime.strategies.base import Side, Signal
from chancetime.utils.config import ExecutionSettings
from chancetime.utils.logging import get_logger

if TYPE_CHECKING:
    from chancetime.execution.live_kalshi import KalshiLiveClient, LiveOrderResult
    from chancetime.execution.live_polymarket import PolymarketUSLiveClient

log = get_logger(__name__)


class OrderStatus(StrEnum):
    FILLED = "filled"
    REJECTED = "rejected"
    SIMULATED = "simulated"
    SUBMITTED = "submitted"  # live accepted; fill may be partial/pending


@dataclass
class Fill:
    order_id: str
    market_id: str
    side: Side
    price: float
    size_usd: float
    status: OrderStatus
    paper: bool
    ts: float = field(default_factory=time.time)
    note: str = ""
    arb_group_id: str | None = None
    venue: str = ""
    contracts: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class ExecutionEngine:
    """Central order gateway. All trading actions go through here."""

    def __init__(
        self,
        settings: ExecutionSettings,
        *,
        paper_mode: bool = True,
        live_enabled: bool = False,
        risk_acknowledged: bool = False,
        kalshi: KalshiLiveClient | None = None,
        polymarket: PolymarketUSLiveClient | None = None,
    ) -> None:
        self.settings = settings
        self.paper_mode = paper_mode
        self.live_enabled = live_enabled
        self.risk_acknowledged = risk_acknowledged
        self.kalshi = kalshi
        self.polymarket = polymarket
        self.fills: list[Fill] = []
        self._arb_pairs_this_poll = 0
        self._arb_notional_this_poll = 0.0
        self._live_orders_session = 0
        self._live_notional_session = 0.0
        self.markets: dict[str, Market] = {}
        if not paper_mode:
            log.warning(
                "LIVE_MODE_ENABLED",
                live_enabled=live_enabled,
                risk_ack=risk_acknowledged,
                max_order_usd=settings.max_live_order_usd,
                max_session_usd=settings.max_live_notional_session,
            )

    def begin_poll(self) -> None:
        self._arb_pairs_this_poll = 0
        self._arb_notional_this_poll = 0.0

    def set_markets(self, markets: dict[str, Market] | list[Market]) -> None:
        if isinstance(markets, dict):
            self.markets = dict(markets)
        else:
            self.markets = {m.id: m for m in markets}

    def _paper_cost_model(self, platform: str = "") -> CostModel:
        """Build paper CostModel.

        ``paper_fee_venue: default`` → use ``paper_fee_bps`` always.
        ``kalshi`` / ``polymarket`` / ``mock`` → venue schedule (still honor slip + depth knobs).
        """
        s = self.settings
        venue = (s.paper_fee_venue or "default").lower()
        if venue in {"kalshi", "polymarket", "mock"}:
            cm = cost_model_for_venue(venue, slippage_bps=s.paper_slippage_bps)
            fee = cm.fee_bps
            vname = cm.venue
        else:
            fee = s.paper_fee_bps
            vname = "default"
        return CostModel(
            fee_bps=fee,
            slippage_bps=s.paper_slippage_bps,
            liquidity_participation=s.liquidity_participation,
            min_fill_ratio=s.min_fill_ratio,
            use_depth_size=s.size_by_depth,
            venue=vname,
        )

    def _live_allowed(self) -> tuple[bool, str]:
        if self.paper_mode:
            return False, "paper_mode"
        if not self.live_enabled:
            return False, "live_disabled"
        if not self.risk_acknowledged:
            return False, "risk_not_acknowledged"
        if self._live_orders_session >= self.settings.max_live_orders_session:
            return False, "max_live_orders_session"
        if self._live_notional_session >= self.settings.max_live_notional_session:
            return False, "max_live_notional_session"
        return True, ""

    def _cap_live_size(self, size: float) -> float:
        return min(
            size,
            self.settings.max_live_order_usd,
            self.settings.max_leg_usd,
            self.settings.max_position_usd_hard,
        )

    async def execute(self, signal: Signal) -> Fill:
        size = signal.size_usd or self.settings.default_order_size_usd
        order_id = str(uuid.uuid4())
        group = _arb_group(signal)

        if self.paper_mode:
            return self._paper_fill(order_id, signal, size, arb_group_id=group)

        ok, reason = self._live_allowed()
        if not ok:
            slogan = miss_slogan(reason=reason)
            log.error("miss", slogan=slogan, reason=reason, market_id=signal.market_id)
            return self._reject(signal, slogan, group)

        size = self._cap_live_size(size)
        if size < self.settings.min_live_order_usd:
            return self._reject(signal, miss_slogan(reason="live size too small"), group)

        return await self._live_fill(signal, size, group)

    async def execute_signals(self, signals: list[Signal]) -> list[Fill]:
        if not signals:
            return []

        groups: dict[str, list[Signal]] = defaultdict(list)
        singles: list[Signal] = []
        for sig in signals:
            gid = _arb_group(sig)
            if gid:
                groups[gid].append(sig)
            else:
                singles.append(sig)

        fills: list[Fill] = []
        for gid, legs in groups.items():
            fills.extend(await self._execute_arb_group(gid, legs))
        for sig in singles:
            fills.append(await self.execute(sig))
        return fills

    async def _execute_arb_group(self, group_id: str, legs: list[Signal]) -> list[Fill]:
        s = self.settings
        if s.require_both_arb_legs and len(legs) < 2:
            slogan = miss_slogan(reason="arb incomplete legs")
            return [self._reject(leg, slogan, group_id) for leg in legs]

        if self._arb_pairs_this_poll >= s.max_arb_pairs_per_poll:
            slogan = miss_slogan(reason="arb pair cap")
            return [self._reject(leg, slogan, group_id) for leg in legs]

        sized: list[tuple[Signal, float]] = []
        total = 0.0
        for leg in legs:
            raw = leg.size_usd if leg.size_usd is not None else s.default_order_size_usd
            size = min(raw, s.max_leg_usd, s.max_position_usd_hard)
            if not self.paper_mode:
                size = self._cap_live_size(size)
            if size <= 0:
                slogan = miss_slogan(reason="arb leg size")
                return [self._reject(x, slogan, group_id) for x in legs]
            sized.append((leg, size))
            total += size

        if total > s.max_arb_pair_usd:
            scale = s.max_arb_pair_usd / total
            sized = [(leg, round(sz * scale, 4)) for leg, sz in sized]
            total = sum(sz for _, sz in sized)

        if total + self._arb_notional_this_poll > s.max_arb_notional_per_poll:
            slogan = miss_slogan(reason="arb notional cap")
            return [self._reject(leg, slogan, group_id) for leg in legs]

        if self.paper_mode:
            out: list[Fill] = []
            for leg, size in sized:
                out.append(self._paper_fill(str(uuid.uuid4()), leg, size, arb_group_id=group_id))
            self._arb_pairs_this_poll += 1
            self._arb_notional_this_poll += total
            log.info(
                "arb_dual_leg_paper",
                group=group_id,
                legs=len(out),
                total_usd=round(total, 4),
                pairs_this_poll=self._arb_pairs_this_poll,
            )
            return out

        if not getattr(self.settings, "dual_leg_live_enabled", True):
            slogan = miss_slogan(reason="dual_leg_live_disabled")
            log.warning("miss", slogan=slogan, group=group_id)
            return [self._reject(leg, slogan, group_id) for leg, _ in sized]

        # LIVE dual-leg: place both; if first fails, do not place second
        ok, reason = self._live_allowed()
        if not ok:
            slogan = miss_slogan(reason=reason)
            return [self._reject(leg, slogan, group_id) for leg, _ in sized]

        out_live: list[Fill] = []
        for leg, size in sized:
            fill = await self._live_fill(leg, size, group_id)
            out_live.append(fill)
            if fill.status == OrderStatus.REJECTED:
                log.error(
                    "arb_live_leg_failed",
                    group=group_id,
                    market_id=leg.market_id,
                    note=fill.note,
                    msg="Remaining legs of group skipped",
                )
                for rest_leg, _rest_sz in sized[len(out_live) :]:
                    out_live.append(
                        self._reject(
                            rest_leg,
                            miss_slogan(reason="arb sibling failed"),
                            group_id,
                        )
                    )
                break
        self._arb_pairs_this_poll += 1
        self._arb_notional_this_poll += sum(
            f.size_usd for f in out_live if f.status != OrderStatus.REJECTED
        )
        return out_live

    async def _live_fill(self, signal: Signal, size: float, group_id: str | None) -> Fill:
        """Live order — no paper fee model. Venue reports fill price / fees in raw."""
        platform = (signal.platform or "").lower()
        price = _limit_price(signal)
        try:
            if platform in {"kalshi"}:
                if self.kalshi is None:
                    return self._reject(
                        signal, miss_slogan(reason="kalshi client missing"), group_id
                    )
                result = await self.kalshi.place_order(
                    ticker=signal.market_id,
                    side=signal.side,
                    size_usd=size,
                    limit_price=price,
                    time_in_force=self.settings.live_tif_kalshi,
                )
            elif platform in {"polymarket", "polymarket_us", "pm"}:
                if self.polymarket is None:
                    return self._reject(
                        signal, miss_slogan(reason="polymarket client missing"), group_id
                    )
                slug = str(
                    signal.metadata.get("slug")
                    or signal.metadata.get("market_slug")
                    or signal.market_id
                )
                result = await self.polymarket.place_order(
                    market_slug=slug,
                    side=signal.side,
                    size_usd=size,
                    limit_price=price,
                    tif=self.settings.live_tif_polymarket,
                )
            else:
                return self._reject(
                    signal, miss_slogan(reason=f"unknown platform {platform}"), group_id
                )
        except Exception as exc:
            log.exception("live_order_error", platform=platform, market_id=signal.market_id)
            return self._reject(signal, miss_slogan(reason=str(exc)[:80]), group_id)

        return self._result_to_fill(signal, result, group_id)

    def _result_to_fill(
        self,
        signal: Signal,
        result: LiveOrderResult,
        group_id: str | None,
    ) -> Fill:
        if result.ok:
            self._live_orders_session += 1
            self._live_notional_session += result.size_usd
            slogan = fill_slogan(paper=False)
            status = OrderStatus.SUBMITTED
            # Treat sync fills as filled when raw shows fill
            raw = result.raw
            if isinstance(raw, dict):
                execs = raw.get("executions")
                if isinstance(execs, list) and execs:
                    status = OrderStatus.FILLED
            fill = Fill(
                order_id=result.order_id or str(uuid.uuid4()),
                market_id=signal.market_id,
                side=signal.side,
                price=result.price,
                size_usd=result.size_usd,
                status=status,
                paper=False,
                note=f"{slogan}; {result.note}",
                arb_group_id=group_id,
                venue=result.venue,
                contracts=result.contracts,
                raw=result.raw if isinstance(result.raw, dict) else {},
            )
            self.fills.append(fill)
            log.info(
                "got_item",
                slogan=slogan,
                order_id=fill.order_id,
                market_id=fill.market_id,
                side=str(fill.side),
                price=round(fill.price, 4),
                size_usd=fill.size_usd,
                venue=result.venue,
                live=True,
            )
            return fill

        slogan = miss_slogan(reason="live reject")
        fill = Fill(
            order_id=result.client_order_id or str(uuid.uuid4()),
            market_id=signal.market_id,
            side=signal.side,
            price=result.price,
            size_usd=result.size_usd,
            status=OrderStatus.REJECTED,
            paper=False,
            note=f"{slogan}; {result.note}",
            arb_group_id=group_id,
            venue=result.venue,
            contracts=result.contracts,
            raw=result.raw if isinstance(result.raw, dict) else {},
        )
        self.fills.append(fill)
        log.error("miss", slogan=slogan, note=result.note[:200], venue=result.venue)
        return fill

    def _reject(self, signal: Signal, note: str, group_id: str | None) -> Fill:
        size = signal.size_usd or self.settings.default_order_size_usd
        fill = Fill(
            order_id=str(uuid.uuid4()),
            market_id=signal.market_id,
            side=signal.side,
            price=0.0,
            size_usd=size,
            status=OrderStatus.REJECTED,
            paper=self.paper_mode,
            note=note,
            arb_group_id=group_id,
        )
        self.fills.append(fill)
        return fill

    def _paper_fill(
        self,
        order_id: str,
        signal: Signal,
        size: float,
        *,
        arb_group_id: str | None = None,
    ) -> Fill:
        """Paper fill ONLY: BBO, simulated fees, depth clip.

        Live path (_live_fill) must never call this — venue trade summaries already
        include fees/fills; we must not double-count paper_fee_bps on real money.
        """
        _eps = 1e-6
        s = self.settings
        mkt = self.markets.get(signal.market_id)
        mid = float(signal.market_prob) if signal.market_prob is not None else 0.5
        if mkt is not None:
            mid = float(mkt.yes_price)
        platform = (signal.platform or (str(mkt.platform) if mkt else "")).lower()
        costs = self._paper_cost_model(platform)

        # Wide spread reject (when BBO present)
        if (
            s.max_spread > 0
            and mkt is not None
            and mkt.yes_bid is not None
            and mkt.yes_ask is not None
        ):
            spr = float(mkt.yes_ask) - float(mkt.yes_bid)
            if spr > s.max_spread + 1e-12:
                return self._reject(
                    signal,
                    miss_slogan(reason=f"wide_spread {spr:.3f}>{s.max_spread:.3f}"),
                    arb_group_id,
                )

        # Size by depth / liquidity
        size_req = float(size)
        if s.size_by_depth and mkt is not None:
            if signal.side == Side.YES:
                depth = mkt.depth_usd_for_yes_buy()
            elif signal.side == Side.NO:
                depth = mkt.depth_usd_for_no_buy()
            else:
                depth = mkt.liquidity_usd
            clipped = costs.clip_size_to_depth(
                size_req,
                depth_usd=depth if depth > 0 else None,
                liquidity_usd=mkt.liquidity_usd,
            )
            if clipped is None:
                return self._reject(
                    signal,
                    miss_slogan(reason="thin_book"),
                    arb_group_id,
                )
            size = float(clipped)
        if size < s.min_live_order_usd and size + 1e-9 < size_req * s.min_fill_ratio:
            return self._reject(signal, miss_slogan(reason="size_after_depth"), arb_group_id)

        # Entry price: BBO prefer (sub-cent ok), else mid ± slip; optional exec_price hint
        exec_hint = signal.metadata.get("exec_price")
        px_src = "mid_slip"
        if isinstance(exec_hint, int | float):
            price = float(exec_hint)
            px_src = "exec_hint"
        elif (
            s.use_bbo_paper
            and mkt is not None
            and signal.side == Side.YES
            and mkt.yes_ask is not None
        ):
            price = float(mkt.yes_ask)
            px_src = "bbo_ask"
        elif (
            s.use_bbo_paper
            and mkt is not None
            and signal.side == Side.NO
            and mkt.yes_bid is not None
        ):
            price = 1.0 - float(mkt.yes_bid)
            px_src = "bbo_no"
        elif signal.side == Side.YES:
            price = costs.apply_slippage(mid, buying=True)
            # apply_slippage floors 0.01 — loosen for cheap mids
            if mid < 0.02:
                slip = s.paper_slippage_bps / 10_000.0
                price = mid + slip
            px_src = "mid_slip"
        elif signal.side == Side.NO:
            no_mid = 1.0 - mid
            slip = s.paper_slippage_bps / 10_000.0
            price = no_mid + slip
            px_src = "mid_slip"
        else:
            price = mid
            px_src = "mid"
        price = max(_eps, min(1.0 - _eps, price))

        fee = costs.fee_usd(size)
        # Fee comes out of notional: fewer contracts for same cash outlay
        net_notional = max(0.0, size - fee)
        contracts = net_notional / price if price > 0 else 0.0
        mark = mid if signal.side == Side.YES else (1.0 - mid)
        # MTM on cash spent (size) vs mark value of contracts
        mtm_value = contracts * mark
        mtm_drag_pct = ((mtm_value - size) / size) * 100.0 if size > 0 else 0.0

        slogan = fill_slogan(paper=True)
        note = (
            f"{slogan}; px={px_src} mid={mid:.4f} entry={price:.4f} "
            f"fee=${fee:.4f} mtm=${mtm_value:.2f} drag={mtm_drag_pct:.1f}%"
        )
        fill = Fill(
            order_id=order_id,
            market_id=signal.market_id,
            side=signal.side,
            price=price,
            size_usd=size,
            status=OrderStatus.SIMULATED,
            paper=True,
            note=note,
            arb_group_id=arb_group_id,
            venue=platform or costs.venue,
            contracts=contracts,
            raw={
                "mid": mid,
                "entry": price,
                "fee_usd": fee,
                "mtm_value": mtm_value,
                "mtm_drag_pct": mtm_drag_pct,
                "px_src": px_src,
                "size_req": size_req,
            },
        )
        self.fills.append(fill)
        log.info(
            "got_item",
            slogan=slogan,
            order_id=order_id,
            market_id=signal.market_id,
            side=str(signal.side),
            price=round(price, 4),
            mid=round(mid, 4),
            size_usd=round(size, 4),
            fee_usd=round(fee, 4),
            contracts=round(contracts, 4),
            mtm_value=round(mtm_value, 4),
            mtm_drag_pct=round(mtm_drag_pct, 2),
            px_src=px_src,
            arb_group=arb_group_id,
        )
        return fill


def _arb_group(signal: Signal) -> str | None:
    raw = signal.metadata.get("arb_group_id")
    if raw is None or raw == "":
        return None
    return str(raw)


def _limit_price(signal: Signal) -> float:
    hint = signal.metadata.get("exec_price")
    if isinstance(hint, int | float):
        return max(0.01, min(0.99, float(hint)))
    if signal.side == Side.YES:
        mid = signal.market_prob if signal.market_prob is not None else 0.5
        return max(0.01, min(0.99, float(mid)))
    if signal.side == Side.NO:
        # market_prob often YES mid; prefer 1-mid or exec_price
        mid = signal.market_prob if signal.market_prob is not None else 0.5
        return max(0.01, min(0.99, 1.0 - float(mid)))
    return 0.5
