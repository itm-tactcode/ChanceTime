"""Presets + stats suggestions (live readiness)."""

from __future__ import annotations

import time
from pathlib import Path

from chancetime.persistence.store import StateStore
from chancetime.utils.presets import apply_preset, get_preset, list_presets
from chancetime.utils.suggest import suggest_from_store, suggestions_to_dict
from chancetime.utils.user_knobs import load_user_overrides_file


def test_list_and_get_preset() -> None:
    names = {p["name"] for p in list_presets()}
    assert "conservative_paper" in names
    p = get_preset("research_shadow")
    assert p["bot"]["shadow_mode"] is True


def test_apply_preset(tmp_path: Path) -> None:
    result = apply_preset("conservative_paper", root=tmp_path)
    assert Path(result["path"]).is_file()
    raw = load_user_overrides_file(root=tmp_path)
    assert raw["strategies"]["simple_edge"]["enabled"] is True
    assert raw["llm"]["enabled"] is False


def test_suggest_cold_strategy(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = StateStore(db, enabled=True)
    # Inject cold stats
    store.record_strategy_fill("bad_strat", size_usd=10.0)
    for _ in range(5):
        store.record_strategy_fill("bad_strat", size_usd=10.0)
    for _ in range(5):
        store.record_strategy_close("bad_strat", realized_pnl=-3.0)
    items = suggest_from_store(store, account="t", cold_min_fills=5, cold_max_realized=-10.0)
    # 5 closes * -3 = -15
    ids = {s.id for s in items}
    assert any(i.startswith("cold_") for i in ids) or any(
        "Cold" in s.title for s in items
    )
    d = suggestions_to_dict(items)
    assert isinstance(d, list)
    store.close()
