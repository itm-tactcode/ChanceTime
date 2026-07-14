"""Append-only JSONL research logs (no orders).

Files under ``data/research/`` (gitignored). One line per observation.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root, resolve_path

log = get_logger(__name__)


def research_dir(directory: str | Path | None = None) -> Path:
    base = resolve_path(directory) if directory else project_root() / "data" / "research"
    base.mkdir(parents=True, exist_ok=True)
    return base


def research_path(name: str, *, directory: str | Path | None = None) -> Path:
    """``name`` without date → ``{name}-YYYYMMDD.jsonl``."""
    day = datetime.now(tz=UTC).strftime("%Y%m%d")
    stem = name if name.endswith(".jsonl") else f"{name}-{day}.jsonl"
    return research_dir(directory) / stem


def append_research(
    name: str,
    rows: list[dict[str, Any]],
    *,
    directory: str | Path | None = None,
) -> int:
    if not rows:
        return 0
    path = research_path(name, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")
            n += 1
    log.info("research_log_write", name=name, path=str(path), rows=n)
    return n


def base_fields(*, poll: int | None = None, strategy: str = "") -> dict[str, Any]:
    ts = time.time()
    return {
        "ts": ts,
        "ts_iso": datetime.fromtimestamp(ts, tz=UTC).isoformat(),
        "poll": poll,
        "strategy": strategy,
    }
