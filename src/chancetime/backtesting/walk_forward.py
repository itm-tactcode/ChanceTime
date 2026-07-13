"""Walk-forward backtest harness (Phase 10).

Split bars by time into train/holdout windows; run strategy on each holdout
(optionally re-fit params on train — currently edge grid on train only).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from chancetime.backtesting.engine import BacktestEngine
from chancetime.backtesting.fees import CostModel, cost_model_for_venue
from chancetime.backtesting.models import BacktestResult, MarketBar
from chancetime.strategies.simple_edge import SimpleEdgeStrategy


@dataclass
class WalkForwardFold:
    fold: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_bars: int
    test_bars: int
    best_edge: float
    train_pnl: float
    test_result: BacktestResult


@dataclass
class WalkForwardReport:
    folds: list[WalkForwardFold]
    mean_test_pnl: float
    mean_test_hit_rate: float

    def summary_lines(self) -> list[str]:
        lines = [
            f"walk_forward folds={len(self.folds)} "
            f"mean_test_pnl=${self.mean_test_pnl:.2f} "
            f"mean_hit_rate={self.mean_test_hit_rate:.3f}",
        ]
        for f in self.folds:
            lines.append(
                f"  fold={f.fold} edge={f.best_edge:.3f} "
                f"train_pnl=${f.train_pnl:.2f} "
                f"test_pnl=${f.test_result.realized_pnl:.2f} "
                f"test_hr={f.test_result.hit_rate:.3f} "
                f"bars_train={f.train_bars} bars_test={f.test_bars}"
            )
        return lines


def split_time_folds(
    bars: list[MarketBar],
    *,
    n_folds: int = 3,
    train_ratio: float = 0.7,
) -> list[tuple[list[MarketBar], list[MarketBar]]]:
    """Contiguous time folds: for each fold, earlier train → later test.

    Divides the full timeline into n_folds sequential test windows; train is
    all bars strictly before each test window (requires enough history).
    """
    if not bars or n_folds < 1:
        return []
    timeline = sorted({b.ts for b in bars})
    if len(timeline) < n_folds + 2:
        # Fall back: single split
        cut = max(1, int(len(timeline) * train_ratio))
        t_cut = timeline[cut]
        train = [b for b in bars if b.ts < t_cut]
        test = [b for b in bars if b.ts >= t_cut]
        return [(train, test)] if train and test else []

    folds: list[tuple[list[MarketBar], list[MarketBar]]] = []
    # Reserve first train_ratio of time as pure train for fold 0 base
    base_cut = max(1, int(len(timeline) * train_ratio))
    test_region = timeline[base_cut:]
    if not test_region:
        return []
    chunk = max(1, len(test_region) // n_folds)
    for i in range(n_folds):
        start_i = i * chunk
        end_i = len(test_region) if i == n_folds - 1 else (i + 1) * chunk
        if start_i >= len(test_region):
            break
        t0 = test_region[start_i]
        t1 = test_region[min(end_i, len(test_region) - 1)]
        train = [b for b in bars if b.ts < t0]
        test = [b for b in bars if t0 <= b.ts <= t1]
        if train and test:
            folds.append((train, test))
    return folds


async def walk_forward_simple_edge(
    bars: list[MarketBar],
    *,
    edge_grid: list[float] | None = None,
    n_folds: int = 3,
    starting_cash: float = 1_000.0,
    order_size_usd: float = 10.0,
    costs: CostModel | None = None,
    venue: str | None = None,
) -> WalkForwardReport:
    """Pick best edge on train (by realized pnl), evaluate on holdout."""
    edges = edge_grid or [0.05, 0.08, 0.12]
    model = costs or (
        cost_model_for_venue(venue) if venue else CostModel()
    )
    folds_out: list[WalkForwardFold] = []
    splits = split_time_folds(bars, n_folds=n_folds)
    for i, (train, test) in enumerate(splits):
        best_edge = edges[0]
        best_train_pnl = float("-inf")
        for e in edges:
            eng = BacktestEngine(
                starting_cash=starting_cash,
                order_size_usd=order_size_usd,
                costs=model,
            )
            strat = SimpleEdgeStrategy(edge_threshold=e, min_liquidity_usd=0.0)
            res = await eng.run(train, strat, params={"edge_threshold": e})
            if res.realized_pnl > best_train_pnl:
                best_train_pnl = res.realized_pnl
                best_edge = e
        eng_t = BacktestEngine(
            starting_cash=starting_cash,
            order_size_usd=order_size_usd,
            costs=model,
        )
        test_res = await eng_t.run(
            test,
            SimpleEdgeStrategy(edge_threshold=best_edge, min_liquidity_usd=0.0),
            params={"edge_threshold": best_edge, "fold": i},
        )
        folds_out.append(
            WalkForwardFold(
                fold=i,
                train_start=train[0].ts,
                train_end=train[-1].ts,
                test_start=test[0].ts,
                test_end=test[-1].ts,
                train_bars=len(train),
                test_bars=len(test),
                best_edge=best_edge,
                train_pnl=best_train_pnl,
                test_result=test_res,
            )
        )
    if not folds_out:
        return WalkForwardReport(folds=[], mean_test_pnl=0.0, mean_test_hit_rate=0.0)
    mean_pnl = sum(f.test_result.realized_pnl for f in folds_out) / len(folds_out)
    mean_hr = sum(f.test_result.hit_rate for f in folds_out) / len(folds_out)
    return WalkForwardReport(
        folds=folds_out,
        mean_test_pnl=mean_pnl,
        mean_test_hit_rate=mean_hr,
    )


def report_to_dict(report: WalkForwardReport) -> dict[str, Any]:
    return {
        "mean_test_pnl": report.mean_test_pnl,
        "mean_test_hit_rate": report.mean_test_hit_rate,
        "folds": [
            {
                "fold": f.fold,
                "best_edge": f.best_edge,
                "train_pnl": f.train_pnl,
                "test_pnl": f.test_result.realized_pnl,
                "test_hit_rate": f.test_result.hit_rate,
                "train_bars": f.train_bars,
                "test_bars": f.test_bars,
            }
            for f in report.folds
        ],
    }
