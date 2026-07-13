"""Local persistence (SQLite by default)."""

from chancetime.persistence.live_book import persist_live_result
from chancetime.persistence.store import StateStore

__all__ = ["StateStore", "persist_live_result"]
