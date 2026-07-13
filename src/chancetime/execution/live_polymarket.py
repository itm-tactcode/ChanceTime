"""Polymarket US authenticated trading (balance + create order).

Docs:
  https://docs.polymarket.us/api-reference/authentication
  https://docs.polymarket.us/api-reference/orders/create-order

Auth: Ed25519 over ``timestamp + METHOD + path`` (base64 secret from developer portal).
"""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import Any

import aiohttp
from cryptography.hazmat.primitives.asymmetric import ed25519

from chancetime.data_layer.polymarket_us import POLYMARKET_US_AUTH_BASE
from chancetime.execution.auth import load_ed25519_private_key, now_ms, polymarket_sign
from chancetime.execution.live_kalshi import LiveOrderResult
from chancetime.strategies.base import Side
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import resolve_path

log = get_logger(__name__)


class PolymarketUSLiveClient:
    """Signed Polymarket US REST for portfolio + orders.

    SAFETY: caller must enforce paper_mode / size caps before calling place_order.
    """

    def __init__(
        self,
        *,
        api_key_id: str,
        private_key_path: str | Path,
        base_url: str = POLYMARKET_US_AUTH_BASE,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.api_key_id = api_key_id
        self.private_key_path = resolve_path(private_key_path)
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._key: ed25519.Ed25519PrivateKey | None = None

    def _load_key(self) -> ed25519.Ed25519PrivateKey:
        if self._key is None:
            self._key = load_ed25519_private_key(self.private_key_path)
        return self._key

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _sign_headers(self, method: str, path: str) -> dict[str, str]:
        ts = now_ms()
        path_clean = path.split("?", 1)[0]
        if not path_clean.startswith("/"):
            path_clean = f"/{path_clean}"
        sig = polymarket_sign(self._load_key(), timestamp_ms=ts, method=method, path=path_clean)
        return {
            "X-PM-Access-Key": self.api_key_id,
            "X-PM-Timestamp": ts,
            "X-PM-Signature": sig,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | list[Any] | str]:
        if not path.startswith("/"):
            path = f"/{path}"
        session = await self._get_session()
        headers = self._sign_headers(method.upper(), path)
        url = f"{self.base_url}{path}"
        async with session.request(method.upper(), url, headers=headers, json=json_body) as resp:
            status = resp.status
            try:
                data: dict[str, Any] | list[Any] = await resp.json()
            except Exception:
                return status, await resp.text()
            return status, data

    async def cancel_order(self, order_id: str) -> tuple[bool, str]:
        """POST /v1/order/{id}/cancel (US API)."""
        for path, method in (
            (f"/v1/order/{order_id}/cancel", "POST"),
            (f"/v1/orders/{order_id}/cancel", "POST"),
            (f"/v1/order/{order_id}", "DELETE"),
        ):
            status, data = await self.request(method, path)
            if status in (200, 201, 204):
                return True, f"canceled {order_id} via {method} {path}"
            if status not in (404, 405):
                return False, f"HTTP {status}: {str(data)[:200]}"
        return False, f"cancel failed for {order_id}"

    async def list_positions(self) -> list[dict[str, Any]]:
        """GET /v1/portfolio/positions."""
        status, data = await self.request("GET", "/v1/portfolio/positions")
        if status != 200 or not isinstance(data, dict):
            log.warning("polymarket_positions_failed", status=status)
            return []
        rows: list[dict[str, Any]] = []
        positions = data.get("positions")
        items: list[tuple[str, dict[str, Any]]] = []
        if isinstance(positions, dict):
            for k, v in positions.items():
                if isinstance(v, dict):
                    items.append((str(k), v))
        elif isinstance(positions, list):
            for p in positions:
                if isinstance(p, dict):
                    items.append((str(p.get("marketSlug") or p.get("slug") or ""), p))
        for slug, p in items:
            if not slug or not isinstance(p, dict):
                continue
            net = p.get("netPosition") or p.get("netPositionDecimal") or p.get("qtyBought")
            try:
                qty = float(net)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if abs(qty) < 1e-9:
                continue
            cost = p.get("cost")
            entry = 0.5
            if isinstance(cost, dict) and cost.get("value") is not None:
                try:
                    cval = float(cost["value"])
                    if abs(qty) > 0:
                        entry = abs(cval / qty)
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            rows.append(
                {
                    "market_id": str(slug),
                    "platform": "polymarket",
                    "contracts": abs(qty),
                    "side": "yes" if qty > 0 else "no",
                    "entry_price": max(0.01, min(0.99, entry)),
                    "raw": p,
                }
            )
        return rows

    async def get_balance_usd(self) -> float | None:
        """Best-effort cash/buying power from account balances."""
        for path in (
            "/v1/account/balances",
            "/v1/portfolio/balances",
            "/v1/portfolio/balance",
            "/v1/account/balance",
        ):
            status, data = await self.request("GET", path)
            if status != 200:
                continue
            bal = _extract_balance(data)
            if bal is not None:
                return bal
        log.warning("polymarket_balance_unavailable")
        return None

    async def _wait_order_terminal(
        self, order_id: str, *, attempts: int = 6, delay_s: float = 0.35
    ) -> dict[str, Any] | None:
        """Poll GET /v1/order/{id} until filled/canceled or attempts exhausted."""
        import asyncio

        last: dict[str, Any] | None = None
        for i in range(attempts):
            if i:
                await asyncio.sleep(delay_s)
            d_status, d_data = await self.request("GET", f"/v1/order/{order_id}")
            if d_status != 200 or not isinstance(d_data, dict):
                continue
            order = d_data.get("order") if isinstance(d_data.get("order"), dict) else d_data
            if not isinstance(order, dict):
                continue
            last = order
            state = str(order.get("state") or "").upper()
            cum = float(order.get("cumQuantity") or 0)
            leaves = float(order.get("leavesQuantity") or 0)
            if (
                cum > 0
                or leaves <= 0
                or any(x in state for x in ("FILL", "CANCEL", "EXPIR", "REJECT"))
            ):
                return order
        return last

    async def place_order(
        self,
        *,
        market_slug: str,
        side: Side,
        size_usd: float,
        limit_price: float,
        client_order_id: str | None = None,
        tif: str = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
    ) -> LiveOrderResult:
        cid = client_order_id or str(uuid.uuid4())
        limit_price = max(0.01, min(0.99, limit_price))
        if side == Side.YES:
            intent = "ORDER_INTENT_BUY_LONG"
            px = limit_price
        elif side == Side.NO:
            intent = "ORDER_INTENT_BUY_SHORT"
            px = limit_price
        else:
            return LiveOrderResult(
                ok=False,
                venue="polymarket",
                order_id="",
                client_order_id=cid,
                status="rejected",
                price=0.0,
                size_usd=size_usd,
                contracts=0.0,
                raw={},
                note="flat side not orderable",
            )

        contracts = max(1.0, round(size_usd / px, 4)) if px > 0 else 1.0
        notional = contracts * px
        body: dict[str, Any] = {
            "marketSlug": market_slug,
            "intent": intent,
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": f"{px:.4f}", "currency": "USD"},
            "quantity": contracts,
            "tif": tif,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": True,
            "maxBlockTime": "5",
        }
        log.warning(
            "LIVE_ORDER_POLYMARKET",
            slug=market_slug,
            intent=intent,
            contracts=contracts,
            price=round(px, 4),
            notional=round(notional, 4),
            tif=tif,
        )
        status, data = await self.request("POST", "/v1/orders", json_body=body)
        raw: dict[str, Any] = data if isinstance(data, dict) else {"body": data}
        if status in (200, 201) and isinstance(data, dict):
            oid = str(data.get("id") or data.get("orderId") or "")
            detail_note = "polymarket order accepted"
            fill_status = "submitted"
            fill_px = px
            # Matching can lag the create response — poll order a few times
            if oid:
                order = await self._wait_order_terminal(oid)
                if order is not None:
                    raw = {"create": data if isinstance(data, dict) else {}, "order": order}
                    cum = float(order.get("cumQuantity") or 0)
                    leaves = float(order.get("leavesQuantity") or 0)
                    state = str(order.get("state") or "")
                    avg = order.get("avgPx")
                    if isinstance(avg, dict) and avg.get("value") is not None:
                        with contextlib.suppress(TypeError, ValueError):
                            fill_px = float(avg["value"]) or px
                    if cum > 0:
                        fill_status = "filled"
                        detail_note = (
                            f"state={state} filled_qty={cum} leaves={leaves} avg_px={fill_px}"
                        )
                    elif leaves <= 0 or "CANCEL" in state.upper() or "EXPIR" in state.upper():
                        fill_status = "canceled_unfilled"
                        detail_note = (
                            f"state={state} cum={cum} leaves={leaves} "
                            "(IOC/unfilled — nothing should appear in positions)"
                        )
                    else:
                        detail_note = f"state={state} cum={cum} leaves={leaves}"
            return LiveOrderResult(
                ok=True,
                venue="polymarket",
                order_id=oid or cid,
                client_order_id=cid,
                status=fill_status,
                price=fill_px,
                size_usd=notional,
                contracts=float(contracts),
                raw=raw,
                note=detail_note,
            )
        return LiveOrderResult(
            ok=False,
            venue="polymarket",
            order_id="",
            client_order_id=cid,
            status="rejected",
            price=px,
            size_usd=notional,
            contracts=float(contracts),
            raw=raw,
            note=f"polymarket HTTP {status}: {str(data)[:240]}",
        )


def _extract_balance(data: dict[str, Any] | list[Any] | str) -> float | None:
    if isinstance(data, str):
        return None
    if isinstance(data, list):
        total = 0.0
        found = False
        for item in data:
            if not isinstance(item, dict):
                continue
            for k in (
                "buyingPower",
                "currentBalance",
                "available",
                "balance",
                "cash",
                "value",
                "amount",
            ):
                if k not in item:
                    continue
                try:
                    v = item[k]
                    if isinstance(v, dict) and "value" in v:
                        total += float(v["value"])
                    else:
                        total += float(v)
                    found = True
                    break  # one primary field per balance row
                except (TypeError, ValueError):
                    pass
        return total if found else None
    if not isinstance(data, dict):
        return None
    for k in (
        "buyingPower",
        "currentBalance",
        "availableBalance",
        "available_balance",
        "cashBalance",
        "balance",
        "cash",
    ):
        if k in data:
            try:
                v = data[k]
                if isinstance(v, dict) and "value" in v:
                    return float(v["value"])
                return float(v)
            except (TypeError, ValueError):
                continue
    # Nested portfolio / balances list
    for nest in ("balances", "portfolio", "account"):
        nested_raw = data.get(nest)
        if isinstance(nested_raw, dict | list):
            nested = _extract_balance(nested_raw)
            if nested is not None:
                return nested
    return None
