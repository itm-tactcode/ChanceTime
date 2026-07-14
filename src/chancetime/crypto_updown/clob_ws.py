"""Optional Polymarket CLOB market WebSocket (Phase 28 optional).

Docs: https://docs.polymarket.com/ — market channel for book updates.
Uses aiohttp WS; fail-closed (falls back to REST if disconnect).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Awaitable

import aiohttp

from chancetime.utils.logging import get_logger

log = get_logger(__name__)

CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

BookHandler = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class ClobMarketWs:
    """Subscribe to token_ids; invoke handler on book messages."""

    def __init__(
        self,
        *,
        url: str = CLOB_WS_URL,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.url = url
        self._session = session
        self._owns = session is None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._books: dict[str, dict[str, Any]] = {}
        self._handler: BookHandler | None = None

    @property
    def books(self) -> dict[str, dict[str, Any]]:
        return dict(self._books)

    def get_book(self, token_id: str) -> dict[str, Any] | None:
        return self._books.get(token_id)

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def start(
        self,
        token_ids: list[str],
        *,
        handler: BookHandler | None = None,
    ) -> None:
        """Connect and subscribe. No-op if empty token list."""
        ids = [t for t in token_ids if t]
        if not ids:
            return
        self._handler = handler
        self._stop.clear()
        session = await self._sess()
        try:
            self._ws = await session.ws_connect(self.url, heartbeat=20)
            # Polymarket market channel subscription shape
            sub = {"assets_ids": ids, "type": "market"}
            await self._ws.send_str(json.dumps(sub))
            log.info("clob_ws_subscribed", n=len(ids))
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            log.warning("clob_ws_connect_failed", error=str(exc))
            self._ws = None
            return
        self._task = asyncio.create_task(self._read_loop(), name="clob_ws_read")

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if self._stop.is_set():
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_text(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("clob_ws_read_error", error=str(exc))
        finally:
            log.info("clob_ws_read_stop")

    async def _handle_text(self, data: str) -> None:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return
        # Messages may be list or dict; normalize
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            # common fields: asset_id / market / price_changes / bids / asks
            tid = str(
                item.get("asset_id")
                or item.get("asset")
                or item.get("market")
                or ""
            )
            if not tid:
                continue
            self._books[tid] = item
            if self._handler:
                try:
                    res = self._handler(tid, item)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:  # noqa: BLE001
                    log.debug("clob_ws_handler_error", error=str(exc))

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
            self._ws = None
        if self._owns and self._session is not None:
            await self._session.close()
            self._session = None
