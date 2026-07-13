"""Shared CLI helpers."""

from __future__ import annotations

from chancetime.utils.config import AppConfig, load_config


def load_app_config(
    config: str | None,
    *,
    account: str | None = None,
) -> AppConfig:
    """Load YAML + env config, optionally via named account book."""
    if account:
        from chancetime.utils.accounts import load_config_for_account

        cfg, _acct = load_config_for_account(account, config_path=config)
        return cfg
    return load_config(config)
