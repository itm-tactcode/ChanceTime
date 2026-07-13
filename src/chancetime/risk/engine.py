"""Risk checks, sizing with strategy weights, position lifecycle hooks."""

from __future__ import annotations

from chancetime.data_layer.models import Market
from chancetime.data_layer.timeparse import hours_until
from chancetime.flair import MISS
from chancetime.risk.families import EventFamily, classify_family, cluster_key
from chancetime.risk.portfolio import ClosedTrade, Portfolio
from chancetime.strategies.base import Side, Signal
from chancetime.utils.config import RiskSettings
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class RiskEngine:
    def __init__(
        self,
        settings: RiskSettings,
        portfolio: Portfolio | None = None,
        *,
        strategy_weights: dict[str, float] | None = None,
        cold_strategies: set[str] | None = None,
        title_by_market: dict[str, str] | None = None,
        cash_basis: float = 1000.0,
        strategy_open_limits: dict[str, int] | None = None,
        strategy_size_caps: dict[str, float] | None = None,
    ) -> None:
        self.settings = settings
        self.portfolio = portfolio or Portfolio()
        self.strategy_weights = strategy_weights or {}
        self.cold_strategies: set[str] = set(cold_strategies or ())
        self.title_by_market = title_by_market or {}
        self.cash_basis = float(cash_basis)
        # name -> max open (0 = unlimited). Empty → use settings.max_open_per_strategy
        self.strategy_open_limits: dict[str, int] = dict(strategy_open_limits or {})
        self.strategy_size_caps: dict[str, float] = dict(strategy_size_caps or {})
        self.markets: dict[str, Market] = {}
        self.consecutive_errors = 0
        self.halted = False
        self._on_halt: list[object] = []  # callables
        # Markets closed this poll (TP/SL) — block same-poll re-entry churn
        self.cooldown_markets: set[str] = set()

    def available_cash(self) -> float:
        """Spendable USD (cannot go negative when enforce_cash is on)."""
        return self.portfolio.available_cash(self.cash_basis)

    def deployed_usd(self) -> float:
        """Notional currently in open positions."""
        return sum(abs(p.size_usd) for p in self.portfolio.positions.values())

    def max_deploy_usd(self) -> float | None:
        """Cap on open notional from max_deploy_pct * cash_basis (None = off)."""
        pct = float(getattr(self.settings, "max_deploy_pct", 0.0) or 0.0)
        if pct <= 0:
            return None
        return max(0.0, self.cash_basis * pct)

    def apply_risk_settings(self, settings: RiskSettings) -> None:
        """Hot-swap risk knobs (Phase 19) without rebuilding the engine."""
        self.settings = settings

    def _open_count_by_strategy(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for pos in self.portfolio.positions.values():
            name = (pos.strategy or "").strip() or "unknown"
            counts[name] = counts.get(name, 0) + 1
        return counts

    def on_halt(self, callback: object) -> None:
        self._on_halt.append(callback)

    def set_cold_strategies(self, names: set[str]) -> None:
        self.cold_strategies = set(names)
        if names:
            log.warning("cold_strategies_active", strategies=sorted(names))

    def set_market_titles(self, titles: dict[str, str]) -> None:
        self.title_by_market = dict(titles)

    def set_markets(self, markets: dict[str, Market] | list[Market]) -> None:
        """Attach latest market snapshots (BBO/depth) for cost/spread checks."""
        if isinstance(markets, dict):
            self.markets = dict(markets)
        else:
            self.markets = {m.id: m for m in markets}

    def _slot_limit_for(self, name: str) -> int:
        if name in self.strategy_open_limits:
            return int(self.strategy_open_limits[name])
        return int(getattr(self.settings, "max_open_per_strategy", 0) or 0)

    def _fire_halt(self, reason: str) -> None:
        for cb in self._on_halt:
            try:
                result = cb(reason)  # type: ignore[operator]
                if hasattr(result, "__await__"):
                    pass  # sync callbacks only here; bot wires async alerter separately
            except Exception:
                log.exception("halt_callback_failed")

    def _family_exposure(self) -> dict[EventFamily, float]:
        exp: dict[EventFamily, float] = {f: 0.0 for f in EventFamily}
        for mid, pos in self.portfolio.positions.items():
            title = self.title_by_market.get(mid, mid)
            fam = classify_family(title, market_id=mid)
            exp[fam] = exp.get(fam, 0.0) + abs(pos.size_usd)
        return exp

    def _cluster_exposure(self) -> dict[str, float]:
        exp: dict[str, float] = {}
        for mid, pos in self.portfolio.positions.items():
            title = self.title_by_market.get(mid, mid)
            key = cluster_key(title, market_id=mid)
            exp[key] = exp.get(key, 0.0) + abs(pos.size_usd)
        return exp

    def _time_to_event_ok(self, market_id: str) -> str | None:
        """Return miss reason if time-to-event filter fails; else None."""
        min_h = float(getattr(self.settings, "min_hours_to_close", 0.0) or 0.0)
        max_d = float(getattr(self.settings, "max_days_to_close", 0.0) or 0.0)
        if min_h <= 0 and max_d <= 0:
            return None
        mkt = self.markets.get(market_id)
        if mkt is None or mkt.close_time is None:
            return None  # unknown horizon — do not reject
        hrs = hours_until(mkt.close_time)
        if hrs is None:
            return None
        if min_h > 0 and hrs < min_h:
            return "too_soon"
        if max_d > 0 and hrs > max_d * 24.0:
            return "too_far"
        return None

    def filter_signals(
        self,
        signals: list[Signal],
        *,
        default_size_usd: float,
        strategy_name_by_signal: dict[int, str] | None = None,
    ) -> list[Signal]:
        """Approve signals; apply max size, open limits, strategy weight sizing.

        ``strategy_name_by_signal`` maps ``id(signal)`` -> strategy name for weights.
        Per-signal ``miss`` lines are aggregated into one ``miss_summary`` so a full
        bag does not spam dozens of identical max_positions lines.
        """
        if self.halted:
            log.warning("miss", slogan=MISS, reason="risk_halted")
            return []

        if self.portfolio.realized_pnl_today <= -abs(self.settings.max_daily_loss_usd):
            log.error(
                "miss",
                slogan=MISS,
                reason="daily_loss_limit",
                pnl=self.portfolio.realized_pnl_today,
                limit=self.settings.max_daily_loss_usd,
            )
            self.halted = True
            self._fire_halt("daily_loss_limit")
            return []

        # Prefer higher |edge| * strength when multiple strategies hit same market
        ranked = sorted(
            signals,
            key=lambda s: abs(s.edge) * max(s.strength, 0.01),
            reverse=True,
        )
        seen_markets: set[str] = set()
        approved: list[Signal] = []
        miss_counts: dict[str, int] = {}
        cash_left = self.available_cash()
        enforce_cash = bool(getattr(self.settings, "enforce_cash", True))
        min_order = max(0.0, float(getattr(self.settings, "min_order_usd", 1.0)))
        min_mid = float(getattr(self.settings, "min_yes_mid", 0.0) or 0.0)
        max_mid = float(getattr(self.settings, "max_yes_mid", 1.0) or 1.0)
        min_net = float(getattr(self.settings, "min_net_edge", 0.0) or 0.0)
        default_half = float(getattr(self.settings, "assumed_half_spread", 0.005) or 0.0)
        fee_pts = float(getattr(self.settings, "assumed_fee", 0.0) or 0.0)
        open_by_strat = self._open_count_by_strategy()
        approved_by_strat: dict[str, int] = {}
        max_spread = float(getattr(self.settings, "max_spread", 0.0) or 0.0)
        deploy_left: float | None = None
        max_dep = self.max_deploy_usd()
        if max_dep is not None:
            deploy_left = max(0.0, max_dep - self.deployed_usd())

        def _bump(reason: str) -> None:
            miss_counts[reason] = miss_counts.get(reason, 0) + 1

        def _apply_deploy(sz: float) -> bool:
            """Reserve deploy budget; False if over max_deploy_pct."""
            nonlocal deploy_left
            if deploy_left is None:
                return True
            if sz > deploy_left + 1e-9:
                _bump("deploy_cap")
                return False
            deploy_left -= sz
            return True

        def _mid_ok(sig: Signal) -> bool:
            mid = sig.market_prob
            if mid is None:
                return True
            m = float(mid)
            if min_mid > 0 and m < min_mid:
                return False
            if max_mid < 1 and m > max_mid:
                return False
            return True

        def _half_spread_for(sig: Signal) -> float:
            mkt = self.markets.get(sig.market_id)
            if mkt is not None and mkt.yes_bid is not None and mkt.yes_ask is not None:
                return max(0.0, (float(mkt.yes_ask) - float(mkt.yes_bid)) / 2.0)
            return default_half

        def _spread_ok(sig: Signal) -> bool:
            if max_spread <= 0:
                return True
            mkt = self.markets.get(sig.market_id)
            if mkt is None or mkt.yes_bid is None or mkt.yes_ask is None:
                return True
            spr = float(mkt.yes_ask) - float(mkt.yes_bid)
            return spr <= max_spread + 1e-12

        def _net_edge_ok(sig: Signal) -> bool:
            """Require |edge| clears half-spread + fee + min_net_edge (probability points)."""
            half_spread = _half_spread_for(sig)
            if min_net <= 0 and half_spread <= 0 and fee_pts <= 0:
                return True
            gross = abs(float(sig.edge))
            net = gross - half_spread - fee_pts
            if net + 1e-12 < min_net:
                return False
            return True

        def _strategy_name(sig: Signal) -> str:
            name = ""
            if strategy_name_by_signal is not None:
                name = strategy_name_by_signal.get(id(sig), "")
            name = name or str(sig.metadata.get("strategy") or "")
            return name or "unknown"

        def _strategy_slot_ok(name: str, need: int = 1) -> bool:
            lim = self._slot_limit_for(name)
            if lim <= 0:
                return True
            have = open_by_strat.get(name, 0) + approved_by_strat.get(name, 0)
            return have + need <= lim

        def _note_approved_strategy(name: str, n: int = 1) -> None:
            approved_by_strat[name] = approved_by_strat.get(name, 0) + n

        def _clip_depth(sig: Signal, size: float) -> float | None:
            """Optional depth clip when market book attached (Phase 17)."""
            mkt = self.markets.get(sig.market_id)
            if mkt is None:
                return size
            if sig.side == Side.YES:
                depth = mkt.depth_usd_for_yes_buy()
            elif sig.side == Side.NO:
                depth = mkt.depth_usd_for_no_buy()
            else:
                return size
            if depth <= 0:
                return size
            if size > depth + 1e-9:
                if depth < float(getattr(self.settings, "min_order_usd", 1.0) or 1.0):
                    return None
                return round(depth, 4)
            return size

        def _apply_cash(sized: Signal) -> Signal | None:
            """Clip or reject so we never reserve more than free cash."""
            nonlocal cash_left
            sz = float(sized.size_usd or 0.0)
            if sz <= 0:
                return None
            if not enforce_cash:
                cash_left -= sz
                return sized
            if cash_left < min_order:
                _bump("insufficient_cash")
                return None
            if sz > cash_left:
                # Partial size to remaining cash (live would reject full order)
                if cash_left < min_order:
                    _bump("insufficient_cash")
                    return None
                sized = sized.model_copy(update={"size_usd": round(cash_left, 4)})
                sz = float(sized.size_usd or 0.0)
                _bump("cash_clipped")
            cash_left -= sz
            return sized

        # Dual-leg arb: approve whole group or none (by arb_group_id)
        pending_groups: dict[str, list[Signal]] = {}
        for sig in ranked:
            gid = sig.metadata.get("arb_group_id")
            if gid:
                pending_groups.setdefault(str(gid), []).append(sig)

        processed_groups: set[str] = set()
        family_exp = self._family_exposure()
        cluster_exp = self._cluster_exposure()
        max_cluster = float(getattr(self.settings, "max_cluster_exposure_usd", 0.0) or 0.0)

        for sig in ranked:
            name = ""
            if strategy_name_by_signal is not None:
                name = strategy_name_by_signal.get(id(sig), "")
            name = name or str(sig.metadata.get("strategy") or "")
            if name and name in self.cold_strategies:
                _bump("cold_strategy")
                continue
            weight = self.strategy_weights.get(name, 1.0) if name else 1.0
            if weight <= 0:
                _bump("zero_weight")
                continue

            if not _mid_ok(sig):
                _bump("mid_band")
                continue
            if not _spread_ok(sig):
                _bump("wide_spread")
                continue
            if not _net_edge_ok(sig):
                _bump("net_edge")
                continue
            tte_reason = self._time_to_event_ok(sig.market_id)
            if tte_reason is not None:
                _bump(tte_reason)
                continue

            name = _strategy_name(sig)

            gid_raw = sig.metadata.get("arb_group_id")
            if gid_raw:
                gid = str(gid_raw)
                if gid in processed_groups:
                    continue
                processed_groups.add(gid)
                legs = pending_groups.get(gid, [sig])
                legs = sorted(legs, key=lambda s: str(s.metadata.get("arb_leg") or ""))
                if not self._group_fits(legs, approved, seen_markets, miss_counts):
                    continue
                if not _strategy_slot_ok(name, need=len(legs)):
                    _bump("strategy_slots")
                    continue
                # Time-to-event: all legs must pass
                if any(self._time_to_event_ok(leg.market_id) for leg in legs):
                    _bump("too_soon" if any(
                        self._time_to_event_ok(leg.market_id) == "too_soon" for leg in legs
                    ) else "too_far")
                    continue
                sized_legs: list[Signal] = []
                for leg in legs:
                    sized = self._size_one(
                        leg,
                        default_size_usd=default_size_usd,
                        strategy_name_by_signal=strategy_name_by_signal,
                        miss_counts=miss_counts,
                    )
                    if sized is None:
                        sized_legs = []
                        break
                    sized_legs.append(sized)
                if len(sized_legs) != len(legs):
                    continue
                # Family budget: sum of legs
                total_leg = sum(s.size_usd or 0 for s in sized_legs)
                title0 = self.title_by_market.get(
                    sized_legs[0].market_id, sized_legs[0].market_id
                )
                fam = classify_family(title0, market_id=sized_legs[0].market_id)
                ckey = cluster_key(title0, market_id=sized_legs[0].market_id)
                if family_exp.get(fam, 0.0) + total_leg > self.settings.max_family_exposure_usd:
                    _bump("family_exposure")
                    continue
                if max_cluster > 0 and cluster_exp.get(ckey, 0.0) + total_leg > max_cluster:
                    _bump("cluster_exposure")
                    continue
                # Dual-leg: all-or-nothing cash (no partial group)
                if enforce_cash and total_leg > cash_left + 1e-9:
                    _bump("insufficient_cash")
                    continue
                if not _apply_deploy(total_leg):
                    continue
                for sized in sized_legs:
                    sz_leg = float(sized.size_usd or 0.0)
                    if enforce_cash:
                        cash_left -= sz_leg
                    approved.append(sized)
                    seen_markets.add(sized.market_id)
                    family_exp[fam] = family_exp.get(fam, 0.0) + sz_leg
                    cluster_exp[ckey] = cluster_exp.get(ckey, 0.0) + sz_leg
                _note_approved_strategy(name, n=len(sized_legs))
                continue

            if sig.market_id in seen_markets:
                continue
            if sig.market_id in self.cooldown_markets:
                _bump("cooldown_reentry")
                continue
            if sig.market_id in self.portfolio.positions:
                _bump("already_open")
                continue
            if self.portfolio.open_count + len(approved) >= self.settings.max_open_positions:
                _bump("max_positions")
                continue
            if not _strategy_slot_ok(name):
                _bump("strategy_slots")
                continue

            sized = self._size_one(
                sig,
                default_size_usd=default_size_usd,
                strategy_name_by_signal=strategy_name_by_signal,
                miss_counts=miss_counts,
            )
            if sized is None:
                continue
            sz0 = float(sized.size_usd or 0.0)
            clipped = _clip_depth(sized, sz0)
            if clipped is None:
                _bump("thin_book")
                continue
            if clipped + 1e-9 < sz0:
                sized = sized.model_copy(update={"size_usd": clipped})
            title = self.title_by_market.get(sized.market_id, sized.market_id)
            fam = classify_family(title, market_id=sized.market_id)
            ckey = cluster_key(title, market_id=sized.market_id)
            sz = sized.size_usd or 0.0
            if family_exp.get(fam, 0.0) + sz > self.settings.max_family_exposure_usd:
                _bump("family_exposure")
                continue
            if max_cluster > 0 and cluster_exp.get(ckey, 0.0) + sz > max_cluster:
                _bump("cluster_exposure")
                continue
            if not _apply_deploy(sz):
                continue
            applied = _apply_cash(sized)
            if applied is None:
                # roll back deploy reservation
                if deploy_left is not None:
                    deploy_left += sz
                continue
            approved.append(applied)
            seen_markets.add(applied.market_id)
            asz = applied.size_usd or 0
            family_exp[fam] = family_exp.get(fam, 0.0) + asz
            cluster_exp[ckey] = cluster_exp.get(ckey, 0.0) + asz
            # If cash clipped size down, free unused deploy
            if deploy_left is not None and asz + 1e-9 < sz:
                deploy_left += sz - asz
            _note_approved_strategy(name)

        if miss_counts:
            total_miss = sum(miss_counts.values())
            top = max(miss_counts, key=miss_counts.get)  # type: ignore[arg-type]
            log.info(
                "miss_summary",
                slogan=MISS,
                total=total_miss,
                by_reason=dict(sorted(miss_counts.items())),
                dominant=top,
                open_positions=self.portfolio.open_count,
                max_open=self.settings.max_open_positions,
                available_cash=round(self.available_cash(), 2),
                cash_left_after=round(cash_left, 2),
                cold_strategies=sorted(self.cold_strategies) or None,
            )
            if top == "max_positions" and self.portfolio.open_count >= self.settings.max_open_positions:
                log.info(
                    "risk_bag_full",
                    open_positions=self.portfolio.open_count,
                    max_open=self.settings.max_open_positions,
                    msg=(
                        f"{self.portfolio.open_count}/{self.settings.max_open_positions} open — "
                        "new entries blocked. Clear book or raise max_open_positions."
                    ),
                )
            if top == "cold_strategy" and self.cold_strategies:
                log.warning(
                    "all_blocked_cold",
                    strategies=sorted(self.cold_strategies),
                    msg=(
                        "Signals exist but strategies are auto-frozen (poor cumulative "
                        "realized PnL). Clear strategy_stats, raise cold_max_realized_pnl, "
                        "or set risk.cold_min_fills: 0 in user.yaml for paper."
                    ),
                )

        return approved

    def _group_fits(
        self,
        legs: list[Signal],
        approved: list[Signal],
        seen_markets: set[str],
        miss_counts: dict[str, int] | None = None,
    ) -> bool:
        for leg in legs:
            if leg.market_id in seen_markets or leg.market_id in self.portfolio.positions:
                if miss_counts is not None:
                    miss_counts["arb_leg_blocked"] = miss_counts.get("arb_leg_blocked", 0) + 1
                else:
                    log.info(
                        "miss",
                        slogan=MISS,
                        reason="arb_leg_blocked",
                        market_id=leg.market_id,
                    )
                return False
        if self.portfolio.open_count + len(approved) + len(legs) > self.settings.max_open_positions:
            if miss_counts is not None:
                miss_counts["max_positions_arb_group"] = (
                    miss_counts.get("max_positions_arb_group", 0) + 1
                )
            else:
                log.info(
                    "miss",
                    slogan=MISS,
                    reason="max_positions_arb_group",
                    n_legs=len(legs),
                )
            return False
        return True

    def _size_one(
        self,
        sig: Signal,
        *,
        default_size_usd: float,
        strategy_name_by_signal: dict[int, str] | None,
        miss_counts: dict[str, int] | None = None,
    ) -> Signal | None:
        base = sig.size_usd if sig.size_usd is not None else default_size_usd
        if base > self.settings.max_position_usd:
            if miss_counts is not None:
                miss_counts["size"] = miss_counts.get("size", 0) + 1
            else:
                log.info("miss", slogan=MISS, reason="size", market_id=sig.market_id, size=base)
            return None
        name = ""
        if strategy_name_by_signal is not None:
            name = strategy_name_by_signal.get(id(sig), "")
        weight = self.strategy_weights.get(name, 1.0) if name else 1.0
        if weight <= 0:
            return None
        # Arb legs already depth-sized; only apply weight (not strength double-shrink)
        if sig.metadata.get("arb_group_id"):
            size = base * max(0.0, weight)
        else:
            size = base * max(0.0, weight) * max(0.1, min(1.0, sig.strength))
        size = min(size, self.settings.max_position_usd)
        # Phase 18: optional per-strategy size budget
        strat_cap = self.strategy_size_caps.get(name) if name else None
        if strat_cap is not None and strat_cap > 0:
            size = min(size, float(strat_cap))
        if size <= 0:
            return None
        meta = dict(sig.metadata)
        if name:
            meta["strategy"] = name
        return sig.model_copy(update={"size_usd": size, "metadata": meta})

    def record_error(self) -> None:
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.settings.max_consecutive_errors:
            self.halted = True
            log.error("risk_circuit_breaker", consecutive=self.consecutive_errors)
            self._fire_halt("circuit_breaker")

    def record_success(self) -> None:
        self.consecutive_errors = 0

    def register_fill(
        self,
        *,
        market_id: str,
        platform: str,
        side: object,
        size_usd: float,
        entry_price: float,
        strategy: str = "",
        contracts: float | None = None,
    ) -> None:
        from chancetime.strategies.base import Side

        self.portfolio.open_position(
            market_id=market_id,
            platform=platform,
            side=side if isinstance(side, Side) else Side(str(side)),
            size_usd=size_usd,
            entry_price=entry_price,
            strategy=strategy,
            contracts=contracts,
        )

    def manage_open_positions(self, yes_mids: dict[str, float]) -> list[ClosedTrade]:
        """Apply take-profit / stop-loss; return list of ClosedTrade."""
        self.cooldown_markets.clear()
        closed: list[ClosedTrade] = []
        tp = self.settings.take_profit_pct
        sl = self.settings.stop_loss_pct
        if tp is None and sl is None:
            self.portfolio.mark_to_market(yes_mids)
            return closed

        for market_id, pos in list(self.portfolio.positions.items()):
            yes = yes_mids.get(market_id)
            if yes is None:
                continue
            exit_px = self.portfolio._side_price(pos.side, yes)
            pnl = self.portfolio._pnl(pos.side, pos.entry_price, exit_px, pos.contracts)
            ret = pnl / pos.size_usd if pos.size_usd else 0.0
            reason = ""
            if tp is not None and ret >= tp:
                reason = f"take_profit ({ret:.1%})"
            elif sl is not None and ret <= -abs(sl):
                reason = f"stop_loss ({ret:.1%})"
            if reason:
                trade = self.portfolio.close(market_id, exit_yes_mid=yes, reason=reason)
                if trade is not None:
                    closed.append(trade)
                    self.cooldown_markets.add(market_id)
                    log.info(
                        "position_exit_rule",
                        market_id=market_id,
                        reason=reason,
                        pnl=round(trade.realized_pnl, 4),
                        entry=round(pos.entry_price, 6),
                        exit_px=round(exit_px, 6),
                        mid=round(float(yes), 6),
                    )
        if closed:
            log.info(
                "exits_this_poll",
                n=len(closed),
                pnl=round(sum(t.realized_pnl for t in closed), 4),
                open_after=self.portfolio.open_count,
            )
        self.portfolio.mark_to_market(yes_mids)
        return closed
