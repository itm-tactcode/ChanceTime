"""Classical ML edge stub (Phase 7).

Loads an optional joblib artifact if present; otherwise stays silent.
Train offline later via CLI (not continuous SGD in the poll loop).

Install: ``uv sync --extra ml`` (scikit-learn + joblib).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chancetime.data_layer.models import Market
from chancetime.strategies.base import BaseStrategy, Side, Signal
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root, resolve_path

log = get_logger(__name__)


class MLEdgeStrategy(BaseStrategy):
    name = "ml_edge"

    def __init__(
        self,
        *,
        model_path: str = "models/ml_edge.joblib",
        edge_threshold: float = 0.05,
        min_liquidity_usd: float = 100.0,
        enabled: bool = False,
        weight: float = 1.0,
        **params: object,
    ) -> None:
        super().__init__(
            model_path=model_path,
            edge_threshold=edge_threshold,
            min_liquidity_usd=min_liquidity_usd,
            enabled=enabled,
            weight=weight,
            **params,
        )
        self.model_path = model_path
        self.edge_threshold = edge_threshold
        self.min_liquidity_usd = min_liquidity_usd
        self.weight = weight
        self._model: Any = None
        self._load_attempted = False

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True
        path = resolve_path(self.model_path)
        if not path.is_file():
            # also try under project root models/
            alt = project_root() / self.model_path
            path = alt if alt.is_file() else path
        if not path.is_file():
            log.info("ml_edge_no_model", path=str(path), msg="train offline later")
            return False
        try:
            import joblib
        except ImportError:
            log.warning("ml_edge_joblib_missing", msg="uv sync --extra ml")
            return False
        try:
            self._model = joblib.load(path)
            log.info("ml_edge_model_loaded", path=str(path))
            return True
        except Exception:
            log.exception("ml_edge_load_failed", path=str(path))
            return False

    def _features(self, m: Market) -> list[float]:
        # Keep stable order for any future sklearn pipeline
        return [
            m.yes_price,
            m.no_price,
            m.liquidity_usd,
            m.volume_usd,
            1.0 if m.has_bbo else 0.0,
            m.yes_bid if m.yes_bid is not None else m.yes_price,
            m.yes_ask if m.yes_ask is not None else m.yes_price,
        ]

    def _predict_fair(self, m: Market) -> float | None:
        if not self._ensure_model():
            return None
        x = [self._features(m)]
        model = self._model
        # Support raw estimator or {"pipeline": ...} artifact from train_ml_edge_from_csv
        if isinstance(model, dict) and "pipeline" in model:
            model = model["pipeline"]
        try:
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(x)[0]
                # assume class 1 = YES
                return float(proba[1] if len(proba) > 1 else proba[0])
            pred = model.predict(x)[0]
            return float(pred)
        except Exception:
            log.exception("ml_edge_predict_failed", market_id=m.id)
            return None

    async def generate_signals(self, markets: list[Market]) -> list[Signal]:
        if not self.enabled:
            return []
        signals: list[Signal] = []
        for m in markets:
            if m.liquidity_usd < self.min_liquidity_usd:
                continue
            fair = self._predict_fair(m)
            if fair is None:
                continue
            fair = max(0.01, min(0.99, fair))
            edge = fair - m.yes_price
            if abs(edge) < self.edge_threshold:
                continue
            side = Side.YES if edge > 0 else Side.NO
            strength = min(1.0, abs(edge) / max(self.edge_threshold * 2, 1e-9))
            signals.append(
                Signal(
                    market_id=m.id,
                    platform=str(m.platform),
                    side=side,
                    strength=strength,
                    edge=edge,
                    fair_prob=fair,
                    market_prob=m.yes_price,
                    reason=f"ml_edge fair={fair:.3f} mkt={m.yes_price:.3f} edge={edge:.3f}",
                    metadata={"strategy": self.name, "model_path": self.model_path},
                )
            )
        return signals


def model_path_exists(path: str = "models/ml_edge.joblib") -> bool:
    p = Path(path)
    if not p.is_absolute():
        p = project_root() / p
    return p.is_file()
