"""Cross-module signal bus (C → D and future consumers).

Modules stay separate for execution. Signals are append-only JSONL under
``data/research/signals/`` — fail-closed consumers ignore stale/missing fields.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from chancetime.utils.paths import project_root

Direction = Literal["up", "down", "flat"]


class ImpliedDirectionSignal(BaseModel):
    """Path C (or others) publishes; Path D may consume for paper/live routing."""

    signal_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    source: str = "crypto_updown"
    asset: str
    direction: Direction
    p_up: float | None = None
    confidence: float = 0.0  # 0–1 rough strength
    window_end_ts: float | None = None
    seconds_remaining: float | None = None
    reference_price: float | None = None  # window open / settle ref when known
    spot: float | None = None
    slug: str | None = None
    complete_set_sum: float | None = None
    up_ask: float | None = None
    down_ask: float | None = None
    note: str = ""
    ts: float = Field(default_factory=time.time)

    def is_actionable(
        self,
        *,
        min_confidence: float = 0.55,
        max_age_sec: float = 120.0,
        now: float | None = None,
    ) -> bool:
        t = now if now is not None else time.time()
        if self.direction == "flat":
            return False
        if self.confidence < min_confidence:
            return False
        if self.spot is None or self.spot <= 0:
            return False
        if t - self.ts > max_age_sec:
            return False
        return True


def signals_dir() -> Path:
    d = project_root() / "data" / "research" / "signals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def publish_signals(signals: list[ImpliedDirectionSignal]) -> Path | None:
    """Append signals to daily JSONL. Returns path written or None if empty."""
    if not signals:
        return None
    day = time.strftime("%Y%m%d", time.gmtime())
    path = signals_dir() / f"direction-{day}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for s in signals:
            f.write(s.model_dump_json() + "\n")
    # Latest snapshot for cheap consumers (overwrite)
    latest = signals_dir() / "latest.jsonl"
    with latest.open("w", encoding="utf-8") as f:
        for s in signals:
            f.write(s.model_dump_json() + "\n")
    return path


def load_latest_signals(
    *,
    max_age_sec: float = 300.0,
    now: float | None = None,
) -> list[ImpliedDirectionSignal]:
    """Read ``latest.jsonl``; drop stale rows. Empty if missing."""
    path = signals_dir() / "latest.jsonl"
    if not path.is_file():
        return []
    t = now if now is not None else time.time()
    out: list[ImpliedDirectionSignal] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw: dict[str, Any] = json.loads(line)
            sig = ImpliedDirectionSignal.model_validate(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if t - sig.ts > max_age_sec:
            continue
        out.append(sig)
    return out


def build_direction_from_book(
    *,
    asset: str,
    slug: str,
    up_mid: float | None,
    down_mid: float | None,
    up_ask: float | None,
    down_ask: float | None,
    spot: float | None,
    seconds_remaining: float | None,
    window_end_ts: float | None,
    complete_set_sum: float | None,
    reference_price: float | None = None,
    edge_threshold: float = 0.08,
) -> ImpliedDirectionSignal | None:
    """Infer direction from CLOB mids/asks (+ optional spot vs window ref)."""
    p_up: float | None = None
    if up_mid is not None and 0.0 < up_mid < 1.0:
        p_up = float(up_mid)
    elif up_ask is not None and down_ask is not None:
        total = up_ask + down_ask
        if total > 0:
            # Normalize asks into soft probs when mids missing
            p_up = 1.0 - (up_ask / total)  # cheaper up ask → higher p_up proxy
            p_up = max(0.01, min(0.99, p_up))

    if p_up is None:
        return None

    # Distance from fair coin
    lean = p_up - 0.5
    notes: list[str] = ["implied_from_clob"]
    if abs(lean) < edge_threshold:
        direction: Direction = "flat"
        confidence = abs(lean) / edge_threshold * 0.5  # weak
    elif lean > 0:
        direction = "up"
        confidence = min(1.0, abs(lean) / 0.45)
    else:
        direction = "down"
        confidence = min(1.0, abs(lean) / 0.45)

    # Spot vs window open/ref: if agrees with CLOB lean, boost; if fights, cut
    if (
        reference_price is not None
        and reference_price > 0
        and spot is not None
        and spot > 0
        and direction != "flat"
    ):
        spot_up = spot >= reference_price
        agrees = (direction == "up" and spot_up) or (direction == "down" and not spot_up)
        if agrees:
            confidence = min(1.0, confidence + 0.12)
            notes.append("spot_agrees_ref")
        else:
            confidence = max(0.0, confidence - 0.15)
            notes.append("spot_fights_ref")

    # Slightly boost confidence near expiry if strongly lean (sniping zone)
    if seconds_remaining is not None and 0 < seconds_remaining < 120 and abs(lean) >= 0.15:
        confidence = min(1.0, confidence + 0.1)
        notes.append("late_window")

    return ImpliedDirectionSignal(
        source="crypto_updown",
        asset=asset.upper(),
        direction=direction,
        p_up=round(p_up, 4),
        confidence=round(confidence, 4),
        window_end_ts=window_end_ts,
        seconds_remaining=None if seconds_remaining is None else round(seconds_remaining, 2),
        reference_price=reference_price,
        spot=spot,
        slug=slug,
        complete_set_sum=complete_set_sum,
        up_ask=up_ask,
        down_ask=down_ask,
        note="+".join(notes),
    )
