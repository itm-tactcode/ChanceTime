"""Venue search and live smoke / ping commands."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from chancetime.cli.common import load_app_config as _load
from chancetime.execution import KalshiLiveClient, PolymarketUSLiveClient
from chancetime.persistence import StateStore
from chancetime.utils.logging import setup_logging


def register(app: typer.Typer) -> None:
    @app.command("markets")
    def markets_cmd(
        query: Annotated[
            str,
            typer.Argument(help="Search text, e.g. 'france world cup' or 'fed rate'"),
        ],
        venue: Annotated[
            str,
            typer.Option("--venue", "-v", help="polymarket | kalshi | both"),
        ] = "polymarket",
        limit: Annotated[int, typer.Option("--limit", "-n")] = 15,
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    ) -> None:
        """Search Polymarket US / Kalshi for market slugs and tickers (no orders).

        Polymarket US has no public web market URLs like polymarket.com.
        Use this to get --pm-slug / --kalshi-ticker for live-smoke.

        Note: https://polymarket.com/... is *international* (different product).
        Chance Time trades Polymarket *US* only (api/gateway.polymarket.us).
        """
        from chancetime.data_layer.kalshi import KalshiClient
        from chancetime.data_layer.polymarket_us import PolymarketUSClient

        cfg = _load(config)
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)
        q = query.strip()
        if not q:
            typer.echo("Pass a search query.", err=True)
            raise typer.Exit(1)
        venues = []
        if venue in {"polymarket", "pm", "both"}:
            venues.append("polymarket")
        if venue in {"kalshi", "both"}:
            venues.append("kalshi")

        async def _go() -> None:
            if "polymarket" in venues:
                p = PolymarketUSClient(
                    api_key_id=cfg.polymarket_api_key,
                    private_key_path=(
                        str(cfg.polymarket_private_key_path)
                        if cfg.polymarket_private_key_path
                        else None
                    ),
                    enrich_bbo=False,
                )
                try:
                    found = await p.search_markets(q, limit=limit)
                    typer.echo(f"=== Polymarket US search: {q!r} ({len(found)} markets) ===")
                    typer.echo("Use --pm-slug <slug> with live-smoke. (Not polymarket.com event URLs.)")
                    if not found:
                        typer.echo("  (no hits — try shorter terms: 'france', 'nba', 'fed')")
                    for m in found:
                        slug = m.slug or m.id
                        typer.echo(
                            f"  slug={slug}\n"
                            f"    mid={m.yes_price:.3f} liq~{m.liquidity_usd:.0f} | {m.title[:90]}"
                        )
                        typer.echo(
                            f"    smoke: uv run chancetime live-smoke --venue polymarket "
                            f"--pm-slug {slug} --size 5 --price {m.yes_price:.2f} --dry-run"
                        )
                finally:
                    await p.close()

            if "kalshi" in venues:
                k = KalshiClient(
                    api_key_id=cfg.kalshi_api_key,
                    private_key_path=(
                        str(cfg.kalshi_private_key_path) if cfg.kalshi_private_key_path else None
                    ),
                    env=cfg.kalshi_env,
                )
                try:
                    found_k = await k.search_markets(q, limit=limit)
                    typer.echo(
                        f"=== Kalshi ({cfg.kalshi_env}) search: {q!r} ({len(found_k)} markets) ==="
                    )
                    typer.echo("Use --kalshi-ticker <ticker> with live-smoke.")
                    if not found_k:
                        typer.echo(
                            "  (no hits — try a series ticker like KXMENWORLDCUP, "
                            "or paste a full ticker)"
                        )
                    for m in found_k:
                        typer.echo(
                            f"  ticker={m.id}\n"
                            f"    mid={m.yes_price:.3f} liq~{m.liquidity_usd:.0f} | {m.title[:90]}"
                        )
                        typer.echo(
                            f"    smoke: uv run chancetime live-smoke --venue kalshi "
                            f"--kalshi-ticker {m.id} --size 5 --price {m.yes_price:.2f} --dry-run"
                        )
                finally:
                    await k.close()

        asyncio.run(_go())



    @app.command("live-ping")
    def live_ping(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    ) -> None:
        """Check Kalshi + Polymarket US auth + balances (no orders)."""
        cfg = _load(config)
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)

        async def _go() -> None:
            if not cfg.kalshi_credentials_configured:
                typer.echo("Kalshi: keys not configured")
            else:
                k = KalshiLiveClient(
                    api_key_id=str(cfg.kalshi_api_key),
                    private_key_path=cfg.kalshi_private_key_path,  # type: ignore[arg-type]
                    env=cfg.kalshi_env,
                )
                try:
                    bal = await k.get_balance_usd()
                    typer.echo(f"Kalshi ({cfg.kalshi_env}): balance_usd={bal}")
                finally:
                    await k.close()

            if not cfg.polymarket_credentials_configured:
                typer.echo("Polymarket US: keys not configured")
            else:
                p = PolymarketUSLiveClient(
                    api_key_id=str(cfg.polymarket_api_key),
                    private_key_path=cfg.polymarket_private_key_path,  # type: ignore[arg-type]
                )
                try:
                    bal = await p.get_balance_usd()
                    typer.echo(f"Polymarket US: balance_usd={bal}")
                finally:
                    await p.close()

        asyncio.run(_go())



    @app.command("live-smoke")
    def live_smoke(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        venue: Annotated[
            str,
            typer.Option("--venue", help="kalshi | polymarket | both"),
        ] = "both",
        size: Annotated[float, typer.Option("--size", help="USD notional per order")] = 5.0,
        kalshi_ticker: Annotated[
            str | None,
            typer.Option("--kalshi-ticker", help="Kalshi market ticker (required for kalshi)"),
        ] = None,
        pm_slug: Annotated[
            str | None,
            typer.Option(
                "--pm-slug",
                help="Polymarket US market slug from: chancetime markets 'query' (not polymarket.com)",
            ),
        ] = None,
        side: Annotated[str, typer.Option("--side", help="yes | no")] = "yes",
        price: Annotated[
            float | None,
            typer.Option("--price", help="Limit price 0-1 (default: mid-ish 0.50)"),
        ] = None,
        i_understand: Annotated[
            bool,
            typer.Option(
                "--i-understand-this-spends-real-money",
                help="Required — places REAL orders",
            ),
        ] = False,
        dry_run: Annotated[
            bool,
            typer.Option("--dry-run", help="Build payload only; do not send"),
        ] = False,
    ) -> None:
        """Place one tiny IOC/limit order per venue (Phase 6 smoke). REAL MONEY."""
        from chancetime.strategies.base import Side

        if not i_understand and not dry_run:
            typer.echo(
                "REFUSING. Pass --i-understand-this-spends-real-money or --dry-run",
                err=True,
            )
            raise typer.Exit(2)

        cfg = _load(config)
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)
        size = min(size, cfg.execution.max_live_order_usd, 10.0)
        px = price if price is not None else 0.50
        trade_side = Side.YES if side.lower() == "yes" else Side.NO
        venues = []
        if venue in {"kalshi", "both"}:
            venues.append("kalshi")
        if venue in {"polymarket", "both", "pm"}:
            venues.append("polymarket")

        async def _go() -> None:
            from chancetime.persistence.live_book import persist_live_result

            store = StateStore(cfg.persistence.db_path, enabled=cfg.persistence.enabled)
            try:
                if "kalshi" in venues:
                    if not cfg.kalshi_credentials_configured:
                        typer.echo("Kalshi skip: credentials missing")
                    elif not kalshi_ticker:
                        typer.echo("Kalshi skip: pass --kalshi-ticker TICKER")
                    elif dry_run:
                        typer.echo(
                            f"DRY kalshi ticker={kalshi_ticker} side={trade_side} "
                            f"size=${size:.2f} price={px:.3f}"
                        )
                    else:
                        k_client = KalshiLiveClient(
                            api_key_id=str(cfg.kalshi_api_key),
                            private_key_path=cfg.kalshi_private_key_path,  # type: ignore[arg-type]
                            env=cfg.kalshi_env,
                        )
                        try:
                            bal = await k_client.get_balance_usd()
                            typer.echo(f"Kalshi balance_usd={bal}")
                            res = await k_client.place_order(
                                ticker=kalshi_ticker,
                                side=trade_side,
                                size_usd=size,
                                limit_price=px,
                            )
                            typer.echo(
                                f"Kalshi order ok={res.ok} id={res.order_id} "
                                f"status={res.status} note={res.note[:200]}"
                            )
                            if res.ok:
                                stored = persist_live_result(
                                    store,
                                    res,
                                    market_id=kalshi_ticker,
                                    platform="kalshi",
                                    side=trade_side,
                                )
                                if stored is not None:
                                    typer.echo(
                                        f"  → Stored in dashboard DB ({cfg.persistence.db_path})"
                                    )
                            if res.ok and "no fill" in res.note.lower():
                                typer.echo("  → Accepted but no fill (IOC). Try --price at/above ask.")
                        finally:
                            await k_client.close()

                if "polymarket" in venues:
                    if not cfg.polymarket_credentials_configured:
                        typer.echo("Polymarket skip: credentials missing")
                    elif not pm_slug:
                        typer.echo("Polymarket skip: pass --pm-slug SLUG")
                    elif dry_run:
                        typer.echo(
                            f"DRY polymarket slug={pm_slug} side={trade_side} "
                            f"size=${size:.2f} price={px:.3f}"
                        )
                    else:
                        p_client = PolymarketUSLiveClient(
                            api_key_id=str(cfg.polymarket_api_key),
                            private_key_path=cfg.polymarket_private_key_path,  # type: ignore[arg-type]
                        )
                        try:
                            bal = await p_client.get_balance_usd()
                            typer.echo(f"Polymarket balance_usd={bal}")
                            res = await p_client.place_order(
                                market_slug=pm_slug,
                                side=trade_side,
                                size_usd=size,
                                limit_price=px,
                            )
                            typer.echo(
                                f"Polymarket order ok={res.ok} id={res.order_id} "
                                f"status={res.status} note={res.note[:220]}"
                            )
                            if res.ok:
                                stored = persist_live_result(
                                    store,
                                    res,
                                    market_id=pm_slug,
                                    platform="polymarket",
                                    side=trade_side,
                                )
                                if stored is not None:
                                    typer.echo(
                                        f"  → Stored in dashboard DB ({cfg.persistence.db_path})"
                                    )
                            if res.ok and res.status == "filled":
                                typer.echo("  → Filled. Refresh dashboard: uv run chancetime dashboard")
                            elif res.ok and res.status in {"canceled_unfilled", "submitted"}:
                                typer.echo("  → No fill / IOC canceled. Try a more aggressive --price.")
                        finally:
                            await p_client.close()
            finally:
                store.close()

        asyncio.run(_go())



