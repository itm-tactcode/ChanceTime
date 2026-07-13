"""Alerts: log sink always; optional Telegram Bot API."""

from __future__ import annotations

from typing import Any, Protocol

import aiohttp

from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class Alerter(Protocol):
    async def send(self, message: str, *, level: str = "info") -> None: ...


class LogAlerter:
    """Always-on structured log alerts."""

    async def send(self, message: str, *, level: str = "info") -> None:
        log_method = getattr(log, level, log.info)
        log_method("alert", message=message[:500], level=level)


class TelegramAlerter:
    """Send messages via Telegram Bot API (optional).

    Requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in env/config.
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._base = f"https://api.telegram.org/bot{bot_token}"

    async def send(self, message: str, *, level: str = "info") -> None:
        url = f"{self._base}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": f"[{level.upper()}] Chance Time\n{message[:3500]}",
            "disable_web_page_preview": True,
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session,
                session.post(url, json=payload) as resp,
            ):
                if resp.status >= 400:
                    body = await resp.text()
                    log.warning(
                        "telegram_alert_failed",
                        status=resp.status,
                        body=body[:200],
                    )
                else:
                    log.debug("telegram_alert_sent", level=level)
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            log.warning("telegram_alert_error", error=str(exc))


class MultiAlerter:
    """Fan-out to several alerters."""

    def __init__(self, sinks: list[Any]) -> None:
        self.sinks = sinks

    async def send(self, message: str, *, level: str = "info") -> None:
        for sink in self.sinks:
            try:
                await sink.send(message, level=level)
            except Exception:
                log.exception("alerter_sink_failed", sink=type(sink).__name__)


def build_alerter(
    *,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> MultiAlerter:
    sinks: list[Any] = [LogAlerter()]
    if telegram_bot_token and telegram_chat_id:
        sinks.append(TelegramAlerter(telegram_bot_token, telegram_chat_id))
        log.info("telegram_alerts_enabled")
    return MultiAlerter(sinks)
