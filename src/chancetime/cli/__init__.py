"""Typer CLI package — command groups register onto the root app."""

from __future__ import annotations

import typer

from chancetime.cli import books, config_cmds, live, llm_cmds, research, run
from chancetime.flair import DISPLAY_NAME

app = typer.Typer(
    name="chancetime",
    help=f"{DISPLAY_NAME}: prediction-market bot (paper mode by default). Not financial advice.",
    add_completion=False,
)

run.register(app)
live.register(app)
books.register(app)
research.register(app)
config_cmds.register(app)
llm_cmds.register(app)

__all__ = ["app"]
