"""Kalshi authenticated trading (balance + create order).

Docs:
  https://docs.kalshi.com/getting_started/quick_start_authenticated_requests
  https://docs.kalshi.com/getting_started/quick_start_create_order
  https://docs.kalshi.com/api-reference/orders/create-order-v2

V2 book: ``bid`` = buy YES, ``ask`` = sell YES (~ buy NO at 1-price).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from cryptography.hazmat.primitives.asymmetric import rsa

from chancetime.data_layer.kalshi import KALSHI_DEMO_BASE, KALSHI_PROD_BASE
from chancetime.execution.auth import kalshi_sign, load_rsa_private_key, now_ms
from chancetime.strategies.base import Side
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import resolve_path

log = get_logger(__name__)


def _fp(raw: object) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _fp_pos(raw: object) -> float | None:
    return _fp(raw)


@dataclass
class LiveOrderResult:
    ok: bool
    venue: str
    order_id: str
    client_order_id: str
    status: str
    price: float
    size_usd: float
    contracts: float
    raw: dict[str, Any]
    note: str = ""


class KalshiLiveClient:
    """Signed Kalshi REST for portfolio + orders.

    SAFETY: caller must enforce paper_mode / size caps before calling place_order.
    """

    def __init__(
        self,
        *,
        api_key_id: str,
        private_key_path: str | Path,
        env: str = "prod",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.api_key_id = api_key_id
        self.private_key_path = resolve_path(private_key_path)
        self.env = env
        self.base_url = (KALSHI_DEMO_BASE if env == "demo" else KALSHI_PROD_BASE).rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._key: rsa.RSAPrivateKey | None = None

    def _load_key(self) -> rsa.RSAPrivateKey:
        if self._key is None:
            self._key = load_rsa_private_key(self.private_key_path)
        return self._key

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _sign_headers(self, method: str, full_path: str) -> dict[str, str]:
        ts = now_ms()
        # full_path must be e.g. /trade-api/v2/portfolio/balance (no query)
        path_for_sign = full_path.split("?", 1)[0]
        sig = kalshi_sign(self._load_key(), timestamp_ms=ts, method=method, path=path_for_sign)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _full_path(self, relative: str) -> str:
        """Relative like /portfolio/balance → absolute URL path for signing."""
        rel = relative if relative.startswith("/") else f"/{relative}"
        # base_url already includes /trade-api/v2
        parsed = urlparse(self.base_url + rel)
        return parsed.path

    async def request(
        self,
        method: str,
        relative: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | list[Any] | str]:
        session = await self._get_session()
        full_path = self._full_path(relative)
        headers = self._sign_headers(method.upper(), full_path)
        url = self.base_url + (relative if relative.startswith("/") else f"/{relative}")
        async with session.request(method.upper(), url, headers=headers, json=json_body) as resp:
            status = resp.status
            try:
                data: dict[str, Any] | list[Any] = await resp.json()
            except Exception:
                text = await resp.text()
                return status, text
            return status, data

    async def cancel_order(self, order_id: str) -> tuple[bool, str]:
        """DELETE /portfolio/events/orders/{order_id}."""
        status, data = await self.request("DELETE", f"/portfolio/events/orders/{order_id}")
        if status in (200, 201, 204):
            return True, f"canceled {order_id}"
        return False, f"HTTP {status}: {str(data)[:200]}"

    async def list_positions(self) -> list[dict[str, Any]]:
        """GET /portfolio/positions — best-effort normalized rows."""
        status, data = await self.request("GET", "/portfolio/positions")
        if status != 200 or not isinstance(data, dict):
            log.warning("kalshi_positions_failed", status=status, body=str(data)[:160])
            return []
        rows: list[dict[str, Any]] = []
        # Shape varies: market_positions | positions | event_positions
        for key in ("market_positions", "positions", "event_positions"):
            raw = data.get(key)
            if not isinstance(raw, list):
                continue
            for p in raw:
                if not isinstance(p, dict):
                    continue
                ticker = str(p.get("ticker") or p.get("market_ticker") or "")
                if not ticker:
                    continue
                pos = _fp_pos(p.get("position") or p.get("position_fp") or p.get("yes_count"))
                if pos is None or abs(pos) < 1e-9:
                    continue
                rows.append(
                    {
                        "market_id": ticker,
                        "platform": "kalshi",
                        "contracts": abs(pos),
                        "side": "yes" if pos > 0 else "no",
                        "raw": p,
                    }
                )
        return rows

    async def get_balance_usd(self) -> float | None:
        """Return available balance in USD, or None on failure."""
        status, data = await self.request("GET", "/portfolio/balance")
        if status != 200 or not isinstance(data, dict):
            log.warning("kalshi_balance_failed", status=status, body=str(data)[:200])
            return None
        # balance often in cents; also balance_dollars / available_balance
        for key in ("balance_dollars", "available_balance_dollars", "balance_fp"):
            if key in data and data[key] is not None:
                try:
                    return float(data[key])
                except (TypeError, ValueError):
                    pass
        bal = data.get("balance")
        if bal is not None:
            try:
                v = float(bal)
                # Heuristic: values > 500 treated as cents if no dollar field
                if v > 500:
                    return v / 100.0
                return v
            except (TypeError, ValueError):
                return None
        return None

    async def place_order(
        self,
        *,
        ticker: str,
        side: Side,
        size_usd: float,
        limit_price: float,
        client_order_id: str | None = None,
        time_in_force: str = "immediate_or_cancel",
    ) -> LiveOrderResult:
        """Place a limit order. YES->bid, NO->ask (sell YES @ 1-no_price)."""
        cid = client_order_id or str(uuid.uuid4())
        limit_price = max(0.01, min(0.99, limit_price))
        if side == Side.YES:
            book_side = "bid"
            yes_px = limit_price
        elif side == Side.NO:
            book_side = "ask"
            # Buy NO at limit_price ⇒ sell YES at 1 - limit_price
            yes_px = max(0.01, min(0.99, 1.0 - limit_price))
        else:
            return LiveOrderResult(
                ok=False,
                venue="kalshi",
                order_id="",
                client_order_id=cid,
                status="rejected",
                price=0.0,
                size_usd=size_usd,
                contracts=0.0,
                raw={},
                note="flat side not orderable",
            )

        contracts = max(1, int(size_usd / yes_px)) if yes_px > 0 else 1
        notional = contracts * yes_px
        # V2 required fields: self_trade_prevention_type (see CreateOrderV2Request)
        body = {
            "ticker": ticker,
            "side": book_side,
            "count": f"{contracts:.2f}",
            "price": f"{yes_px:.4f}",
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": cid,
        }
        log.warning(
            "LIVE_ORDER_KALSHI",
            ticker=ticker,
            book_side=book_side,
            contracts=contracts,
            yes_price=round(yes_px, 4),
            notional=round(notional, 4),
            tif=time_in_force,
            env=self.env,
        )
        status, data = await self.request("POST", "/portfolio/events/orders", json_body=body)
        raw = data if isinstance(data, dict) else {"body": data}
        if status in (200, 201) and isinstance(data, dict):
            oid = str(data.get("order_id") or data.get("id") or "")
            if not oid and isinstance(data.get("order"), dict):
                oid = str(data["order"].get("order_id") or data["order"].get("id") or "")
            fill_c = _fp(data.get("fill_count"))
            rem_c = _fp(data.get("remaining_count"))
            avg_px = _fp(data.get("average_fill_price"))
            note = f"kalshi accepted fill_count={fill_c} remaining={rem_c} avg_px={avg_px}"
            if fill_c is not None and fill_c <= 0:
                note += " (no fill — IOC canceled rest)"
            return LiveOrderResult(
                ok=True,
                venue="kalshi",
                order_id=oid or cid,
                client_order_id=cid,
                status="submitted" if (fill_c is None or fill_c <= 0) else "filled",
                price=float(avg_px) if avg_px else (yes_px if side == Side.YES else limit_price),
                size_usd=notional,
                contracts=float(contracts),
                raw=raw,
                note=note,
            )
        return LiveOrderResult(
            ok=False,
            venue="kalshi",
            order_id="",
            client_order_id=cid,
            status="rejected",
            price=yes_px if side == Side.YES else limit_price,
            size_usd=notional,
            contracts=float(contracts),
            raw=raw,
            note=f"kalshi HTTP {status}: {str(data)[:240]}",
        )
