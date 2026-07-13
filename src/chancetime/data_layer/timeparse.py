"""Parse venue close / expiration timestamps into aware UTC datetimes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_close_time(*candidates: object) -> datetime | None:
    """Best-effort parse of ISO / epoch close times from venue payloads."""
    for raw in candidates:
        if raw is None or raw == "":
            continue
        if isinstance(raw, (int, float)):
            ts = float(raw)
            # ms vs s
            if ts > 1e12:
                ts = ts / 1000.0
            try:
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
        s = str(raw).strip()
        if not s:
            continue
        # numeric string
        try:
            if s.isdigit() or (s.replace(".", "", 1).isdigit() and s.count(".") < 2):
                return parse_close_time(float(s))
        except ValueError:
            pass
        # ISO-8601
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def hours_until(close: datetime | None, *, now: datetime | None = None) -> float | None:
    if close is None:
        return None
    now = now or datetime.now(timezone.utc)
    if close.tzinfo is None:
        close = close.replace(tzinfo=timezone.utc)
    return (close - now).total_seconds() / 3600.0
