"""Stats-based setting suggestions (not auto-applied). Phase 15.

Uses strategy_stats + closed trades in a book. No LLM authority on size/edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chancetime.persistence.store import StateStore


@dataclass
class Suggestion:
    id: str
    severity: str  # info | warn | action
    title: str
    detail: str
    # Optional nested user.yaml patch if user clicks Apply
    patch: dict[str, Any] = field(default_factory=dict)


def suggest_from_store(
    store: StateStore,
    *,
    account: str = "paper",
    min_fills_for_weight: int = 5,
    cold_min_fills: int = 5,
    cold_max_realized: float = -10.0,
) -> list[Suggestion]:
    """Return ordered suggestions from SQLite strategy_stats / summary."""
    out: list[Suggestion] = []
    if not store.enabled:
        return [
            Suggestion(
                id="no_store",
                severity="warn",
                title="No persistence",
                detail="Enable persistence to accumulate stats for suggestions.",
            )
        ]

    summary = store.summary()
    stats = store.list_strategy_stats()
    open_n = int(summary.get("open_positions") or 0)
    fills_total = int(summary.get("fills_total") or 0)
    le = summary.get("last_equity") or {}
    exposure = float(le.get("exposure_usd") or 0.0)

    # Effective knobs from default.yaml + user.yaml (never hard-coded 10 / .env comments)
    max_open = 10
    family_cap = 50.0
    max_per_strat = 8
    min_net = 0.02
    try:
        from chancetime.utils.user_knobs import snapshot_user_knobs

        knobs = snapshot_user_knobs()
        max_open = int(knobs.get("max_open_positions") if knobs.get("max_open_positions") is not None else max_open)
        family_cap = float(
            knobs.get("max_family_exposure_usd")
            if knobs.get("max_family_exposure_usd") is not None
            else family_cap
        )
        max_per_strat = int(
            knobs.get("max_open_per_strategy")
            if knobs.get("max_open_per_strategy") is not None
            else max_per_strat
        )
        min_net = float(knobs.get("min_net_edge") if knobs.get("min_net_edge") is not None else min_net)
    except Exception:
        pass

    if open_n > 0 and open_n >= max_open:
        out.append(
            Suggestion(
                id="bag_full",
                severity="action",
                title="Position bag full — all new signals miss",
                detail=(
                    f"{open_n}/{max_open} open positions (effective max_open_positions "
                    f"from user.yaml+default.yaml). Close positions, raise max_open, "
                    "or clear-book. Restart bot after changing YAML."
                ),
                patch={"risk": {"max_open_positions": max(open_n + 3, max_open + 3)}},
            )
        )
    elif open_n > 0 and open_n < max_open:
        out.append(
            Suggestion(
                id="room_but_no_entries",
                severity="info",
                title=f"Room in bag ({open_n}/{max_open}) — misses are not bag-full",
                detail=(
                    "Bot can still open new markets. Common blocks: already_open "
                    f"(same markets re-signaled), max_open_per_strategy={max_per_strat}, "
                    f"min_net_edge={min_net} after spread/fees, family/cash/mid band."
                ),
                patch={},
            )
        )

    if open_n >= 3 and exposure >= family_cap * 0.9:
        out.append(
            Suggestion(
                id="family_cap_tight",
                severity="warn",
                title="Family exposure budget may be capping new entries",
                detail=(
                    f"{open_n} open, ~${exposure:.0f} exposure vs "
                    f"max_family_exposure_usd=${family_cap:.0f}. "
                    "Raising max_open alone won't help if family budget is full "
                    "(many markets share sports/crypto/other buckets)."
                ),
                patch={"risk": {"max_family_exposure_usd": round(family_cap * 1.5, 0)}},
            )
        )

    if fills_total < 5:
        out.append(
            Suggestion(
                id="need_fills",
                severity="info",
                title="Need more paper fills",
                detail=(
                    f"Only {fills_total} fills in this book. Run paper_bag or "
                    "conservative_paper longer before trusting suggestions."
                ),
                patch={},
            )
        )

    for row in stats:
        name = str(row.get("strategy") or "")
        if not name:
            continue
        fills = int(row.get("fills") or 0)
        realized = float(row.get("realized_pnl") or 0)
        signals = int(row.get("signals") or 0)

        if fills >= cold_min_fills and realized <= cold_max_realized:
            out.append(
                Suggestion(
                    id=f"cold_{name}",
                    severity="action",
                    title=f"Cold strategy: {name}",
                    detail=(
                        f"{fills} fills, realized_pnl={realized:.2f} ≤ {cold_max_realized}. "
                        "Suggest disable until reviewed."
                    ),
                    patch={"strategies": {name: {"enabled": False, "weight": 0.0}}},
                )
            )
        elif fills >= min_fills_for_weight and realized > 2.0:
            out.append(
                Suggestion(
                    id=f"boost_{name}",
                    severity="info",
                    title=f"Positive track: {name}",
                    detail=(
                        f"{fills} fills, realized_pnl={realized:.2f}. "
                        "Optional weight bump (still paper-first)."
                    ),
                    patch={"strategies": {name: {"enabled": True, "weight": 1.2}}},
                )
            )
        elif signals >= 20 and fills == 0:
            out.append(
                Suggestion(
                    id=f"no_fill_{name}",
                    severity="warn",
                    title=f"Signals but no fills: {name}",
                    detail=(
                        f"{signals} signals, 0 fills — edge may be too tight or "
                        "risk blocking. Consider lower edge_threshold slightly in paper."
                    ),
                    patch={
                        "strategies": {
                            name: {"enabled": True, "edge_threshold": 0.06},
                        }
                    }
                    if name == "simple_edge"
                    else {"strategies": {name: {"enabled": True}}},
                )
            )

    # History nudge
    out.append(
        Suggestion(
            id="history_on",
            severity="info",
            title="Keep history recording on",
            detail="Enable history for walk-forward / backtest --history gates.",
            patch={"history": {"enabled": True}},
        )
    )

    if not any(s.severity == "action" for s in out) and fills_total >= 20:
        out.insert(
            0,
            Suggestion(
                id="ready_hint",
                severity="info",
                title="Stats look active",
                detail=(
                    f"Book {account} has {fills_total} fills. "
                    "Run walk-forward + digest week before live micro."
                ),
            ),
        )

    return out


def suggestions_to_dict(items: list[Suggestion]) -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "severity": s.severity,
            "title": s.title,
            "detail": s.detail,
            "patch": s.patch,
        }
        for s in items
    ]


def merge_suggestion_patches(items: list[Suggestion]) -> dict[str, Any]:
    """Deep-merge all patches (later overwrites) for bulk apply."""
    from chancetime.utils.config import deep_merge

    acc: dict[str, Any] = {}
    for s in items:
        if s.patch:
            acc = deep_merge(acc, s.patch)
    return acc
