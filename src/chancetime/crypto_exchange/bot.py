"""Path D paper poll: spot quotes + optional Path C signals → paper fills."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from chancetime.crypto_exchange.paper import ExchangePaperBook, SpotPosition
from chancetime.crypto_exchange.store import ExchangePaperStore
from chancetime.crypto_exchange.venues import DEFAULT_WATCHLIST, make_price_venue
from chancetime.modules.signals import load_latest_signals
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root

log = get_logger(__name__)


class ExchangeBot:
    def __init__(
        self,
        *,
        poll_interval: float = 20.0,
        venue: str = "coinbase",
        watchlist: tuple[str, ...] = DEFAULT_WATCHLIST,
        db_path: str = "data/crypto_exchange_paper.db",
        cash: float = 1000.0,
        fee_bps: float = 30.0,
        consume_signals: bool = True,
        trade_on_signals: bool = False,
        signal_size_usd: float = 25.0,
        min_signal_confidence: float = 0.65,
        max_signal_age_sec: float = 180.0,
        max_positions: int = 4,
        max_notional_per_asset: float = 100.0,
        max_signal_fills_per_poll: int = 2,
    ) -> None:
        self.poll_interval = poll_interval
        self.venue_name = venue
        self.watchlist = tuple(a.upper() for a in watchlist)
        self.price = make_price_venue(venue if venue != "robinhood" else "coinbase")
        # Prefer Coinbase public prices always for paper; remember preferred venue label
        self.exec_venue_label = venue
        self.store = ExchangePaperStore(db_path)
        start_cash = self.store.last_cash(default=cash)
        self.book = ExchangePaperBook(
            cash=start_cash, fee_bps=fee_bps, venue=self.exec_venue_label
        )
        for row in self.store.load_positions():
            self.book.positions[str(row["asset"]).upper()] = SpotPosition(
                asset=str(row["asset"]).upper(),
                qty=float(row["qty"]),
                avg_price=float(row["avg_price"]),
                cost_usd=float(row["cost_usd"]),
            )
        self.consume_signals = consume_signals
        self.trade_on_signals = trade_on_signals
        self.signal_size_usd = signal_size_usd
        self.min_signal_confidence = min_signal_confidence
        self.max_signal_age_sec = max_signal_age_sec
        self.max_positions = max_positions
        self.max_notional_per_asset = max_notional_per_asset
        self.max_signal_fills_per_poll = max_signal_fills_per_poll
        self._stop = asyncio.Event()
        self.poll_count = 0
        self.research_dir = project_root() / "data" / "research" / "crypto_exchange"
        self._filled_signal_ids: set[str] = set()

    def request_stop(self) -> None:
        self._stop.set()

    async def close(self) -> None:
        await self.price.close()
        self.store.close()

    def _persist_last_fill(self) -> None:
        if not self.book.fills:
            return
        f = self.book.fills[-1]
        self.store.record_fill(
            asset=f.asset,
            side=f.side,
            price=f.price,
            qty=f.qty,
            size_usd=f.size_usd,
            fee_usd=f.fee_usd,
            venue=f.venue,
            signal_id=f.signal_id,
            note=f.note,
            cash_after=self.book.cash,
        )
        pos = self.book.positions.get(f.asset)
        if pos and pos.qty > 0:
            self.store.upsert_position(
                asset=f.asset,
                qty=pos.qty,
                avg_price=pos.avg_price,
                cost_usd=pos.cost_usd,
            )
        else:
            self.store.upsert_position(asset=f.asset, qty=0, avg_price=0, cost_usd=0)

    async def poll_once(self) -> dict[str, Any]:
        quotes = {}
        missing: list[str] = []
        for asset in self.watchlist:
            q = await self.price.get_quote(asset)
            if q is None or not q.has_price:
                missing.append(asset)
            else:
                quotes[asset] = q

        signal_actions: list[dict[str, Any]] = []
        signals = []
        fills_this_poll = 0
        if self.consume_signals:
            signals = load_latest_signals(max_age_sec=self.max_signal_age_sec)
            # Dedup by asset — keep highest confidence
            by_asset: dict[str, Any] = {}
            for s in signals:
                prev = by_asset.get(s.asset)
                if prev is None or s.confidence > prev.confidence:
                    by_asset[s.asset] = s
            # Prefer strongest actionable first
            ranked = sorted(
                by_asset.values(),
                key=lambda s: s.confidence,
                reverse=True,
            )
            for sig in ranked:
                asset = sig.asset
                actionable = sig.is_actionable(
                    min_confidence=self.min_signal_confidence,
                    max_age_sec=self.max_signal_age_sec,
                )
                row: dict[str, Any] = {
                    "asset": asset,
                    "direction": sig.direction,
                    "confidence": sig.confidence,
                    "p_up": sig.p_up,
                    "signal_id": sig.signal_id,
                    "reference_price": sig.reference_price,
                    "actionable": actionable,
                    "would_trade": actionable and sig.direction in {"up", "down"},
                }
                if not self.trade_on_signals:
                    row["trade"] = "shadow_only"
                    signal_actions.append(row)
                    continue
                if not actionable:
                    row["trade"] = "skip_not_actionable"
                    signal_actions.append(row)
                    continue
                if sig.signal_id in self._filled_signal_ids:
                    row["trade"] = "skip_already_filled"
                    signal_actions.append(row)
                    continue
                if fills_this_poll >= self.max_signal_fills_per_poll:
                    row["trade"] = "skip_poll_fill_cap"
                    signal_actions.append(row)
                    continue
                open_n = sum(1 for p in self.book.positions.values() if p.qty > 0)
                q = quotes.get(asset)
                if q is None:
                    row["trade"] = "skip_no_quote"
                elif sig.direction == "up":
                    pos = self.book.positions.get(asset)
                    pos_cost = pos.cost_usd if pos else 0.0
                    if pos_cost + self.signal_size_usd > self.max_notional_per_asset + 1e-9:
                        row["trade"] = "skip_asset_notional_cap"
                    elif open_n >= self.max_positions and (not pos or pos.qty <= 0):
                        row["trade"] = "skip_max_positions"
                    else:
                        err = self.book.try_buy(
                            q,
                            size_usd=self.signal_size_usd,
                            signal_id=sig.signal_id,
                            note="poly_implied_up",
                        )
                        row["trade"] = "bought" if err is None else err
                        if err is None:
                            fills_this_poll += 1
                            self._filled_signal_ids.add(sig.signal_id)
                            self._persist_last_fill()
                elif sig.direction == "down":
                    # Spot: reduce long only (no short in paper v1)
                    err = self.book.try_sell(
                        q,
                        size_usd=self.signal_size_usd,
                        signal_id=sig.signal_id,
                        note="poly_implied_down_reduce",
                    )
                    row["trade"] = "sold" if err is None else err
                    if err is None:
                        fills_this_poll += 1
                        self._filled_signal_ids.add(sig.signal_id)
                        self._persist_last_fill()
                else:
                    row["trade"] = "flat"
                signal_actions.append(row)

        equity = self.book.mark_equity(quotes)
        exposure = self.book.exposure_usd(quotes)
        open_pos = sum(1 for p in self.book.positions.values() if p.qty > 0)
        self.store.snapshot_equity(
            cash=self.book.cash,
            equity=equity,
            exposure_usd=exposure,
            open_positions=open_pos,
            poll_count=self.poll_count + 1,
            extra={
                "quotes": {a: q.mid for a, q in quotes.items()},
                "missing": missing,
                "signals_seen": len(signals),
                "trade_on_signals": self.trade_on_signals,
            },
        )

        # Research log
        self.research_dir.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d", time.gmtime())
        path = self.research_dir / f"scan-{day}.jsonl"
        row = {
            "ts": time.time(),
            "poll": self.poll_count + 1,
            "quotes": {
                a: {"mid": q.mid, "bid": q.bid, "ask": q.ask, "source": q.source}
                for a, q in quotes.items()
            },
            "missing": missing,
            "signals": signal_actions,
            "equity": equity,
            "cash": self.book.cash,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

        would = sum(1 for a in signal_actions if a.get("would_trade"))
        traded = sum(1 for a in signal_actions if a.get("trade") in {"bought", "sold"})
        summary = {
            "poll": self.poll_count + 1,
            "quotes": {a: q.mid for a, q in quotes.items()},
            "missing": missing,
            "signals": len(signals),
            "would_trade": would,
            "traded": traded,
            "signal_actions": signal_actions,
            "equity": equity,
            "cash": self.book.cash,
            "open_positions": open_pos,
            "fills": len(self.book.fills),
            "venue": self.exec_venue_label,
            "trade_on_signals": self.trade_on_signals,
        }
        log.info(
            "crypto_exchange_poll",
            **{k: v for k, v in summary.items() if k != "signal_actions"},
        )
        return summary

    async def run(self, *, max_polls: int | None = None) -> None:
        log.info(
            "crypto_exchange_start",
            venue=self.exec_venue_label,
            watchlist=list(self.watchlist),
            trade_on_signals=self.trade_on_signals,
            msg="PAPER only — no live exchange orders",
        )
        try:
            while not self._stop.is_set():
                try:
                    await self.poll_once()
                except Exception:
                    log.exception("crypto_exchange_poll_error")
                self.poll_count += 1
                if max_polls is not None and self.poll_count >= max_polls:
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
        finally:
            await self.close()
            log.info("crypto_exchange_stop", polls=self.poll_count)
