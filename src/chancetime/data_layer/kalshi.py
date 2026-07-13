"""Kalshi market data client.

Public market list does not require API keys; env only selects the host.
Credentials are environment-specific for authenticated routes (orders, balances).
Demo keys (demo.kalshi.co) ≠ prod keys (kalshi.com).
Docs: https://docs.kalshi.com/getting_started/api_environments
     https://docs.kalshi.com/api-reference/market/get-markets
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp

from chancetime.data_layer.base import MarketDataClient
from chancetime.data_layer.matching import normalize_title
from chancetime.data_layer.models import Market, Platform
from chancetime.data_layer.timeparse import parse_close_time
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import load_text_secret, resolve_path

log = get_logger(__name__)

KALSHI_DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"
KALSHI_PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _fp_float(raw: object) -> float | None:
    """Parse Kalshi fixed-point dollar/count strings or numbers."""
    if raw is None or raw == "":
        return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class KalshiClient(MarketDataClient):
    """Kalshi open markets fetcher (cursor-paginated, MVE excluded).

    SAFETY: read-only market listing only. No order placement here.
    """

    def __init__(
        self,
        *,
        api_key_id: str | None = None,
        private_key_path: str | Path | None = None,
        env: str = "prod",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.api_key_id = api_key_id
        self.private_key_path: Path | None = (
            resolve_path(private_key_path) if private_key_path else None
        )
        self.env = env
        self.base_url = KALSHI_DEMO_BASE if env == "demo" else KALSHI_PROD_BASE
        self._session = session
        self._owns_session = session is None
        self._private_key_pem: str | None = None

        if self.private_key_path is not None:
            if self.private_key_path.is_file():
                log.info("kalshi_private_key_loaded_path", path=str(self.private_key_path))
            else:
                log.warning("kalshi_private_key_missing", path=str(self.private_key_path))

    @property
    def credentials_configured(self) -> bool:
        return bool(
            self.api_key_id
            and self.private_key_path is not None
            and self.private_key_path.is_file()
        )

    def load_private_key_pem(self) -> str:
        if self._private_key_pem is not None:
            return self._private_key_pem
        if self.private_key_path is None:
            raise FileNotFoundError("KALSHI_PRIVATE_KEY_PATH is not set")
        pem = load_text_secret(self.private_key_path)
        if "PRIVATE KEY" not in pem and "BEGIN" not in pem and len(pem.strip()) < 20:
            raise ValueError(f"Key file looks empty: {self.private_key_path}")
        self._private_key_pem = pem
        return pem

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    @staticmethod
    def _looks_like_parlay(title: str, ticker: str) -> bool:
        """Multi-leg leftovers (API should already exclude via mve_filter)."""
        t = title.lower().strip()
        if ticker.upper().startswith("KXMVE"):
            return True
        if t.count("yes ") + t.count("no ") >= 2:
            return True
        return t.count(",") >= 2 and ("yes " in t or "no " in t)

    async def list_markets(self, *, limit: int = 20) -> list[Market]:
        """Cursor-paginated GET /markets?status=open&mve_filter=exclude."""
        url = f"{self.base_url}/markets"
        session = await self._get_session()
        result: list[Market] = []
        skipped_parlay = 0
        raw_total = 0
        cursor = ""
        page_limit = min(200, max(limit, 50))

        while len(result) < limit:
            params: dict[str, str | int] = {
                "limit": page_limit,
                "status": "open",
                "mve_filter": "exclude",
            }
            if cursor:
                params["cursor"] = cursor
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning(
                            "kalshi_list_markets_failed",
                            status=resp.status,
                            body=body[:200],
                            env=self.env,
                        )
                        break
                    payload: dict[str, Any] = await resp.json()
            except (TimeoutError, aiohttp.ClientError, OSError) as exc:
                log.warning("kalshi_list_markets_error", error=str(exc), env=self.env)
                break

            markets_raw = payload.get("markets") or []
            raw_total += len(markets_raw)
            if not markets_raw:
                break
            for m in markets_raw:
                if len(result) >= limit:
                    break
                try:
                    market = self._normalize(m)
                except (KeyError, TypeError, ValueError) as exc:
                    log.debug("kalshi_market_skip", error=str(exc))
                    continue
                if self._looks_like_parlay(market.title, market.id):
                    skipped_parlay += 1
                    continue
                result.append(market)

            cursor = str(payload.get("cursor") or "")
            if not cursor or len(markets_raw) < page_limit:
                break

        log.info(
            "kalshi_markets_fetched",
            count=len(result),
            skipped_parlay=skipped_parlay,
            raw_returned=raw_total,
            env=self.env,
            auth_configured=self.credentials_configured,
        )
        return result

    async def search_markets(self, query: str, *, limit: int = 20) -> list[Market]:
        """Find markets by ticker, series_ticker, or title tokens.

        Kalshi has no full-text search on open markets; recent ``list_markets``
        pages are sports props-heavy. This tries:
        1. Exact ticker GET if query looks like a ticker
        2. ``series_ticker`` / ``event_ticker`` filters for known series tokens
        3. Title filter over a larger open-market scan
        """
        session = await self._get_session()
        q = query.strip()
        if not q:
            return []
        found: list[Market] = []
        seen: set[str] = set()

        def _add(raw: dict[str, Any]) -> None:
            try:
                m = self._normalize(raw)
            except (KeyError, TypeError, ValueError):
                return
            if m.id in seen:
                return
            seen.add(m.id)
            found.append(m)

        # 1) Exact ticker (e.g. KXMENWORLDCUP-26-FR)
        compact = q.upper().replace(" ", "")
        if compact.startswith("KX") or "-" in compact:
            url = f"{self.base_url}/markets/{compact}"
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        payload: dict[str, Any] = await resp.json()
                        raw_m = payload.get("market")
                        if isinstance(raw_m, dict):
                            _add(raw_m)
            except (TimeoutError, aiohttp.ClientError, OSError):
                pass

        # 2) Series / event filters (futures often not on page-1 open book)
        series_guesses = _series_guesses_for_query(q)
        if compact.startswith("KX") and "-" not in compact:
            series_guesses.insert(0, compact)
        for tok in q.replace(",", " ").split():
            t = tok.strip().upper()
            if t.startswith("KX") and len(t) >= 4:
                series_guesses.append(t)

        ql = q.lower()
        for series in dict.fromkeys(series_guesses):  # dedupe preserve order
            for param_key, param_val in (
                ("series_ticker", series),
                ("event_ticker", f"{series}-26"),
                ("event_ticker", f"{series}-27"),
                ("event_ticker", series),
            ):
                url = f"{self.base_url}/markets"
                params: dict[str, str | int] = {
                    "limit": min(200, max(limit * 5, 50)),
                    "status": "open",
                    "mve_filter": "exclude",
                    param_key: param_val,
                }
                try:
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            continue
                        payload = await resp.json()
                        for raw in payload.get("markets") or []:
                            if isinstance(raw, dict):
                                _add(raw)
                            if len(found) >= limit * 3:
                                break
                except (TimeoutError, aiohttp.ClientError, OSError, TypeError, ValueError):
                    continue

        # 3) Title token scan of open book (fallback) — skip if series already hit
        if len(found) < max(3, limit // 4):
            pool = await self.list_markets(limit=max(limit * 15, 200))
            tokens = [t for t in ql.split() if len(t) > 2]
            scored: list[tuple[int, Market]] = []
            for m in pool:
                if m.id in seen:
                    continue
                blob = f"{m.title} {m.id}".lower()
                hits = sum(1 for t in tokens if t in blob)
                if hits:
                    scored.append((hits, m))
            scored.sort(key=lambda x: (-x[0], -x[1].volume_usd))
            for _, m in scored:
                if m.id not in seen:
                    seen.add(m.id)
                    found.append(m)
                if len(found) >= limit:
                    break

        # Prefer title match to query tokens when ranking
        tokens = [t for t in ql.split() if len(t) > 2]

        def _rank(m: Market) -> tuple[int, float]:
            blob = f"{m.title} {m.id}".lower()
            hits = sum(1 for t in tokens if t in blob)
            return (hits, m.volume_usd)

        found.sort(key=_rank, reverse=True)
        log.info("kalshi_search", query=q, count=min(len(found), limit))
        return found[:limit]

    @staticmethod
    def _normalize(raw: dict[str, Any]) -> Market:
        # Prefer dollar fields (current API); fall back to cents
        bid_d = _fp_float(raw.get("yes_bid_dollars"))
        ask_d = _fp_float(raw.get("yes_ask_dollars"))
        last_d = _fp_float(raw.get("last_price_dollars"))
        if bid_d is not None and ask_d is not None:
            yes = (bid_d + ask_d) / 2.0
        elif last_d is not None:
            yes = last_d
        else:
            cents_bid = raw.get("yes_bid")
            cents_ask = raw.get("yes_ask")
            last = raw.get("last_price")
            if cents_bid is not None and cents_ask is not None:
                yes = (float(cents_bid) + float(cents_ask)) / 200.0
            elif last is not None:
                yes = float(last) / 100.0
            else:
                yes = 0.5
        yes = max(0.0, min(1.0, yes))

        ticker = str(raw.get("ticker") or raw.get("id") or "unknown")
        # Build a richer title for matching
        yes_sub = str(raw.get("yes_sub_title") or "").strip()
        title = str(raw.get("title") or "").strip()
        rules = str(raw.get("rules_primary") or "").strip()
        if title and yes_sub and yes_sub.lower() not in title.lower():
            display = f"{title}: {yes_sub}"
        elif title:
            display = title
        elif yes_sub:
            display = yes_sub
        elif rules:
            display = rules.split(".")[0].strip()[:160]
        else:
            display = ticker

        vol = _fp_float(raw.get("volume_24h_fp") or raw.get("volume_fp") or raw.get("volume"))
        oi = _fp_float(
            raw.get("open_interest_fp") or raw.get("open_interest") or raw.get("liquidity")
        )

        # List endpoint often includes bid/ask without sizes
        yes_bid_q: float | None = None
        yes_ask_q: float | None = None
        if bid_d is not None:
            yes_bid_q = max(0.0, min(1.0, bid_d))
        elif raw.get("yes_bid") is not None:
            try:
                yes_bid_q = max(0.0, min(1.0, float(raw["yes_bid"]) / 100.0))
            except (TypeError, ValueError, KeyError):
                yes_bid_q = None
        if ask_d is not None:
            yes_ask_q = max(0.0, min(1.0, ask_d))
        elif raw.get("yes_ask") is not None:
            try:
                yes_ask_q = max(0.0, min(1.0, float(raw["yes_ask"]) / 100.0))
            except (TypeError, ValueError, KeyError):
                yes_ask_q = None
        has_quote = yes_bid_q is not None and yes_ask_q is not None
        close = parse_close_time(
            raw.get("close_time"),
            raw.get("expected_expiration_time"),
            raw.get("expiration_time"),
            raw.get("latest_expiration_time"),
            raw.get("end_date"),
        )

        return Market(
            id=ticker,
            platform=Platform.KALSHI,
            title=display,
            description=rules or str(raw.get("subtitle") or ""),
            yes_price=yes,
            no_price=max(0.0, min(1.0, 1.0 - yes)),
            volume_usd=vol or 0.0,
            liquidity_usd=oi or 0.0,
            close_time=close,
            slug=ticker,
            canonical_key=normalize_title(display),
            url=f"https://kalshi.com/markets/{ticker}",
            yes_bid=yes_bid_q,
            yes_ask=yes_ask_q,
            has_bbo=has_quote,
            raw=dict(raw),
        )

    async def fetch_orderbook(self, ticker: str) -> dict[str, Any] | None:
        """GET /markets/{ticker}/orderbook — public, bids only (asks via reciprocity)."""
        url = f"{self.base_url}/markets/{ticker}/orderbook"
        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                payload: dict[str, Any] = await resp.json()
                return payload
        except (TimeoutError, aiohttp.ClientError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _bbo_from_orderbook(payload: dict[str, Any]) -> dict[str, float | None]:
        """Parse Kalshi orderbook_fp into YES bid/ask + top-of-book sizes (contracts)."""
        ob = payload.get("orderbook_fp") or payload.get("orderbook") or {}
        if not isinstance(ob, dict):
            return {
                "yes_bid": None,
                "yes_ask": None,
                "yes_bid_size": None,
                "yes_ask_size": None,
            }

        def _levels(key: str) -> list[tuple[float, float]]:
            raw_lv = ob.get(key) or ob.get(key.replace("_dollars", "")) or []
            out: list[tuple[float, float]] = []
            if not isinstance(raw_lv, list):
                return out
            for row in raw_lv:
                try:
                    if isinstance(row, list | tuple) and len(row) >= 2:
                        px = float(row[0])
                        sz = float(row[1])
                    elif isinstance(row, dict):
                        px = float(row.get("price") or row.get("price_dollars") or 0)
                        sz = float(row.get("count") or row.get("quantity") or 0)
                    else:
                        continue
                    if px > 1.0:
                        px = px / 100.0
                    out.append((max(0.0, min(1.0, px)), max(0.0, sz)))
                except (TypeError, ValueError):
                    continue
            return out

        yes_lv = _levels("yes_dollars")
        no_lv = _levels("no_dollars")
        # Arrays sorted ascending; best bid = last
        yes_bid = yes_lv[-1][0] if yes_lv else None
        yes_bid_size = yes_lv[-1][1] if yes_lv else None
        no_bid = no_lv[-1][0] if no_lv else None
        no_bid_size = no_lv[-1][1] if no_lv else None
        # YES ask = 1 - best NO bid; size at that level is NO bid size
        yes_ask = (1.0 - no_bid) if no_bid is not None else None
        yes_ask_size = no_bid_size
        return {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "yes_bid_size": yes_bid_size,
            "yes_ask_size": yes_ask_size,
        }

    async def enrich_bbo_markets(self, markets: list[Market]) -> list[Market]:
        """Fetch orderbook BBO for each market (pair-only use recommended)."""
        out: list[Market] = []
        for m in markets:
            if m.platform != Platform.KALSHI:
                out.append(m)
                continue
            payload = await self.fetch_orderbook(m.id)
            if not payload:
                out.append(m)
                continue
            bbo = self._bbo_from_orderbook(payload)
            bid = bbo["yes_bid"]
            ask = bbo["yes_ask"]
            if bid is None and ask is None:
                out.append(m)
                continue
            mid = m.yes_price
            if bid is not None and ask is not None:
                mid = (bid + ask) / 2.0
            elif ask is not None:
                mid = ask
            elif bid is not None:
                mid = bid
            mid = max(0.0, min(1.0, mid))
            out.append(
                m.model_copy(
                    update={
                        "yes_price": mid,
                        "no_price": max(0.0, min(1.0, 1.0 - mid)),
                        "yes_bid": bid,
                        "yes_ask": ask,
                        "yes_bid_size": bbo["yes_bid_size"],
                        "yes_ask_size": bbo["yes_ask_size"],
                        "has_bbo": True,
                    }
                )
            )
        return out


# Keyword → Kalshi series tickers that dual-list on Polymarket US more often
# than page-1 sports props. Order within a group = preference.
_QUERY_SERIES_MAP: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("fed rate", "federal funds", "fomc", "fed decision", "interest rate"),
        ("KXFED", "KXRATECUT", "KXRATECUTCOUNT", "KXFEDHIKE", "KXRATEHIKE"),
    ),
    (
        ("rate cut", "fed cut"),
        ("KXRATECUT", "KXRATECUTCOUNT", "KXFED"),
    ),
    (
        ("bitcoin", "btc"),
        ("KXBTCMAXY", "KXBTCD", "KXBTC", "KXBTCMAXM", "KXBTCMAXW", "KXBTC15M"),
    ),
    (
        ("ethereum", "eth "),
        ("KXETHMAXY", "KXETHD", "KXETH"),
    ),
    (
        ("world series", "mlb champion"),
        ("KXMLBWS", "KXWSAL", "KXWSNL"),
    ),
    (
        ("national league", "nl champion", "nl pennant"),
        ("KXWSNL",),
    ),
    (
        ("american league", "al champion", "al pennant"),
        ("KXWSAL",),
    ),
    (
        ("nba champion", "nba finals", "pro basketball", "nba"),
        ("KXNBA", "KXNBAFINALSEXACT"),
    ),
    (
        ("super bowl", "nfl champion", "pro football champion"),
        ("KXSB", "KXNFLSBMVP"),
    ),
    (
        ("world cup", "worldcup", "fifa"),
        ("KXMENWORLDCUP", "KXWWC", "KXWC"),
    ),
    (
        ("all-star", "all star", "mvp"),
        ("KXNBAALLSTARMVP", "KXMLBASGAME", "KXNBAALLSTAR"),
    ),
    (
        ("election", "president"),
        ("KXPRES",),
    ),
    (
        ("inflation", "cpi"),
        ("CPI", "CPIYOY", "CPICORE", "KXINFLATION"),
    ),
    (
        ("recession",),
        ("KXRECESSION",),
    ),
)


def _series_guesses_for_query(query: str) -> list[str]:
    """Map a natural-language query to likely Kalshi series tickers."""
    ql = query.lower().strip()
    out: list[str] = []
    compact = query.upper().replace(" ", "")
    if compact.startswith("KX"):
        out.append(compact.split("-")[0] if "-" in compact else compact)
    for keywords, series in _QUERY_SERIES_MAP:
        if any(k in ql for k in keywords):
            out.extend(series)
    # bare "fed" without rate still useful
    if "fed" in ql and "KXFED" not in out:
        out.append("KXFED")
    return out
