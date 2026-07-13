"""Paper/live book separation + dashboard dual paths."""

from __future__ import annotations

from pathlib import Path

from chancetime.dashboard.app import create_app
from chancetime.persistence.store import StateStore
from chancetime.utils.config import load_config


def test_default_db_is_paper(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    # minimal: load defaults without user env files if possible
    cfg = load_config("config/default.yaml", env_file=None) if False else None
    # Use model defaults
    from chancetime.utils.config import PersistenceSettings, DashboardSettings

    p = PersistenceSettings()
    d = DashboardSettings()
    assert p.db_path.endswith("paper.db")
    assert d.paper_db_path.endswith("paper.db")
    assert d.live_db_path.endswith("live.db")
    assert p.db_path != d.live_db_path


def test_dual_books_isolated(tmp_path: Path) -> None:
    paper = tmp_path / "paper.db"
    live = tmp_path / "live.db"
    sp = StateStore(paper, enabled=True)
    sl = StateStore(live, enabled=True)
    from chancetime.execution.engine import Fill, OrderStatus
    from chancetime.strategies.base import Side
    import time

    sp.record_fill(
        Fill(
            order_id="p1",
            market_id="m-paper",
            side=Side.YES,
            price=0.5,
            size_usd=10.0,
            status=OrderStatus.FILLED,
            paper=True,
            ts=time.time(),
        )
    )
    sl.record_fill(
        Fill(
            order_id="l1",
            market_id="m-live",
            side=Side.NO,
            price=0.4,
            size_usd=5.0,
            status=OrderStatus.FILLED,
            paper=False,
            ts=time.time(),
        )
    )
    assert sp.summary()["fills_total"] == 1
    assert sl.summary()["fills_total"] == 1
    assert sp.list_fills()[0]["market_id"] == "m-paper"
    assert sl.list_fills()[0]["market_id"] == "m-live"
    sp.close()
    sl.close()

    app = create_app(paper_db=paper, live_db=live)
    from fastapi.testclient import TestClient

    try:
        client = TestClient(app)
    except Exception:
        # starlette/fastapi test client may need httpx
        import pytest

        pytest.importorskip("httpx")
        client = TestClient(app)

    r = client.get("/api/summary", params={"book": "paper"})
    assert r.status_code == 200
    assert r.json()["fills_total"] == 1
    assert r.json()["book"] == "paper"

    r2 = client.get("/api/summary", params={"book": "live"})
    assert r2.status_code == 200
    assert r2.json()["fills_total"] == 1
    assert r2.json()["book"] == "live"

    fills_p = client.get("/api/fills", params={"book": "paper"}).json()
    fills_l = client.get("/api/fills", params={"book": "live"}).json()
    assert fills_p[0]["market_id"] == "m-paper"
    assert fills_l[0]["market_id"] == "m-live"
