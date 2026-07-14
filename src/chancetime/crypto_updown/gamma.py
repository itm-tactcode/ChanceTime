"""Gamma API — public market discovery (no auth).

https://docs.polymarket.com/market-data/fetching-markets
https://gamma-api.polymarket.com

Crypto 5m/15m Up/Down markets are *recurring series*. The website loads them by
slug ``{asset}-updown-5m-{window_start_unix}``. Generic ``/events?active=true``
pagination often returns *pre-listed future* windows and **misses the current
hour**, so discovery must construct slugs from the clock (and optionally fall
back to the events list).
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from chancetime.crypto_updown.models import OutcomeBook, UpDownMarket
from chancetime.utils.logging import get_logger

log = get_logger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"

_UPDOWN_RE = re.compile(
    r"^(Bitcoin|Ethereum|Solana|XRP|Dogecoin|BNB|Hyperliquid)\s+Up or Down",
    re.I,
)
_ASSET_MAP = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "xrp": "XRP",
    "dogecoin": "DOGE",
    "bnb": "BNB",
    "hyperliquid": "HYPE",
}

# slug prefix → display asset
_SLUG_ASSETS_5M = ("btc", "eth", "sol", "xrp", "doge", "bnb", "hype")
_SLUG_ASSETS_15M = ("btc", "eth", "sol", "xrp", "doge", "bnb", "hype")


def _parse_dt(raw: object) -> datetime | None:
    if raw is None or raw == "":
        return None
    s = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


_SLUG_PREFIX_ASSET = {
    "btc": "BTC",
    "eth": "ETH",
    "sol": "SOL",
    "xrp": "XRP",
    "doge": "DOGE",
    "bnb": "BNB",
    "hype": "HYPE",
}


def _start_from_slug(slug: str) -> datetime | None:
    """Parse trailing unix timestamp from updown slug (window *start*)."""
    if not slug:
        return None
    tail = slug.rsplit("-", 1)[-1]
    if not tail.isdigit():
        return None
    try:
        ts = int(tail)
    except ValueError:
        return None
    # Sanity: 2020–2100 unix range
    if ts < 1_500_000_000 or ts > 4_000_000_000:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


def _end_from_slug(slug: str) -> datetime | None:
    """Parse window *end* from updown slug (start unix + 5m/15m duration).

    Historical name kept for callers; trailing epoch is the start, not end.
    """
    bounds = window_bounds_from_slug(slug)
    if bounds is None:
        return None
    return datetime.fromtimestamp(bounds[1], tz=UTC)


def window_bounds_from_slug(slug: str) -> tuple[float, float] | None:
    """Return ``(start_ts, end_ts)`` from ``{asset}-updown-{5m|15m}-{start_unix}``."""
    start_dt = _start_from_slug(slug)
    if start_dt is None:
        return None
    start = start_dt.timestamp()
    low = slug.lower()
    if "-15m-" in low:
        return start, start + 900.0
    # default 5m (and unknown short windows)
    return start, start + 300.0


def asset_from_slug(slug: str) -> str | None:
    """Map ``btc-updown-5m-…`` → ``BTC``."""
    if not slug:
        return None
    prefix = slug.split("-", 1)[0].lower()
    return _SLUG_PREFIX_ASSET.get(prefix)


def resolved_up_from_event(event: dict[str, Any]) -> bool | None:
    """Infer Up win from a Gamma event/market payload when settled.

    Uses outcomePrices near 0/1 on closed markets; returns None if unknown.
    """
    markets = event.get("markets")
    m: dict[str, Any] | None = None
    if isinstance(markets, list) and markets and isinstance(markets[0], dict):
        m = markets[0]
    elif event.get("outcomePrices") is not None or event.get("outcomes") is not None:
        m = event
    if m is None:
        return None

    closed = bool(m.get("closed") or event.get("closed"))
    prices_raw = m.get("outcomePrices") or m.get("outcome_prices")
    outcomes_raw = m.get("outcomes")
    prices: list[float] = []
    if isinstance(prices_raw, str):
        try:
            prices = [float(x) for x in json.loads(prices_raw)]
        except (json.JSONDecodeError, TypeError, ValueError):
            prices = []
    elif isinstance(prices_raw, list):
        try:
            prices = [float(x) for x in prices_raw]
        except (TypeError, ValueError):
            prices = []
    outcomes: list[str] = []
    if isinstance(outcomes_raw, str):
        try:
            outcomes = [str(x) for x in json.loads(outcomes_raw)]
        except json.JSONDecodeError:
            outcomes = []
    elif isinstance(outcomes_raw, list):
        outcomes = [str(x) for x in outcomes_raw]

    if len(prices) >= 2 and outcomes:
        # Winner has price ~1 after resolve
        best_i = max(range(len(prices)), key=lambda i: prices[i])
        if prices[best_i] >= 0.9 or closed:
            name = outcomes[best_i].lower() if best_i < len(outcomes) else ""
            if name in {"up", "yes"}:
                return True
            if name in {"down", "no"}:
                return False
    if len(prices) >= 2 and (closed or max(prices) >= 0.95):
        # Assume [Up, Down] order when outcomes missing
        if prices[0] >= 0.9 and prices[1] <= 0.1:
            return True
        if prices[1] >= 0.9 and prices[0] <= 0.1:
            return False
    return None


def is_live_window(
    market: UpDownMarket,
    *,
    now: float | None = None,
    max_horizon_sec: float = 45 * 60,
    start_grace_sec: float = 120.0,
    end_grace_sec: float = 90.0,
) -> bool:
    """True if window is active or starts soon and ends within max_horizon.

    Excludes tomorrow's pre-listed 5m markets that would never resolve in a session.
    """
    import time

    t = now if now is not None else time.time()
    if market.window_end is None:
        return False
    end = market.window_end.timestamp()
    start = (
        market.window_start.timestamp()
        if market.window_start is not None
        else end - 300.0
    )
    if end + end_grace_sec < t:
        return False  # already over
    if end - t > max_horizon_sec:
        return False  # ends too far out
    if start - t > start_grace_sec:
        return False  # has not started (and not about to)
    return True


def _asset_from_title(title: str) -> str | None:
    t = title.lower()
    for k, v in _ASSET_MAP.items():
        if k in t:
            return v
    return None


def _token_ids(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            return []
    return []


def _prices(raw: object) -> list[float]:
    if raw is None:
        return []
    if isinstance(raw, list):
        out = []
        for x in raw:
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                out.append(0.5)
        return out
    if isinstance(raw, str):
        try:
            return _prices(json.loads(raw))
        except json.JSONDecodeError:
            return []
    return []


class GammaClient:
    """Read-only Gamma markets/events."""

    def __init__(
        self,
        *,
        base_url: str = GAMMA_BASE,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns = session is None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._owns and self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_event_by_slug(self, slug: str) -> dict[str, Any] | None:
        """GET /events?slug=… — works for current and recent 5m windows."""
        session = await self._sess()
        url = f"{self.base_url}/events"
        try:
            async with session.get(url, params={"slug": slug}) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()
        except (TimeoutError, aiohttp.ClientError, OSError):
            return None
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        if isinstance(payload, dict) and payload.get("slug"):
            return payload
        return None

    @staticmethod
    def _floor_epoch(ts: float, period_sec: int) -> int:
        return int(ts) - (int(ts) % period_sec)

    async def list_updown_events_by_slug_clock(
        self,
        *,
        lookback_windows: int = 2,
        lookahead_windows: int = 6,
        include_15m: bool = True,
    ) -> list[dict[str, Any]]:
        """Discover live/near Up/Down events by constructing slugs from time.

        Slug pattern (verified): ``btc-updown-5m-{window_start_unix}`` where the
        unix is the **start** of the 5m (or 15m) window in UTC.
        """
        now = time.time()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        # 5-minute series
        base5 = self._floor_epoch(now, 300)
        for i in range(-lookback_windows, lookahead_windows + 1):
            start = base5 + i * 300
            for asset in _SLUG_ASSETS_5M:
                slug = f"{asset}-updown-5m-{start}"
                if slug in seen:
                    continue
                ev = await self.fetch_event_by_slug(slug)
                if ev is None:
                    continue
                seen.add(slug)
                out.append(ev)

        if include_15m:
            base15 = self._floor_epoch(now, 900)
            for i in range(-1, 4):
                start = base15 + i * 900
                for asset in _SLUG_ASSETS_15M:
                    slug = f"{asset}-updown-15m-{start}"
                    if slug in seen:
                        continue
                    ev = await self.fetch_event_by_slug(slug)
                    if ev is None:
                        continue
                    seen.add(slug)
                    out.append(ev)

        log.info(
            "gamma_updown_slug_clock",
            events=len(out),
            lookback=lookback_windows,
            lookahead=lookahead_windows,
        )
        return out

    async def list_updown_events(self, *, limit: int = 40) -> list[dict[str, Any]]:
        """Prefer slug-clock discovery (live windows); fall back to events list."""
        by_slug = await self.list_updown_events_by_slug_clock()
        if by_slug:
            return by_slug[: max(limit * 3, len(by_slug))]

        # Fallback: generic events pagination (often future-only — last resort)
        session = await self._sess()
        url = f"{self.base_url}/events"
        out: list[dict[str, Any]] = []
        for offset in (0, 100, 200):
            params: dict[str, str | int] = {
                "limit": 100,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "order": "id",
                "ascending": "false",
            }
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        log.warning("gamma_events_failed", status=resp.status, offset=offset)
                        break
                    payload = await resp.json()
            except (TimeoutError, aiohttp.ClientError, OSError) as exc:
                log.warning("gamma_events_error", error=str(exc))
                break
            if not isinstance(payload, list) or not payload:
                break
            for e in payload:
                if not isinstance(e, dict):
                    continue
                title = str(e.get("title") or "")
                if "up or down" not in title.lower():
                    continue
                out.append(e)
            if len(out) >= limit * 5:
                break
        log.info("gamma_updown_events_fallback", count=len(out))
        return out

    def events_to_markets(
        self,
        events: list[dict[str, Any]],
        *,
        live_only: bool = True,
        max_horizon_sec: float = 45 * 60,
    ) -> list[UpDownMarket]:
        """Normalize markets; by default keep only near-term live/soon windows.

        Gamma lists many *future* 5m Up/Down events (e.g. tomorrow). Those must not
        dominate the bot or resolution will never fire.
        """
        markets: list[UpDownMarket] = []
        for e in events:
            nested = e.get("markets")
            if not isinstance(nested, list):
                continue
            for m in nested:
                if not isinstance(m, dict):
                    continue
                um = self._normalize_market(m, event=e)
                if um is not None:
                    markets.append(um)
        if live_only:
            before = len(markets)
            live = [m for m in markets if is_live_window(m, max_horizon_sec=max_horizon_sec)]
            if live:
                markets = live
                tier = "live"
            else:
                # Pre-listed windows (common): keep soonest that end within 6h
                soon = [
                    m
                    for m in markets
                    if is_live_window(
                        m,
                        max_horizon_sec=6 * 3600,
                        start_grace_sec=6 * 3600,
                    )
                ]
                if soon:
                    markets = soon
                    tier = "soon_6h"
                else:
                    # Last resort: soonest-ending N (still real Poly markets)
                    markets = sorted(
                        markets,
                        key=lambda m: m.window_end.timestamp() if m.window_end else 1e18,
                    )
                    tier = "soonest_any"
            log.info(
                "gamma_live_filter",
                before=before,
                after=len(markets),
                tier=tier,
                max_horizon_sec=max_horizon_sec,
            )
        return markets

    def _normalize_market(
        self, m: dict[str, Any], *, event: dict[str, Any] | None = None
    ) -> UpDownMarket | None:
        q = str(m.get("question") or m.get("title") or event.get("title") if event else "")
        if "up or down" not in q.lower() and event:
            q = str(event.get("title") or q)
        if "up or down" not in q.lower():
            return None
        asset = _asset_from_title(q)
        if not asset:
            return None
        tokens = _token_ids(m.get("clobTokenIds") or m.get("clob_token_ids"))
        prices = _prices(m.get("outcomePrices") or m.get("outcome_prices"))
        outcomes_raw = m.get("outcomes")
        outcomes: list[str] = []
        if isinstance(outcomes_raw, str):
            try:
                outcomes = [str(x) for x in json.loads(outcomes_raw)]
            except json.JSONDecodeError:
                outcomes = ["Up", "Down"]
        elif isinstance(outcomes_raw, list):
            outcomes = [str(x) for x in outcomes_raw]
        else:
            outcomes = ["Up", "Down"]
        up: OutcomeBook | None = None
        down: OutcomeBook | None = None
        for i, tok in enumerate(tokens[:2]):
            name = outcomes[i] if i < len(outcomes) else ("Up" if i == 0 else "Down")
            mid = prices[i] if i < len(prices) else None
            book = OutcomeBook(token_id=tok, outcome=name, mid=mid)
            if name.lower() in {"up", "yes"}:
                up = book
            else:
                down = book
        if up is None and len(tokens) >= 1:
            up = OutcomeBook(
                token_id=tokens[0],
                outcome="Up",
                mid=prices[0] if prices else None,
            )
        if down is None and len(tokens) >= 2:
            down = OutcomeBook(
                token_id=tokens[1],
                outcome="Down",
                mid=prices[1] if len(prices) > 1 else None,
            )
        end = _parse_dt(m.get("endDate") or m.get("end_date_iso") or (event or {}).get("endDate"))
        start = _parse_dt(
            m.get("eventStartTime")
            or m.get("startDate")
            or (event or {}).get("startTime")
            or (event or {}).get("eventStartTime")
        )
        cid = str(m.get("conditionId") or m.get("condition_id") or m.get("id") or "")
        slug = str(m.get("slug") or (event or {}).get("slug") or cid)
        # Prefer short Gamma window (eventStart → endDate) when duration is 5–60m.
        # Trailing slug unix is the *window start* (not series end).
        slug_start = _start_from_slug(slug)
        short = (
            start is not None
            and end is not None
            and 60 <= (end - start).total_seconds() <= 3600
        )
        if short:
            pass  # trust eventStartTime / endDate
        elif slug_start is not None:
            start = slug_start
            if "-15m-" in slug.lower():
                end = slug_start + timedelta(minutes=15)
            else:
                end = slug_start + timedelta(minutes=5)
        elif start is not None and end is None:
            if "-15m-" in slug.lower():
                end = start + timedelta(minutes=15)
            elif "-5m-" in slug.lower():
                end = start + timedelta(minutes=5)
        try:
            vol = float(m.get("volume") or m.get("volumeNum") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        return UpDownMarket(
            condition_id=cid,
            slug=slug,
            question=q,
            asset=asset,
            window_start=start,
            window_end=end,
            up=up,
            down=down,
            volume=vol,
            raw={"market": m, "event_slug": (event or {}).get("slug")},
        )
