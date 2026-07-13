"""Train a tiny logistic model for ``ml_edge`` from resolved bar CSV.

Feature layout must match ``MLEdgeStrategy._features``:
  yes_price, no_price, liquidity, volume, has_bbo, yes_bid, yes_ask

Labels: final ``resolve`` column (1=YES, 0=NO) applied to all bars of that market.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chancetime.backtesting.loader import load_bars_csv
from chancetime.backtesting.models import ResolveOutcome
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root, resolve_path

log = get_logger(__name__)


@dataclass
class TrainResult:
    model_path: Path
    n_samples: int
    n_markets: int
    train_accuracy: float
    walk_forward_accuracy: float | None = None
    note: str = ""


def _features_row(
    yes: float,
    liquidity: float,
    volume: float = 0.0,
) -> list[float]:
    no = max(0.0, min(1.0, 1.0 - yes))
    # Match ml_edge._features without Market object
    return [yes, no, liquidity, volume or liquidity, 0.0, yes, yes]


def train_ml_edge_from_csv(
    fixture: str | Path,
    *,
    out_path: str | Path = "models/ml_edge.joblib",
    min_samples: int = 6,
    walk_forward: bool = True,
) -> TrainResult:
    """Fit LogisticRegression on fixture bars labeled by market resolution.

    When ``walk_forward`` is True, also report accuracy on a time-ordered holdout
    (last ~25% of samples by bar order) so we don't only quote in-sample fit.
    """
    try:
        import joblib
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise ImportError("ML deps missing. Install with: uv sync --extra ml") from exc

    bars = load_bars_csv(fixture)
    # Map market_id -> final resolve
    labels: dict[str, int] = {}
    for b in bars:
        if b.resolve == ResolveOutcome.YES:
            labels[b.market_id] = 1
        elif b.resolve == ResolveOutcome.NO:
            labels[b.market_id] = 0

    x_rows: list[list[float]] = []
    y_rows: list[int] = []
    for b in bars:
        if b.market_id not in labels:
            continue
        # Prefer pre-resolution bars only
        if b.resolve != ResolveOutcome.OPEN:
            continue
        x_rows.append(_features_row(b.yes_price, b.liquidity_usd, b.liquidity_usd))
        y_rows.append(labels[b.market_id])

    if len(x_rows) < min_samples:
        raise ValueError(
            f"Need >={min_samples} labeled open bars; got {len(x_rows)}. "
            "Add resolve=0/1 on final rows per market in the CSV."
        )
    if len(set(y_rows)) < 2:
        raise ValueError("Need both YES and NO resolved markets in the fixture.")

    def _pipe() -> Any:
        return Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(max_iter=500, class_weight="balanced"),
                ),
            ]
        )

    wf_acc: float | None = None
    note = "full-sample fit"
    if walk_forward and len(x_rows) >= 8:
        cut = max(min_samples, int(len(x_rows) * 0.75))
        if cut < len(x_rows) and len(set(y_rows[:cut])) >= 2 and len(set(y_rows[cut:])) >= 1:
            wf = _pipe()
            wf.fit(x_rows[:cut], y_rows[:cut])
            wf_acc = float(wf.score(x_rows[cut:], y_rows[cut:]))
            note = (
                f"walk-forward holdout acc={wf_acc:.3f} "
                f"(train n={cut}, test n={len(x_rows) - cut}); full-sample model saved"
            )

    pipe = _pipe()
    pipe.fit(x_rows, y_rows)
    acc = float(pipe.score(x_rows, y_rows))

    out = resolve_path(out_path)
    if not out.is_absolute():
        out = project_root() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "pipeline": pipe,
            "feature_names": [
                "yes_price",
                "no_price",
                "liquidity_usd",
                "volume_usd",
                "has_bbo",
                "yes_bid",
                "yes_ask",
            ],
            "version": 2,
            "walk_forward_accuracy": wf_acc,
            "train_accuracy": acc,
        },
        out,
    )
    log.info(
        "ml_edge_trained",
        path=str(out),
        n_samples=len(x_rows),
        n_markets=len(labels),
        train_accuracy=round(acc, 4),
        walk_forward_accuracy=None if wf_acc is None else round(wf_acc, 4),
    )
    return TrainResult(
        model_path=out,
        n_samples=len(x_rows),
        n_markets=len(labels),
        train_accuracy=acc,
        walk_forward_accuracy=wf_acc,
        note=note,
    )
