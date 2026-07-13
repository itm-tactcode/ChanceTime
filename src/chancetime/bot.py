"""Async paper/live trading orchestrator (data → strategies → risk → execution)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from chancetime import __version__
from chancetime.data_layer import build_data_client
from chancetime.data_layer.history import MarketHistoryRecorder
from chancetime.execution import ExecutionEngine, KalshiLiveClient, PolymarketUSLiveClient
from chancetime.flair import CHANCE_TIME, DISPLAY_NAME
from chancetime.llm.client import GrokClient
from chancetime.monitoring import build_alerter, log_and_store_poll
from chancetime.persistence import StateStore
from chancetime.risk import RiskEngine
from chancetime.risk.cold import cold_strategies_from_store
from chancetime.strategies import (
    build_strategies,
    strategy_open_limits_from_config,
    strategy_size_caps_from_config,
    strategy_weights_from_config,
)
from chancetime.utils.config import AppConfig, load_config
from chancetime.utils.logging import get_logger

log = get_logger(__name__)


def _merge_markets_by_id(base: list[Any], extra: list[Any]) -> list[Any]:
    """Append discovery markets not already in the open-book list."""
    seen = {getattr(m, "id", id(m)) for m in base}
    out = list(base)
    for m in extra:
        mid = getattr(m, "id", None)
        if mid is None or mid in seen:
            continue
        seen.add(mid)
        out.append(m)
    return out


class Bot:
    """Orchestrates data → strategies → risk → execution in a poll loop."""

    def __init__(
        self,
        cfg: AppConfig,
        *,
        risk_acknowledged: bool = False,
        force_live: bool = False,
    ) -> None:
        self.cfg = cfg
        self.data = build_data_client(
            cfg.data.source,
            kalshi_api_key=cfg.kalshi_api_key,
            kalshi_private_key_path=(
                str(cfg.kalshi_private_key_path) if cfg.kalshi_private_key_path else None
            ),
            kalshi_env=cfg.kalshi_env,
            polymarket_api_key=cfg.polymarket_api_key,
            polymarket_private_key_path=(
                str(cfg.polymarket_private_key_path) if cfg.polymarket_private_key_path else None
            ),
        )
        self.llm = GrokClient.from_config(cfg)
        self.strategies = build_strategies(cfg, llm=self.llm)
        # Rare tool pulls: daily news brief cached for no-tools calibrations
        self.news_brief = None
        if getattr(cfg.llm, "news_brief_enabled", True) and cfg.llm.enabled:
            from chancetime.llm.news_brief import DailyNewsBrief

            self.news_brief = DailyNewsBrief(
                self.llm,
                max_pulls_per_day=int(
                    getattr(cfg.llm, "news_brief_max_pulls_per_day", 4) or 4
                ),
                min_hours_between_pulls=float(
                    getattr(cfg.llm, "news_brief_min_hours_between", 4.0) or 4.0
                ),
            )
        self.alerter = build_alerter(
            telegram_bot_token=cfg.telegram_bot_token if cfg.alerts.telegram_enabled else None,
            telegram_chat_id=cfg.telegram_chat_id if cfg.alerts.telegram_enabled else None,
        )
        self.store = StateStore(
            cfg.persistence.db_path,
            enabled=cfg.persistence.enabled,
        )
        portfolio = self.store.load_portfolio() if self.store.enabled else None
        cold = cold_strategies_from_store(self.store, cfg.risk) if self.store.enabled else set()
        self.risk = RiskEngine(
            cfg.risk,
            portfolio=portfolio,
            strategy_weights=strategy_weights_from_config(cfg),
            cold_strategies=cold,
            cash_basis=cfg.persistence.cash_basis_usd,
            strategy_open_limits=strategy_open_limits_from_config(cfg),
            strategy_size_caps=strategy_size_caps_from_config(cfg),
        )
        paper = cfg.paper_mode and not force_live
        if force_live:
            paper = False
        live_on = (not paper) and (cfg.execution.live_enabled or force_live)
        self.kalshi_live: KalshiLiveClient | None = None
        self.pm_live: PolymarketUSLiveClient | None = None
        if live_on:
            if cfg.kalshi_credentials_configured:
                self.kalshi_live = KalshiLiveClient(
                    api_key_id=str(cfg.kalshi_api_key),
                    private_key_path=cfg.kalshi_private_key_path,  # type: ignore[arg-type]
                    env=cfg.kalshi_env,
                )
            if cfg.polymarket_credentials_configured:
                self.pm_live = PolymarketUSLiveClient(
                    api_key_id=str(cfg.polymarket_api_key),
                    private_key_path=cfg.polymarket_private_key_path,  # type: ignore[arg-type]
                )
        self.execution = ExecutionEngine(
            cfg.execution,
            paper_mode=paper,
            live_enabled=live_on,
            risk_acknowledged=risk_acknowledged if live_on else False,
            kalshi=self.kalshi_live,
            polymarket=self.pm_live,
        )
        self._stop = asyncio.Event()
        self.poll_count = 0
        self._budget_warned = False
        self.cash_basis = cfg.persistence.cash_basis_usd
        self.history = MarketHistoryRecorder.from_config(
            enabled=cfg.history.enabled,
            directory=cfg.history.directory,
            filename=cfg.history.filename or None,
        )
        # Dual-venue discovery clients (arb needs overlapping catalogs)
        self._discover_kalshi: Any = None
        self._discover_pm: Any = None
        self._discovery_cache: list[Any] = []
        ac = cfg.strategies.arb_cross
        src = cfg.data.source.lower().strip()
        want_discovery = (
            ac.enabled
            and getattr(ac, "deep_discovery", True)
            and src in {"both", "multi", "kalshi+polymarket"}
            and int(getattr(cfg.data, "discovery_every_polls", 0) or 0) > 0
        )
        if want_discovery:
            from chancetime.data_layer.kalshi import KalshiClient
            from chancetime.data_layer.polymarket_us import PolymarketUSClient

            self._discover_kalshi = KalshiClient(
                api_key_id=cfg.kalshi_api_key,
                private_key_path=(
                    str(cfg.kalshi_private_key_path) if cfg.kalshi_private_key_path else None
                ),
                env=cfg.kalshi_env,
            )
            self._discover_pm = PolymarketUSClient(
                api_key_id=cfg.polymarket_api_key,
                private_key_path=(
                    str(cfg.polymarket_private_key_path)
                    if cfg.polymarket_private_key_path
                    else None
                ),
                enrich_bbo=False,
            )

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self, *, max_polls: int | None = None) -> None:
        mode = "PAPER" if self.cfg.paper_mode else "LIVE"
        if self.cfg.bot.shadow_mode:
            mode = f"{mode}+SHADOW"
        cold = sorted(self.risk.cold_strategies)
        if cold:
            log.warning(
                "cold_strategies_active",
                strategies=cold,
                msg=(
                    "These strategies generate signals but risk rejects all entries "
                    "until stats recover or cold_min_fills=0"
                ),
            )
        # Effective knobs are frozen at process start — editing user.yaml needs a restart
        log.info(
            "bot_start",
            app=DISPLAY_NAME,
            slogan=CHANCE_TIME,
            version=__version__,
            mode=mode,
            shadow_mode=self.cfg.bot.shadow_mode,
            data_source=self.cfg.data.source,
            strategies=[s.name for s in self.strategies if s.enabled],
            weights=strategy_weights_from_config(self.cfg),
            cold_strategies=cold,
            poll_interval=self.cfg.bot.poll_interval_seconds,
            llm_enabled=self.cfg.llm.enabled,
            persistence=self.store.enabled,
            db_path=str(self.store.path) if self.store.enabled else None,
            open_restored=self.risk.portfolio.open_count,
            realized_pnl_restored=self.risk.portfolio.realized_pnl_today,
            max_open_positions=self.cfg.risk.max_open_positions,
            max_position_usd=self.cfg.risk.max_position_usd,
            max_family_exposure_usd=self.cfg.risk.max_family_exposure_usd,
            default_order_size_usd=self.cfg.execution.default_order_size_usd,
            enforce_cash=self.cfg.risk.enforce_cash,
            min_net_edge=self.cfg.risk.min_net_edge,
            max_open_per_strategy=self.cfg.risk.max_open_per_strategy,
            assumed_half_spread=self.cfg.risk.assumed_half_spread,
            cash_basis=self.cash_basis,
            available_cash=round(self.risk.available_cash(), 2),
            note="risk/strategy YAML is load-once; restart bot after user.yaml changes",
        )
        await self.alerter.send(
            f"bot start mode={mode} strategies={[s.name for s in self.strategies if s.enabled]}",
            level="info",
        )
        if not self.cfg.paper_mode:
            log.warning(
                "paper_mode_off",
                msg="LIVE mode active — real orders may be sent (caps + risk ack required)",
            )

        try:
            while not self._stop.is_set():
                await self.poll_once()
                self.poll_count += 1
                if max_polls is not None and self.poll_count >= max_polls:
                    log.info("max_polls_reached", polls=self.poll_count)
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self.cfg.bot.poll_interval_seconds,
                    )
        finally:
            if self.cfg.llm.enabled and self.cfg.llm.post_trade_review and self.execution.fills:
                from chancetime.llm.review import review_fills

                try:
                    review = await review_fills(self.llm, self.execution.fills)
                    if review is not None:
                        log.info(
                            "session_review",
                            grade=review.overall_grade,
                            summary=review.summary[:240],
                        )
                        await self.alerter.send(
                            f"session review grade={review.overall_grade}: {review.summary[:200]}",
                            level="info",
                        )
                except Exception:
                    log.exception("session_review_failed")
            self.store.save_portfolio(self.risk.portfolio)
            await self.data.close()
            if self._discover_kalshi is not None:
                await self._discover_kalshi.close()
            if self._discover_pm is not None:
                await self._discover_pm.close()
            if self.kalshi_live is not None:
                await self.kalshi_live.close()
            if self.pm_live is not None:
                await self.pm_live.close()
            self.store.close()
            eq = self.risk.portfolio.equity_snapshot(self.cash_basis, {})
            log.info(
                "bot_stop",
                polls=self.poll_count,
                fills=len(self.execution.fills),
                open_positions=self.risk.portfolio.open_count,
                realized_pnl=self.risk.portfolio.realized_pnl_today,
                llm_spend=self.llm.spend_summary(),
                equity=eq,
            )
            await self.alerter.send(
                f"bot stop polls={self.poll_count} fills={len(self.execution.fills)} "
                f"open={self.risk.portfolio.open_count} "
                f"realized_pnl={self.risk.portfolio.realized_pnl_today:.2f}",
                level="info",
            )

    async def _maybe_deep_discover(self, markets: list[Any]) -> list[Any]:
        """Merge dual-venue discovery pool so arb_cross can see same-event pairs."""
        every = int(getattr(self.cfg.data, "discovery_every_polls", 0) or 0)
        ac = self.cfg.strategies.arb_cross
        if (
            every <= 0
            or not ac.enabled
            or not getattr(ac, "deep_discovery", True)
            or self._discover_kalshi is None
            or self._discover_pm is None
        ):
            return markets
        # poll_count is pre-increment in run(); first poll is 0 → always refresh
        if self.poll_count % every != 0 and self._discovery_cache:
            return _merge_markets_by_id(markets, self._discovery_cache)

        from chancetime.data_layer.arb_discovery import deep_discover, load_aliases

        limit = int(getattr(self.cfg.data, "discovery_limit", 150) or 150)
        try:
            result = await deep_discover(
                self._discover_kalshi,
                self._discover_pm,
                limit_per_venue=limit,
                min_score=ac.min_match_score,
                aliases={**load_aliases(), **dict(ac.aliases)},
                llm=self.llm if ac.use_llm_match else None,
                use_llm_match=ac.use_llm_match,
                llm_match_min_confidence=ac.llm_match_min_confidence,
                llm_match_max_each=ac.llm_match_max_each,
                llm_match_band_low=getattr(ac, "llm_match_band_low", 0.40),
                llm_bulk_fallback=getattr(ac, "llm_bulk_fallback", False),
            )
        except Exception:
            log.exception("arb_discovery_failed")
            if self._discovery_cache:
                return _merge_markets_by_id(markets, self._discovery_cache)
            return markets

        self._discovery_cache = [*result.kalshi, *result.polymarket]
        # Stash pairs on arb strategy so it does not re-pair from a thinner pool
        for strategy in self.strategies:
            if strategy.name == "arb_cross" and result.pairs:
                strategy.last_pairs = result.pairs  # type: ignore[attr-defined]
        log.info(
            "arb_discovery_refresh",
            kalshi=len(result.kalshi),
            polymarket=len(result.polymarket),
            pairs=len(result.pairs),
            poll=self.poll_count,
        )
        return _merge_markets_by_id(markets, self._discovery_cache)

    def _maybe_hot_reload_risk(self) -> None:
        """Phase 19: optionally re-read risk + strategy caps/weights from YAML each poll."""
        if not getattr(self.cfg.bot, "hot_reload_risk", False):
            return
        try:
            fresh = load_config()
        except Exception:
            log.exception("hot_reload_risk_failed")
            return
        old = self.cfg.risk
        self.cfg.risk = fresh.risk
        self.cfg.strategies = fresh.strategies
        # Keep bot/data/llm secrets from original process; only risk knobs + strategy budgets
        self.risk.apply_risk_settings(fresh.risk)
        self.risk.strategy_weights = strategy_weights_from_config(fresh)
        self.risk.strategy_open_limits = strategy_open_limits_from_config(fresh)
        self.risk.strategy_size_caps = strategy_size_caps_from_config(fresh)
        # Per-strategy enabled/weight on live strategy objects
        by_name = {s.name: s for s in self.strategies}
        for name, s in by_name.items():
            st = getattr(fresh.strategies, name, None)
            if st is None:
                continue
            s.enabled = bool(getattr(st, "enabled", s.enabled))
            if hasattr(s, "weight"):
                s.weight = float(getattr(st, "weight", getattr(s, "weight", 1.0)))
        changed = (
            old.max_open_positions != fresh.risk.max_open_positions
            or old.max_position_usd != fresh.risk.max_position_usd
            or getattr(old, "max_deploy_pct", None) != getattr(fresh.risk, "max_deploy_pct", None)
            or old.max_family_exposure_usd != fresh.risk.max_family_exposure_usd
        )
        if changed:
            log.info(
                "hot_reload_risk",
                max_open=fresh.risk.max_open_positions,
                max_position_usd=fresh.risk.max_position_usd,
                max_deploy_pct=getattr(fresh.risk, "max_deploy_pct", None),
                max_family_exposure_usd=fresh.risk.max_family_exposure_usd,
                max_cluster_exposure_usd=getattr(fresh.risk, "max_cluster_exposure_usd", None),
            )

    async def _maybe_refresh_news_brief(self) -> None:
        """At most a few tool pulls/day; inject short cache into calibrators."""
        if self.news_brief is None:
            return
        try:
            state = await self.news_brief.maybe_refresh()
            text = self.news_brief.current_text()
            if not text:
                return
            # Feed cached brief into any calibrator (no per-market tools)
            for strat in self.strategies:
                cal = getattr(strat, "calibrator", None)
                if cal is not None and hasattr(cal, "news_context"):
                    base = (self.cfg.llm.news_context or "").strip()
                    cal.news_context = f"{base}\n{text}".strip() if base else text
                if (
                    strat.name == "news_impulse"
                    and hasattr(strat, "news_context")
                    and not (strat.news_context or "").strip()
                ):
                    strat.news_context = text
            log.info(
                "news_brief_active",
                source=state.source,
                pulls_today=state.pulls_today,
                chars=len(text),
            )
        except Exception:
            log.exception("news_brief_poll_failed")

    async def poll_once(self) -> None:
        try:
            self._maybe_hot_reload_risk()
            await self._maybe_refresh_news_brief()
            markets = await self.data.list_markets(limit=self.cfg.data.max_markets)
            markets = await self._maybe_deep_discover(markets)
            log.info("markets_fetched", count=len(markets), source=self.cfg.data.source)
            if self.history.enabled:
                self.history.record_markets(
                    markets,
                    source=self.cfg.data.source,
                    poll=self.poll_count + 1,
                )
            yes_mids = {m.id: m.yes_price for m in markets}
            self.risk.set_market_titles({m.id: m.title for m in markets})
            self.risk.set_markets(markets)
            self.execution.set_markets(markets)

            # Manage exits (TP/SL) before new entries
            closed = self.risk.manage_open_positions(yes_mids)
            for trade in closed:
                self.store.append_closed_trade(trade)
                if trade.strategy:
                    self.store.record_strategy_close(
                        trade.strategy, realized_pnl=trade.realized_pnl
                    )
                await self.alerter.send(
                    f"exit {trade.market_id} pnl={trade.realized_pnl:.2f} ({trade.reason})",
                    level="info",
                )

            all_signals = []
            name_by_id: dict[int, str] = {}
            strategy_counts: dict[str, int] = {}
            for strategy in self.strategies:
                if not strategy.enabled:
                    continue
                try:
                    sigs = await strategy.generate_signals(markets)
                    strategy_counts[strategy.name] = len(sigs)
                    for s in sigs:
                        name_by_id[id(s)] = strategy.name
                    all_signals.extend(sigs)
                except Exception:
                    log.exception("strategy_error", strategy=strategy.name)
                    self.risk.record_error()
                    if self.risk.halted:
                        await self.alerter.send(
                            f"HALT: circuit breaker after strategy error ({strategy.name})",
                            level="error",
                        )

            approved = self.risk.filter_signals(
                all_signals,
                default_size_usd=self.cfg.execution.default_order_size_usd,
                strategy_name_by_signal=name_by_id,
            )
            open_n = self.risk.portfolio.open_count
            max_open = self.cfg.risk.max_open_positions
            bag_full = open_n >= max_open
            log.info(
                "signals",
                generated=len(all_signals),
                approved=len(approved),
                open_positions=open_n,
                max_open_positions=max_open,
                bag_full=bag_full,
                available_cash=round(self.risk.available_cash(), 2),
                by_strategy=strategy_counts,
            )
            if bag_full and all_signals and not approved:
                log.info(
                    "risk_bag_full",
                    open=open_n,
                    max_open=max_open,
                    msg=(
                        f"Bag full: {open_n}/{max_open} open (session max_open_positions). "
                        "New names blocked until something closes. "
                        "YAML changes need a bot restart to take effect."
                    ),
                )
            elif (
                not approved
                and all_signals
                and open_n > 0
                and open_n < max_open
            ):
                # Typical: simple_edge re-fires the same markets already held
                log.info(
                    "no_new_entries",
                    open=open_n,
                    max_open=max_open,
                    room=max_open - open_n,
                    msg=(
                        "Signals fired but none approved (often already_open on held "
                        "markets, family/cash/mid band). Not bag-full — room remains."
                    ),
                )

            self.execution.begin_poll()
            sig_by_market = {s.market_id: s for s in approved}
            if self.cfg.bot.shadow_mode:
                log.info(
                    "shadow_mode_skip_execution",
                    approved=len(approved),
                    generated=len(all_signals),
                )
                fills = []
            else:
                fills = await self.execution.execute_signals(approved)
            n_ok = 0
            for fill in fills:
                if fill.status.value not in {"filled", "simulated", "submitted"}:
                    continue
                n_ok += 1
                sig = sig_by_market.get(fill.market_id)
                platform = sig.platform if sig is not None else ""
                strat_name = ""
                if sig is not None:
                    strat_name = str(sig.metadata.get("strategy") or name_by_id.get(id(sig), ""))
                self.risk.register_fill(
                    market_id=fill.market_id,
                    platform=platform,
                    side=fill.side,
                    size_usd=fill.size_usd,
                    entry_price=fill.price,
                    strategy=strat_name,
                    contracts=fill.contracts if fill.contracts > 0 else None,
                )
                self.store.record_fill(fill, strategy=strat_name, platform=platform)
                if strat_name:
                    self.store.record_strategy_fill(strat_name, size_usd=fill.size_usd)
                self.risk.record_success()
                group_note = f" group={fill.arb_group_id}" if fill.arb_group_id else ""
                await self.alerter.send(
                    f"got item {fill.market_id} {fill.side} @ {fill.price:.3f} "
                    f"${fill.size_usd:.2f} [{strat_name}]{group_note}",
                    level="info",
                )

            snap = self.risk.portfolio.equity_snapshot(self.cash_basis, yes_mids)
            self.store.save_portfolio(self.risk.portfolio)
            spend = self.llm.tracker.spent_usd if self.cfg.llm.enabled else None
            rem = self.llm.tracker.remaining() if self.cfg.llm.enabled else None
            log_and_store_poll(
                self.store,
                snap=snap,
                poll_count=self.poll_count + 1,
                paper=self.cfg.paper_mode,
                generated=len(all_signals),
                approved=len(approved),
                filled=n_ok,
                llm_spent=spend,
                llm_remaining=rem,
                strategies=[s.name for s in self.strategies if s.enabled],
                strategy_counts=strategy_counts,
            )

            if (
                self.cfg.llm.enabled
                and rem is not None
                and not self._budget_warned
                and rem < self.cfg.llm.daily_budget_usd * 0.15
            ):
                self._budget_warned = True
                await self.alerter.send(
                    f"LLM budget low: ${rem:.4f} remaining of ${self.cfg.llm.daily_budget_usd:.2f}",
                    level="warning",
                )

            if (
                self.cfg.llm.enabled
                and self.cfg.llm.call_on_every_poll
                and self.llm.tracker.remaining() > 0
            ):
                await self.llm.chat(
                    [
                        {
                            "role": "user",
                            "content": (
                                f"Summarize bot poll: {len(markets)} markets, "
                                f"{len(approved)} approved signals."
                            ),
                        }
                    ],
                    prompt_summary="poll_status",
                    use_cache=False,
                )
        except Exception:
            log.exception("poll_error")
            self.risk.record_error()
            if self.risk.halted:
                await self.alerter.send("HALT: poll errors hit circuit breaker", level="error")


