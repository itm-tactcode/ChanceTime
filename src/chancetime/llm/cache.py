"""Simple in-memory + optional file cache for LLM responses."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from chancetime.utils.logging import get_logger

log = get_logger(__name__)


class LLMCache:
    """TTL cache for LLM results keyed by prompt+model hash."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 3600.0,
        max_entries: int = 256,
        disk_dir: Path | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._mem: dict[str, tuple[float, Any]] = {}
        self.disk_dir = disk_dir
        if self.disk_dir is not None:
            self.disk_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(model: str, messages: list[dict[str, str]], **extra: Any) -> str:
        payload = json.dumps(
            {"model": model, "messages": messages, "extra": extra},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        now = time.time()
        if key in self._mem:
            expires, value = self._mem[key]
            if now < expires:
                log.debug("llm_cache_hit", layer="memory", key=key[:12])
                return value
            del self._mem[key]

        if self.disk_dir is not None:
            path = self.disk_dir / f"{key}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if data.get("expires_at", 0) > now:
                        log.debug("llm_cache_hit", layer="disk", key=key[:12])
                        self._mem[key] = (float(data["expires_at"]), data["value"])
                        return data["value"]
                    path.unlink(missing_ok=True)
                except (OSError, json.JSONDecodeError, KeyError, TypeError):
                    path.unlink(missing_ok=True)
        return None

    def set(self, key: str, value: Any) -> None:
        expires = time.time() + self.ttl_seconds
        if len(self._mem) >= self.max_entries:
            # Drop oldest by expiry
            oldest = min(self._mem.items(), key=lambda kv: kv[1][0])
            del self._mem[oldest[0]]
        self._mem[key] = (expires, value)

        if self.disk_dir is not None:
            path = self.disk_dir / f"{key}.json"
            try:
                path.write_text(
                    json.dumps({"expires_at": expires, "value": value}, default=str),
                    encoding="utf-8",
                )
            except OSError as exc:
                log.warning("llm_cache_disk_write_failed", error=str(exc))
