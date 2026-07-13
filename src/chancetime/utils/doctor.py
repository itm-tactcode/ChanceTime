"""Environment / secrets health checks (no secret values printed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chancetime.utils.config import AppConfig, load_config
from chancetime.utils.paths import project_root, resolve_path


def run_doctor(
    *,
    config: str | Path | None = None,
    env_file: str | Path | None = ".env",
) -> dict[str, Any]:
    """Return a structured report: ok, checks[], summary."""
    root = project_root()
    checks: list[dict[str, Any]] = []
    cfg: AppConfig | None = None

    def add(
        name: str,
        ok: bool,
        *,
        level: str = "info",
        detail: str = "",
    ) -> None:
        checks.append(
            {
                "name": name,
                "ok": ok,
                "level": level if not ok else "ok",
                "detail": detail,
            }
        )

    try:
        cfg = load_config(config, env_file=env_file)
        add("config_load", True, detail=f"root={root}")
    except Exception as exc:
        add("config_load", False, level="error", detail=str(exc))
        return {"ok": False, "checks": checks, "summary": "config failed to load"}

    add(
        "paper_mode",
        True,
        detail=f"paper_mode={cfg.paper_mode} shadow_mode={cfg.bot.shadow_mode}",
    )
    if not cfg.paper_mode:
        add(
            "live_flag",
            False,
            level="warn",
            detail="PAPER_MODE/paper_mode is false — live path may be active",
        )
    else:
        add("live_flag", True, detail="paper mode on (safe default)")

    # Secrets — presence only
    add(
        "xai_api_key",
        bool(cfg.xai_api_key),
        level="warn" if not cfg.xai_api_key else "ok",
        detail="set" if cfg.xai_api_key else "missing (LLM strategies disabled without it)",
    )

    k_ok = cfg.kalshi_credentials_configured
    add(
        "kalshi_credentials",
        k_ok,
        level="warn" if not k_ok else "ok",
        detail=(
            f"key_id={'set' if cfg.kalshi_api_key else 'missing'} "
            f"pem={cfg.kalshi_private_key_path} "
            f"exists={bool(cfg.kalshi_private_key_path and Path(cfg.kalshi_private_key_path).is_file())} "
            f"env={cfg.kalshi_env}"
        ),
    )
    p_ok = cfg.polymarket_credentials_configured
    add(
        "polymarket_credentials",
        p_ok,
        level="warn" if not p_ok else "ok",
        detail=(
            f"key_id={'set' if cfg.polymarket_api_key else 'missing'} "
            f"pem={cfg.polymarket_private_key_path} "
            f"exists={bool(cfg.polymarket_private_key_path and Path(cfg.polymarket_private_key_path).is_file())}"
        ),
    )

    # Books
    for label, raw in (
        ("paper_db", cfg.dashboard.paper_db_path),
        ("live_db", cfg.dashboard.live_db_path),
        ("active_db", cfg.persistence.db_path),
    ):
        path = resolve_path(raw)
        parent = path.parent
        writable = parent.is_dir() and os_access_write(parent)
        add(
            label,
            writable,
            level="error" if not writable else "ok",
            detail=f"{path} parent_writable={writable} exists={path.is_file()}",
        )

    # Optional dashboard deps
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401

        add("dashboard_deps", True, detail="fastapi+uvicorn importable")
    except ImportError:
        add(
            "dashboard_deps",
            False,
            level="warn",
            detail="missing — uv sync --extra dashboard",
        )

    # user.yaml
    from chancetime.utils.config import user_config_path

    up = user_config_path(root=root)
    add(
        "user_yaml",
        True,
        detail=f"{up} exists={up.is_file()}",
    )

    errors = sum(1 for c in checks if not c["ok"] and c["level"] == "error")
    warns = sum(1 for c in checks if not c["ok"] and c["level"] == "warn")
    ok = errors == 0
    summary = f"{'OK' if ok else 'ISSUES'} — {errors} error(s), {warns} warning(s)"
    return {
        "ok": ok,
        "summary": summary,
        "checks": checks,
        "paper_mode": cfg.paper_mode,
        "shadow_mode": cfg.bot.shadow_mode,
        "data_source": cfg.data.source,
    }


def os_access_write(path: Path) -> bool:
    import os

    try:
        return os.access(path, os.W_OK)
    except OSError:
        return False
