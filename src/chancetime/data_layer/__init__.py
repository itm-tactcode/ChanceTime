"""Market data ingestion (mock, Kalshi, Polymarket US, composite)."""

from __future__ import annotations

from chancetime.data_layer.arb_discovery import deep_discover, load_aliases, save_aliases
from chancetime.data_layer.base import MarketDataClient
from chancetime.data_layer.composite import CompositeMarketClient
from chancetime.data_layer.kalshi import KalshiClient
from chancetime.data_layer.matching import MarketPair, normalize_title, pair_markets
from chancetime.data_layer.mock import MockMarketClient
from chancetime.data_layer.models import Market, Platform
from chancetime.data_layer.polymarket_us import PolymarketUSClient

__all__ = [
    "CompositeMarketClient",
    "KalshiClient",
    "Market",
    "MarketDataClient",
    "MarketPair",
    "MockMarketClient",
    "Platform",
    "PolymarketUSClient",
    "build_data_client",
    "deep_discover",
    "load_aliases",
    "normalize_title",
    "pair_markets",
    "save_aliases",
]


def build_data_client(
    source: str,
    *,
    kalshi_api_key: str | None = None,
    kalshi_private_key_path: str | None = None,
    kalshi_env: str = "demo",
    polymarket_api_key: str | None = None,
    polymarket_private_key_path: str | None = None,
) -> MarketDataClient:
    """Factory for the configured data source.

    ``source``: mock | kalshi | polymarket | polymarket_us | both
    (``polymarket`` means Polymarket US account API, not international CLOB.)
    """
    src = source.lower().strip()
    if src == "mock":
        return MockMarketClient()
    if src == "kalshi":
        return KalshiClient(
            api_key_id=kalshi_api_key,
            private_key_path=kalshi_private_key_path,
            env=kalshi_env,
        )
    if src in {"polymarket", "polymarket_us", "polymarket-us"}:
        return PolymarketUSClient(
            api_key_id=polymarket_api_key,
            private_key_path=polymarket_private_key_path,
        )
    if src in {"both", "multi", "kalshi+polymarket"}:
        return CompositeMarketClient(
            [
                KalshiClient(
                    api_key_id=kalshi_api_key,
                    private_key_path=kalshi_private_key_path,
                    env=kalshi_env,
                ),
                PolymarketUSClient(
                    api_key_id=polymarket_api_key,
                    private_key_path=polymarket_private_key_path,
                ),
            ]
        )
    return MockMarketClient()
