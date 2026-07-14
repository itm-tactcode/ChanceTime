"""Path D CLI: US crypto exchange paper bot + signal consumer."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from chancetime.utils.logging import setup_logging


def register(app: typer.Typer) -> None:
    ex = typer.Typer(
        name="exchange",
        help="Path D: US crypto exchange spot paper (Coinbase feed; optional C signals).",
        add_completion=False,
    )
    app.add_typer(ex, name="exchange")

    @ex.command("run")
    def exchange_run(
        once: Annotated[bool, typer.Option("--once")] = False,
        max_polls: Annotated[int | None, typer.Option("--max-polls")] = None,
        interval: Annotated[
            float | None,
            typer.Option("--interval", help="Default: crypto_exchange.poll_interval_seconds"),
        ] = None,
        venue: Annotated[
            str | None,
            typer.Option(
                "--venue",
                help="coinbase | robinhood label (default: config)",
            ),
        ] = None,
        trade_signals: Annotated[
            bool | None,
            typer.Option(
                "--trade-signals/--no-trade-signals",
                help="Paper-trade on Path C signals (default: config)",
            ),
        ] = None,
        size: Annotated[
            float | None,
            typer.Option("--size", help="USD per signal trade (default: config)"),
        ] = None,
        min_conf: Annotated[
            float | None,
            typer.Option("--min-confidence", help="Default: config"),
        ] = None,
        log_level: Annotated[str | None, typer.Option("--log-level")] = None,
    ) -> None:
        """Paper poll: knobs from default.yaml ← user.yaml; CLI overrides. No live orders."""
        from chancetime.crypto_exchange.bot import ExchangeBot
        from chancetime.utils.config import load_config

        cfg = load_config()
        d = cfg.crypto_exchange
        setup_logging(log_level or cfg.logging.level)

        bot = ExchangeBot(
            poll_interval=float(
                interval if interval is not None else d.poll_interval_seconds
            ),
            venue=str(venue if venue is not None else d.venue),
            db_path=d.db_path,
            cash=d.starting_cash,
            fee_bps=d.fee_bps,
            consume_signals=d.consume_signals,
            trade_on_signals=bool(
                trade_signals if trade_signals is not None else d.trade_signals
            ),
            signal_size_usd=float(
                size if size is not None else d.signal_size_usd
            ),
            min_signal_confidence=float(
                min_conf if min_conf is not None else d.min_signal_confidence
            ),
            max_signal_age_sec=d.max_signal_age_sec,
            max_positions=d.max_positions,
            max_notional_per_asset=d.max_notional_per_asset,
            max_signal_fills_per_poll=d.max_signal_fills_per_poll,
        )
        polls = 1 if once else max_polls
        try:
            asyncio.run(bot.run(max_polls=polls))
        except KeyboardInterrupt:
            bot.request_stop()
            typer.echo("stopped")

    @ex.command("status")
    def exchange_status() -> None:
        """Exchange paper book summary."""
        from chancetime.crypto_exchange.store import ExchangePaperStore

        store = ExchangePaperStore()
        try:
            typer.echo(json.dumps(store.summary(), indent=2, default=str))
        finally:
            store.close()

    @ex.command("scan")
    def exchange_scan(
        venue: Annotated[str, typer.Option("--venue")] = "coinbase",
        json_out: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """One-shot spot quotes for default watchlist."""

        async def _go() -> list[dict]:
            from chancetime.crypto_exchange.venues import DEFAULT_WATCHLIST, make_price_venue

            v = make_price_venue("coinbase" if venue == "robinhood" else venue)
            try:
                rows = []
                for a in DEFAULT_WATCHLIST:
                    q = await v.get_quote(a)
                    if q is None:
                        rows.append({"asset": a, "mid": None, "ok": False})
                    else:
                        rows.append(
                            {
                                "asset": a,
                                "mid": q.mid,
                                "bid": q.bid,
                                "ask": q.ask,
                                "source": q.source,
                                "ok": True,
                            }
                        )
                return rows
            finally:
                await v.close()

        rows = asyncio.run(_go())
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            for r in rows:
                typer.echo(
                    f"{r['asset']:5} mid={r.get('mid')!s:>12}  "
                    f"bid={r.get('bid')!s:>10} ask={r.get('ask')!s:>10}  {r.get('source', '')}"
                )

    @ex.command("signals")
    def exchange_signals(
        max_age: Annotated[float, typer.Option("--max-age")] = 300.0,
    ) -> None:
        """Show latest Path C direction signals (if any)."""
        from chancetime.modules.signals import load_latest_signals

        sigs = load_latest_signals(max_age_sec=max_age)
        if not sigs:
            typer.echo("[]  (no fresh signals — run: chancetime crypto run --once)")
            return
        typer.echo(
            json.dumps([s.model_dump() for s in sigs], indent=2, default=str)
        )

    @ex.command("paper-buy")
    def exchange_paper_buy(
        asset: Annotated[str, typer.Argument(help="BTC, ETH, …")],
        size: Annotated[float, typer.Option("--size", help="USD notional")] = 25.0,
        venue: Annotated[str, typer.Option("--venue")] = "coinbase",
    ) -> None:
        """Manual paper buy at current public quote (no live order)."""

        async def _go() -> dict:
            from chancetime.crypto_exchange.paper import ExchangePaperBook
            from chancetime.crypto_exchange.store import ExchangePaperStore
            from chancetime.crypto_exchange.venues import make_price_venue

            from chancetime.crypto_exchange.paper import SpotPosition

            v = make_price_venue("coinbase" if venue == "robinhood" else venue)
            store = ExchangePaperStore()
            book = ExchangePaperBook(cash=store.last_cash(default=1000.0), venue=venue)
            for row in store.load_positions():
                book.positions[str(row["asset"]).upper()] = SpotPosition(
                    asset=str(row["asset"]).upper(),
                    qty=float(row["qty"]),
                    avg_price=float(row["avg_price"]),
                    cost_usd=float(row["cost_usd"]),
                )
            try:
                q = await v.get_quote(asset.upper())
                if q is None or not q.has_price:
                    return {"ok": False, "error": "no_quote", "asset": asset.upper()}
                err = book.try_buy(q, size_usd=size, note="manual_paper_buy")
                if err:
                    return {"ok": False, "error": err, "quote": q.model_dump()}
                f = book.fills[-1]
                store.record_fill(
                    asset=f.asset,
                    side=f.side,
                    price=f.price,
                    qty=f.qty,
                    size_usd=f.size_usd,
                    fee_usd=f.fee_usd,
                    venue=f.venue,
                    note=f.note,
                    cash_after=book.cash,
                )
                pos = book.positions[f.asset]
                store.upsert_position(
                    asset=f.asset,
                    qty=pos.qty,
                    avg_price=pos.avg_price,
                    cost_usd=pos.cost_usd,
                )
                mtm = book.mark_equity({f.asset: q})
                exp = book.exposure_usd({f.asset: q})
                store.snapshot_equity(
                    cash=book.cash,
                    equity=mtm,
                    exposure_usd=exp,
                    open_positions=1,
                    poll_count=0,
                    extra={"manual": True},
                )
                return {
                    "ok": True,
                    "asset": f.asset,
                    "price": f.price,
                    "size_usd": f.size_usd,
                    "fee_usd": f.fee_usd,
                    "qty": f.qty,
                    "cash": book.cash,
                    "position_mtm": exp,
                    "equity": mtm,
                    "note": (
                        "equity = cash + position MTM. Buy spends cash; "
                        "you do not 'make' size_usd as profit."
                    ),
                }
            finally:
                await v.close()
                store.close()

        typer.echo(json.dumps(asyncio.run(_go()), indent=2, default=str))
