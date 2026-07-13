"""Backtest, history, walk-forward, scorecard, arb scan."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from chancetime.bot import Bot
from chancetime.cli.common import load_app_config as _load
from chancetime.data_layer import build_data_client
from chancetime.persistence import StateStore
from chancetime.utils.logging import setup_logging


def register(app: typer.Typer) -> None:
    @app.command()
    def backtest(
        fixture: Annotated[
            str | None,
            typer.Option("--fixture", "-f", help="CSV market series path"),
        ] = "backtests/fixtures/sample_series.csv",
        history: Annotated[
            str | None,
            typer.Option(
                "--history",
                help="Replay JSONL from record-history (overrides --fixture)",
            ),
        ] = None,
        edge_threshold: Annotated[
            float,
            typer.Option("--edge", help="SimpleEdge edge_threshold (ignored if --grid)"),
        ] = 0.08,
        grid: Annotated[
            bool,
            typer.Option("--grid", help="Sweep edge_threshold 0.05/0.08/0.12"),
        ] = False,
        cash: Annotated[float, typer.Option("--cash", help="Starting cash USD")] = 1000.0,
        size: Annotated[float, typer.Option("--size", help="Order size USD")] = 10.0,
        venue: Annotated[
            str | None,
            typer.Option(
                "--venue",
                help="Use venue fee schedule (kalshi|polymarket|default); overrides --fee-bps",
            ),
        ] = None,
        fee_bps: Annotated[float, typer.Option("--fee-bps")] = 100.0,
        slip_bps: Annotated[float, typer.Option("--slip-bps")] = 50.0,
        prior: Annotated[
            str,
            typer.Option("--prior", help="static | trailing_mean"),
        ] = "static",
    ) -> None:
        """Run a paper backtest on a CSV fixture (Phase 1 / 10)."""
        from chancetime.backtesting import (
            BacktestEngine,
            CostModel,
            load_bars_csv,
            run_param_grid,
        )
        from chancetime.backtesting.fees import cost_model_for_venue
        from chancetime.strategies.simple_edge import SimpleEdgeStrategy

        setup_logging("INFO", json_logs=False)
        if history:
            from chancetime.data_layer.history import load_bars_from_history

            bars = load_bars_from_history(history)
            typer.echo(f"history_replay bars={len(bars)} file={history}")
            if not bars:
                typer.echo("No bars in history file", err=True)
                raise typer.Exit(1)
        else:
            bars = load_bars_csv(fixture or "backtests/fixtures/sample_series.csv")
        if venue:
            costs = cost_model_for_venue(venue, slippage_bps=slip_bps)
            typer.echo(f"venue={venue} fee_bps={costs.fee_bps} slip_bps={costs.slippage_bps}")
        else:
            costs = CostModel(fee_bps=fee_bps, slippage_bps=slip_bps)

        async def _go() -> None:
            if grid:
                results = await run_param_grid(
                    bars,
                    edge_thresholds=[0.05, 0.08, 0.12],
                    starting_cash=cash,
                    order_size_usd=size,
                    costs=costs,
                )
                for res in results:
                    typer.echo("---")
                    for line in res.summary_lines():
                        typer.echo(line)
                best = max(results, key=lambda r: r.realized_pnl)
                typer.echo("---")
                typer.echo(
                    f"best_by_pnl: edge={best.params.get('edge_threshold')} "
                    f"pnl=${best.realized_pnl:.2f}"
                )
            else:
                strat = SimpleEdgeStrategy(
                    edge_threshold=edge_threshold,
                    min_liquidity_usd=100.0,
                    prior_mode=prior,
                )
                engine = BacktestEngine(
                    starting_cash=cash,
                    order_size_usd=size,
                    costs=costs,
                )
                res = await engine.run(
                    bars,
                    strat,
                    params={"edge_threshold": edge_threshold, "prior_mode": prior},
                )
                for line in res.summary_lines():
                    typer.echo(line)
                typer.echo(f"fills={len(res.fills)} settlements={len(res.settlements)}")

        asyncio.run(_go())



    @app.command("record-history")
    def record_history(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        source: Annotated[
            str | None,
            typer.Option("--source", help="Override data.source"),
        ] = None,
        out: Annotated[
            str | None,
            typer.Option("--out", help="JSONL path (default data/history/markets-YYYYMMDD.jsonl)"),
        ] = None,
        limit: Annotated[int | None, typer.Option("--limit")] = None,
    ) -> None:
        """One-shot fetch markets (+BBO fields when present) and append JSONL history."""
        from pathlib import Path

        from chancetime.data_layer.history import MarketHistoryRecorder

        cfg = _load(config)
        if source:
            cfg.data.source = source
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)
        rec = MarketHistoryRecorder.from_config(
            enabled=True,
            directory=cfg.history.directory if not out else str(Path(out).parent),
            filename=Path(out).name if out else (cfg.history.filename or None),
        )
        if out:
            rec.path = Path(out)
            rec.path.parent.mkdir(parents=True, exist_ok=True)

        async def _go() -> None:
            client = build_data_client(
                cfg.data.source,
                kalshi_api_key=cfg.kalshi_api_key,
                kalshi_private_key_path=(
                    str(cfg.kalshi_private_key_path) if cfg.kalshi_private_key_path else None
                ),
                polymarket_api_key=cfg.polymarket_api_key,
                polymarket_private_key_path=(
                    str(cfg.polymarket_private_key_path)
                    if cfg.polymarket_private_key_path
                    else None
                ),
                kalshi_env=cfg.kalshi_env,
            )
            try:
                markets = await client.list_markets(limit=limit or cfg.data.max_markets)
                n = rec.record_markets(markets, source=cfg.data.source, poll=0)
                typer.echo(f"recorded {n} markets → {rec.path}")
            finally:
                await client.close()

        asyncio.run(_go())



    @app.command("history-to-csv")
    def history_to_csv(
        history: Annotated[str, typer.Option("--history", "-i", help="JSONL history file")],
        out: Annotated[
            str,
            typer.Option("--out", "-o", help="Output CSV for backtest"),
        ] = "backtests/fixtures/from_history.csv",
    ) -> None:
        """Convert recorded JSONL history into a backtest CSV (with BBO columns)."""
        from chancetime.data_layer.history import history_to_bars_csv

        path = history_to_bars_csv(history, out)
        typer.echo(f"wrote {path}")



    @app.command("list-history")
    def list_history(
        directory: Annotated[
            str | None,
            typer.Option("--dir", help="History directory (default data/history)"),
        ] = None,
    ) -> None:
        """List recorded markets-*.jsonl files and row counts."""
        from chancetime.data_layer.history import list_history_files

        files = list_history_files(directory)
        if not files:
            typer.echo("(no history files)")
            return
        for p in files:
            try:
                n = sum(1 for line in p.open(encoding="utf-8") if line.strip())
            except OSError:
                n = -1
            typer.echo(f"{n:6d}  {p}")



    @app.command("walk-forward")
    def walk_forward_cmd(
        fixture: Annotated[
            str,
            typer.Option("--fixture", "-f"),
        ] = "backtests/fixtures/sample_series.csv",
        folds: Annotated[int, typer.Option("--folds")] = 3,
        cash: Annotated[float, typer.Option("--cash")] = 1000.0,
        size: Annotated[float, typer.Option("--size")] = 10.0,
        venue: Annotated[
            str | None,
            typer.Option(
                "--venue",
                help="Fee schedule (default: default costs ON). kalshi|polymarket|default",
            ),
        ] = "default",
        zero_cost: Annotated[
            bool,
            typer.Option("--zero-cost", help="Disable fees/slippage (research only)"),
        ] = False,
        json_out: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Walk-forward SimpleEdge: pick edge on train, score holdout folds.

        Phase 20: costs-on by default (fee + slippage). Use --zero-cost only for
        comparison experiments — live gate should use costed folds.
        """
        import json

        from chancetime.backtesting.fees import CostModel, cost_model_for_venue
        from chancetime.backtesting.loader import load_bars_csv
        from chancetime.backtesting.walk_forward import report_to_dict, walk_forward_simple_edge

        bars = load_bars_csv(fixture)
        setup_logging("WARNING", json_logs=False)
        costs = CostModel(fee_bps=0.0, slippage_bps=0.0) if zero_cost else None
        if costs is None and venue:
            costs = cost_model_for_venue(venue)

        async def _go() -> None:
            report = await walk_forward_simple_edge(
                bars,
                n_folds=folds,
                starting_cash=cash,
                order_size_usd=size,
                costs=costs,
                venue=None if costs is not None else venue,
            )
            if json_out:
                payload = report_to_dict(report)
                payload["costs_on"] = not zero_cost
                payload["venue"] = venue
                typer.echo(json.dumps(payload, indent=2, default=str))
            else:
                cost_note = "zero_cost" if zero_cost else f"costs_on venue={venue}"
                typer.echo(f"walk-forward ({cost_note})")
                for line in report.summary_lines():
                    typer.echo(line)

        asyncio.run(_go())



    @app.command("scorecard")
    def scorecard_cmd(
        account: Annotated[str, typer.Option("--account", "-a")] = "paper",
        fee_bps: Annotated[
            float,
            typer.Option("--fee-bps", help="Assumed fee bps on notional if fee_usd missing"),
        ] = 70.0,
        json_out: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Phase 20: per-strategy / family edge-after-cost scorecard (paper→live gate)."""
        import json

        from chancetime.monitoring.scorecard import build_edge_scorecard, scorecard_to_dict

        cfg = _load(None, account=account)
        store = StateStore(cfg.persistence.db_path, enabled=True)
        try:
            card = build_edge_scorecard(store, account=account, fee_bps=fee_bps)
            if json_out:
                typer.echo(json.dumps(scorecard_to_dict(card), indent=2, default=str))
            else:
                for line in card.summary_lines():
                    typer.echo(line)
        finally:
            store.close()



    @app.command("scan-arb")
    def scan_arb(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        source: Annotated[
            str | None,
            typer.Option("--source", help="Override data.source: mock|both|kalshi|polymarket"),
        ] = None,
        min_spread: Annotated[float | None, typer.Option("--min-spread")] = None,
        llm_match: Annotated[
            bool,
            typer.Option("--llm-match", help="Use Grok to propose dual listings (costs tokens)"),
        ] = False,
        debug: Annotated[
            bool,
            typer.Option("--debug", help="Print sample titles + top fuzzy scores"),
        ] = False,
        deep: Annotated[
            bool,
            typer.Option(
                "--deep",
                help="Deep discovery: cursor pages + Polymarket search queries (recommended)",
            ),
        ] = False,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Max markets per venue (default 80; deep default 250)"),
        ] = None,
        save_aliases: Annotated[
            bool,
            typer.Option("--save-aliases", help="Write high-score pairs to config/arb_aliases.json"),
        ] = False,
        query: Annotated[
            list[str] | None,
            typer.Option("--query", "-q", help="Extra search query (repeatable); deep mode"),
        ] = None,
        bbo: Annotated[
            bool,
            typer.Option(
                "--bbo",
                help="Refresh bid/ask only on pair legs (before trade intent)",
            ),
        ] = False,
        require_bbo: Annotated[
            bool,
            typer.Option("--require-bbo", help="Skip pairs without BBO on both legs"),
        ] = False,
    ) -> None:
        """One-shot Kalshi <-> Polymarket US pair scan + arb signals (no orders)."""
        from chancetime.data_layer.arb_discovery import (
            deep_discover,
            discovery_summary,
            load_aliases,
            pairs_to_aliases,
        )
        from chancetime.data_layer.arb_discovery import (
            save_aliases as persist_aliases,
        )
        from chancetime.data_layer.bbo import apply_bbo_to_market_list, enrich_pairs_bbo
        from chancetime.data_layer.kalshi import KalshiClient
        from chancetime.data_layer.matching import split_by_platform, title_similarity
        from chancetime.data_layer.models import Platform
        from chancetime.data_layer.polymarket_us import PolymarketUSClient
        from chancetime.strategies.arb_cross import ArbCrossStrategy

        cfg = _load(config)
        if source:
            cfg.data.source = source
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)
        bot = Bot(cfg)
        ac = cfg.strategies.arb_cross
        use_llm = llm_match or ac.use_llm_match
        file_aliases = load_aliases()
        merged_aliases = {**file_aliases, **dict(ac.aliases)}
        fetch_limit = limit or (250 if deep else max(cfg.data.max_markets, 80))
        do_bbo = bbo or require_bbo or ac.require_bbo

        async def _go() -> None:
            k_client: KalshiClient | None = None
            p_client: PolymarketUSClient | None = None
            if deep:
                k_client = KalshiClient(
                    api_key_id=cfg.kalshi_api_key,
                    private_key_path=(
                        str(cfg.kalshi_private_key_path) if cfg.kalshi_private_key_path else None
                    ),
                    env=cfg.kalshi_env,
                )
                p_client = PolymarketUSClient(
                    api_key_id=cfg.polymarket_api_key,
                    private_key_path=(
                        str(cfg.polymarket_private_key_path)
                        if cfg.polymarket_private_key_path
                        else None
                    ),
                    enrich_bbo=False,
                )
                try:
                    result = await deep_discover(
                        k_client,
                        p_client,
                        limit_per_venue=fetch_limit,
                        queries=query,
                        min_score=ac.min_match_score,
                        aliases=merged_aliases,
                        llm=bot.llm if use_llm else None,
                        use_llm_match=use_llm,
                        llm_match_min_confidence=ac.llm_match_min_confidence,
                        llm_match_max_each=ac.llm_match_max_each,
                        llm_match_band_low=getattr(ac, "llm_match_band_low", 0.40),
                        llm_bulk_fallback=getattr(ac, "llm_bulk_fallback", False),
                    )
                finally:
                    if not do_bbo:
                        await k_client.close()
                        await p_client.close()
                        k_client = None
                        p_client = None
                markets = [*result.kalshi, *result.polymarket]
                pairs = result.pairs
                typer.echo(
                    f"deep discovery kalshi={len(result.kalshi)} "
                    f"polymarket={len(result.polymarket)} pairs={len(pairs)} "
                    f"kalshi_env={cfg.kalshi_env}"
                )
                if debug:
                    typer.echo("--- sample Kalshi ---")
                    for m in result.kalshi[:8]:
                        typer.echo(f"  {m.id[:36]:36} | {m.title[:70]}")
                    typer.echo("--- sample Polymarket US ---")
                    for m in result.polymarket[:8]:
                        typer.echo(f"  {m.id[:36]:36} | {m.title[:70]}")
                    scores: list[tuple[float, str, str]] = []
                    for a in result.kalshi[:60]:
                        for b in result.polymarket[:60]:
                            scores.append((title_similarity(a.title, b.title), a.title, b.title))
                    scores.sort(reverse=True)
                    typer.echo("--- top fuzzy ---")
                    for score, at, bt in scores[:10]:
                        typer.echo(f"  {score:.3f} | K: {at[:40]} || P: {bt[:40]}")
                summary = discovery_summary(result)
                typer.echo(f"summary pairs={summary['pair_count']} alias_file={summary['alias_file']}")
            else:
                markets = await bot.data.list_markets(limit=fetch_limit)
                by_plat = split_by_platform(markets)
                kalshi = by_plat.get(Platform.KALSHI, [])
                pm = by_plat.get(Platform.POLYMARKET, [])
                typer.echo(
                    f"markets total={len(markets)} kalshi={len(kalshi)} "
                    f"polymarket={len(pm)} kalshi_env={cfg.kalshi_env}"
                )
                if debug:
                    typer.echo("--- sample Kalshi ---")
                    for m in kalshi[:8]:
                        typer.echo(f"  {m.id[:36]:36} | {m.title[:70]}")
                    typer.echo("--- sample Polymarket US ---")
                    for m in pm[:8]:
                        typer.echo(f"  {m.id[:36]:36} | {m.title[:70]}")
                    scores = []
                    for a in kalshi[:50]:
                        for b in pm[:50]:
                            scores.append((title_similarity(a.title, b.title), a.title, b.title))
                    scores.sort(reverse=True)
                    typer.echo("--- top fuzzy ---")
                    for score, at, bt in scores[:10]:
                        typer.echo(f"  {score:.3f} | K: {at[:40]} || P: {bt[:40]}")
                strat_tmp = ArbCrossStrategy(
                    llm=bot.llm if use_llm else None,
                    enabled=True,
                    min_match_score=ac.min_match_score,
                    use_llm_match=use_llm,
                    llm_match_min_confidence=ac.llm_match_min_confidence,
                    llm_match_max_each=ac.llm_match_max_each,
                    aliases=merged_aliases,
                )
                pairs = await strat_tmp._build_pairs(kalshi, pm)
                if do_bbo and (kalshi or pm):
                    k_client = KalshiClient(
                        api_key_id=cfg.kalshi_api_key,
                        private_key_path=(
                            str(cfg.kalshi_private_key_path) if cfg.kalshi_private_key_path else None
                        ),
                        env=cfg.kalshi_env,
                    )
                    p_client = PolymarketUSClient(
                        api_key_id=cfg.polymarket_api_key,
                        private_key_path=(
                            str(cfg.polymarket_private_key_path)
                            if cfg.polymarket_private_key_path
                            else None
                        ),
                        enrich_bbo=False,
                    )

            if do_bbo and pairs and (k_client is not None or p_client is not None):
                try:
                    pairs = await enrich_pairs_bbo(pairs, kalshi=k_client, polymarket=p_client)
                    markets = apply_bbo_to_market_list(markets, pairs)
                    typer.echo(f"bbo enriched pair legs (pairs={len(pairs)})")
                finally:
                    if k_client is not None:
                        await k_client.close()
                    if p_client is not None:
                        await p_client.close()
            elif k_client is not None or p_client is not None:
                if k_client is not None:
                    await k_client.close()
                if p_client is not None:
                    await p_client.close()

            thr = min_spread if min_spread is not None else ac.min_spread
            mid_thr = thr + ac.fee_buffer
            if not pairs:
                typer.echo(
                    "No pairs. Try --deep --limit 300, --llm-match, "
                    "--query 'world series', or arb aliases."
                )
            for p in pairs[:25]:
                flag = " **" if abs(p.yes_spread) >= mid_thr else ""
                bbo_flag = ""
                if p.left.has_bbo or p.right.has_bbo:
                    lb = f"{p.left.yes_bid:.3f}" if p.left.yes_bid is not None else "-"
                    la = f"{p.left.yes_ask:.3f}" if p.left.yes_ask is not None else "-"
                    rb = f"{p.right.yes_bid:.3f}" if p.right.yes_bid is not None else "-"
                    ra = f"{p.right.yes_ask:.3f}" if p.right.yes_ask is not None else "-"
                    bbo_flag = f" | bbo K {lb}/{la} P {rb}/{ra}"
                typer.echo(
                    f"pair score={p.score:.2f} mid_spread={p.yes_spread:+.3f}{flag} | "
                    f"K:{p.left.id}@{p.left.yes_price:.3f} || "
                    f"P:{p.right.id}@{p.right.yes_price:.3f} | "
                    f"{p.left.title[:32]} <-> {p.right.title[:32]}{bbo_flag}"
                )

            strat = ArbCrossStrategy(
                enabled=True,
                min_spread=thr,
                fee_buffer=ac.fee_buffer,
                min_match_score=ac.min_match_score,
                min_liquidity_usd=ac.min_liquidity_usd,
                emit_hedge_legs=ac.emit_hedge_legs,
                use_llm_match=False,
                require_bbo=require_bbo or ac.require_bbo,
                use_executable_prices=ac.use_executable_prices,
                size_by_depth=ac.size_by_depth,
                max_leg_usd=ac.max_leg_usd,
                max_pair_usd=ac.max_pair_usd,
                min_depth_usd=ac.min_depth_usd,
                aliases=merged_aliases,
            )
            # Prefer BBO-updated pairs when present
            if pairs:
                strat.last_pairs = pairs
            sigs = await strat.generate_signals(markets)
            typer.echo(
                f"arb_signals={len(sigs)} (exec edge thr={thr:.3f}+fee={ac.fee_buffer:.3f}; "
                f"** = mid above {mid_thr:.3f})"
            )
            for sig in sigs:
                typer.echo(
                    f"  {sig.side} {sig.platform}:{sig.market_id} "
                    f"edge={sig.edge:.3f} size=${sig.size_usd or 0:.2f} {sig.reason[:90]}"
                )
            if save_aliases and pairs:
                new_aliases = pairs_to_aliases(pairs, min_score=max(0.8, ac.min_match_score))
                path = persist_aliases(new_aliases)
                typer.echo(f"saved {len(new_aliases)} alias entries -> {path}")
            if use_llm:
                typer.echo(f"llm_spend={bot.llm.spend_summary()}")
            await bot.data.close()

        asyncio.run(_go())



