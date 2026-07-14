"""Paper poll loop: discover Up/Down windows, spot, BBO, research log.

Phase 28: infrastructure only — no live orders, no invented prices.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any

from chancetime.crypto_updown.clob import ClobPublicClient
from chancetime.crypto_updown.clob_ws import ClobMarketWs
from chancetime.crypto_updown.gamma import GammaClient
from chancetime.crypto_updown.kill_switches import KillSwitchConfig, KillSwitchState
from chancetime.crypto_updown.paper import CryptoPaperBook, PaperPosition
from chancetime.crypto_updown.spot import SpotClient
from chancetime.crypto_updown.store import CryptoPaperStore
from chancetime.crypto_updown.strategies import (
    TweetHybridStrategy,
    TweetStrategyConfig,
    paper_buy_favored_side,
    scan_implied_direction,
)
from chancetime.modules.signals import publish_signals
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root
from chancetime.utils.research_log import base_fields

log = get_logger(__name__)


class CryptoUpDownBot:
    def __init__(
        self,
        *,
        poll_interval: float = 15.0,
        max_markets: int = 20,
        enrich_bbo: bool = True,
        bbo_limit: int = 12,
        db_path: str = "data/crypto_paper.db",
        cash: float = 1000.0,
        paper_trade_complete_set: bool = False,
        complete_set_size_usd: float = 5.0,
        paper_trade_direction: bool = False,
        direction_size_usd: float = 5.0,
        direction_min_confidence: float = 0.65,
        # Canonical tweet hybrid strategy (steps 1–5)
        paper_strategy: bool = False,
        strategy_size_usd: float = 5.0,
        strategy_min_edge: float = 0.06,
        strategy_config: TweetStrategyConfig | None = None,
        publish_direction_signals: bool = True,
        fee_bps: float = 50.0,
        use_ws: bool = False,
        max_daily_loss_usd: float = 50.0,
        max_spot_age_sec: float = 90.0,
    ) -> None:
        self.poll_interval = poll_interval
        self.max_markets = max_markets
        self.enrich_bbo = enrich_bbo
        self.bbo_limit = bbo_limit
        self.use_ws = use_ws
        self.gamma = GammaClient()
        self.clob = ClobPublicClient()
        self.spot = SpotClient()
        self.ws = ClobMarketWs() if use_ws else None
        self._ws_started = False
        self.store = CryptoPaperStore(db_path, starting_cash=cash)
        self.book = CryptoPaperBook(cash=self.store.get_cash(), fee_bps=fee_bps)
        for row in self.store.load_positions():
            key = (str(row["market_slug"]), str(row["side"]).lower())
            self.book.positions[key] = PaperPosition(
                market_slug=key[0],
                side=key[1],
                size_usd=float(row["size_usd"]),
                entry_price=float(row["entry_price"]),
                contracts=float(row["contracts"]),
                fees_paid=float(row.get("fees_paid") or 0),
            )
        meta = self.store.summary()
        self.book.realized_pnl = float(meta.get("realized_pnl") or 0)
        self.paper_trade_complete_set = paper_trade_complete_set
        self.complete_set_size_usd = complete_set_size_usd
        self.paper_trade_direction = paper_trade_direction
        self.direction_size_usd = direction_size_usd
        self.direction_min_confidence = direction_min_confidence
        self.paper_strategy = paper_strategy
        self.publish_direction_signals = publish_direction_signals
        if strategy_config is not None:
            scfg = strategy_config
        else:
            scfg = TweetStrategyConfig(
                size_usd=strategy_size_usd,
                snipe_size_usd=strategy_size_usd,
                complete_set_size_usd=complete_set_size_usd,
                min_edge=strategy_min_edge,
            )
        self.strategy = TweetHybridStrategy(scfg)
        self.kill_cfg = KillSwitchConfig(
            max_daily_loss_usd=max_daily_loss_usd,
            max_spot_age_sec=max_spot_age_sec,
            starting_equity=cash,
        )
        self.kills = KillSwitchState()
        self._stop = asyncio.Event()
        self.poll_count = 0
        self.research_dir = project_root() / "data" / "research" / "crypto_updown"
        # slug → first observed spot (window reference / open proxy)  [step 1]
        self._window_refs: dict[str, float] = {}
        # slug → late_join if first sight not near open
        self._window_ref_quality: dict[str, str] = {}
        # slug → {asset, end_ts, start_ts} for resolution after market drops from list
        self._window_meta: dict[str, dict[str, Any]] = {}
        # slug → already logged resolution (this process)
        self._resolved: set[str] = set()
        self._load_persisted_window_refs()

    def request_stop(self) -> None:
        self._stop.set()

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        await self.gamma.close()
        await self.clob.close()
        await self.spot.close()
        self.store.close()

    async def poll_once(self) -> dict[str, Any]:
        events = await self.gamma.list_updown_events(limit=self.max_markets)
        markets = self.gamma.events_to_markets(events)
        # Prefer soonest ending
        markets.sort(key=lambda m: m.window_end.timestamp() if m.window_end else 1e18)
        markets = markets[: self.max_markets]

        if self.enrich_bbo:
            enriched = []
            for m in markets[: self.bbo_limit]:
                enriched.append(await self.clob.enrich_market(m))
            enriched.extend(markets[self.bbo_limit :])
            markets = enriched
            # Optional WS: subscribe once to token ids for fresher books next polls
            if self.ws is not None and not self._ws_started:
                tids: list[str] = []
                for m in markets[: self.bbo_limit]:
                    if m.up:
                        tids.append(m.up.token_id)
                    if m.down:
                        tids.append(m.down.token_id)
                await self.ws.start(tids)
                self._ws_started = True
            # Overlay WS books onto REST BBO when available
            if self.ws is not None:
                markets = [self._overlay_ws_book(m) for m in markets]

        # Spot for unique assets — fail closed: no guess if missing
        assets = sorted({m.asset for m in markets})
        spots: dict[str, float] = {}
        spot_missing: list[str] = []
        spot_ages: dict[str, float | None] = {}
        for a in assets:
            tick = await self.spot.get_price(a)
            if tick is None:
                spot_missing.append(a)
                spot_ages[a] = self.spot.last_ok_age(a)
            else:
                spots[a] = tick.price
                spot_ages[a] = 0.0

        # Capture window reference (step 1) — prefer near-open sightings
        from chancetime.crypto_updown.gamma import window_bounds_from_slug

        for m in markets:
            px = spots.get(m.asset)
            end_ts = m.window_end.timestamp() if m.window_end else None
            start_ts = m.window_start.timestamp() if m.window_start else None
            bounds = window_bounds_from_slug(m.slug)
            if bounds is not None:
                start_ts = bounds[0]
                end_ts = bounds[1]
            if m.slug not in self._window_meta:
                self._window_meta[m.slug] = {
                    "asset": m.asset,
                    "end_ts": end_ts,
                    "start_ts": start_ts,
                }
            else:
                if end_ts is not None:
                    self._window_meta[m.slug]["end_ts"] = end_ts
                if start_ts is not None:
                    self._window_meta[m.slug]["start_ts"] = start_ts
            if px is None or px <= 0:
                continue
            if m.slug not in self._window_refs:
                quality = self._ref_quality(m)
                self._set_window_ref(
                    m.slug,
                    asset=str(m.asset),
                    ref=px,
                    quality=quality,
                    start_ts=start_ts,
                    end_ts=end_ts,
                )

        # Resolutions: live tracked windows + any expired open positions (restart-safe)
        resolutions = await self._check_resolutions(markets, spots)

        # Kill switches (before strategy paper fills)
        equity_pre = self.book.mark_equity(markets)
        worst_spot_age = None
        for a in assets:
            age = spot_ages.get(a)
            if age is not None:
                worst_spot_age = age if worst_spot_age is None else max(worst_spot_age, age)
        kill_reason = self.kills.check(
            spot_age_sec=worst_spot_age,
            spread=None,
            equity=equity_pre,
            cfg=self.kill_cfg,
        )
        allow_fills = self.paper_strategy and not self.kills.halted
        if kill_reason and self.kills.halted:
            log.warning("crypto_kill_switch", reason=kill_reason)

        # --- Tweet hybrid strategy (eval always; paper fill if paper_strategy) ---
        strat_result = self.strategy.run_poll(
            self.book,
            markets,
            spots,
            self._window_refs,
            execute=allow_fills,
        )
        strategy_fills = 0
        if allow_fills:
            for act in strat_result.actions:
                if act.get("action") == "paper_buy":
                    strategy_fills += 1
                    self._persist_position_fill(
                        slug=str(act.get("slug") or ""),
                        side=str(act.get("side") or ""),
                        size_usd=float(act.get("size_usd") or 0),
                        note=f"tweet_hybrid:{act.get('phase')}",
                        markets=markets,
                    )
        elif self.paper_strategy and self.kills.halted:
            for act in strat_result.actions:
                if act.get("action") == "shadow":
                    act["kill"] = self.kills.reason

        rows: list[dict[str, Any]] = []
        complete_hits = 0
        eval_by_slug = {e["slug"]: e for e in strat_result.evaluations}
        for m in markets:
            csum = m.complete_set_ask_sum()
            if csum is not None and csum < 1.0:
                complete_hits += 1
            # Legacy flags (if not using full tweet strategy)
            if (
                not self.paper_strategy
                and self.paper_trade_complete_set
                and csum is not None
                and csum < 1.0
                and m.asset in spots
            ):
                err_u = self.book.try_buy(
                    m, side="up", size_usd=self.complete_set_size_usd / 2
                )
                err_d = self.book.try_buy(
                    m, side="down", size_usd=self.complete_set_size_usd / 2
                )
                if err_u is None and err_d is None:
                    half = self.complete_set_size_usd / 2
                    fee_half = half * (self.book.fee_bps / 10_000.0)
                    self.store.record_fill(
                        market_slug=m.slug,
                        side="up",
                        price=float(m.up.best_ask or 0),
                        size_usd=half,
                        fee_usd=fee_half,
                        note="complete_set_paper",
                        cash_after=None,
                    )
                    self.store.record_fill(
                        market_slug=m.slug,
                        side="down",
                        price=float(m.down.best_ask or 0),
                        size_usd=half,
                        fee_usd=fee_half,
                        note="complete_set_paper",
                        cash_after=self.book.cash,
                    )
                    for side in ("up", "down"):
                        pos = self.book.positions.get((m.slug, side))
                        if pos:
                            self.store.upsert_position(
                                market_slug=m.slug,
                                side=side,
                                size_usd=pos.size_usd,
                                entry_price=pos.entry_price,
                                contracts=pos.contracts,
                                fees_paid=pos.fees_paid,
                            )

            sec = m.seconds_remaining()
            ref = self._window_refs.get(m.slug)
            ev = eval_by_slug.get(m.slug, {})
            rows.append(
                {
                    **base_fields(
                        poll=self.poll_count,
                        strategy="tweet_hybrid_updown" if self.paper_strategy else "crypto_updown_scan",
                    ),
                    "slug": m.slug,
                    "question": m.question[:120],
                    "asset": m.asset,
                    "spot": spots.get(m.asset),
                    "spot_ok": m.asset in spots,
                    "reference_price": ref,
                    "ref_quality": self._window_ref_quality.get(m.slug),
                    "spot_vs_ref": (
                        None
                        if ref is None or spots.get(m.asset) is None
                        else round(spots[m.asset] - ref, 6)
                    ),
                    "seconds_remaining": None if sec is None else round(sec, 2),
                    "model_p_up": ev.get("model_p_up"),
                    "market_p_up": ev.get("market_p_up"),
                    "vol": ev.get("vol"),
                    "direction_spot": ev.get("direction_spot"),
                    "up_mid": m.up.mid if m.up else None,
                    "down_mid": m.down.mid if m.down else None,
                    "up_ask": m.up.best_ask if m.up else None,
                    "down_ask": m.down.best_ask if m.down else None,
                    "up_bid": m.up.best_bid if m.up else None,
                    "down_bid": m.down.best_bid if m.down else None,
                    "has_bbo_up": bool(m.up and m.up.has_bbo),
                    "has_bbo_down": bool(m.down and m.down.has_bbo),
                    "complete_set_ask_sum": csum,
                    "complete_set_edge": None if csum is None else round(1.0 - csum, 4),
                    "volume": m.volume,
                }
            )

        # Path C → signal bus (for Path D)
        signals = strat_result.signals
        if not signals:
            signals = scan_implied_direction(
                markets, spots, references=self._window_refs
            )
        if self.publish_direction_signals:
            publish_signals(signals)

        direction_fills = 0
        if not self.paper_strategy and self.paper_trade_direction:
            by_slug = {s.slug: s for s in signals if s.slug}
            for m in markets:
                sig = by_slug.get(m.slug)
                if sig is None:
                    continue
                act = paper_buy_favored_side(
                    self.book,
                    m,
                    sig,
                    size_usd=self.direction_size_usd,
                    min_confidence=self.direction_min_confidence,
                    require_spot=True,
                )
                if act.get("action") == "paper_buy":
                    direction_fills += 1
                    self._persist_position_fill(
                        slug=m.slug,
                        side=str(act["side"]),
                        size_usd=self.direction_size_usd,
                        note=f"direction_paper conf={sig.confidence}",
                        markets=markets,
                    )

        # Research logs
        self.research_dir.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d", time.gmtime())
        path = self.research_dir / f"scan-{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
        if strat_result.actions:
            apath = self.research_dir / f"actions-{day}.jsonl"
            with apath.open("a", encoding="utf-8") as f:
                for act in strat_result.actions:
                    f.write(
                        json.dumps(
                            {**act, "poll": self.poll_count + 1, "ts": time.time()},
                            default=str,
                        )
                        + "\n"
                    )

        equity = self.book.mark_equity(markets)
        mtm = self.book.exposure_mtm(markets)
        self.store.snapshot_equity(
            cash=self.book.cash,
            equity=equity,
            exposure_usd=mtm,
            open_positions=len(self.book.positions),
            poll_count=self.poll_count + 1,
            realized_pnl=self.book.realized_pnl,
            extra={
                "markets": len(markets),
                "complete_hits": complete_hits,
                "spot_missing": spot_missing,
                "signals": len(signals),
                "direction_fills": direction_fills,
                "strategy_fills": strategy_fills,
                "paper_strategy": self.paper_strategy,
                "kill_halted": self.kills.halted,
                "kill_reason": self.kills.reason or None,
                "use_ws": self.use_ws,
                "resolutions": len(resolutions),
                "window_refs": len(self._window_refs),
            },
        )
        summary = {
            "poll": self.poll_count + 1,
            "markets": len(markets),
            "with_bbo": sum(1 for m in markets if m.up and m.up.has_bbo),
            "complete_set_hits": complete_hits,
            "signals": len(signals),
            "actionable_signals": sum(
                1 for s in signals if s.is_actionable(min_confidence=self.direction_min_confidence)
            ),
            "direction_fills": direction_fills,
            "strategy_fills": strategy_fills,
            "paper_strategy": self.paper_strategy,
            "kill_halted": self.kills.halted,
            "kill_reason": self.kills.reason or None,
            "use_ws": self.use_ws,
            "resolutions": len(resolutions),
            "window_refs": len(self._window_refs),
            "spot_assets": spots,
            "spot_missing": spot_missing,
            "equity": equity,
            "positions": len(self.book.positions),
            "fills": self.book.fills,
        }
        log.info("crypto_updown_poll", **summary)
        return summary

    def _ref_quality(self, m: Any) -> str:
        """Label open-print quality: near_open | mid_window | unknown."""
        sec = m.seconds_remaining()
        if m.window_start and m.window_end:
            total = m.window_end.timestamp() - m.window_start.timestamp()
            if total > 0 and sec is not None:
                # first sight within first 20% of window → near_open
                if sec >= total * 0.8:
                    return "near_open"
                return "mid_window_join"
        if sec is not None and sec >= 240:
            return "near_open"
        if sec is not None:
            return "mid_window_join"
        return "unknown"

    def _overlay_ws_book(self, market: Any) -> Any:
        """Merge latest WS payload into outcome books when present."""
        if self.ws is None:
            return market
        from chancetime.crypto_updown.clob import ClobPublicClient

        up, down = market.up, market.down
        if up is not None:
            raw = self.ws.get_book(up.token_id)
            if raw:
                # normalize to REST-like bids/asks if present
                payload = raw
                if "bids" not in payload and "buys" in payload:
                    payload = {
                        "bids": payload.get("buys") or payload.get("bids") or [],
                        "asks": payload.get("sells") or payload.get("asks") or [],
                    }
                up = ClobPublicClient.apply_book(up, payload)
        if down is not None:
            raw = self.ws.get_book(down.token_id)
            if raw:
                payload = raw
                if "bids" not in payload and "buys" in payload:
                    payload = {
                        "bids": payload.get("buys") or payload.get("bids") or [],
                        "asks": payload.get("sells") or payload.get("asks") or [],
                    }
                down = ClobPublicClient.apply_book(down, payload)
        return market.model_copy(update={"up": up, "down": down})

    def _persist_position_fill(
        self,
        *,
        slug: str,
        side: str,
        size_usd: float,
        note: str,
        markets: list[Any],
    ) -> None:
        """Write fill + position row after book.try_buy already mutated cash/positions."""
        side = side.lower()
        m = next((x for x in markets if x.slug == slug), None)
        price = 0.0
        if m is not None:
            book_side = m.up if side == "up" else m.down
            if book_side and book_side.best_ask is not None:
                price = float(book_side.best_ask)
        fee = size_usd * (self.book.fee_bps / 10_000.0)
        self.store.record_fill(
            market_slug=slug,
            side=side,
            price=price,
            size_usd=size_usd,
            fee_usd=fee,
            note=note,
            cash_after=self.book.cash,
        )
        pos = self.book.positions.get((slug, side))
        if pos:
            self.store.upsert_position(
                market_slug=slug,
                side=side,
                size_usd=pos.size_usd,
                entry_price=pos.entry_price,
                contracts=pos.contracts,
                fees_paid=pos.fees_paid,
            )

    def _load_persisted_window_refs(self) -> None:
        """Hydrate in-memory refs/meta from SQLite (survives process restart)."""
        from chancetime.crypto_updown.gamma import asset_from_slug, window_bounds_from_slug

        n = 0
        for row in self.store.load_window_refs():
            slug = str(row["market_slug"])
            asset = str(row.get("asset") or asset_from_slug(slug) or "")
            ref = float(row["ref_price"])
            if not slug or ref <= 0:
                continue
            self._window_refs[slug] = ref
            self._window_ref_quality[slug] = str(row.get("ref_quality") or "unknown")
            start_ts = row.get("start_ts")
            end_ts = row.get("end_ts")
            bounds = window_bounds_from_slug(slug)
            if bounds is not None:
                start_ts = bounds[0] if start_ts is None else start_ts
                end_ts = bounds[1] if end_ts is None else end_ts
            self._window_meta[slug] = {
                "asset": asset or None,
                "start_ts": float(start_ts) if start_ts is not None else None,
                "end_ts": float(end_ts) if end_ts is not None else None,
            }
            n += 1
        if n:
            log.info("window_refs_loaded", count=n)

    def _set_window_ref(
        self,
        slug: str,
        *,
        asset: str,
        ref: float,
        quality: str,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> None:
        self._window_refs[slug] = ref
        self._window_ref_quality[slug] = quality
        meta = self._window_meta.setdefault(slug, {})
        meta["asset"] = asset
        if start_ts is not None:
            meta["start_ts"] = start_ts
        if end_ts is not None:
            meta["end_ts"] = end_ts
        self.store.upsert_window_ref(
            market_slug=slug,
            asset=asset,
            ref_price=ref,
            ref_quality=quality,
            start_ts=start_ts if start_ts is not None else meta.get("start_ts"),
            end_ts=end_ts if end_ts is not None else meta.get("end_ts"),
        )
        log.info(
            "window_ref_set",
            slug=slug,
            ref=ref,
            asset=asset,
            quality=quality,
        )

    def _candidate_resolve_slugs(self) -> set[str]:
        """Tracked windows + any open paper inventory (for offline catch-up)."""
        slugs = set(self._window_refs)
        slugs.update(self._window_meta)
        for slug, _side in self.book.positions:
            slugs.add(slug)
        return slugs

    def _window_end_ts(self, slug: str, market: Any | None = None) -> float | None:
        from chancetime.crypto_updown.gamma import window_bounds_from_slug

        bounds = window_bounds_from_slug(slug)
        if bounds is not None:
            return bounds[1]
        meta = self._window_meta.get(slug, {})
        if meta.get("end_ts") is not None:
            return float(meta["end_ts"])
        if market is not None and market.window_end is not None:
            return market.window_end.timestamp()
        return None

    async def _resolve_outcome(
        self,
        slug: str,
        *,
        asset: str | None,
        ref: float | None,
        spots: dict[str, float],
        markets: list[Any],
    ) -> tuple[bool | None, float | None, str]:
        """Return (resolved_up, resolve_spot, method). Prefer Gamma settle, else spot vs ref."""
        from chancetime.crypto_updown.gamma import resolved_up_from_event

        # 1) Official / closed market prices from Gamma (best after downtime)
        try:
            event = await self.gamma.fetch_event_by_slug(slug)
        except Exception as exc:  # noqa: BLE001 — network; fall through
            log.warning("resolve_gamma_error", slug=slug, error=str(exc))
            event = None
        if event is not None:
            gamma_up = resolved_up_from_event(event)
            if gamma_up is not None:
                return gamma_up, None, "gamma_outcome"

        # 2) Spot vs first-seen open ref (in-session paper rule)
        px = spots.get(str(asset)) if asset else None
        if px is None and asset:
            for mm in markets:
                if mm.asset == asset and spots.get(mm.asset) is not None:
                    px = spots[mm.asset]
                    break
        if px is None and asset:
            tick = await self.spot.get_price(str(asset))
            if tick is not None and tick.price > 0:
                px = tick.price
        if ref is not None and ref > 0 and px is not None and px > 0:
            return px >= ref, px, "spot_vs_ref"

        if px is None:
            log.warning("resolve_skip_no_spot", slug=slug, asset=asset, has_ref=ref is not None)
        elif ref is None:
            log.warning("resolve_skip_no_ref_or_gamma", slug=slug, asset=asset)
        return None, px, "unresolved"

    async def _check_resolutions(
        self,
        markets: list[Any],
        spots: dict[str, float],
    ) -> list[dict[str, Any]]:
        """When a window has ended, settle paper + log resolution.

        Restart-safe: also walks open positions whose windows expired offline.
        Window end = slug start + 5m/15m. Outcome: Gamma closed prices if known,
        else spot >= open ref → Up.
        """
        from chancetime.crypto_updown.gamma import asset_from_slug, window_bounds_from_slug

        now = time.time()
        out: list[dict[str, Any]] = []
        by_slug = {m.slug: m for m in markets}

        for slug in sorted(self._candidate_resolve_slugs()):
            if slug in self._resolved:
                continue
            m = by_slug.get(slug)
            meta = self._window_meta.get(slug, {})
            asset = (
                (m.asset if m else None)
                or meta.get("asset")
                or asset_from_slug(slug)
            )
            end_ts = self._window_end_ts(slug, m)
            if end_ts is None or now < float(end_ts) + 1.0:
                continue
            if not asset:
                log.warning("resolve_skip_no_asset", slug=slug)
                continue

            ref = self._window_refs.get(slug)
            resolved_up, px, method = await self._resolve_outcome(
                slug, asset=str(asset), ref=ref, spots=spots, markets=markets
            )
            if resolved_up is None:
                continue

            settles = self.book.settle_market(slug, resolved_up=resolved_up)
            settle_note = method
            for srow in settles:
                self.store.record_settlement(
                    market_slug=slug,
                    side=srow["side"],
                    contracts=srow["contracts"],
                    payout=srow["payout"],
                    pnl=srow["pnl"],
                    resolved_up=resolved_up,
                    note=settle_note,
                )
            if settles:
                self.store.clear_positions_for_slug(slug)
                self.store.set_cash(self.book.cash, realized_pnl=self.book.realized_pnl)
            self.store.delete_window_ref(slug)
            bounds = window_bounds_from_slug(slug)
            row = {
                "ts": now,
                "slug": slug,
                "asset": asset,
                "reference_price": ref,
                "resolve_spot": px,
                "resolved_up": resolved_up,
                "outcome": "Up" if resolved_up else "Down",
                "end_ts": end_ts,
                "start_ts": bounds[0] if bounds else meta.get("start_ts"),
                "settlements": len(settles),
                "settlement_pnl": sum(s["pnl"] for s in settles),
                "method": method,
                "note": f"{method}+slug_window_end",
            }
            out.append(row)
            self._resolved.add(slug)
            log.info(
                "crypto_updown_resolution",
                **{k: v for k, v in row.items() if k != "note"},
            )

        if out:
            self.research_dir.mkdir(parents=True, exist_ok=True)
            day = time.strftime("%Y%m%d", time.gmtime())
            path = self.research_dir / f"resolutions-{day}.jsonl"
            with path.open("a", encoding="utf-8") as f:
                for row in out:
                    f.write(json.dumps(row, default=str) + "\n")
            n_settled = sum(int(r.get("settlements") or 0) for r in out)
            if n_settled:
                log.info(
                    "crypto_updown_reconcile",
                    resolutions=len(out),
                    position_settlements=n_settled,
                    cash=round(self.book.cash, 4),
                    open_positions=len(self.book.positions),
                    realized_pnl=round(self.book.realized_pnl, 4),
                )
        return out

    async def run(self, *, max_polls: int | None = None) -> None:
        from chancetime.crypto_updown.lock import CryptoSessionLock

        lock = CryptoSessionLock()
        lock.acquire()  # fail fast if another session owns the paper book
        log.info(
            "crypto_updown_start",
            poll_interval=self.poll_interval,
            max_markets=self.max_markets,
            paper_strategy=self.paper_strategy,
            paper_complete_set=self.paper_trade_complete_set,
            paper_direction=self.paper_trade_direction,
            publish_signals=self.publish_direction_signals,
            cash=round(self.book.cash, 4),
            positions=len(self.book.positions),
            window_refs=len(self._window_refs),
            msg=(
                "PAPER only — tweet hybrid eval always; "
                "fills only with --paper-strategy; no live CLOB orders; "
                "expired inventory reconciled on poll via Gamma/spot"
            ),
        )
        try:
            while not self._stop.is_set():
                try:
                    await self.poll_once()
                except Exception:
                    log.exception("crypto_updown_poll_error")
                self.poll_count += 1
                if max_polls is not None and self.poll_count >= max_polls:
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
        finally:
            await self.close()
            lock.release()
            log.info(
                "crypto_updown_stop",
                polls=self.poll_count,
                cash=round(self.book.cash, 4),
                positions=len(self.book.positions),
            )
