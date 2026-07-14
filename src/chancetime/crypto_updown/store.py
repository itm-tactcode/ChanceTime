"""SQLite paper book for crypto Up/Down (cash + positions persisted)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root, resolve_path

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS book_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    starting_cash REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    updated_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    exposure_usd REAL NOT NULL,
    open_positions INTEGER NOT NULL,
    poll_count INTEGER NOT NULL DEFAULT 0,
    extra_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size_usd REAL NOT NULL,
    fee_usd REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS positions (
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    size_usd REAL NOT NULL,
    entry_price REAL NOT NULL,
    contracts REAL NOT NULL,
    fees_paid REAL NOT NULL DEFAULT 0,
    opened_ts REAL NOT NULL,
    PRIMARY KEY (market_slug, side)
);
CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    contracts REAL NOT NULL,
    payout REAL NOT NULL,
    pnl REAL NOT NULL,
    resolved_up INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS window_refs (
    market_slug TEXT PRIMARY KEY,
    asset TEXT NOT NULL,
    ref_price REAL NOT NULL,
    ref_quality TEXT NOT NULL DEFAULT 'unknown',
    start_ts REAL,
    end_ts REAL,
    updated_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crypto_eq_ts ON equity_snapshots(ts);
"""


class CryptoPaperStore:
    def __init__(
        self,
        db_path: str | Path = "data/crypto_paper.db",
        *,
        starting_cash: float = 1000.0,
    ) -> None:
        path = resolve_path(db_path) if not Path(db_path).is_absolute() else Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.starting_cash = starting_cash
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()
        self._ensure_meta()

    def _migrate(self) -> None:
        # Older DBs may lack fee_usd / fees_paid / settlements
        cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(fills)").fetchall()
        }
        if "fee_usd" not in cols:
            self._conn.execute("ALTER TABLE fills ADD COLUMN fee_usd REAL NOT NULL DEFAULT 0")
        pcols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(positions)").fetchall()
        }
        if "fees_paid" not in pcols:
            self._conn.execute(
                "ALTER TABLE positions ADD COLUMN fees_paid REAL NOT NULL DEFAULT 0"
            )

    def _ensure_meta(self) -> None:
        row = self._conn.execute("SELECT cash FROM book_meta WHERE id = 1").fetchone()
        if row is not None:
            cash = float(row["cash"])
            pos_cost = self.total_cost_basis()
            # Repair: cash still starting with open inventory (old bug)
            if pos_cost > 1.0 and abs(cash - self.starting_cash) < 0.02:
                rebuilt = self.cash_from_fills()
                log.warning(
                    "crypto_cash_repair",
                    old=cash,
                    new=round(rebuilt, 4),
                    pos_cost=round(pos_cost, 4),
                )
                self.set_cash(rebuilt)
            return
        # Init from fills ledger if any, else starting
        if self._conn.execute("SELECT COUNT(*) AS n FROM fills").fetchone()["n"]:
            cash = self.cash_from_fills()
        else:
            cash = self.starting_cash
        self._conn.execute(
            """INSERT INTO book_meta (id, cash, starting_cash, realized_pnl, updated_ts)
               VALUES (1, ?, ?, 0, ?)""",
            (cash, self.starting_cash, time.time()),
        )
        self._conn.commit()

    def cash_from_fills(self) -> float:
        cash = self.starting_cash
        for r in self._conn.execute(
            "SELECT side, size_usd, fee_usd FROM fills ORDER BY id"
        ).fetchall():
            side = str(r["side"]).lower()
            size = float(r["size_usd"] or 0)
            fee = float(r["fee_usd"] or 0)
            if side in {"up", "down", "up+down"}:
                # up+down is complete set package size for both
                cash -= size + fee
        for r in self._conn.execute(
            "SELECT payout FROM settlements ORDER BY id"
        ).fetchall():
            cash += float(r["payout"] or 0)
        return cash

    def total_cost_basis(self) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(size_usd),0) AS c FROM positions"
        ).fetchone()
        return float(row["c"] or 0)

    def get_cash(self) -> float:
        row = self._conn.execute("SELECT cash FROM book_meta WHERE id = 1").fetchone()
        return float(row["cash"]) if row else self.starting_cash

    def set_cash(self, cash: float, *, realized_pnl: float | None = None) -> None:
        if realized_pnl is None:
            self._conn.execute(
                """UPDATE book_meta SET cash = ?, updated_ts = ? WHERE id = 1""",
                (cash, time.time()),
            )
        else:
            self._conn.execute(
                """UPDATE book_meta SET cash = ?, realized_pnl = ?, updated_ts = ?
                   WHERE id = 1""",
                (cash, realized_pnl, time.time()),
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def summary(self) -> dict[str, Any]:
        cur = self._conn
        pos = cur.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_usd),0) AS exp FROM positions"
        ).fetchone()
        fills = cur.execute("SELECT COUNT(*) AS n FROM fills").fetchone()
        settles = cur.execute("SELECT COUNT(*) AS n FROM settlements").fetchone()
        meta = cur.execute(
            "SELECT cash, starting_cash, realized_pnl FROM book_meta WHERE id = 1"
        ).fetchone()
        eq = cur.execute(
            "SELECT equity, cash, exposure_usd, ts FROM equity_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        cash = float(meta["cash"]) if meta else self.starting_cash
        cost = float(pos["exp"] or 0)
        n_pos = int(pos["n"] or 0)
        last_eq = float(eq["equity"]) if eq else None
        last_snap_cash = float(eq["cash"]) if eq else None
        last_mtm = float(eq["exposure_usd"]) if eq else None
        # Prefer last snapshot only if its cash matches book_meta (same writer).
        # Avoid: cash=700 + stale exposure=1000 from a second process → fake 1700.
        if (
            eq is not None
            and last_snap_cash is not None
            and abs(last_snap_cash - cash) < 0.05
            and last_eq is not None
        ):
            equity = last_eq
            exposure = last_mtm if last_mtm is not None else cost
        else:
            # Book value at cost (no free double-count)
            equity = cash + cost
            exposure = cost
        start = float(meta["starting_cash"]) if meta else self.starting_cash
        return {
            "module": "crypto_updown",
            "db_path": str(self.path),
            "cash": cash,
            "starting_cash": start,
            "cost_basis_usd": cost,
            "open_positions": n_pos,
            "exposure_usd": exposure,
            "fills_total": int(fills["n"] or 0),
            "settlements_total": int(settles["n"] or 0),
            "realized_pnl": float(meta["realized_pnl"]) if meta else 0.0,
            "equity": round(equity, 6),
            "last_equity": last_eq,
            "last_cash": cash,
            "last_ts": float(eq["ts"]) if eq else None,
            "pnl_vs_start": round(equity - start, 6),
            "note": (
                "equity = cash + position value (MTM when last snap matches cash). "
                "Buys spend cash; only settlements realize win/loss vs cost."
            ),
        }

    def snapshot_equity(
        self,
        *,
        cash: float,
        equity: float,
        exposure_usd: float,
        open_positions: int,
        poll_count: int = 0,
        extra: dict[str, Any] | None = None,
        realized_pnl: float | None = None,
    ) -> None:
        self.set_cash(cash, realized_pnl=realized_pnl)
        self._conn.execute(
            """INSERT INTO equity_snapshots
               (ts, cash, equity, exposure_usd, open_positions, poll_count, extra_json)
               VALUES (?,?,?,?,?,?,?)""",
            (
                time.time(),
                cash,
                equity,
                exposure_usd,
                open_positions,
                poll_count,
                json.dumps(extra or {}),
            ),
        )
        self._conn.commit()

    def record_fill(
        self,
        *,
        market_slug: str,
        side: str,
        price: float,
        size_usd: float,
        fee_usd: float = 0.0,
        note: str = "",
        cash_after: float | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO fills (ts, market_slug, side, price, size_usd, fee_usd, note)
               VALUES (?,?,?,?,?,?,?)""",
            (time.time(), market_slug, side, price, size_usd, fee_usd, note),
        )
        self._conn.commit()
        if cash_after is not None:
            self.set_cash(cash_after)

    def upsert_position(
        self,
        *,
        market_slug: str,
        side: str,
        size_usd: float,
        entry_price: float,
        contracts: float,
        fees_paid: float = 0.0,
    ) -> None:
        if contracts <= 1e-12:
            self._conn.execute(
                "DELETE FROM positions WHERE market_slug = ? AND side = ?",
                (market_slug, side),
            )
        else:
            self._conn.execute(
                """INSERT INTO positions
                   (market_slug, side, size_usd, entry_price, contracts, fees_paid, opened_ts)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(market_slug, side) DO UPDATE SET
                     size_usd=excluded.size_usd,
                     entry_price=excluded.entry_price,
                     contracts=excluded.contracts,
                     fees_paid=excluded.fees_paid""",
                (
                    market_slug,
                    side,
                    size_usd,
                    entry_price,
                    contracts,
                    fees_paid,
                    time.time(),
                ),
            )
        self._conn.commit()

    def load_positions(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT market_slug, side, size_usd, entry_price, contracts, fees_paid FROM positions"
        ).fetchall()
        return [dict(r) for r in rows]

    def record_settlement(
        self,
        *,
        market_slug: str,
        side: str,
        contracts: float,
        payout: float,
        pnl: float,
        resolved_up: bool,
        note: str = "",
    ) -> None:
        self._conn.execute(
            """INSERT INTO settlements
               (ts, market_slug, side, contracts, payout, pnl, resolved_up, note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                time.time(),
                market_slug,
                side,
                contracts,
                payout,
                pnl,
                1 if resolved_up else 0,
                note,
            ),
        )
        self._conn.commit()

    def clear_positions_for_slug(self, market_slug: str) -> None:
        self._conn.execute(
            "DELETE FROM positions WHERE market_slug = ?", (market_slug,)
        )
        self._conn.commit()

    def upsert_window_ref(
        self,
        *,
        market_slug: str,
        asset: str,
        ref_price: float,
        ref_quality: str = "unknown",
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> None:
        """Persist open-print ref so restart can settle after downtime."""
        self._conn.execute(
            """INSERT INTO window_refs
               (market_slug, asset, ref_price, ref_quality, start_ts, end_ts, updated_ts)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(market_slug) DO UPDATE SET
                 asset=excluded.asset,
                 ref_price=excluded.ref_price,
                 ref_quality=excluded.ref_quality,
                 start_ts=COALESCE(excluded.start_ts, window_refs.start_ts),
                 end_ts=COALESCE(excluded.end_ts, window_refs.end_ts),
                 updated_ts=excluded.updated_ts""",
            (
                market_slug,
                asset,
                ref_price,
                ref_quality,
                start_ts,
                end_ts,
                time.time(),
            ),
        )
        self._conn.commit()

    def load_window_refs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT market_slug, asset, ref_price, ref_quality, start_ts, end_ts
               FROM window_refs"""
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_window_ref(self, market_slug: str) -> None:
        self._conn.execute(
            "DELETE FROM window_refs WHERE market_slug = ?", (market_slug,)
        )
        self._conn.commit()

    def reset_book(self, *, starting_cash: float | None = None) -> dict[str, Any]:
        """Wipe fills, positions, settlements, snapshots; restore cash.

        Stop any running crypto session first so it does not rewrite old state.
        """
        cash0 = float(starting_cash if starting_cash is not None else self.starting_cash)
        before = self.summary()
        self._conn.execute("DELETE FROM fills")
        self._conn.execute("DELETE FROM positions")
        self._conn.execute("DELETE FROM settlements")
        self._conn.execute("DELETE FROM equity_snapshots")
        self._conn.execute("DELETE FROM window_refs")
        self._conn.execute("DELETE FROM book_meta")
        self._conn.execute(
            """INSERT INTO book_meta (id, cash, starting_cash, realized_pnl, updated_ts)
               VALUES (1, ?, ?, 0, ?)""",
            (cash0, cash0, time.time()),
        )
        self.starting_cash = cash0
        self._conn.commit()
        self.snapshot_equity(
            cash=cash0,
            equity=cash0,
            exposure_usd=0.0,
            open_positions=0,
            poll_count=0,
            extra={"reset": True},
            realized_pnl=0.0,
        )
        after = self.summary()
        log.info(
            "crypto_book_reset",
            path=str(self.path),
            cash=cash0,
            fills_before=before.get("fills_total"),
            positions_before=before.get("open_positions"),
        )
        return {
            "ok": True,
            "path": str(self.path),
            "cash": cash0,
            "before": {
                "fills_total": before.get("fills_total"),
                "open_positions": before.get("open_positions"),
                "equity": before.get("equity"),
            },
            "after": after,
            "note": "Stop crypto session before reset if it is running.",
        }


def default_crypto_db() -> Path:
    return project_root() / "data" / "crypto_paper.db"
