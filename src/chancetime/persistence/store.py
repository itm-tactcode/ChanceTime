"""SQLite state store: portfolio, fills, equity snapshots, session meta.

Single-file DB under ``data/`` (gitignored) — restart-safe paper/live bookkeeping.
stdlib ``sqlite3`` only (no ORM) so installable packaging stays light.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from chancetime.execution.engine import Fill, OrderStatus
from chancetime.risk.portfolio import ClosedTrade, Portfolio, Position
from chancetime.strategies.base import Side
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root, resolve_path

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    market_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    side TEXT NOT NULL,
    size_usd REAL NOT NULL,
    entry_price REAL NOT NULL,
    contracts REAL NOT NULL,
    strategy TEXT NOT NULL DEFAULT '',
    opened_ts REAL NOT NULL,
    last_mark REAL
);

CREATE TABLE IF NOT EXISTS closed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    side TEXT NOT NULL,
    size_usd REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    contracts REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    reason TEXT NOT NULL,
    strategy TEXT NOT NULL DEFAULT '',
    closed_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    order_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size_usd REAL NOT NULL,
    status TEXT NOT NULL,
    paper INTEGER NOT NULL,
    ts REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    arb_group_id TEXT,
    strategy TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    cash_basis REAL NOT NULL,
    realized_pnl_today REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    equity REAL NOT NULL,
    open_positions REAL NOT NULL,
    exposure_usd REAL NOT NULL,
    poll_count INTEGER NOT NULL DEFAULT 0,
    paper INTEGER NOT NULL DEFAULT 1,
    extra_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS signal_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    generated INTEGER NOT NULL,
    approved INTEGER NOT NULL,
    filled INTEGER NOT NULL,
    strategy_counts_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS strategy_stats (
    strategy TEXT PRIMARY KEY,
    signals INTEGER NOT NULL DEFAULT 0,
    fills INTEGER NOT NULL DEFAULT 0,
    fill_notional_usd REAL NOT NULL DEFAULT 0.0,
    closed_trades INTEGER NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0.0,
    last_ts REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts);
CREATE INDEX IF NOT EXISTS idx_closed_ts ON closed_trades(closed_ts);
"""


class StateStore:
    """Thin SQLite wrapper for bot restart safety + dashboard queries."""

    def __init__(self, db_path: str | Path, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.path = resolve_path(db_path) if enabled else Path(db_path)
        self._conn: sqlite3.Connection | None = None
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            log.info("state_store_open", path=str(self.path))

    @classmethod
    def from_config_path(
        cls,
        db_path: str | Path = "data/paper.db",
        *,
        enabled: bool = True,
    ) -> StateStore:
        return cls(db_path, enabled=enabled)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("StateStore is disabled or closed")
        return self._conn

    # --- meta ---
    def set_meta(self, key: str, value: str) -> None:
        if not self.enabled:
            return
        conn = self._require()
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        if not self.enabled:
            return default
        row = self._require().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    # --- portfolio ---
    def save_portfolio(self, portfolio: Portfolio) -> None:
        if not self.enabled:
            return
        conn = self._require()
        conn.execute("DELETE FROM positions")
        for p in portfolio.positions.values():
            conn.execute(
                """
                INSERT INTO positions(
                    market_id, platform, side, size_usd, entry_price, contracts,
                    strategy, opened_ts, last_mark
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p.market_id,
                    p.platform,
                    str(p.side),
                    p.size_usd,
                    p.entry_price,
                    p.contracts,
                    p.strategy,
                    p.opened_ts,
                    p.last_mark,
                ),
            )
        self.set_meta("realized_pnl_today", f"{portfolio.realized_pnl_today:.8f}")
        self.set_meta("realized_pnl_day", time.strftime("%Y-%m-%d"))
        conn.commit()

    def load_portfolio(self) -> Portfolio:
        portfolio = Portfolio()
        if not self.enabled:
            return portfolio
        conn = self._require()
        # Roll daily realized if calendar day changed
        day = self.get_meta("realized_pnl_day")
        today = time.strftime("%Y-%m-%d")
        if day == today:
            raw = self.get_meta("realized_pnl_today", "0")
            try:
                portfolio.realized_pnl_today = float(raw or 0.0)
            except ValueError:
                portfolio.realized_pnl_today = 0.0
        else:
            portfolio.realized_pnl_today = 0.0

        for row in conn.execute("SELECT * FROM positions"):
            side = Side(str(row["side"]))
            pos = Position(
                market_id=str(row["market_id"]),
                platform=str(row["platform"]),
                side=side,
                size_usd=float(row["size_usd"]),
                entry_price=float(row["entry_price"]),
                contracts=float(row["contracts"]),
                strategy=str(row["strategy"] or ""),
                opened_ts=float(row["opened_ts"]),
                last_mark=float(row["last_mark"]) if row["last_mark"] is not None else None,
            )
            portfolio.positions[pos.market_id] = pos

        # Restore closed trades for dashboard (cap memory)
        for row in conn.execute("SELECT * FROM closed_trades ORDER BY closed_ts DESC LIMIT 500"):
            portfolio.closed.append(
                ClosedTrade(
                    market_id=str(row["market_id"]),
                    side=Side(str(row["side"])),
                    size_usd=float(row["size_usd"]),
                    entry_price=float(row["entry_price"]),
                    exit_price=float(row["exit_price"]),
                    contracts=float(row["contracts"]),
                    realized_pnl=float(row["realized_pnl"]),
                    reason=str(row["reason"]),
                    strategy=str(row["strategy"] or ""),
                    closed_ts=float(row["closed_ts"]),
                )
            )
        # Keep chronological order oldest→newest like in-memory
        portfolio.closed.reverse()
        log.info(
            "portfolio_loaded",
            open=portfolio.open_count,
            closed=len(portfolio.closed),
            realized_today=round(portfolio.realized_pnl_today, 4),
        )
        return portfolio

    def append_closed_trade(self, trade: ClosedTrade) -> None:
        if not self.enabled:
            return
        conn = self._require()
        conn.execute(
            """
            INSERT INTO closed_trades(
                market_id, side, size_usd, entry_price, exit_price, contracts,
                realized_pnl, reason, strategy, closed_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.market_id,
                str(trade.side),
                trade.size_usd,
                trade.entry_price,
                trade.exit_price,
                trade.contracts,
                trade.realized_pnl,
                trade.reason,
                trade.strategy,
                trade.closed_ts,
            ),
        )
        conn.commit()

    def record_fill(
        self,
        fill: Fill,
        *,
        strategy: str = "",
        platform: str = "",
    ) -> None:
        if not self.enabled:
            return
        conn = self._require()
        conn.execute(
            """
            INSERT OR REPLACE INTO fills(
                order_id, market_id, side, price, size_usd, status, paper, ts,
                note, arb_group_id, strategy, platform
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.order_id,
                fill.market_id,
                str(fill.side),
                fill.price,
                fill.size_usd,
                str(fill.status),
                1 if fill.paper else 0,
                fill.ts,
                fill.note,
                fill.arb_group_id,
                strategy,
                platform,
            ),
        )
        conn.commit()

    def record_equity(
        self,
        snap: dict[str, float],
        *,
        poll_count: int = 0,
        paper: bool = True,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        conn = self._require()
        conn.execute(
            """
            INSERT INTO equity_snapshots(
                ts, cash_basis, realized_pnl_today, unrealized_pnl, equity,
                open_positions, exposure_usd, poll_count, paper, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                float(snap.get("cash_basis", 0.0)),
                float(snap.get("realized_pnl_today", 0.0)),
                float(snap.get("unrealized_pnl", 0.0)),
                float(snap.get("equity", 0.0)),
                float(snap.get("open_positions", 0.0)),
                float(snap.get("exposure_usd", 0.0)),
                poll_count,
                1 if paper else 0,
                json.dumps(extra or {}),
            ),
        )
        conn.commit()

    def record_signal_stats(
        self,
        *,
        generated: int,
        approved: int,
        filled: int,
        strategy_counts: dict[str, int] | None = None,
    ) -> None:
        if not self.enabled:
            return
        conn = self._require()
        conn.execute(
            """
            INSERT INTO signal_stats(ts, generated, approved, filled, strategy_counts_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                generated,
                approved,
                filled,
                json.dumps(strategy_counts or {}),
            ),
        )
        # Phase 8: accumulate per-strategy signal counts
        now = time.time()
        for name, n in (strategy_counts or {}).items():
            if not name or n <= 0:
                continue
            conn.execute(
                """
                INSERT INTO strategy_stats(strategy, signals, fills, fill_notional_usd,
                    closed_trades, realized_pnl, last_ts)
                VALUES (?, ?, 0, 0, 0, 0, ?)
                ON CONFLICT(strategy) DO UPDATE SET
                    signals = strategy_stats.signals + excluded.signals,
                    last_ts = excluded.last_ts
                """,
                (str(name), int(n), now),
            )
        conn.commit()

    def record_strategy_fill(self, strategy: str, *, size_usd: float) -> None:
        if not self.enabled or not strategy:
            return
        conn = self._require()
        now = time.time()
        conn.execute(
            """
            INSERT INTO strategy_stats(strategy, signals, fills, fill_notional_usd,
                closed_trades, realized_pnl, last_ts)
            VALUES (?, 0, 1, ?, 0, 0, ?)
            ON CONFLICT(strategy) DO UPDATE SET
                fills = strategy_stats.fills + 1,
                fill_notional_usd = strategy_stats.fill_notional_usd + excluded.fill_notional_usd,
                last_ts = excluded.last_ts
            """,
            (strategy, float(size_usd), now),
        )
        conn.commit()

    def record_strategy_close(self, strategy: str, *, realized_pnl: float) -> None:
        if not self.enabled or not strategy:
            return
        conn = self._require()
        now = time.time()
        conn.execute(
            """
            INSERT INTO strategy_stats(strategy, signals, fills, fill_notional_usd,
                closed_trades, realized_pnl, last_ts)
            VALUES (?, 0, 0, 0, 1, ?, ?)
            ON CONFLICT(strategy) DO UPDATE SET
                closed_trades = strategy_stats.closed_trades + 1,
                realized_pnl = strategy_stats.realized_pnl + excluded.realized_pnl,
                last_ts = excluded.last_ts
            """,
            (strategy, float(realized_pnl), now),
        )
        conn.commit()

    def list_strategy_stats(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        # Ensure table exists on older DBs
        self._require().executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_stats (
                strategy TEXT PRIMARY KEY,
                signals INTEGER NOT NULL DEFAULT 0,
                fills INTEGER NOT NULL DEFAULT 0,
                fill_notional_usd REAL NOT NULL DEFAULT 0.0,
                closed_trades INTEGER NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0.0,
                last_ts REAL NOT NULL DEFAULT 0.0
            );
            """
        )
        rows = (
            self._require()
            .execute("SELECT * FROM strategy_stats ORDER BY fills DESC, signals DESC, strategy")
            .fetchall()
        )
        return [dict(r) for r in rows]

    # --- dashboard queries ---
    def summary(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        conn = self._require()
        open_n = conn.execute("SELECT COUNT(*) AS c FROM positions").fetchone()["c"]
        fills_n = conn.execute("SELECT COUNT(*) AS c FROM fills").fetchone()["c"]
        closed_n = conn.execute("SELECT COUNT(*) AS c FROM closed_trades").fetchone()["c"]
        # Always recompute exposure from open positions (equity_snapshots can be stale
        # after venue_sync / live_smoke left an old open_count/exposure).
        exp_row = conn.execute(
            "SELECT COALESCE(SUM(ABS(size_usd)), 0) AS e FROM positions"
        ).fetchone()
        exposure_now = float(exp_row["e"] if exp_row is not None else 0.0)
        last_eq = conn.execute("SELECT * FROM equity_snapshots ORDER BY ts DESC LIMIT 1").fetchone()
        realized = self.get_meta("realized_pnl_today", "0")
        out: dict[str, Any] = {
            "enabled": True,
            "db_path": str(self.path),
            "open_positions": int(open_n),
            "fills_total": int(fills_n),
            "closed_trades": int(closed_n),
            "realized_pnl_today": float(realized or 0.0),
            "exposure_usd": exposure_now,
            "last_equity": None,
        }
        if last_eq is not None:
            # sqlite3.Row iterates values, not keys — use keys()
            row_dict = {str(k): last_eq[k] for k in last_eq.keys()}
            extra = row_dict.pop("extra_json", "{}")
            extra_obj = json.loads(str(extra or "{}"))
            out["last_equity"] = row_dict
            out["last_equity"]["extra"] = extra_obj
            # Prefer live position totals over stale snapshot
            out["last_equity"]["open_positions"] = float(open_n)
            out["last_equity"]["exposure_usd"] = exposure_now
            # Flatten accounting helpers for dashboard KPIs
            for k in ("free_cash_approx", "position_mtm"):
                if k in extra_obj and k not in out["last_equity"]:
                    out["last_equity"][k] = extra_obj[k]
            # Recompute position_mtm when we have marks; else cost basis = exposure
            out["last_equity"]["position_mtm"] = exposure_now + float(
                out["last_equity"].get("unrealized_pnl") or 0.0
            )
        else:
            out["last_equity"] = {
                "cash_basis": 0.0,
                "realized_pnl_today": float(realized or 0.0),
                "unrealized_pnl": 0.0,
                "equity": float(realized or 0.0),
                "open_positions": float(open_n),
                "exposure_usd": exposure_now,
                "position_mtm": exposure_now,
                "extra": {},
            }
        return out

    def list_positions(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        rows = self._require().execute("SELECT * FROM positions ORDER BY opened_ts").fetchall()
        return [dict(r) for r in rows]

    def list_fills(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        rows = (
            self._require()
            .execute("SELECT * FROM fills ORDER BY ts DESC LIMIT ?", (limit,))
            .fetchall()
        )
        return [dict(r) for r in rows]

    def list_closed(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        rows = (
            self._require()
            .execute(
                "SELECT * FROM closed_trades ORDER BY closed_ts DESC LIMIT ?",
                (limit,),
            )
            .fetchall()
        )
        return [dict(r) for r in rows]

    def equity_series(self, *, limit: int = 200) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        rows = (
            self._require()
            .execute(
                "SELECT ts, equity, realized_pnl_today, unrealized_pnl, exposure_usd, "
                "open_positions, poll_count FROM equity_snapshots ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            .fetchall()
        )
        data = [dict(r) for r in rows]
        data.reverse()
        return data

    def fill_from_row(self, row: dict[str, Any]) -> Fill:
        return Fill(
            order_id=str(row["order_id"]),
            market_id=str(row["market_id"]),
            side=Side(str(row["side"])),
            price=float(row["price"]),
            size_usd=float(row["size_usd"]),
            status=OrderStatus(str(row["status"])),
            paper=bool(row["paper"]),
            ts=float(row["ts"]),
            note=str(row.get("note") or ""),
            arb_group_id=row.get("arb_group_id"),
        )


def default_db_path() -> Path:
    return project_root() / "data" / "paper.db"


def default_live_db_path() -> Path:
    return project_root() / "data" / "live.db"


def position_to_dict(p: Position) -> dict[str, Any]:
    d = asdict(p)
    d["side"] = str(p.side)
    return d
