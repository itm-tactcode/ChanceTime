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
        interval: Annotated[
            float | None,
            typer.Option("--interval", help="Seconds between polls (default: user.yaml / default.yaml)"),
        ] = None,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Max Up/Down markets (default: config)"),
        ] = None,
        bbo_limit: Annotated[
            int | None, typer.Option("--bbo-limit", help="Markets with BBO enrich (default: config)")
        ] = None,
        complete_set: Annotated[
            bool | None,
            typer.Option(
                "--paper-complete-set/--no-paper-complete-set",
                help="Paper-buy both sides when ask_up+ask_down < 1 (default: config)",
            ),
        ] = None,
        size: Annotated[
            float | None,
            typer.Option("--size", help="USD size for strategy legs (default: config)"),
        ] = None,
        direction: Annotated[
            bool | None,
            typer.Option(
                "--paper-direction/--no-paper-direction",
                help="Legacy: paper-buy favored side on CLOB lean only (default: config)",
            ),
        ] = None,
        direction_size: Annotated[
            float | None,
            typer.Option("--direction-size", help="USD per direction paper fill (default: size)"),
        ] = None,
        paper_strategy: Annotated[
            bool | None,
            typer.Option(
                "--paper-strategy/--shadow-strategy",
                help=(
                    "Tweet hybrid paper fills vs shadow. "
                    "Default from config/user.yaml crypto_updown.paper_strategy."
                ),
            ),
        ] = None,
        strategy_edge: Annotated[
            float | None,
            typer.Option(
                "--strategy-edge",
                help="Min model vs market edge (prefer user.yaml crypto_updown.min_edge)",
            ),
        ] = None,
        use_ws: Annotated[
            bool | None,
            typer.Option("--ws/--no-ws", help="Optional CLOB market WebSocket (default: config)"),
        ] = None,
        no_signals: Annotated[
            bool,
            typer.Option("--no-signals", help="Do not publish C→D direction signals"),
        ] = False,
        log_level: Annotated[str | None, typer.Option("--log-level")] = None,
    ) -> None:
        """Paper scan loop: knobs from default.yaml ← user.yaml; CLI overrides. No live CLOB."""
        from chancetime.crypto_updown.bot import CryptoUpDownBot
        from chancetime.crypto_updown.strategies import TweetStrategyConfig
        from chancetime.utils.config import load_config

        cfg = load_config()
        c = cfg.crypto_updown
        setup_logging(log_level or cfg.logging.level)

        size_usd = float(size if size is not None else c.size_usd)
        edge = float(strategy_edge if strategy_edge is not None else c.min_edge)
        strat_cfg = TweetStrategyConfig(
            min_edge=edge,
            size_usd=size_usd,
            complete_set_max_sum=c.complete_set_max_sum,
            complete_set_size_usd=float(
                size if size is not None else c.complete_set_size_usd
            ),
            max_spread=c.max_spread,
            snipe_seconds=c.snipe_seconds,
            snipe_min_p=c.snipe_min_p,
            snipe_size_usd=float(size if size is not None else c.snipe_size_usd),
            max_usd_per_market_side=c.max_usd_per_market_side,
            signal_edge_threshold=c.signal_edge_threshold,
        )
        bot = CryptoUpDownBot(
            poll_interval=float(interval if interval is not None else c.poll_interval_seconds),
            max_markets=int(limit if limit is not None else c.max_markets),
            bbo_limit=int(bbo_limit if bbo_limit is not None else c.bbo_limit),
            db_path=c.db_path,
            cash=c.starting_cash,
            paper_trade_complete_set=bool(
                complete_set if complete_set is not None else c.paper_complete_set
            ),
            complete_set_size_usd=strat_cfg.complete_set_size_usd,
            paper_trade_direction=bool(
                direction if direction is not None else c.paper_direction
            ),
            direction_size_usd=float(
                direction_size if direction_size is not None else size_usd
            ),
            paper_strategy=bool(
                paper_strategy if paper_strategy is not None else c.paper_strategy
            ),
            strategy_config=strat_cfg,
            publish_direction_signals=(
                False if no_signals else bool(c.publish_signals)
            ),
            fee_bps=c.fee_bps,
            use_ws=bool(use_ws if use_ws is not None else c.use_ws),
            max_daily_loss_usd=c.max_daily_loss_usd,
            max_spot_age_sec=c.max_spot_age_sec,
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
