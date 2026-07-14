"""Path C research scorecard — resolve-aware, multi-day paper evaluation."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from chancetime.utils.paths import project_root


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _day_paths(day: str) -> dict[str, Path]:
    root = project_root()
    cdir = root / "data" / "research" / "crypto_updown"
    sdir = root / "data" / "research" / "signals"
    return {
        "scan": cdir / f"scan-{day}.jsonl",
        "actions": cdir / f"actions-{day}.jsonl",
        "resolutions": cdir / f"resolutions-{day}.jsonl",
        "signals": sdir / f"direction-{day}.jsonl",
    }


def build_scorecard(
    day: str | None = None,
    *,
    starting_cash: float = 1000.0,
) -> dict[str, Any]:
    """Summarize Path C research logs for a UTC day (YYYYMMDD)."""
    d = day or time.strftime("%Y%m%d", time.gmtime())
    paths = _day_paths(d)
    scans = _read_jsonl(paths["scan"])
    actions = _read_jsonl(paths["actions"])
    resolutions = _read_jsonl(paths["resolutions"])
    signals = _read_jsonl(paths["signals"])

    # --- market / book stats ---
    edges = [
        float(r["complete_set_edge"])
        for r in scans
        if r.get("complete_set_edge") is not None
    ]
    model_ps = [
        float(r["model_p_up"])
        for r in scans
        if r.get("model_p_up") is not None
    ]
    dirs = Counter(str(s.get("direction")) for s in signals)

    # --- actions by phase ---
    phase_counts: Counter[str] = Counter()
    phase_buys: Counter[str] = Counter()
    for a in actions:
        ph = str(a.get("phase") or "unknown")
        phase_counts[ph] += 1
        if a.get("action") == "paper_buy":
            phase_buys[ph] += 1

    # --- resolve join: match actions/signals to resolutions by slug ---
    resolve_by_slug = {str(r["slug"]): r for r in resolutions if r.get("slug")}
    hits = 0
    misses = 0
    by_phase_hit: dict[str, list[int]] = defaultdict(list)
    for a in actions:
        if a.get("action") != "paper_buy":
            continue
        slug = str(a.get("slug") or "")
        res = resolve_by_slug.get(slug)
        if not res:
            continue
        side = str(a.get("side") or "").lower()
        resolved_up = bool(res.get("resolved_up"))
        won = (side == "up" and resolved_up) or (side == "down" and not resolved_up)
        if won:
            hits += 1
        else:
            misses += 1
        ph = str(a.get("phase") or "unknown")
        by_phase_hit[ph].append(1 if won else 0)

    phase_hit_rate = {
        ph: (sum(v) / len(v) if v else None)
        for ph, v in by_phase_hit.items()
    }

    # model calibration vs resolve (scan row near end)
    cal_buckets: dict[str, list[int]] = defaultdict(list)
    for r in scans:
        slug = str(r.get("slug") or "")
        res = resolve_by_slug.get(slug)
        mp = r.get("model_p_up")
        if res is None or mp is None:
            continue
        p = float(mp)
        bucket = f"{int(p * 10) / 10:.1f}-{int(p * 10) / 10 + 0.1:.1f}"
        cal_buckets[bucket].append(1 if res.get("resolved_up") else 0)

    calibration = {
        b: {
            "n": len(v),
            "resolve_up_rate": round(sum(v) / len(v), 4) if v else None,
        }
        for b, v in sorted(cal_buckets.items())
    }

    # paper book summary if present
    paper_book: dict[str, Any] = {}
    try:
        from chancetime.crypto_updown.store import CryptoPaperStore

        store = CryptoPaperStore()
        try:
            paper_book = store.summary()
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        paper_book = {"error": str(exc)}

    n_joined = hits + misses
    return {
        "day": d,
        "paths": {k: str(v) for k, v in paths.items()},
        "scan_rows": len(scans),
        "action_rows": len(actions),
        "signal_rows": len(signals),
        "resolutions": len(resolutions),
        "resolve_up": sum(1 for r in resolutions if r.get("resolved_up")),
        "direction_counts": dict(dirs),
        "complete_set_edges": {
            "n": len(edges),
            "positive_n": sum(1 for e in edges if e > 0),
            "mean": round(sum(edges) / len(edges), 5) if edges else None,
            "max": round(max(edges), 5) if edges else None,
        },
        "model_p_up": {
            "n": len(model_ps),
            "mean": round(sum(model_ps) / len(model_ps), 4) if model_ps else None,
        },
        "actions_by_phase": dict(phase_counts),
        "paper_buys_by_phase": dict(phase_buys),
        "resolve_joined_buys": n_joined,
        "hit_rate": round(hits / n_joined, 4) if n_joined else None,
        "hits": hits,
        "misses": misses,
        "hit_rate_by_phase": {
            k: (round(v, 4) if v is not None else None)
            for k, v in phase_hit_rate.items()
        },
        "model_calibration_vs_resolve": calibration,
        "paper_book": paper_book,
        "go_nogo": _go_nogo(
            n_joined=n_joined,
            hit_rate=(hits / n_joined if n_joined else None),
            edges_positive=sum(1 for e in edges if e > 0),
            edges_n=len(edges),
        ),
    }


def _go_nogo(
    *,
    n_joined: int,
    hit_rate: float | None,
    edges_positive: int,
    edges_n: int,
) -> dict[str, Any]:
    """Honest research gate — not a live trading recommendation."""
    reasons: list[str] = []
    if n_joined < 20:
        reasons.append(f"too few resolved paper buys joined ({n_joined} < 20)")
    if hit_rate is not None and hit_rate < 0.52:
        reasons.append(f"hit_rate {hit_rate:.2%} not clearly above coin-flip after costs")
    if edges_n and edges_positive == 0:
        reasons.append("no positive complete-set edges observed")
    status = "INSUFFICIENT_DATA" if n_joined < 20 else (
        "NO_GO" if reasons else "CANDIDATE_FOR_MORE_PAPER"
    )
    if status == "CANDIDATE_FOR_MORE_PAPER" and n_joined < 100:
        reasons.append("need multi-day volume before micro-live")
        status = "CANDIDATE_FOR_MORE_PAPER"
    return {"status": status, "reasons": reasons or ["sample looks non-negative — keep papering"]}
