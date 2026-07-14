"""Path C scorecard + kill switches unit tests."""

from __future__ import annotations

from chancetime.crypto_updown.kill_switches import KillSwitchConfig, KillSwitchState
from chancetime.crypto_updown.scorecard import build_scorecard


def test_kill_stale_spot() -> None:
    st = KillSwitchState()
    cfg = KillSwitchConfig(max_spot_age_sec=30.0, max_daily_loss_usd=50.0)
    r = st.check(spot_age_sec=90.0, spread=None, equity=1000.0, cfg=cfg)
    assert r is not None
    assert st.halted
    assert "stale" in (r or "")


def test_kill_daily_loss() -> None:
    st = KillSwitchState()
    cfg = KillSwitchConfig(max_daily_loss_usd=25.0, starting_equity=1000.0)
    st.check(spot_age_sec=1.0, spread=None, equity=1000.0, cfg=cfg)
    r = st.check(spot_age_sec=1.0, spread=None, equity=970.0, cfg=cfg)
    assert r is not None
    assert st.halted


def test_wide_spread_does_not_full_halt() -> None:
    st = KillSwitchState()
    cfg = KillSwitchConfig(max_spread=0.10)
    r = st.check(spot_age_sec=1.0, spread=0.20, equity=1000.0, cfg=cfg)
    assert r is not None and "wide_spread" in r
    assert not st.halted


def test_scorecard_shape() -> None:
    sc = build_scorecard()
    assert "day" in sc
    assert "go_nogo" in sc
    assert "hit_rate" in sc
    assert sc["go_nogo"]["status"] in {
        "INSUFFICIENT_DATA",
        "NO_GO",
        "CANDIDATE_FOR_MORE_PAPER",
    }
