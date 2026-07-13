"""Kalshi private key path resolution (file-based secrets)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chancetime.data_layer.kalshi import KalshiClient
from chancetime.utils.config import load_config
from chancetime.utils.paths import resolve_path


def test_resolve_path_relative_to_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    resolved = resolve_path("./secrets/kalshi.key", root=root)
    assert resolved == (root / "secrets" / "kalshi.key").resolve()


def test_config_loads_private_key_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    key_file = tmp_path / "kalshi.key"
    key_file.write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KALSHI_API_KEY", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_file))
    cfg = load_config(root / "config" / "default.yaml", env_file=None)
    assert cfg.kalshi_api_key == "test-key-id"
    assert cfg.kalshi_private_key_path == key_file.resolve()
    assert cfg.kalshi_credentials_configured is True


def test_legacy_secret_env_as_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    key_file = tmp_path / "legacy.key"
    key_file.write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("KALSHI_API_SECRET", str(key_file))
    cfg = load_config(root / "config" / "default.yaml", env_file=None)
    assert cfg.kalshi_private_key_path == key_file.resolve()


def test_rejects_inline_pem_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv(
        "KALSHI_PRIVATE_KEY_PATH",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",
    )
    with pytest.raises(ValueError, match="file path"):
        load_config(root / "config" / "default.yaml", env_file=None)


def test_kalshi_client_loads_pem(tmp_path: Path) -> None:
    key_file = tmp_path / "k.key"
    pem = "-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----\n"
    key_file.write_text(pem, encoding="utf-8")
    client = KalshiClient(api_key_id="id", private_key_path=key_file)
    assert client.credentials_configured is True
    assert client.load_private_key_pem() == pem


def test_polymarket_us_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    key_file = tmp_path / "polymarket.key"
    key_file.write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nPM\n-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POLYMARKET_API_KEY", "pm-key-id")
    monkeypatch.setenv("POLYMARKET_API_SECRET", str(key_file))  # legacy path alias
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY_PATH", raising=False)
    cfg = load_config(root / "config" / "default.yaml", env_file=None)
    assert cfg.polymarket_api_key == "pm-key-id"
    assert cfg.polymarket_private_key_path == key_file.resolve()
    assert cfg.polymarket_credentials_configured is True
