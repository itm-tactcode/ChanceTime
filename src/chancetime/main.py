"""CLI entrypoint (thin re-export).

Implementation lives in ``chancetime.cli`` and the poll loop in ``chancetime.bot``.
Keep ``chancetime.main:app`` as the stable script entry for packaging / desktop.
"""

from __future__ import annotations

from chancetime.bot import Bot
from chancetime.cli import app

__all__ = ["Bot", "app"]


if __name__ == "__main__":
    app()
