"""Phase 20: edge-after-cost scorecard for paper→live gate.

Answers: per strategy / family, did realized PnL beat estimated fees?
Uses SQLite book + conservative fee estimate from notional when fee not stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chancetime.persistence.store import StateStore
from chancetime.risk.families import classify_family


@dataclass
class StrategyScore:
    strategy: str
    fills: int
    closed: int
    notional_usd: float
    realized_pnl: float
    est_fees_usd: float
    edge_after_cost: float  # realized - est fees (or net if fees known)
    beat_fees: bool | None  # None if no closed trades yet


@dataclass
class FamilyScore:
    family: str
    closed: int
    realized_pnl: float
    notional_usd: float
    est_fees_usd: float
    edge_after_cost: float
    beat_fees: bool | None


@dataclass
class EdgeScorecard:
    account: str
    fee_bps: float
    strategies: list[StrategyScore] = field(default_factory=list)
    families: list[FamilyScore] = field(default_factory=list)
    total_realized: float = 0.0
    total_est_fees: float = 0.0
    total_edge_after_cost: float = 0.0
    open_positions: int = 0
    gate_ok: bool = False
    gate_notes: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            f"edge-after-cost scorecard account={self.account} fee_bps={self.fee_bps:.0f}",
            f"total realized=${self.total_realized:.2f}  est_fees=${self.total_est_fees:.2f}  "
            f"after_cost=${self.total_edge_after_cost:.2f}  open={self.open_positions}",
            f"gate={'PASS' if self.gate_ok else 'HOLD'}  "
            + ("; ".join(self.gate_notes) if self.gate_notes else "ok"),
            "strategies:",
        ]
        for s in self.strategies:
            flag = (
                "beat"
                if s.beat_fees is True
                else ("miss" if s.beat_fees is False else "open")
            )
            lines.append(
                f"  [{flag}] {s.strategy}: closed={s.closed} fills={s.fills} "
                f"pnl=${s.realized_pnl:.2f} fees≈${s.est_fees_usd:.2f} "
                f"after=${s.edge_after_cost:.2f}"
            )
        if self.families:
            lines.append("families:")
            for f in self.families:
                flag = (
                    "beat"
                    if f.beat_fees is True
                    else ("miss" if f.beat_fees is False else "open")
                )
                lines.append(
                    f"  [{flag}] {f.family}: closed={f.closed} "
                    f"pnl=${f.realized_pnl:.2f} after=${f.edge_after_cost:.2f}"
                )
        return lines


def _est_fee(notional: float, fee_bps: float) -> float:
    return abs(notional) * (fee_bps / 10_000.0)


def build_edge_scorecard(
    store: StateStore,
    *,
    account: str = "paper",
    fee_bps: float = 70.0,
    min_closed_for_gate: int = 5,
    min_edge_after_cost: float = 0.0,
) -> EdgeScorecard:
    """Build scorecard from strategy_stats + closed trades + fills."""
    summary = store.summary() if store.enabled else {}
    open_n = int(summary.get("open_positions") or 0)
    stats = store.list_strategy_stats() if store.enabled else []
    closed = store.list_closed(limit=10_000) if store.enabled else []
    fills = store.list_fills(limit=10_000) if store.enabled else []

    # Notional by strategy from fills
    notional_by: dict[str, float] = {}
    for f in fills:
        name = str(f.get("strategy") or "unknown") or "unknown"
        fee = float(f.get("fee_usd") or 0)
        size = float(f.get("size_usd") or 0)
        notional_by[name] = notional_by.get(name, 0.0) + size
        # accumulate known fees separately via size if fee_usd present
        if fee > 0:
            notional_by.setdefault(f"__fee__{name}", 0.0)
            notional_by[f"__fee__{name}"] = notional_by.get(f"__fee__{name}", 0.0) + fee

    strategies: list[StrategyScore] = []
    for row in stats:
        name = str(row.get("strategy") or "unknown")
        notional = float(row.get("fill_notional_usd") or notional_by.get(name, 0) or 0)
        known_fee = float(notional_by.get(f"__fee__{name}", 0) or 0)
        est_fees = known_fee if known_fee > 0 else _est_fee(notional, fee_bps)
        realized = float(row.get("realized_pnl") or 0)
        closed_n = int(row.get("closed_trades") or 0)
        after = realized - est_fees
        beat: bool | None
        if closed_n <= 0:
            beat = None
        else:
            beat = after > min_edge_after_cost
        strategies.append(
            StrategyScore(
                strategy=name,
                fills=int(row.get("fills") or 0),
                closed=closed_n,
                notional_usd=round(notional, 4),
                realized_pnl=round(realized, 4),
                est_fees_usd=round(est_fees, 4),
                edge_after_cost=round(after, 4),
                beat_fees=beat,
            )
        )
    strategies.sort(key=lambda s: s.edge_after_cost, reverse=True)

    # Family scores from closed trades + title/id classification
    fam_pnl: dict[str, float] = {}
    fam_n: dict[str, int] = {}
    fam_notional: dict[str, float] = {}
    for c in closed:
        mid = str(c.get("market_id") or "")
        title = str(c.get("title") or mid)
        fam = classify_family(title, market_id=mid).value
        pnl = float(c.get("realized_pnl") or 0)
        size = float(c.get("size_usd") or 0)
        fam_pnl[fam] = fam_pnl.get(fam, 0.0) + pnl
        fam_n[fam] = fam_n.get(fam, 0) + 1
        fam_notional[fam] = fam_notional.get(fam, 0.0) + size

    families: list[FamilyScore] = []
    for fam, pnl in sorted(fam_pnl.items(), key=lambda x: -x[1]):
        notional = fam_notional.get(fam, 0.0)
        est = _est_fee(notional, fee_bps)
        after = pnl - est
        n = fam_n.get(fam, 0)
        families.append(
            FamilyScore(
                family=fam,
                closed=n,
                realized_pnl=round(pnl, 4),
                notional_usd=round(notional, 4),
                est_fees_usd=round(est, 4),
                edge_after_cost=round(after, 4),
                beat_fees=(after > min_edge_after_cost) if n > 0 else None,
            )
        )

    total_realized = sum(s.realized_pnl for s in strategies)
    total_fees = sum(s.est_fees_usd for s in strategies)
    total_after = total_realized - total_fees

    total_closed = sum(s.closed for s in strategies)
    notes: list[str] = []
    if total_closed < min_closed_for_gate:
        notes.append(f"need ≥{min_closed_for_gate} closed trades (have {total_closed})")
    if total_after <= min_edge_after_cost:
        notes.append(f"after_cost ${total_after:.2f} ≤ gate ${min_edge_after_cost:.2f}")
    losers = [s.strategy for s in strategies if s.beat_fees is False and s.closed >= 3]
    if losers:
        notes.append(f"strategies missing fees: {', '.join(losers)}")
    gate_ok = total_closed >= min_closed_for_gate and total_after > min_edge_after_cost

    return EdgeScorecard(
        account=account,
        fee_bps=fee_bps,
        strategies=strategies,
        families=families,
        total_realized=round(total_realized, 4),
        total_est_fees=round(total_fees, 4),
        total_edge_after_cost=round(total_after, 4),
        open_positions=open_n,
        gate_ok=gate_ok,
        gate_notes=notes or (["edge after cost positive"] if gate_ok else []),
    )


def scorecard_to_dict(card: EdgeScorecard) -> dict[str, Any]:
    return {
        "account": card.account,
        "fee_bps": card.fee_bps,
        "total_realized": card.total_realized,
        "total_est_fees": card.total_est_fees,
        "total_edge_after_cost": card.total_edge_after_cost,
        "open_positions": card.open_positions,
        "gate_ok": card.gate_ok,
        "gate_notes": card.gate_notes,
        "strategies": [
            {
                "strategy": s.strategy,
                "fills": s.fills,
                "closed": s.closed,
                "notional_usd": s.notional_usd,
                "realized_pnl": s.realized_pnl,
                "est_fees_usd": s.est_fees_usd,
                "edge_after_cost": s.edge_after_cost,
                "beat_fees": s.beat_fees,
            }
            for s in card.strategies
        ],
        "families": [
            {
                "family": f.family,
                "closed": f.closed,
                "realized_pnl": f.realized_pnl,
                "notional_usd": f.notional_usd,
                "est_fees_usd": f.est_fees_usd,
                "edge_after_cost": f.edge_after_cost,
                "beat_fees": f.beat_fees,
            }
            for f in card.families
        ],
        "text": "\n".join(card.summary_lines()),
    }
