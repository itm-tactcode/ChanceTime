"""Combined portfolio snapshot across modules (US + Path C + Path D)."""

from __future__ import annotations

from typing import Any

from chancetime.crypto_exchange.store import ExchangePaperStore
from chancetime.crypto_updown.store import CryptoPaperStore
from chancetime.modules import list_modules
from chancetime.persistence.store import StateStore
from chancetime.utils.paths import project_root, resolve_path


def _us_book_summary(rel: str) -> dict[str, Any] | None:
    path = resolve_path(f"data/{rel}") if not rel.startswith("data/") else resolve_path(rel)
    # db_keys are like paper.db
    if not str(path).endswith(".db"):
        path = project_root() / "data" / rel
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "equity": None,
            "exposure_usd": 0.0,
            "open_positions": 0,
        }
    store = StateStore(path, enabled=True)
    try:
        s = store.summary()
        last = s.get("last_equity") or {}
        eq = last.get("equity") if isinstance(last, dict) else None
        return {
            "path": str(path),
            "exists": True,
            "equity": eq,
            "exposure_usd": s.get("exposure_usd"),
            "open_positions": s.get("open_positions"),
            "fills_total": s.get("fills_total"),
            "realized_pnl_today": (
                last.get("realized_pnl_today") if isinstance(last, dict) else None
            ),
        }
    finally:
        store.close()


def _crypto_summary() -> dict[str, Any]:
    path = project_root() / "data" / "crypto_paper.db"
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "equity": None,
            "exposure_usd": 0.0,
            "open_positions": 0,
        }
    store = CryptoPaperStore(path)
    try:
        return {**store.summary(), "exists": True}
    finally:
        store.close()


def combined_portfolio() -> dict[str, Any]:
    """Hub dashboard payload: per-module books + naive sum of equities."""
    modules = list_modules()
    books: dict[str, Any] = {}
    total_equity = 0.0
    n_eq = 0

    us = {
        "paper": _us_book_summary("paper.db"),
        "live": _us_book_summary("live.db"),
        "paper_bag": _us_book_summary("paper_bag.db"),
    }
    books["us_venues"] = us
    for b in us.values():
        if b and b.get("equity") is not None:
            total_equity += float(b["equity"])
            n_eq += 1

    crypto = _crypto_summary()
    books["crypto_updown"] = crypto
    eq_c = crypto.get("equity") if crypto.get("equity") is not None else crypto.get("last_equity")
    if eq_c is not None:
        total_equity += float(eq_c)
        n_eq += 1

    exchange = _exchange_summary()
    books["crypto_exchange"] = exchange
    eq_d = (
        exchange.get("equity")
        if exchange.get("equity") is not None
        else exchange.get("last_equity")
    )
    if eq_d is not None:
        total_equity += float(eq_d)
        n_eq += 1

    return {
        "modules": modules,
        "books": books,
        "combined_equity": total_equity if n_eq else None,
        "books_with_equity": n_eq,
        "note": (
            "Naive sum of last equity snapshots per book — not a NAV audit. "
            "Each module has its own cash basis. C→D signals are not positions."
        ),
    }


def _exchange_summary() -> dict[str, Any]:
    path = project_root() / "data" / "crypto_exchange_paper.db"
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "equity": None,
            "exposure_usd": 0.0,
            "open_positions": 0,
        }
    store = ExchangePaperStore(path)
    try:
        return {**store.summary(), "exists": True}
    finally:
        store.close()
