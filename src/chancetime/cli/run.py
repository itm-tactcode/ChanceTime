"""Run loop and version commands."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from pathlib import Path
from typing import Annotated

import typer

from chancetime import __version__
from chancetime.bot import Bot
from chancetime.cli.common import load_app_config as _load
from chancetime.utils.logging import get_logger, setup_logging

log = get_logger(__name__)


def register(app: typer.Typer) -> None:
    @app.command()
    def run(
        config: Annotated[
            str | None,
            typer.Option("--config", "-c", help="Path to YAML config"),
        ] = None,
        account: Annotated[
            str | None,
            typer.Option(
                "--account",
                "-a",
                help="Named book from config/accounts.yaml (isolates db_path)",
            ),
        ] = None,
        once: Annotated[
            bool,
            typer.Option("--once", help="Run a single poll then exit"),
        ] = False,
        max_polls: Annotated[
            int | None,
            typer.Option("--max-polls", help="Stop after N polls"),
        ] = None,
        live: Annotated[
            bool,
            typer.Option(
                "--live",
                help="REAL MONEY: force live path (requires risk ack flag)",
            ),
        ] = False,
        i_understand: Annotated[
            bool,
            typer.Option(
                "--i-understand-this-spends-real-money",
                help="Required with --live; acknowledges real funds at risk",
            ),
        ] = False,
        fresh_db: Annotated[
            bool,
            typer.Option(
                "--fresh-db",
                help="Delete this config's SQLite book before run (opt-in clean slate)",
            ),
        ] = False,
    ) -> None:
        """Start the trading loop (paper by default)."""
        from chancetime.utils.paths import resolve_path

        cfg = _load(config, account=account)
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)
        if account:
            typer.echo(
                f"account={account} db={cfg.persistence.db_path} "
                f"paper_mode={cfg.paper_mode}"
            )
        if fresh_db and cfg.persistence.enabled:
            db = resolve_path(cfg.persistence.db_path)
            removed = 0
            for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
                if p.is_file():
                    p.unlink()
                    removed += 1
            log.warning("fresh_db", path=str(db), removed_files=removed)
            typer.echo(f"fresh-db: cleared {db} ({removed} file(s))")
        if live or not cfg.paper_mode:
            if not i_understand:
                typer.echo(
                    "REFUSING live trading without "
                    "--i-understand-this-spends-real-money\n"
                    "Stay on paper: omit --live and keep PAPER_MODE=true",
                    err=True,
                )
                raise typer.Exit(2)
            if live:
                cfg.bot.paper_mode = False
                cfg.execution.live_enabled = True
            typer.echo(
                "WARNING: LIVE MODE — real orders may be sent. "
                f"max_order=${cfg.execution.max_live_order_usd} "
                f"session=${cfg.execution.max_live_notional_session}"
            )
        bot = Bot(cfg, risk_acknowledged=i_understand, force_live=live)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def _handle_sig(*_: object) -> None:
            log.info("shutdown_signal")
            bot.request_stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _handle_sig)

        polls = 1 if once else max_polls
        try:
            loop.run_until_complete(bot.run(max_polls=polls))
        finally:
            loop.close()



    @app.command()
    def version() -> None:
        """Print package version."""
        typer.echo(__version__)



