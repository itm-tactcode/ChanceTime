"""Read-only Chance Time dashboard — paper vs live books from separate SQLite files.

Run: ``uv run chancetime dashboard`` (requires optional ``dashboard`` deps).
No trading controls.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from chancetime import __version__
from chancetime.flair import DISPLAY_NAME
from chancetime.persistence.store import StateStore
from chancetime.utils.paths import resolve_path

async def _enrich_live_marks(
    summary: dict[str, Any],
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fetch current mids for open live positions → unrealized + MTM equity."""
    if not positions:
        return summary
    from chancetime.utils.config import load_config

    cfg = load_config()
    mids: dict[str, float] = {}

    kalshi_ids = [p["market_id"] for p in positions if p.get("platform") == "kalshi"]
    pm_ids = [p["market_id"] for p in positions if p.get("platform") == "polymarket"]

    if kalshi_ids and cfg.kalshi_credentials_configured:
        from chancetime.data_layer.kalshi import KalshiClient

        k = KalshiClient(
            api_key_id=cfg.kalshi_api_key,
            private_key_path=(
                str(cfg.kalshi_private_key_path) if cfg.kalshi_private_key_path else None
            ),
            env=cfg.kalshi_env,
        )
        try:
            for mid in kalshi_ids:
                # Public market GET via search/list normalize
                found = await k.search_markets(mid, limit=1)
                for m in found:
                    if m.id == mid or mid in m.id:
                        mids[mid] = float(m.yes_price)
                        break
                if mid not in mids:
                    # Direct ticker
                    session = await k._get_session()
                    url = f"{k.base_url}/markets/{mid}"
                    try:
                        import aiohttp

                        async with session.get(url) as resp:
                            if resp.status == 200:
                                payload = await resp.json()
                                raw = payload.get("market") or payload
                                if isinstance(raw, dict):
                                    m = k._normalize(raw)
                                    mids[mid] = float(m.yes_price)
                    except Exception:
                        pass
        finally:
            await k.close()

    if pm_ids:
        from chancetime.data_layer.polymarket_us import PolymarketUSClient

        p = PolymarketUSClient(
            api_key_id=cfg.polymarket_api_key,
            private_key_path=(
                str(cfg.polymarket_private_key_path) if cfg.polymarket_private_key_path else None
            ),
            enrich_bbo=True,
            bbo_limit=min(20, len(pm_ids)),
        )
        try:
            markets = await p.list_markets(limit=50)
            by_id = {m.id: m for m in markets}
            for mid in pm_ids:
                if mid in by_id:
                    mids[mid] = float(by_id[mid].yes_price)
                else:
                    # try search by id/slug
                    found = await p.search_markets(mid, limit=5)
                    for m in found:
                        if m.id == mid or (m.slug and mid in str(m.slug)):
                            mids[mid] = float(m.yes_price)
                            break
        finally:
            await p.close()

    unreal = 0.0
    marked = 0
    position_mtm = 0.0
    for pos in positions:
        mid = str(pos.get("market_id") or "")
        side = str(pos.get("side") or "yes").lower()
        entry = float(pos.get("entry_price") or 0.5)
        contracts = float(pos.get("contracts") or 0)
        size = float(pos.get("size_usd") or 0)
        if contracts <= 0 and entry > 0:
            contracts = size / entry
        yes = mids.get(mid)
        if yes is None:
            position_mtm += size  # no mark → keep cost
            continue
        marked += 1
        # entry_price is the price paid for that side (YES or NO)
        if side == "yes":
            exit_px = float(yes)
        else:
            exit_px = max(0.0, min(1.0, 1.0 - float(yes)))
        pnl = (exit_px - entry) * contracts
        unreal += pnl
        position_mtm += contracts * exit_px

    le = summary.get("last_equity") or {}
    le["unrealized_pnl"] = round(unreal, 4)
    le["position_mtm"] = round(position_mtm, 4)
    le["exposure_usd"] = float(summary.get("exposure_usd") or le.get("exposure_usd") or 0)
    le["marks"] = {k: round(v, 4) for k, v in mids.items()}
    le["marked_positions"] = marked
    # Paper-style equity without cash_basis: realized + unrealized only for PnL display
    # Total wealth for live UI uses venue cash + position_mtm in the browser.
    le["equity"] = round(float(summary.get("realized_pnl_today") or 0) + unreal, 4)
    summary["last_equity"] = le
    summary["marks"] = le["marks"]
    return summary


def create_app(
    paper_db: str | Path = "data/paper.db",
    live_db: str | Path = "data/live.db",
    *,
    # Back-compat single-db callers
    db_path: str | Path | None = None,
    extra_books: dict[str, str | Path] | None = None,
) -> Any:
    try:
        from fastapi import FastAPI, Query
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:
        raise ImportError(
            "Dashboard requires optional deps: uv sync --extra dashboard "
            "(or pip install 'chancetime[dashboard]')"
        ) from exc

    if db_path is not None:
        paper_path = resolve_path(db_path)
        live_path = paper_path
        books: dict[str, Path] = {"paper": paper_path, "live": live_path}
    else:
        # Explicit paper/live args win; other accounts merge as extra books
        books = {"paper": resolve_path(paper_db), "live": resolve_path(live_db)}
        try:
            from chancetime.utils.accounts import load_accounts

            for name, acct in load_accounts().items():
                if name in ("paper", "live"):
                    # Keep constructor paths (tests + intentional overrides)
                    continue
                books[name] = resolve_path(acct.db_path)
        except Exception:
            pass
        if extra_books:
            for name, p in extra_books.items():
                books[name] = resolve_path(p)

    default_book = "paper" if "paper" in books else next(iter(books))

    app = FastAPI(
        title=f"{DISPLAY_NAME} Dashboard",
        version=__version__,
        description="Read-only multi-book status (accounts). Not financial advice.",
    )

    def store_for(book: str) -> StateStore:
        path = books.get(book) or books[default_book]
        return StateStore(path, enabled=True)

    def parse_book(book: str | None) -> str:
        b = (book or default_book).strip().lower()
        if b not in books:
            return default_book
        return b

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "app": DISPLAY_NAME,
            "version": __version__,
            "books": {
                name: {"path": str(p), "exists": p.is_file()}
                for name, p in books.items()
            },
            "ts": time.time(),
        }

    @app.get("/api/summary")
    def summary(book: str = Query("paper")) -> dict[str, Any]:
        b = parse_book(book)
        s = store_for(b)
        try:
            out = s.summary()
            out["book"] = b
            # Live free cash comes from venues, not paper cash_basis (often 0)
            if b == "live":
                out["cash_model"] = "venue"
                # Mark open positions to market so unrealized is not stuck at 0
                try:
                    import asyncio

                    out = asyncio.run(_enrich_live_marks(out, s.list_positions()))
                except Exception as exc:
                    out["mark_error"] = str(exc)
            else:
                out["cash_model"] = "paper_ledger"
            return out
        finally:
            s.close()

    @app.get("/api/balances")
    def api_balances() -> dict[str, Any]:
        """Pull live buying power from Kalshi + Polymarket US (signed).

        Not used for paper free-cash (that is ledger-based). Used by Monitor
        LIVE book so free cash is not stuck at 0 when cash_basis was never set.
        """
        import asyncio

        from chancetime.utils.config import load_config

        # Load .env secrets so signed balance routes work (same as bot)
        cfg = load_config()

        async def _fetch() -> dict[str, Any]:
            kalshi_bal: float | None = None
            pm_bal: float | None = None
            errors: list[str] = []
            if cfg.kalshi_credentials_configured:
                from chancetime.execution.live_kalshi import KalshiLiveClient

                k = KalshiLiveClient(
                    api_key_id=str(cfg.kalshi_api_key),
                    private_key_path=cfg.kalshi_private_key_path,  # type: ignore[arg-type]
                    env=cfg.kalshi_env,
                )
                try:
                    kalshi_bal = await k.get_balance_usd()
                except Exception as exc:
                    errors.append(f"kalshi:{exc}")
                finally:
                    await k.close()
            else:
                errors.append("kalshi:credentials_missing")
            if cfg.polymarket_credentials_configured:
                from chancetime.execution.live_polymarket import PolymarketUSLiveClient

                p = PolymarketUSLiveClient(
                    api_key_id=str(cfg.polymarket_api_key),
                    private_key_path=cfg.polymarket_private_key_path,  # type: ignore[arg-type]
                )
                try:
                    pm_bal = await p.get_balance_usd()
                except Exception as exc:
                    errors.append(f"polymarket:{exc}")
                finally:
                    await p.close()
            else:
                errors.append("polymarket:credentials_missing")
            parts = [x for x in (kalshi_bal, pm_bal) if x is not None]
            total = sum(parts) if parts else None
            return {
                "kalshi_usd": kalshi_bal,
                "polymarket_usd": pm_bal,
                "total_usd": total,
                "kalshi_env": cfg.kalshi_env,
                "source": "venue" if total is not None else "unavailable",
                "errors": errors,
                "ts": time.time(),
            }

        try:
            return asyncio.run(_fetch())
        except Exception as exc:
            return {
                "kalshi_usd": None,
                "polymarket_usd": None,
                "total_usd": None,
                "source": "error",
                "errors": [str(exc)],
                "ts": time.time(),
            }

    @app.get("/api/positions")
    def positions(book: str = Query("paper")) -> list[dict[str, Any]]:
        b = parse_book(book)
        s = store_for(b)
        try:
            return s.list_positions()
        finally:
            s.close()

    @app.get("/api/fills")
    def fills(
        limit: int = 50,
        book: str = Query("paper"),
    ) -> list[dict[str, Any]]:
        b = parse_book(book)
        s = store_for(b)
        try:
            return s.list_fills(limit=min(limit, 500))
        finally:
            s.close()

    @app.get("/api/closed")
    def closed(
        limit: int = 50,
        book: str = Query("paper"),
    ) -> list[dict[str, Any]]:
        b = parse_book(book)
        s = store_for(b)
        try:
            return s.list_closed(limit=min(limit, 500))
        finally:
            s.close()

    @app.get("/api/equity")
    def equity(
        limit: int = 200,
        book: str = Query("paper"),
    ) -> list[dict[str, Any]]:
        b = parse_book(book)
        s = store_for(b)
        try:
            return s.equity_series(limit=min(limit, 2000))
        finally:
            s.close()

    @app.get("/api/strategies")
    def strategies(book: str = Query("paper")) -> list[dict[str, Any]]:
        b = parse_book(book)
        s = store_for(b)
        try:
            return s.list_strategy_stats()
        finally:
            s.close()

    # --- Phase 9: user.yaml write path (localhost only; never secrets) ---
    @app.get("/api/user-config")
    def get_user_config() -> dict[str, Any]:
        from chancetime.utils.user_knobs import (
            build_knobs_snapshot,
            load_user_overrides_file,
        )

        raw = load_user_overrides_file()
        return {
            "raw": raw,
            "snapshot": build_knobs_snapshot(raw),
            "note": "POST /api/user-config with nested overrides or flat snapshot",
        }

    @app.post("/api/user-config")
    def post_user_config(body: dict[str, Any]) -> Any:
        from chancetime.utils.user_knobs import (
            apply_user_overrides,
            snapshot_to_overrides,
        )

        payload = body
        if "data_source" in body or (
            "poll_interval_seconds" in body and "bot" not in body
        ):
            payload = snapshot_to_overrides(body)
        try:
            result = apply_user_overrides(payload)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {"ok": True, **result}

    @app.get("/api/doctor")
    def api_doctor() -> dict[str, Any]:
        from chancetime.utils.doctor import run_doctor

        return run_doctor()

    @app.get("/api/accounts")
    def api_accounts() -> list[dict[str, Any]]:
        from chancetime.utils.accounts import list_accounts_summary

        return list_accounts_summary()

    @app.get("/api/digest")
    def api_digest(account: str = Query("paper")) -> dict[str, Any]:
        from chancetime.monitoring.digest import build_digest, digest_to_dict

        b = parse_book(account)
        s = store_for(b)
        try:
            return digest_to_dict(build_digest(s, account=b))
        finally:
            s.close()

    @app.get("/api/scorecard")
    def api_scorecard(
        account: str = Query("paper"),
        fee_bps: float = Query(70.0),
    ) -> dict[str, Any]:
        """Phase 20: edge-after-cost scorecard (paper→live gate)."""
        from chancetime.monitoring.scorecard import build_edge_scorecard, scorecard_to_dict

        b = parse_book(account)
        s = store_for(b)
        try:
            return scorecard_to_dict(build_edge_scorecard(s, account=b, fee_bps=fee_bps))
        finally:
            s.close()

    @app.get("/api/scan-arb")
    def api_scan_arb(
        source: str = Query("mock"),
        min_spread: float = Query(0.04),
        limit: int = Query(40),
    ) -> dict[str, Any]:
        """Lightweight arb scan for dashboard (mock-friendly; no orders)."""
        import asyncio

        from chancetime.data_layer import build_data_client
        from chancetime.strategies.arb_cross import ArbCrossStrategy
        from chancetime.utils.config import load_config

        cfg = load_config(env_file=None)
        cfg.data.source = source
        cfg.data.max_markets = min(limit, 200)
        client = build_data_client(cfg.data.source)

        async def _run() -> dict[str, Any]:
            markets = await client.list_markets(limit=cfg.data.max_markets)
            strat = ArbCrossStrategy(
                enabled=True,
                min_spread=min_spread,
                fee_buffer=cfg.strategies.arb_cross.fee_buffer,
                min_match_score=cfg.strategies.arb_cross.min_match_score,
                require_bbo=False,
            )
            signals = await strat.generate_signals(markets)
            rows = []
            for s in signals[:50]:
                rows.append(
                    {
                        "market_id": s.market_id,
                        "platform": s.platform,
                        "side": str(s.side),
                        "strength": round(s.strength, 4),
                        "edge": round(
                            float(
                                s.metadata.get("exec_edge")
                                or s.metadata.get("edge")
                                or 0
                            ),
                            4,
                        ),
                        "group": s.metadata.get("arb_group_id")
                        or s.metadata.get("group"),
                        "meta": {
                            k: s.metadata.get(k)
                            for k in (
                                "cheap",
                                "rich",
                                "yes_cost",
                                "no_cost",
                                "match",
                                "mid_spread",
                                "exec_edge",
                            )
                            if k in s.metadata
                        },
                    }
                )
            return {
                "source": source,
                "markets": len(markets),
                "signals": len(signals),
                "rows": rows,
            }

        try:
            return asyncio.run(_run())
        except Exception as exc:
            return {"error": str(exc), "source": source, "rows": []}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _DASHBOARD_HTML

    @app.get("/api")
    def api_index() -> JSONResponse:
        return JSONResponse(
            {
                "endpoints": [
                    "/api/health",
                    "/api/summary?book=paper|live",
                    "/api/positions?book=…",
                    "/api/fills?book=…",
                    "/api/closed?book=…",
                    "/api/equity?book=…",
                    "/api/strategies?book=…",
                    "/api/user-config",
                    "POST /api/user-config",
                    "/api/doctor",
                    "/api/scan-arb",
                    "/api/accounts",
                    "/api/digest?account=paper",
                    "/api/scorecard?account=paper",
                    "/api/balances",
                ],
                "books": {n: str(p) for n, p in books.items()},
            }
        )

    return app


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Chance Time</title>
  <style>
    :root {
      --bg: #0f1419; --card: #1a2332; --text: #e7ecf3; --muted: #8b9bb4;
      --accent: #5b9fd4; --good: #3dcc91; --bad: #f07178; --border: #2a3548;
      --paper: #3dcc91; --live: #f07178;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--bg); color: var(--text); margin: 0;
      line-height: 1.4;
      overflow-y: auto;
      overflow-x: hidden;
      scrollbar-width: thin;
      scrollbar-color: #3d4f6a #121a24;
    }
    body::-webkit-scrollbar { width: 10px; }
    body::-webkit-scrollbar-track { background: #121a24; }
    body::-webkit-scrollbar-thumb {
      background: #3d4f6a; border-radius: 8px; border: 2px solid #121a24;
    }
    body::-webkit-scrollbar-thumb:hover { background: #5b9fd4; }
    .wrap { padding: 0.55rem 0.75rem 1.1rem; max-width: 1100px; margin: 0 auto; }
    header.bar {
      display: flex; align-items: center; gap: 0.55rem; flex-wrap: wrap;
      margin-bottom: 0.5rem;
    }
    h1 { font-size: 1.05rem; margin: 0; letter-spacing: 0.02em; font-weight: 650; }
    .sub { color: var(--muted); font-size: 0.72rem; margin: 0; flex: 1; min-width: 8rem; }
    .book-toggle {
      display: inline-flex; border: 1px solid var(--border); border-radius: 999px;
      overflow: auto; background: #121a24; max-width: 100%; flex-wrap: wrap;
    }
    .book-toggle button {
      border: 0; background: transparent; color: var(--muted);
      padding: 0.28rem 0.65rem; font-size: 0.72rem; font-weight: 700;
      cursor: pointer; letter-spacing: 0.03em;
    }
    .book-toggle button.active { background: #1a3550; color: var(--accent); }
    .book-toggle button.active.paper { background: #1e3a2f; color: var(--paper); }
    .book-toggle button.active.live { background: #3a1e22; color: var(--live); }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
      gap: 0.4rem;
      margin-bottom: 0.55rem;
    }
    .card {
      background: var(--card); border: 1px solid var(--border); border-radius: 8px;
      padding: 0.45rem 0.6rem;
    }
    .card .label {
      color: var(--muted); font-size: 0.65rem; text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .card .value {
      font-size: 1.05rem; font-weight: 600; margin-top: 0.08rem;
      font-variant-numeric: tabular-nums;
    }
    .pos { color: var(--good); } .neg { color: var(--bad); }
    h2 {
      font-size: 0.72rem; margin: 0.65rem 0 0.3rem; color: var(--accent);
      text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600;
    }
    .table-wrap { overflow-x: auto; scrollbar-width: thin; scrollbar-color: #3d4f6a #121a24; }
    table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
    th, td { text-align: left; padding: 0.3rem 0.35rem; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 500; font-size: 0.65rem; text-transform: uppercase; }
    td { font-variant-numeric: tabular-nums; }
    .chart-wrap { width: 100%; height: 160px; background: #121a24; border-radius: 8px; }
    .chart-wrap svg {
      width: 100%; height: 160px; display: block;
      shape-rendering: geometricPrecision;
    }
    .err { color: var(--bad); }
    footer { margin-top: 0.85rem; color: var(--muted); font-size: 0.7rem; }
    a { color: var(--accent); }
    #dbPath { font-family: ui-monospace, monospace; font-size: 0.68rem; color: var(--muted); }
    #lastRefresh { color: var(--good); font-size: 0.7rem; margin-left: 0.35rem; }
  </style>
</head>
<body>
  <div class="wrap">
    <header class="bar">
      <h1>Chance Time</h1>
      <div class="book-toggle" role="group" aria-label="Book" id="bookToggle"></div>
      <p class="sub">auto-refresh 5s<span id="lastRefresh"></span> · <a href="/api">API</a> · <span id="dbPath"></span></p>
    </header>
    <div class="grid" id="kpis"></div>
    <h2>Open positions</h2>
    <div class="card table-wrap"><table id="positions"><thead></thead><tbody></tbody></table></div>
    <h2>Recent fills</h2>
    <div class="card table-wrap"><table id="fills"><thead></thead><tbody></tbody></table></div>
    <h2>Equity</h2>
    <div class="card chart-wrap"><svg id="chart" viewBox="0 0 640 160" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Equity"></svg></div>
    <h2>Arb scan <button type="button" id="btnScan" style="margin-left:0.5rem;font-size:0.7rem;cursor:pointer;background:#1a3550;color:#e7ecf3;border:1px solid #2d5a86;border-radius:6px;padding:0.2rem 0.5rem">Run mock scan</button></h2>
    <div class="card table-wrap"><table id="arb"><thead></thead><tbody></tbody></table>
      <p id="arbMeta" class="sub" style="margin:0.35rem 0 0"></p>
    </div>
    <h2>Edge after cost <span class="sub">(Phase 20 scorecard)</span></h2>
    <pre id="scorecard" class="card" style="font-size:0.72rem;white-space:pre-wrap;color:var(--muted);margin:0"></pre>
    <h2>Doctor</h2>
    <pre id="doctor" class="card" style="font-size:0.72rem;white-space:pre-wrap;color:var(--muted);margin:0"></pre>
    <footer>Not financial advice. Paper and live use separate SQLite files. Settings: desktop Control or POST /api/user-config.</footer>
  </div>
  <script>
    let book = "paper";
    let bookNames = ["paper", "live"];
    (function initBookFromUrl() {
      try {
        const sp = new URLSearchParams(location.search);
        const h = (location.hash || "").replace(/^#/, "");
        const hp = new URLSearchParams(h.includes("=") ? h : "");
        const raw = (sp.get("book") || hp.get("book") || "").toLowerCase();
        if (raw) book = raw;
      } catch (_) {}
    })();
    const fmt = (n, d=2) => (n == null || Number.isNaN(n)) ? "—" : Number(n).toFixed(d);
    const cls = (n) => n > 0 ? "pos" : (n < 0 ? "neg" : "");
    const q = () => "book=" + encodeURIComponent(book);

    async function j(url) {
      const r = await fetch(url);
      if (!r.ok) throw new Error(url + " " + r.status);
      return r.json();
    }

    function renderBookToggle() {
      const el = document.getElementById("bookToggle");
      if (!el) return;
      el.innerHTML = bookNames.map(name => {
        const clsName = " " + name + (name === book ? " active" : "");
        return `<button type="button" data-book="${name}" class="${clsName.trim()}">${name.toUpperCase()}</button>`;
      }).join("");
      el.querySelectorAll("button").forEach(btn => {
        btn.onclick = () => setBook(btn.dataset.book);
      });
    }

    function setBook(b) {
      book = b;
      renderBookToggle();
      try {
        const u = new URL(location.href);
        u.searchParams.set("book", b);
        history.replaceState(null, "", u.pathname + u.search + "#book=" + b);
      } catch (_) {}
      refresh();
    }

    async function loadBooks() {
      try {
        const h = await j("/api/health");
        if (h.books) bookNames = Object.keys(h.books);
        if (bookNames.indexOf(book) < 0) book = bookNames[0] || "paper";
      } catch (_) {}
      renderBookToggle();
    }

    function renderTable(id, cols, rows, map) {
      const t = document.getElementById(id);
      t.querySelector("thead").innerHTML = "<tr>" + cols.map(c => `<th>${c}</th>`).join("") + "</tr>";
      t.querySelector("tbody").innerHTML = rows.length
        ? rows.map(r => "<tr>" + map(r).map(c => `<td>${c}</td>`).join("") + "</tr>").join("")
        : `<tr><td colspan="${cols.length}" style="color:var(--muted)">None in this book yet.</td></tr>`;
    }

    function drawEquity(series) {
      const svg = document.getElementById("chart");
      const W = 640, H = 160, padL = 36, padR = 12, padT = 16, padB = 20;
      const innerW = W - padL - padR, innerH = H - padT - padB;
      const stroke = book === "live" ? "#f07178" : "#5b9fd4";
      if (!series.length) {
        svg.innerHTML = '<text x="24" y="80" fill="#8b9bb4" font-size="13" font-family="system-ui">No equity snapshots yet — run the bot</text>';
        return;
      }
      const ys = series.map(p => Number(p.equity));
      let min = Math.min(...ys), max = Math.max(...ys);
      if (min === max) { min -= 1; max += 1; }
      const pad = (max - min) * 0.15 || 1;
      const y0 = min - pad, y1 = max + pad;
      const pts = series.map((p, i) => {
        const x = padL + (i / Math.max(series.length - 1, 1)) * innerW;
        const y = padT + innerH - ((Number(p.equity) - y0) / (y1 - y0)) * innerH;
        return [x, y];
      });
      // Smooth-ish polyline (no stretch: preserveAspectRatio meet)
      let line = "";
      pts.forEach((p, i) => {
        line += (i === 0 ? "M" : "L") + p[0].toFixed(2) + " " + p[1].toFixed(2) + " ";
      });
      const lastPt = pts[pts.length - 1];
      const area = line.trim() + " L " + lastPt[0].toFixed(2) + " " + (padT+innerH) +
        " L " + pts[0][0].toFixed(2) + " " + (padT+innerH) + " Z";
      const last = ys[ys.length - 1];
      const midY = (min + max) / 2;
      svg.innerHTML =
        '<defs><linearGradient id="eqg" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="' + stroke + '" stop-opacity="0.4"/>' +
        '<stop offset="100%" stop-color="' + stroke + '" stop-opacity="0.02"/></linearGradient></defs>' +
        '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#121a24"/>' +
        '<line x1="' + padL + '" y1="' + (padT+innerH) + '" x2="' + (W-padR) + '" y2="' + (padT+innerH) + '" stroke="#2a3548" stroke-width="1"/>' +
        '<line x1="' + padL + '" y1="' + padT + '" x2="' + padL + '" y2="' + (padT+innerH) + '" stroke="#2a3548" stroke-width="1"/>' +
        '<path d="' + area + '" fill="url(#eqg)"/>' +
        '<path d="' + line + '" fill="none" stroke="' + stroke + '" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>' +
        '<circle cx="' + lastPt[0].toFixed(2) + '" cy="' + lastPt[1].toFixed(2) + '" r="4" fill="' + stroke + '" stroke="#0f1419" stroke-width="1"/>' +
        '<text x="' + (W-padR) + '" y="14" text-anchor="end" fill="#e7ecf3" font-size="12" font-family="ui-monospace,monospace">$' + last.toFixed(2) + '</text>' +
        '<text x="4" y="' + (padT+6) + '" fill="#8b9bb4" font-size="9" font-family="ui-monospace,monospace">$' + max.toFixed(0) + '</text>' +
        '<text x="4" y="' + (padT+innerH) + '" fill="#8b9bb4" font-size="9" font-family="ui-monospace,monospace">$' + min.toFixed(0) + '</text>';
    }

    async function refresh() {
      try {
        const [sum, pos, fills, eq, health] = await Promise.all([
          j("/api/summary?" + q()),
          j("/api/positions?" + q()),
          j("/api/fills?limit=25&" + q()),
          j("/api/equity?limit=120&" + q()),
          j("/api/health"),
        ]);
        const path = (health.books && health.books[book] && health.books[book].path) || sum.db_path || "";
        document.getElementById("dbPath").textContent = path;
        const le = sum.last_equity || {};
        const realized = sum.realized_pnl_today ?? le.realized_pnl_today ?? 0;
        const unreal = le.unrealized_pnl ?? 0;
        const equity = le.equity ?? realized;
        const exposure = le.exposure_usd ?? 0;
        // Prefer live position totals (API recomputes from positions table)
        const exposureLive = sum.exposure_usd != null ? Number(sum.exposure_usd) : Number(exposure);
        let freeCash = le.free_cash_approx ?? le.available_cash ?? null;
        let freeLabel = "Free cash ≈";
        // LIVE: free cash from venues (Kalshi+PM), not paper cash_basis (often 0)
        if (book === "live") {
          freeLabel = "Venue cash";
          freeCash = null;
          try {
            const bal = await j("/api/balances");
            if (bal && bal.total_usd != null && Number.isFinite(Number(bal.total_usd))) {
              freeCash = Number(bal.total_usd);
              const bits = [];
              if (bal.kalshi_usd != null) bits.push("K $" + Number(bal.kalshi_usd).toFixed(0));
              if (bal.polymarket_usd != null) bits.push("P $" + Number(bal.polymarket_usd).toFixed(0));
              if (bits.length) freeLabel = "Venue cash (" + bits.join(" + ") + ")";
            }
          } catch (_) { /* keep null → em dash */ }
        } else if (freeCash != null && Number(freeCash) === 0 && Number(le.cash_basis || 0) === 0) {
          freeCash = null;
        }
        const pnlTotal = Number(realized) + Number(unreal);
        // Equity: paper = bankroll model from snap; live = venue cash + open MTM
        let equityLabel = "Equity";
        let equityVal = equity;
        if (book === "live") {
          const mtm = le.position_mtm != null
            ? Number(le.position_mtm)
            : (Number.isFinite(exposureLive) ? exposureLive + Number(unreal || 0) : null);
          if (freeCash != null && mtm != null && Number.isFinite(mtm)) {
            equityVal = freeCash + mtm;
            equityLabel = "Equity ≈ cash+MTM";
          } else if (mtm != null) {
            equityVal = mtm;
            equityLabel = "Position MTM";
          }
        }
        document.getElementById("kpis").innerHTML = [
          ["Book", book.toUpperCase(), book === "live" ? "neg" : "pos"],
          ["Open", sum.open_positions ?? 0, ""],
          [equityLabel, fmt(equityVal), cls(pnlTotal)],
          ["Realized PnL", fmt(realized), cls(realized)],
          ["Unrealized PnL", fmt(unreal), cls(unreal)],
          ["At risk (exposure)", fmt(exposureLive), ""],
          [freeLabel, freeCash == null ? "—" : fmt(freeCash), ""],
          ["Fills", sum.fills_total ?? 0, ""],
        ].map(([l,v,c]) => `<div class="card"><div class="label">${l}</div><div class="value ${c}">${v}</div></div>`).join("");
        renderTable("positions",
          ["Market", "Platform", "Side", "Size $", "Entry", "Strategy"],
          pos,
          r => [r.market_id, r.platform, r.side, fmt(r.size_usd), fmt(r.entry_price, 3), r.strategy || "—"]
        );
        renderTable("fills",
          ["Time", "Market", "Side", "Price", "Size $", "Status", "Strategy"],
          fills,
          r => [
            new Date(r.ts * 1000).toLocaleString(),
            r.market_id, r.side, fmt(r.price, 3), fmt(r.size_usd), r.status, r.strategy || "—"
          ]
        );
        drawEquity(eq);
        try {
          const sc = await j("/api/scorecard?account=" + encodeURIComponent(book));
          const el = document.getElementById("scorecard");
          if (el) {
            const gate = sc.gate_ok ? "PASS" : "HOLD";
            el.textContent = (sc.text || "") + "\\n\\ngate=" + gate +
              (sc.gate_notes && sc.gate_notes.length ? " · " + sc.gate_notes.join("; ") : "");
            el.style.color = sc.gate_ok ? "var(--pos,#6bcb8a)" : "var(--muted)";
          }
        } catch (_) {
          const el = document.getElementById("scorecard");
          if (el) el.textContent = "Scorecard unavailable";
        }
        const lr = document.getElementById("lastRefresh");
        if (lr) lr.textContent = " · updated " + new Date().toLocaleTimeString();
      } catch (e) {
        document.getElementById("kpis").innerHTML =
          `<div class="card err">Failed: ${e.message}. Run bot once; or migrate-books.</div>`;
      }
    }
    async function runScan() {
      document.getElementById("arbMeta").textContent = "scanning…";
      try {
        const data = await j("/api/scan-arb?source=mock&limit=40");
        document.getElementById("arbMeta").textContent =
          `source=${data.source} markets=${data.markets} signals=${data.signals}` +
          (data.error ? " err=" + data.error : "");
        renderTable("arb",
          ["Market", "Platform", "Side", "Edge", "Strength", "Group"],
          data.rows || [],
          r => [r.market_id, r.platform, r.side, r.edge, r.strength, r.group || "—"]
        );
      } catch (e) {
        document.getElementById("arbMeta").textContent = "scan failed: " + e.message;
      }
    }
    async function loadDoctor() {
      try {
        const d = await j("/api/doctor");
        const lines = [d.summary || ""].concat(
          (d.checks || []).map(c =>
            `[${c.ok ? "ok" : c.level}] ${c.name}: ${c.detail}`
          )
        );
        document.getElementById("doctor").textContent = lines.join("\\n");
      } catch (e) {
        document.getElementById("doctor").textContent = "doctor failed: " + e.message;
      }
    }
    document.getElementById("btnScan").onclick = () => runScan();
    loadBooks().then(() => { refresh(); loadDoctor(); });
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""
