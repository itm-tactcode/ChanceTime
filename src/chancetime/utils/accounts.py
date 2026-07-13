"""Named trading books / accounts (Phase 11 multi-book isolation)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from chancetime.utils.config import AppConfig, deep_merge, load_config
from chancetime.utils.paths import project_root, resolve_path


class AccountDef(BaseModel):
    """One isolated book (SQLite + optional base YAML)."""

    label: str = ""
    db_path: str
    paper_mode: bool = True
    # Optional path to a base config (e.g. live_micro.yaml)
    config: str | None = None
    # Optional history dir isolation
    history_directory: str | None = None


class AccountsFile(BaseModel):
    accounts: dict[str, AccountDef] = Field(default_factory=dict)


def default_accounts() -> dict[str, AccountDef]:
    """Built-in books when accounts.yaml is missing."""
    return {
        "paper": AccountDef(
            label="Paper main",
            db_path="data/paper.db",
            paper_mode=True,
            config="config/default.yaml",
        ),
        "live": AccountDef(
            label="Live micro",
            db_path="data/live.db",
            paper_mode=False,
            config="config/live_micro.yaml",
        ),
        "paper_bag": AccountDef(
            label="Paper strategy bag",
            db_path="data/paper_bag.db",
            paper_mode=True,
            config="config/paper_bag.yaml",
        ),
    }


def accounts_path(*, root: Path | None = None) -> Path:
    return (root or project_root()) / "config" / "accounts.yaml"


def load_accounts(*, root: Path | None = None) -> dict[str, AccountDef]:
    """Load accounts.yaml or fall back to built-in paper/live/paper_bag."""
    r = root or project_root()
    path = accounts_path(root=r)
    if not path.is_file():
        return default_accounts()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        return default_accounts()
    try:
        parsed = AccountsFile.model_validate(raw)
    except Exception:
        return default_accounts()
    return parsed.accounts or default_accounts()


def get_account(name: str, *, root: Path | None = None) -> AccountDef:
    accounts = load_accounts(root=root)
    key = name.strip().lower()
    if key not in accounts:
        known = ", ".join(sorted(accounts))
        raise KeyError(f"Unknown account {name!r}. Known: {known}")
    return accounts[key]


def load_config_for_account(
    account_name: str,
    *,
    config_path: str | Path | None = None,
    env_file: str | Path | None = ".env",
    user_config: str | Path | bool | None = True,
    root: Path | None = None,
) -> tuple[AppConfig, AccountDef]:
    """Load config isolated to a named account book.

    Order: account.config (or --config) → user.yaml → env secrets → account overrides
    (db_path, paper_mode).
    """
    acct = get_account(account_name, root=root)
    base = config_path or acct.config or "config/default.yaml"
    cfg = load_config(base, env_file=env_file, user_config=user_config)
    # Force book isolation
    cfg.persistence.db_path = acct.db_path
    cfg.bot.paper_mode = acct.paper_mode
    if acct.history_directory:
        cfg.history.directory = acct.history_directory
    # Dashboard defaults still show all known books
    books = load_accounts(root=root)
    if "paper" in books:
        cfg.dashboard.paper_db_path = books["paper"].db_path
    if "live" in books:
        cfg.dashboard.live_db_path = books["live"].db_path
    return cfg, acct


def list_accounts_summary(*, root: Path | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, a in sorted(load_accounts(root=root).items()):
        path = resolve_path(a.db_path)
        out.append(
            {
                "name": name,
                "label": a.label or name,
                "db_path": str(path),
                "db_exists": path.is_file(),
                "paper_mode": a.paper_mode,
                "config": a.config,
            }
        )
    return out
