"""Portfolio books, export, digest, dashboard, strategies."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Annotated

import typer

from chancetime.cli.common import load_app_config as _load
from chancetime.execution import KalshiLiveClient, PolymarketUSLiveClient
from chancetime.flair import DISPLAY_NAME
from chancetime.persistence import StateStore
from chancetime.strategies import (
    strategy_weights_from_config,
)
from chancetime.utils.logging import setup_logging


def register(app: typer.Typer) -> None:
    @app.command()
    def status(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    ) -> None:
        """Print portfolio / DB summary (no trading)."""
        import json

        cfg = _load(config)
        store = StateStore(cfg.persistence.db_path, enabled=cfg.persistence.enabled)
        try:
            summary = store.summary()
            typer.echo(json.dumps(summary, indent=2, default=str))
            if summary.get("open_positions"):
                typer.echo("--- positions ---")
                for p in store.list_positions():
                    typer.echo(
                        f"  {p['platform']}:{p['market_id']} {p['side']} "
                        f"${p['size_usd']:.2f} @ {p['entry_price']:.3f} [{p['strategy']}]"
                    )
        finally:
            store.close()



    @app.command("sync-positions")
    def sync_positions(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        account: Annotated[
            str | None,
            typer.Option("--account", "-a", help="Named book for SQLite target"),
        ] = None,
        venue: Annotated[str, typer.Option("--venue", help="kalshi|polymarket|both")] = "both",
    ) -> None:
        """Pull live positions from venues into local SQLite (dashboard book)."""
        from chancetime.persistence.sync import apply_venue_positions

        cfg = _load(config, account=account or "live")
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)
        store = StateStore(cfg.persistence.db_path, enabled=cfg.persistence.enabled)
        rows: list[dict[str, object]] = []

        async def _go() -> None:
            nonlocal rows
            if venue in {"kalshi", "both"} and cfg.kalshi_credentials_configured:
                k = KalshiLiveClient(
                    api_key_id=str(cfg.kalshi_api_key),
                    private_key_path=cfg.kalshi_private_key_path,  # type: ignore[arg-type]
                    env=cfg.kalshi_env,
                )
                try:
                    k_rows = await k.list_positions()
                    rows.extend(k_rows)
                    typer.echo(f"Kalshi positions: {len(k_rows)}")
                finally:
                    await k.close()
            if venue in {"polymarket", "both", "pm"} and cfg.polymarket_credentials_configured:
                p = PolymarketUSLiveClient(
                    api_key_id=str(cfg.polymarket_api_key),
                    private_key_path=cfg.polymarket_private_key_path,  # type: ignore[arg-type]
                )
                try:
                    p_rows = await p.list_positions()
                    rows.extend(p_rows)
                    typer.echo(f"Polymarket positions: {len(p_rows)}")
                finally:
                    await p.close()

        asyncio.run(_go())
        platforms = {str(r.get("platform") or "") for r in rows}
        portfolio = apply_venue_positions(store, rows, replace_platforms=platforms)
        store.close()
        typer.echo(f"Local book open_positions={portfolio.open_count}")
        for mid, pos in portfolio.positions.items():
            typer.echo(f"  {pos.platform}:{mid} {pos.side} ${pos.size_usd:.2f} @ {pos.entry_price:.3f}")



    @app.command("cancel-order")
    def cancel_order(
        order_id: Annotated[str, typer.Argument(help="Venue order id")],
        venue: Annotated[str, typer.Option("--venue", help="kalshi | polymarket")],
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        i_understand: Annotated[
            bool,
            typer.Option("--i-understand-this-spends-real-money", help="Required"),
        ] = False,
    ) -> None:
        """Cancel a resting live order on one venue."""
        if not i_understand:
            typer.echo("Pass --i-understand-this-spends-real-money", err=True)
            raise typer.Exit(2)
        cfg = _load(config)
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)

        async def _go() -> None:
            v = venue.lower()
            if v == "kalshi":
                if not cfg.kalshi_credentials_configured:
                    typer.echo("Kalshi credentials missing", err=True)
                    raise typer.Exit(1)
                client = KalshiLiveClient(
                    api_key_id=str(cfg.kalshi_api_key),
                    private_key_path=cfg.kalshi_private_key_path,  # type: ignore[arg-type]
                    env=cfg.kalshi_env,
                )
                try:
                    ok, note = await client.cancel_order(order_id)
                    typer.echo(f"kalshi cancel ok={ok} {note}")
                finally:
                    await client.close()
            elif v in {"polymarket", "pm"}:
                if not cfg.polymarket_credentials_configured:
                    typer.echo("Polymarket credentials missing", err=True)
                    raise typer.Exit(1)
                client_p = PolymarketUSLiveClient(
                    api_key_id=str(cfg.polymarket_api_key),
                    private_key_path=cfg.polymarket_private_key_path,  # type: ignore[arg-type]
                )
                try:
                    ok, note = await client_p.cancel_order(order_id)
                    typer.echo(f"polymarket cancel ok={ok} {note}")
                finally:
                    await client_p.close()
            else:
                typer.echo("venue must be kalshi or polymarket", err=True)
                raise typer.Exit(1)

        asyncio.run(_go())



    @app.command("strategies")
    def strategies_cmd(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        stats: Annotated[
            bool,
            typer.Option("--stats", help="Show Phase 8 cumulative performance from SQLite"),
        ] = False,
    ) -> None:
        """List equipped strategies, enabled flags, and risk weights."""
        cfg = _load(config)
        weights = strategy_weights_from_config(cfg)
        rows = [
            ("simple_edge", cfg.strategies.simple_edge.enabled),
            ("llm_calibrated", cfg.strategies.llm_calibrated.enabled),
            ("arb_cross", cfg.strategies.arb_cross.enabled),
            ("mean_revert", cfg.strategies.mean_revert.enabled),
            ("news_impulse", cfg.strategies.news_impulse.enabled),
            ("ml_edge", cfg.strategies.ml_edge.enabled),
        ]
        typer.echo(f"{'strategy':18} {'on':4} weight")
        for name, en in rows:
            w = weights.get(name, 1.0)
            typer.echo(f"{name:18} {en!s:4} {w:.2f}")
        typer.echo(
            f"\npaper_mode={cfg.paper_mode} data={cfg.data.source} "
            f"live_enabled={cfg.execution.live_enabled}"
        )
        if stats:
            store = StateStore(cfg.persistence.db_path, enabled=cfg.persistence.enabled)
            try:
                st = store.list_strategy_stats()
                typer.echo("\n--- strategy performance (cumulative) ---")
                if not st:
                    typer.echo("(empty — run paper polls first)")
                else:
                    typer.echo(
                        f"{'strategy':16} {'sigs':>6} {'fills':>6} "
                        f"{'notional':>10} {'closed':>6} {'pnl':>10}"
                    )
                    for r in st:
                        typer.echo(
                            f"{r['strategy']!s:16} {int(r['signals']):6d} "
                            f"{int(r['fills']):6d} {float(r['fill_notional_usd']):10.2f} "
                            f"{int(r['closed_trades']):6d} {float(r['realized_pnl']):10.4f}"
                        )
            finally:
                store.close()



    @app.command("train-ml")
    def train_ml_cmd(
        fixture: Annotated[
            str,
            typer.Option("--fixture", "-f", help="CSV with resolve labels"),
        ] = "backtests/fixtures/sample_series.csv",
        out: Annotated[
            str,
            typer.Option("--out", "-o", help="joblib output path"),
        ] = "models/ml_edge.joblib",
    ) -> None:
        """Train ml_edge logistic model offline (requires: uv sync --extra ml)."""
        from chancetime.ml.train import train_ml_edge_from_csv

        try:
            result = train_ml_edge_from_csv(fixture, out_path=out)
        except ImportError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        except (ValueError, FileNotFoundError) as exc:
            typer.echo(f"train-ml failed: {exc}", err=True)
            raise typer.Exit(1) from exc
        wf = (
            f" walk_forward_acc={result.walk_forward_accuracy:.3f}"
            if result.walk_forward_accuracy is not None
            else ""
        )
        typer.echo(
            f"wrote {result.model_path}\n"
            f"samples={result.n_samples} markets={result.n_markets} "
            f"train_acc={result.train_accuracy:.3f}{wf}\n"
            f"note: {result.note}"
        )
        typer.echo("Enable strategies.ml_edge.enabled: true to equip in the bot.")



    @app.command("export")
    def export_cmd(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        account: Annotated[
            str | None,
            typer.Option("--account", "-a", help="Named book (isolates DB)"),
        ] = None,
        year: Annotated[
            int | None,
            typer.Option("--year", help="Filter fills/closed to calendar year (UTC)"),
        ] = None,
        fills: Annotated[
            str | None,
            typer.Option("--fills", help="CSV path for fills"),
        ] = None,
        closed: Annotated[
            str | None,
            typer.Option("--closed", help="CSV path for closed trades"),
        ] = None,
        summary: Annotated[
            str | None,
            typer.Option("--summary", help="CSV path for one-row summary"),
        ] = None,
        out_dir: Annotated[
            str,
            typer.Option("--out-dir", help="Default export directory"),
        ] = "data/exports",
    ) -> None:
        """Export fills + closed trades to CSV (tax/bookkeeping aid — not tax advice)."""
        from chancetime.persistence.export import (
            export_closed_csv,
            export_fills_csv,
            export_summary_csv,
        )

        cfg = _load(config, account=account)
        book = account or "default"
        ytag = f"-{year}" if year else ""
        fills_path = fills or f"{out_dir}/{book}{ytag}-fills.csv"
        closed_path = closed or f"{out_dir}/{book}{ytag}-closed.csv"
        summary_path = summary or f"{out_dir}/{book}{ytag}-summary.csv"
        store = StateStore(cfg.persistence.db_path, enabled=cfg.persistence.enabled)
        try:
            p1 = export_fills_csv(store, fills_path, book=book, year=year)
            p2 = export_closed_csv(store, closed_path, book=book, year=year)
            p3 = export_summary_csv(store, summary_path, book=book, year=year)
            typer.echo(f"fills → {p1}")
            typer.echo(f"closed → {p2}")
            typer.echo(f"summary → {p3}")
            typer.echo("Not tax advice — verify with a professional.")
        finally:
            store.close()



    @app.command("accounts")
    def accounts_cmd() -> None:
        """List named books (config/accounts.yaml or built-in defaults)."""
        from chancetime.utils.accounts import list_accounts_summary

        rows = list_accounts_summary()
        for r in rows:
            flag = "paper" if r["paper_mode"] else "LIVE"
            exists = "yes" if r["db_exists"] else "no"
            typer.echo(
                f"{r['name']:12} [{flag:5}] db_exists={exists:3}  "
                f"{r['label']}  → {r['db_path']}"
            )



    @app.command("digest")
    def digest_cmd(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        account: Annotated[
            str | None,
            typer.Option("--account", "-a", help="Named book (default: paper)"),
        ] = "paper",
        send: Annotated[
            bool,
            typer.Option("--send", help="Also send via Telegram if configured"),
        ] = False,
        write: Annotated[
            bool,
            typer.Option("--write/--no-write", help="Write data/digests/*.txt"),
        ] = True,
        json_out: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Daily P&L / fills digest for a book (optional Telegram)."""
        import asyncio
        import json

        from chancetime.monitoring.digest import (
            build_digest,
            digest_to_dict,
            send_digest,
            write_digest_file,
        )

        name = account or "paper"
        cfg = _load(config, account=name)
        store = StateStore(cfg.persistence.db_path, enabled=True)
        try:
            report = build_digest(store, account=name)
            if write:
                path = write_digest_file(report)
                typer.echo(f"wrote {path}")
            if json_out:
                typer.echo(json.dumps(digest_to_dict(report), indent=2, default=str))
            else:
                typer.echo(report.text)
            if send:
                sent = asyncio.run(
                    send_digest(
                        report,
                        telegram_bot_token=cfg.telegram_bot_token,
                        telegram_chat_id=cfg.telegram_chat_id,
                    )
                )
                typer.echo("telegram: sent" if sent else "telegram: not configured (log only)")
        finally:
            store.close()



    @app.command()
    def dashboard(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        host: Annotated[str | None, typer.Option("--host")] = None,
        port: Annotated[int | None, typer.Option("--port")] = None,
        db: Annotated[
            str | None,
            typer.Option(
                "--db",
                help="Legacy single-DB mode (both books point here). Prefer paper/live paths.",
            ),
        ] = None,
        paper_db: Annotated[
            str | None,
            typer.Option("--paper-db", help="Paper book SQLite path"),
        ] = None,
        live_db: Annotated[
            str | None,
            typer.Option("--live-db", help="Live book SQLite path"),
        ] = None,
        allow_remote: Annotated[
            bool,
            typer.Option(
                "--allow-remote",
                help="Permit bind on non-loopback hosts (exposes book APIs on the network)",
            ),
        ] = False,
    ) -> None:
        """Run read-only local dashboard (FastAPI). Requires: uv sync --extra dashboard.

        Default bind is loopback only. Use --allow-remote if you intentionally bind
        0.0.0.0 / a LAN interface (still no auth — do not expose to the public internet).
        """
        cfg = _load(config)
        bind_host = host or cfg.dashboard.host
        bind_port = port or cfg.dashboard.port
        loopback = {"127.0.0.1", "localhost", "::1"}
        if bind_host not in loopback and not allow_remote:
            typer.echo(
                f"Refusing non-loopback dashboard bind {bind_host!r}. "
                "Use --allow-remote only on trusted networks (no auth on this API).",
                err=True,
            )
            raise typer.Exit(2)
        try:
            import uvicorn
        except ImportError:
            typer.echo(
                "Missing dashboard deps. Install with:\n"
                "  uv sync --extra dashboard\n"
                "Then: uv run chancetime dashboard",
                err=True,
            )
            raise typer.Exit(1) from None
        from chancetime.dashboard.app import create_app

        if db:
            app_asgi = create_app(db_path=db)
            typer.echo(
                f"{DISPLAY_NAME} dashboard http://{bind_host}:{bind_port}  "
                f"(legacy single db={db})"
            )
        else:
            pdb = paper_db or cfg.dashboard.paper_db_path
            ldb = live_db or cfg.dashboard.live_db_path
            app_asgi = create_app(paper_db=pdb, live_db=ldb)
            typer.echo(
                f"{DISPLAY_NAME} dashboard http://{bind_host}:{bind_port}  "
                f"paper={pdb} live={ldb}"
            )
        if allow_remote and bind_host not in loopback:
            typer.echo(
                "WARNING: dashboard bound beyond loopback with no authentication.",
                err=True,
            )
        uvicorn.run(app_asgi, host=bind_host, port=bind_port, log_level="info")



    @app.command("migrate-books")
    def migrate_books(
        source: Annotated[
            str,
            typer.Option(
                "--source",
                help="Legacy combined DB (default data/chancetime.db if present)",
            ),
        ] = "data/chancetime.db",
        paper_db: Annotated[str, typer.Option("--paper-db")] = "data/paper.db",
        live_db: Annotated[str, typer.Option("--live-db")] = "data/live.db",
        force: Annotated[
            bool,
            typer.Option("--force", help="Overwrite existing paper/live targets"),
        ] = False,
    ) -> None:
        """Split legacy data/chancetime.db into paper.db + live.db.

        - Fills/equity: ``paper=0`` → live, ``paper=1`` → paper
        - Positions: strategies ``venue_sync`` / ``live_smoke`` / ``live_*`` → live;
          everything else → paper (open positions have no paper column historically)
        """
        import shutil
        import sqlite3

        from chancetime.utils.paths import resolve_path

        src = resolve_path(source)
        paper_path = resolve_path(paper_db)
        live_path = resolve_path(live_db)
        paper_path.parent.mkdir(parents=True, exist_ok=True)

        live_strat_sql = (
            "strategy IN ('venue_sync', 'live_smoke') OR strategy LIKE 'live_%'"
        )

        def _copy_rows(
            src_conn: sqlite3.Connection,
            dst_conn: sqlite3.Connection,
            table: str,
            where: str,
        ) -> int:
            src_conn.row_factory = sqlite3.Row
            rows = src_conn.execute(f"SELECT * FROM {table} WHERE {where}").fetchall()
            if not rows:
                return 0
            for row in rows:
                cols = [c for c in row.keys() if not (table != "positions" and c == "id")]
                # For positions PK is market_id; keep all cols including no auto-id
                if table == "positions":
                    cols = list(row.keys())
                placeholders = ",".join("?" * len(cols))
                col_list = ",".join(cols)
                dst_conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
            return len(rows)

        if not src.is_file():
            from chancetime.persistence.store import StateStore

            if not paper_path.is_file() or force:
                StateStore(paper_path, enabled=True).close()
                typer.echo(f"created empty paper book {paper_path}")
            if not live_path.is_file() or force:
                StateStore(live_path, enabled=True).close()
                typer.echo(f"created empty live book {live_path}")
            typer.echo(f"(no source {src}; done)")
            return

        from chancetime.persistence.store import StateStore

        # --- paper book ---
        if paper_path.is_file() and not force:
            typer.echo(f"skip paper (exists): {paper_path}  (use --force to overwrite)")
        else:
            shutil.copy2(src, paper_path)
            pconn = sqlite3.connect(str(paper_path))
            try:
                pconn.execute("DELETE FROM fills WHERE paper = 0")
                with contextlib.suppress(sqlite3.OperationalError):
                    pconn.execute("DELETE FROM equity_snapshots WHERE paper = 0")
                # Live-looking open positions off paper book
                cur = pconn.execute(f"DELETE FROM positions WHERE {live_strat_sql}")
                removed_pos = cur.rowcount
                pconn.commit()
                typer.echo(
                    f"paper book ← {src} (removed live fills/equity + {removed_pos} live-ish positions)"
                )
            finally:
                pconn.close()

        # --- live book ---
        if live_path.is_file() and not force:
            typer.echo(f"skip live (exists): {live_path}")
        else:
            if live_path.is_file():
                live_path.unlink()
            StateStore(live_path, enabled=True).close()
            src_conn = sqlite3.connect(str(src))
            dst_conn = sqlite3.connect(str(live_path))
            try:
                n_fills = _copy_rows(src_conn, dst_conn, "fills", "paper = 0")
                try:
                    n_eq = _copy_rows(src_conn, dst_conn, "equity_snapshots", "paper = 0")
                except sqlite3.OperationalError:
                    n_eq = 0
                n_pos = _copy_rows(src_conn, dst_conn, "positions", live_strat_sql)
                dst_conn.commit()
                typer.echo(
                    f"live book {live_path}: {n_fills} fills, {n_eq} equity, {n_pos} positions"
                )
            finally:
                src_conn.close()
                dst_conn.close()

        typer.echo(
            "Done. Kill/restart the desktop API server so it loads paper.db + live.db "
            "(not the old chancetime.db). Monitor has PAPER | LIVE toggle in the page header."
        )



    @app.command("clear-book")
    def clear_book_cmd(
        account: Annotated[str, typer.Option("--account", "-a")] = "paper",
        yes: Annotated[
            bool,
            typer.Option("--yes", help="Confirm delete of this account's SQLite file"),
        ] = False,
    ) -> None:
        """Delete an account's SQLite book (positions/fills). Opt-in clean slate."""
        from chancetime.utils.accounts import get_account
        from chancetime.utils.paths import resolve_path

        if not yes:
            typer.echo("Refusing without --yes (this deletes the book file).", err=True)
            raise typer.Exit(2)
        acct = get_account(account)
        db = resolve_path(acct.db_path)
        removed = 0
        for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
            if p.is_file():
                p.unlink()
                removed += 1
                typer.echo(f"removed {p}")
        if removed == 0:
            typer.echo(f"no files for {db}")
        else:
            typer.echo(f"cleared account={account} ({removed} file(s)). Restart bot.")



    @app.command("positions")
    def positions_cmd(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    ) -> None:
        """Show that portfolio is session-scoped (empty until bot run)."""
        typer.echo(
            "Positions live in the running bot process (in-memory Portfolio). "
            "Start with: uv run chancetime run --once"
        )


    if __name__ == "__main__":
        app()

