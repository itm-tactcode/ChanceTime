"""Path C CLI: crypto Up/Down paper bot + hub."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from chancetime.utils.logging import setup_logging


def register(app: typer.Typer) -> None:
    crypto = typer.Typer(
        name="crypto",
        help="Path C: global Polymarket crypto Up/Down (paper-first, separate module).",
        add_completion=False,
    )
    app.add_typer(crypto, name="crypto")

    @crypto.command("run")
    def crypto_run(
        once: Annotated[bool, typer.Option("--once", help="Single poll then exit")] = False,
        max_polls: Annotated[
            int | None,
            typer.Option("--max-polls", help="Stop after N polls"),
        ] = None,
        interval: Annotated[float, typer.Option("--interval", help="Seconds between polls")] = 15.0,
        limit: Annotated[int, typer.Option("--limit", help="Max Up/Down markets")] = 20,
        bbo_limit: Annotated[int, typer.Option("--bbo-limit")] = 12,
        complete_set: Annotated[
            bool,
            typer.Option(
                "--paper-complete-set",
                help="Paper-buy both sides when ask_up+ask_down < 1 (requires BBO+spot)",
            ),
        ] = False,
        size: Annotated[float, typer.Option("--size", help="USD for complete-set package")] = 5.0,
        direction: Annotated[
            bool,
            typer.Option(
                "--paper-direction",
                help="Legacy: paper-buy favored side on CLOB lean only",
            ),
        ] = False,
        direction_size: Annotated[
            float, typer.Option("--direction-size", help="USD per direction paper fill")
        ] = 5.0,
        paper_strategy: Annotated[
            bool,
            typer.Option(
                "--paper-strategy/--shadow-strategy",
                help=(
                    "Run tweet hybrid strategy (mispricing + complete-set + snipe). "
                    "Default shadow = evaluate/log only, no paper fills."
                ),
            ),
        ] = False,
        strategy_edge: Annotated[
            float,
            typer.Option("--strategy-edge", help="Min model vs market edge for mispricing leg"),
        ] = 0.06,
        use_ws: Annotated[
            bool,
            typer.Option("--ws/--no-ws", help="Optional CLOB market WebSocket (Phase 28)"),
        ] = False,
        no_signals: Annotated[
            bool,
            typer.Option("--no-signals", help="Do not publish C→D direction signals"),
        ] = False,
        log_level: Annotated[str, typer.Option("--log-level")] = "INFO",
    ) -> None:
        """Paper scan loop: tweet hybrid Up/Down (shadow by default). No live CLOB orders."""
        setup_logging(log_level)
        from chancetime.crypto_updown.bot import CryptoUpDownBot

        bot = CryptoUpDownBot(
            poll_interval=interval,
            max_markets=limit,
            bbo_limit=bbo_limit,
            paper_trade_complete_set=complete_set,
            complete_set_size_usd=size,
            paper_trade_direction=direction,
            direction_size_usd=direction_size,
            paper_strategy=paper_strategy,
            strategy_size_usd=size,
            strategy_min_edge=strategy_edge,
            publish_direction_signals=not no_signals,
            use_ws=use_ws,
        )
        polls = 1 if once else max_polls
        try:
            asyncio.run(bot.run(max_polls=polls))
        except RuntimeError as exc:
            # Exclusive session lock
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        except KeyboardInterrupt:
            bot.request_stop()
            typer.echo("stopped")

    @crypto.command("status")
    def crypto_status() -> None:
        """Crypto paper book summary."""
        from chancetime.crypto_updown.store import CryptoPaperStore

        store = CryptoPaperStore()
        try:
            typer.echo(json.dumps(store.summary(), indent=2, default=str))
        finally:
            store.close()

    @crypto.command("reset-book")
    def crypto_reset_book(
        yes: Annotated[
            bool,
            typer.Option("--yes", "-y", help="Skip confirmation prompt"),
        ] = False,
        cash: Annotated[
            float,
            typer.Option("--cash", help="Starting cash after reset"),
        ] = 1000.0,
    ) -> None:
        """Wipe Path C paper book (fills, positions, settlements) and restore cash."""
        if not yes:
            typer.confirm(
                "Reset crypto_paper.db (all Path C paper fills/positions)?",
                abort=True,
            )
        from chancetime.crypto_updown.store import CryptoPaperStore

        store = CryptoPaperStore(starting_cash=cash)
        try:
            result = store.reset_book(starting_cash=cash)
            typer.echo(json.dumps(result, indent=2, default=str))
        finally:
            store.close()

    @crypto.command("scan")
    def crypto_scan(
        limit: Annotated[int, typer.Option("--limit")] = 15,
        bbo: Annotated[bool, typer.Option("--bbo/--no-bbo")] = True,
        json_out: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """One-shot list of active Up/Down markets (+ optional BBO + spot)."""

        async def _go() -> list[dict]:
            from chancetime.crypto_updown.clob import ClobPublicClient
            from chancetime.crypto_updown.gamma import GammaClient
            from chancetime.crypto_updown.spot import SpotClient

            g, c, s = GammaClient(), ClobPublicClient(), SpotClient()
            try:
                events = await g.list_updown_events(limit=limit)
                markets = g.events_to_markets(events)[:limit]
                if bbo:
                    markets = [await c.enrich_market(m) for m in markets[: min(12, len(markets))]]
                assets = {m.asset for m in markets}
                spots = {}
                for a in assets:
                    t = await s.get_price(a)
                    if t:
                        spots[a] = t.price
                rows = []
                for m in markets:
                    csum = m.complete_set_ask_sum()
                    rows.append(
                        {
                            "slug": m.slug,
                            "asset": m.asset,
                            "question": m.question[:80],
                            "spot": spots.get(m.asset),
                            "up_ask": m.up.best_ask if m.up else None,
                            "down_ask": m.down.best_ask if m.down else None,
                            "complete_set_sum": csum,
                            "sec_left": m.seconds_remaining(),
                        }
                    )
                return rows
            finally:
                await g.close()
                await c.close()
                await s.close()

        rows = asyncio.run(_go())
        # Also publish signals so desktop Path D can read them after a scan
        try:
            from chancetime.crypto_updown.models import OutcomeBook, UpDownMarket
            from chancetime.crypto_updown.strategies import scan_implied_direction
            from chancetime.modules.signals import publish_signals

            # Lightweight signal publish from scan rows (mids only)
            markets = []
            spots = {}
            for r in rows:
                if r.get("spot") is not None:
                    spots[r["asset"]] = float(r["spot"])
                up_a, dn_a = r.get("up_ask"), r.get("down_ask")
                mid_u = float(up_a) if up_a is not None else None
                mid_d = float(dn_a) if dn_a is not None else None
                markets.append(
                    UpDownMarket(
                        condition_id=r.get("slug") or "x",
                        slug=r.get("slug") or "x",
                        question=r.get("question") or r.get("slug") or "",
                        asset=r["asset"],
                        up=OutcomeBook(
                            token_id="u",
                            outcome="Up",
                            mid=mid_u,
                            best_ask=mid_u,
                            has_bbo=mid_u is not None,
                        ),
                        down=OutcomeBook(
                            token_id="d",
                            outcome="Down",
                            mid=mid_d,
                            best_ask=mid_d,
                            has_bbo=mid_d is not None,
                        ),
                    )
                )
            sigs = scan_implied_direction(markets, spots)
            publish_signals(sigs)
            n_act = sum(1 for s in sigs if s.is_actionable(min_confidence=0.55))
        except Exception as exc:  # noqa: BLE001 — scan still succeeds
            sigs = []
            n_act = 0
            _sig_err = str(exc)
        else:
            _sig_err = None

        if json_out:
            typer.echo(
                json.dumps(
                    {"markets": rows, "signals": len(sigs), "actionable": n_act},
                    indent=2,
                    default=str,
                )
            )
        else:
            for r in rows:
                typer.echo(
                    f"{r['asset']:5} sum={r['complete_set_sum']!s:>8} "
                    f"spot={r['spot']!s:>10}  {r['slug'][:48]}"
                )
            typer.echo(f"({len(rows)} markets · signals={len(sigs)} actionable≈{n_act})")
            if _sig_err:
                typer.echo(f"(signal publish note: {_sig_err})")

    @crypto.command("hub")
    def crypto_hub() -> None:
        """Combined multi-module portfolio snapshot (JSON)."""
        import os

        os.environ.setdefault("CHANCETIME_QUIET", "1")
        setup_logging("ERROR")
        from chancetime.crypto_updown.hub import combined_portfolio

        typer.echo(json.dumps(combined_portfolio(), indent=2, default=str))

    @crypto.command("scorecard")
    def crypto_scorecard(
        day: Annotated[
            str | None,
            typer.Option("--day", help="UTC day YYYYMMDD (default today)"),
        ] = None,
    ) -> None:
        """Phase 29 resolve-aware scorecard (hit rate, phases, go/no-go)."""
        from chancetime.crypto_updown.scorecard import build_scorecard

        typer.echo(json.dumps(build_scorecard(day), indent=2, default=str))
