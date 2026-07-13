"""Phase 6 live path: caps, auth helpers, reject without risk ack."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chancetime.execution.auth import kalshi_sign, now_ms, polymarket_sign
from chancetime.execution.engine import ExecutionEngine, OrderStatus
from chancetime.execution.live_kalshi import LiveOrderResult
from chancetime.strategies.base import Side, Signal
from chancetime.utils.config import ExecutionSettings


def test_now_ms_is_digits() -> None:
    assert now_ms().isdigit()
    assert len(now_ms()) >= 13


def test_kalshi_sign_roundtrip_rsa() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sig = kalshi_sign(key, timestamp_ms="123", method="GET", path="/trade-api/v2/portfolio/balance")
    assert isinstance(sig, str) and len(sig) > 20
    # ensure PEM load path works
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    assert b"PRIVATE KEY" in pem


def test_polymarket_sign_ed25519() -> None:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    sig = polymarket_sign(key, timestamp_ms="123", method="GET", path="/v1/portfolio/positions")
    assert isinstance(sig, str) and len(sig) > 20


@pytest.mark.asyncio
async def test_live_rejects_without_risk_ack() -> None:
    eng = ExecutionEngine(
        ExecutionSettings(live_enabled=True, max_live_order_usd=5.0),
        paper_mode=False,
        live_enabled=True,
        risk_acknowledged=False,
    )
    fill = await eng.execute(
        Signal(
            market_id="T",
            platform="kalshi",
            side=Side.YES,
            strength=1.0,
            size_usd=5.0,
            market_prob=0.4,
            reason="t",
        )
    )
    assert fill.status == OrderStatus.REJECTED
    assert "risk" in fill.note.lower() or "miss" in fill.note.lower()


@pytest.mark.asyncio
async def test_live_caps_and_calls_kalshi() -> None:
    mock_k = MagicMock()
    mock_k.place_order = AsyncMock(
        return_value=LiveOrderResult(
            ok=True,
            venue="kalshi",
            order_id="ord-1",
            client_order_id="c1",
            status="submitted",
            price=0.4,
            size_usd=5.0,
            contracts=12.0,
            raw={"order_id": "ord-1"},
            note="ok",
        )
    )
    eng = ExecutionEngine(
        ExecutionSettings(
            live_enabled=True,
            max_live_order_usd=5.0,
            max_live_orders_session=2,
            max_live_notional_session=20.0,
        ),
        paper_mode=False,
        live_enabled=True,
        risk_acknowledged=True,
        kalshi=mock_k,
    )
    fill = await eng.execute(
        Signal(
            market_id="KXTEST",
            platform="kalshi",
            side=Side.YES,
            strength=1.0,
            size_usd=50.0,  # should cap to 5
            market_prob=0.4,
            reason="t",
        )
    )
    assert fill.status in {OrderStatus.SUBMITTED, OrderStatus.FILLED}
    assert fill.paper is False
    mock_k.place_order.assert_awaited_once()
    kwargs = mock_k.place_order.await_args.kwargs
    assert kwargs["size_usd"] <= 5.0


@pytest.mark.asyncio
async def test_paper_still_default() -> None:
    eng = ExecutionEngine(ExecutionSettings(), paper_mode=True)
    fill = await eng.execute(
        Signal(
            market_id="m",
            platform="mock",
            side=Side.YES,
            strength=0.5,
            size_usd=10.0,
            market_prob=0.4,
            reason="t",
        )
    )
    assert fill.status == OrderStatus.SIMULATED
    assert fill.paper is True
