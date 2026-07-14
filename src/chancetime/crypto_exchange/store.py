"""SQLite paper book for Path D (separate from US venues + Path C).

Cash source of truth is ``book_meta.cash`` (updated on every fill).
Equity snapshots are history only — never reload cash from a bad snapshot
while positions still exist (that double-counted inventory as free equity).
"""

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
    asset TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    size_usd REAL NOT NULL,
    fee_usd REAL NOT NULL,
    venue TEXT NOT NULL,
    signal_id TEXT,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS positions (
    asset TEXT PRIMARY KEY,
    qty REAL NOT NULL,
    avg_price REAL NOT NULL,
    cost_usd REAL NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS book_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    starting_cash REAL NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ex_eq_ts ON equity_snapshots(ts);
"""


class ExchangePaperStore:
    def __init__(
        self,
        db_path: str | Path = "data/crypto_exchange_paper.db",
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
        self._conn.commit()
        self._ensure_book_meta()

    def close(self) -> None:
        self._conn.close()

    def _ensure_book_meta(self) -> None:
        row = self._conn.execute("SELECT cash FROM book_meta WHERE id = 1").fetchone()
        if row is not None:
            # Repair: if cash looks unsynced with fills, rebuild from ledger
            ledger = self.cash_from_fills(self.starting_cash)
            pos_cost = self.total_cost_basis()
            cash = float(row["cash"])
            # Classic bug: cash still ~starting while open cost basis > 0
            if pos_cost > 1.0 and abs(cash - self.starting_cash) < 0.02:
                log.warning(
                    "exchange_cash_repair",
                    old_cash=cash,
                    new_cash=round(ledger, 6),
                    pos_cost=round(pos_cost, 4),
                    msg="cash was still starting_cash with open positions — rebuilt from fills",
                )
                self.set_cash(ledger)
            return
        # First open: prefer ledger from fills, else last snapshot, else starting
        if self._conn.execute("SELECT COUNT(*) AS n FROM fills").fetchone()["n"]:
            cash = self.cash_from_fills(self.starting_cash)
        else:
            snap = self._conn.execute(
                "SELECT cash FROM equity_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            cash = float(snap["cash"]) if snap else self.starting_cash
        self._conn.execute(
            """INSERT INTO book_meta (id, cash, starting_cash, updated_ts)
               VALUES (1, ?, ?, ?)""",
            (cash, self.starting_cash, time.time()),
        )
        self._conn.commit()
        log.info("exchange_book_meta_init", cash=round(cash, 4), path=str(self.path))

    def cash_from_fills(self, starting: float | None = None) -> float:
        """Rebuild cash from fill ledger (authoritative after trades)."""
        cash = float(self.starting_cash if starting is None else starting)
        rows = self._conn.execute(
            "SELECT side, size_usd, fee_usd FROM fills ORDER BY id ASC"
        ).fetchall()
        for r in rows:
            side = str(r["side"]).lower()
            size = float(r["size_usd"] or 0)
            fee = float(r["fee_usd"] or 0)
            if side == "buy":
                cash -= size + fee
            elif side == "sell":
                # size_usd is gross proceeds; fee deducted from cash separately
                cash += size - fee
        return cash

    def total_cost_basis(self) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS c FROM positions WHERE qty > 0"
        ).fetchone()
        return float(row["c"] or 0)

    def get_cash(self) -> float:
        row = self._conn.execute("SELECT cash FROM book_meta WHERE id = 1").fetchone()
        if row is None:
            return self.starting_cash
        return float(row["cash"])

    def set_cash(self, cash: float) -> None:
        self._conn.execute(
            """INSERT INTO book_meta (id, cash, starting_cash, updated_ts)
               VALUES (1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 cash=excluded.cash, updated_ts=excluded.updated_ts""",
            (cash, self.starting_cash, time.time()),
        )
        self._conn.commit()

    def summary(self) -> dict[str, Any]:
        cur = self._conn
        pos = cur.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS cost FROM positions WHERE qty > 0"
        ).fetchone()
        fills = cur.execute("SELECT COUNT(*) AS n FROM fills").fetchone()
        eq = cur.execute(
            "SELECT equity, cash, exposure_usd, ts FROM equity_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        cash = self.get_cash()
        cost = float(pos["cost"] or 0)
        # Naive book value if no MTM: cash + cost basis (≈ starting − fees if flat MTM)
        book_value_at_cost = cash + cost
        last_eq = float(eq["equity"]) if eq else None
        last_snap_cash = float(eq["cash"]) if eq else None
        last_mtm_exp = float(eq["exposure_usd"]) if eq else None
        # Rebuild equity with authoritative cash + last known MTM exposure.
        # (Old snapshots sometimes double-counted: cash≈starting while positions open.)
        if last_mtm_exp is not None and int(pos["n"] or 0) > 0:
            equity_now = cash + last_mtm_exp
        elif last_eq is not None and last_snap_cash is not None:
            # Preserve unrealized from last snap: equity - snap_cash = old MTM
            old_mtm = last_eq - last_snap_cash
            equity_now = cash + old_mtm
        else:
            equity_now = book_value_at_cost
        return {
            "module": "crypto_exchange",
            "db_path": str(self.path),
            "cash": cash,
            "cost_basis_usd": cost,
            "book_value_at_cost": round(book_value_at_cost, 6),
            "open_positions": int(pos["n"] or 0),
            "exposure_usd": last_mtm_exp if last_mtm_exp is not None else cost,
            "fills_total": int(fills["n"] or 0),
            "equity": round(equity_now, 6),
            "last_equity_snapshot": last_eq,
            "last_cash": cash,
            "last_ts": float(eq["ts"]) if eq else None,
            "unrealized_approx": (
                round(last_mtm_exp - cost, 6)
                if last_mtm_exp is not None and cost > 0
                else 0.0
            ),
            "note": (
                "equity = cash + mark-to-market positions. "
                "Buy spends cash (size+fee); inventory is not profit. "
                "PnL ≈ equity − starting_cash (fees + price moves)."
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
    ) -> None:
        self.set_cash(cash)
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
        asset: str,
        side: str,
        price: float,
        qty: float,
        size_usd: float,
        fee_usd: float,
        venue: str,
        signal_id: str | None = None,
        note: str = "",
        cash_after: float | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO fills
               (ts, asset, side, price, qty, size_usd, fee_usd, venue, signal_id, note)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(),
                asset,
                side,
                price,
                qty,
                size_usd,
                fee_usd,
                venue,
                signal_id,
                note,
            ),
        )
        self._conn.commit()
        if cash_after is not None:
            self.set_cash(cash_after)

    def upsert_position(
        self, *, asset: str, qty: float, avg_price: float, cost_usd: float
    ) -> None:
        if qty <= 1e-12:
            self._conn.execute("DELETE FROM positions WHERE asset = ?", (asset,))
        else:
            self._conn.execute(
                """INSERT INTO positions (asset, qty, avg_price, cost_usd, updated_ts)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(asset) DO UPDATE SET
                     qty=excluded.qty, avg_price=excluded.avg_price,
                     cost_usd=excluded.cost_usd, updated_ts=excluded.updated_ts""",
                (asset, qty, avg_price, cost_usd, time.time()),
            )
        self._conn.commit()

    def load_positions(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT asset, qty, avg_price, cost_usd FROM positions WHERE qty > 0"
        ).fetchall()
        return [dict(r) for r in rows]

    def last_cash(self, default: float = 1000.0) -> float:
        """Load authoritative cash from book_meta (not a random equity snapshot)."""
        try:
            return self.get_cash()
        except Exception:
            return default


def default_exchange_db() -> Path:
    return project_root() / "data" / "crypto_exchange_paper.db"
