"""Multi-module registry: US venues, global crypto Up/Down, future brokers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModuleInfo:
    id: str
    title: str
    blurb: str
    status: str  # active | paper_only | planned
    db_keys: tuple[str, ...]  # relative paths under data/
    cli_hint: str
    desktop_view: str  # home card → view id


MODULES: tuple[ModuleInfo, ...] = (
    ModuleInfo(
        id="us_venues",
        title="Kalshi + Polymarket US",
        blurb="Account APIs, dual-list arb research, paper/live books.",
        status="active",
        db_keys=("paper.db", "live.db", "paper_bag.db"),
        cli_hint="chancetime run --account paper",
        desktop_view="us",
    ),
    ModuleInfo(
        id="crypto_updown",
        title="Global Polymarket · Crypto Up/Down",
        blurb="Intl CLOB 5m/15m binaries + external spot. Paper-first Path C.",
        status="paper_only",
        db_keys=("crypto_paper.db",),
        cli_hint="chancetime crypto run --once",
        desktop_view="crypto",
    ),
    ModuleInfo(
        id="crypto_exchange",
        title="US Crypto Exchange",
        blurb="Path D paper: Coinbase spot feed + optional C signals. No live orders yet.",
        status="paper_only",
        db_keys=("crypto_exchange_paper.db",),
        cli_hint="chancetime exchange run --once",
        desktop_view="exchange",
    ),
    ModuleInfo(
        id="alpaca",
        title="Alpaca (stocks)",
        blurb="Broker equities/options — stretch module, not scheduled.",
        status="planned",
        db_keys=(),
        cli_hint="(stretch)",
        desktop_view="planned",
    ),
)


def list_modules() -> list[dict[str, Any]]:
    return [
        {
            "id": m.id,
            "title": m.title,
            "blurb": m.blurb,
            "status": m.status,
            "db_keys": list(m.db_keys),
            "cli_hint": m.cli_hint,
            "desktop_view": m.desktop_view,
        }
        for m in MODULES
    ]


def get_module(module_id: str) -> ModuleInfo | None:
    for m in MODULES:
        if m.id == module_id:
            return m
    return None
