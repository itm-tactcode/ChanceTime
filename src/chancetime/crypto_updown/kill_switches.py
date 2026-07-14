"""Path C paper kill switches — halt strategy fills when conditions fail."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class KillSwitchConfig:
    max_spot_age_sec: float = 60.0
    max_spread: float = 0.15
    max_daily_loss_usd: float = 50.0
    starting_equity: float = 1000.0


@dataclass
class KillSwitchState:
    halted: bool = False
    reason: str = ""
    day_start_equity: float | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def halt(self, reason: str) -> None:
        self.halted = True
        self.reason = reason
        self.events.append({"halt": True, "reason": reason})

    def check(
        self,
        *,
        spot_age_sec: float | None,
        spread: float | None,
        equity: float,
        cfg: KillSwitchConfig,
    ) -> str | None:
        """Return halt reason or None if OK to trade."""
        if self.halted:
            return self.reason or "halted"
        if self.day_start_equity is None:
            self.day_start_equity = equity
        if spot_age_sec is not None and spot_age_sec > cfg.max_spot_age_sec:
            self.halt(f"stale_spot age={spot_age_sec:.1f}s")
            return self.reason
        if spread is not None and spread > cfg.max_spread:
            # per-trade skip, not full halt
            return f"wide_spread={spread:.3f}"
        loss = (self.day_start_equity or cfg.starting_equity) - equity
        if loss >= cfg.max_daily_loss_usd:
            self.halt(f"daily_loss={loss:.2f}>={cfg.max_daily_loss_usd}")
            return self.reason
        return None
