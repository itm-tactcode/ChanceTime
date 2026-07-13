"""Venue request signing (Kalshi RSA-PSS, Polymarket US Ed25519).

Never log private key material.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from chancetime.utils.paths import load_text_secret, resolve_path


def load_rsa_private_key(path: str | Path) -> rsa.RSAPrivateKey:
    pem = load_text_secret(path).encode("utf-8")
    key = serialization.load_pem_private_key(pem, password=None, backend=default_backend())
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(f"Expected RSA private key at {path}")
    return key


def load_ed25519_private_key(path: str | Path) -> ed25519.Ed25519PrivateKey:
    """Load Polymarket US secret: base64 raw seed (32 bytes) or PEM.

    Developer portal secrets are typically one-line base64 (see PM US auth docs).
    """
    raw = load_text_secret(path).strip()
    if "BEGIN" in raw and "PRIVATE KEY" in raw:
        key = serialization.load_pem_private_key(
            raw.encode("utf-8"), password=None, backend=default_backend()
        )
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise TypeError(f"Expected Ed25519 private key PEM at {path}")
        return key
    # Base64 seed (optionally longer; docs use first 32 bytes)
    seed = base64.b64decode(raw)
    if len(seed) < 32:
        raise ValueError(f"Ed25519 secret too short in {path}")
    return ed25519.Ed25519PrivateKey.from_private_bytes(seed[:32])


def kalshi_sign(
    private_key: rsa.RSAPrivateKey,
    *,
    timestamp_ms: str,
    method: str,
    path: str,
) -> str:
    """Sign ``timestamp + METHOD + path`` (no query string) with RSA-PSS SHA256."""
    path_clean = path.split("?", 1)[0]
    message = f"{timestamp_ms}{method.upper()}{path_clean}".encode()
    sig = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("ascii")


def polymarket_sign(
    private_key: ed25519.Ed25519PrivateKey,
    *,
    timestamp_ms: str,
    method: str,
    path: str,
) -> str:
    """Sign ``timestamp + METHOD + path`` with Ed25519; base64 signature."""
    path_clean = path.split("?", 1)[0]
    message = f"{timestamp_ms}{method.upper()}{path_clean}".encode()
    return base64.b64encode(private_key.sign(message)).decode("ascii")


def now_ms() -> str:
    return str(int(time.time() * 1000))


def resolve_key_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    return resolve_path(path)


def key_type_label(key: PrivateKeyTypes) -> str:
    if isinstance(key, rsa.RSAPrivateKey):
        return "rsa"
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return "ed25519"
    return type(key).__name__
