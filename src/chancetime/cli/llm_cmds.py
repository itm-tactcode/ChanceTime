"""LLM smoke and one-shot calibration."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from chancetime.cli.common import load_app_config as _load
from chancetime.llm.client import GrokClient
from chancetime.utils.logging import setup_logging


def register(app: typer.Typer) -> None:
    @app.command("llm-smoke")
    def llm_smoke(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    ) -> None:
        """One cheap Grok call to verify XAI_API_KEY (uses budget)."""
        cfg = _load(config)
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)
        llm = GrokClient.from_config(cfg)
        if not cfg.xai_api_key:
            typer.echo("XAI_API_KEY not set — will use mock response.")

        async def _go() -> None:
            text = await llm.chat(
                [
                    {
                        "role": "user",
                        "content": 'Reply with JSON only: {"ok": true, "msg": "chance time"}',
                    }
                ],
                max_tokens=64,
                prompt_summary="llm_smoke",
                use_cache=False,
            )
            typer.echo(text)
            typer.echo(f"spend={llm.spend_summary()}")

        asyncio.run(_go())



    @app.command()
    def calibrate(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        market: Annotated[
            str,
            typer.Option("--market", "-m", help="Market title to calibrate"),
        ] = "Will the Fed cut rates at the next meeting?",
        yes_price: Annotated[float, typer.Option("--yes", help="Market YES price 0-1")] = 0.42,
        batch: Annotated[
            bool,
            typer.Option("--batch", help="Calibrate mock markets in a small batch"),
        ] = False,
    ) -> None:
        """Calibrate one market (or a mock batch) with Grok."""
        from chancetime.data_layer.mock import MockMarketClient
        from chancetime.data_layer.models import Market, Platform
        from chancetime.llm.calibrate import ProbabilityCalibrator

        cfg = _load(config)
        setup_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)
        llm = GrokClient.from_config(cfg)
        calibrator = ProbabilityCalibrator(
            llm,
            price_move_bust=cfg.llm.price_move_bust,
            news_context=cfg.llm.news_context,
        )

        async def _go() -> None:
            if batch:
                markets = await MockMarketClient().list_markets()
                results = await calibrator.calibrate_batch(markets, max_markets=3)
                for mid, cal in results.items():
                    typer.echo(f"{mid}: fair={cal.probability:.3f} conf={cal.confidence:.2f}")
            else:
                m = Market(
                    id="cli-calibrate",
                    platform=Platform.MOCK,
                    title=market,
                    description="CLI one-shot calibration",
                    yes_price=yes_price,
                    no_price=max(0.0, min(1.0, 1.0 - yes_price)),
                    liquidity_usd=10_000.0,
                )
                cal_one = await calibrator.calibrate(m)
                if cal_one is None:
                    typer.echo("calibration skipped or failed (budget/disabled/error)")
                    raise typer.Exit(code=1)
                typer.echo(cal_one.model_dump_json(indent=2))
            typer.echo(f"spend={llm.spend_summary()}")

        asyncio.run(_go())



