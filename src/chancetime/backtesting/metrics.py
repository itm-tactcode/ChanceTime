"""Performance metrics from equity curve and settlements."""

from __future__ import annotations

from chancetime.backtesting.models import EquityPoint, SimSettlement


def max_drawdown(equity_curve: list[EquityPoint]) -> float:
    """Max peak-to-trough drawdown as a fraction of peak (0-1)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0].equity
    max_dd = 0.0
    for pt in equity_curve:
        peak = max(peak, pt.equity)
        if peak > 0:
            dd = (peak - pt.equity) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def hit_rate(settlements: list[SimSettlement]) -> tuple[int, int, float]:
    """Return (wins, losses, hit_rate). Flat PnL counts as neither."""
    wins = sum(1 for s in settlements if s.pnl_usd > 0)
    losses = sum(1 for s in settlements if s.pnl_usd < 0)
    n = wins + losses
    rate = (wins / n) if n else 0.0
    return wins, losses, rate
