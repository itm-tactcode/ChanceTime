"""Config, doctor, presets, readiness, user knobs."""

from __future__ import annotations

from typing import Annotated

import typer

from chancetime.cli.common import load_app_config as _load
from chancetime.persistence import StateStore
from chancetime.utils.logging import setup_logging


def register(app: typer.Typer) -> None:
    @app.command("check-config")
    def check_config(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    ) -> None:
        """Load and print sanitized config (no secret material)."""
        cfg = _load(config)
        safe = cfg.model_dump(mode="json")
        if safe.get("xai_api_key"):
            safe["xai_api_key"] = "***set***"
        if safe.get("kalshi_api_key"):
            safe["kalshi_api_key"] = "***set***"
        if safe.get("polymarket_api_key"):
            safe["polymarket_api_key"] = "***set***"
        if safe.get("telegram_bot_token"):
            safe["telegram_bot_token"] = "***set***"
        if cfg.kalshi_private_key_path is not None:
            safe["kalshi_private_key_path"] = str(cfg.kalshi_private_key_path)
            safe["kalshi_private_key_file_exists"] = cfg.kalshi_private_key_path.is_file()
        safe["kalshi_credentials_configured"] = cfg.kalshi_credentials_configured
        if cfg.polymarket_private_key_path is not None:
            safe["polymarket_private_key_path"] = str(cfg.polymarket_private_key_path)
            safe["polymarket_private_key_file_exists"] = cfg.polymarket_private_key_path.is_file()
        safe["polymarket_credentials_configured"] = cfg.polymarket_credentials_configured
        safe.pop("polymarket_api_secret", None)
        import json

        typer.echo(json.dumps(safe, indent=2, default=str))



    @app.command("doctor")
    def doctor(
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        json_out: Annotated[
            bool,
            typer.Option("--json", help="Machine-readable report"),
        ] = False,
    ) -> None:
        """Validate secrets presence, key files, books, and dashboard deps (no secret values)."""
        import json

        from chancetime.utils.doctor import run_doctor

        report = run_doctor(config=config)
        if json_out:
            typer.echo(json.dumps(report, indent=2, default=str))
            # Always 0 for --json so desktop can parse warnings without failing spawn
            return
        typer.echo(report["summary"])
        for c in report["checks"]:
            mark = "ok" if c["ok"] else c["level"].upper()
            typer.echo(f"  [{mark}] {c['name']}: {c['detail']}")
        if not report["ok"]:
            raise typer.Exit(1)



    @app.command("presets")
    def presets_cmd(
        action: Annotated[
            str,
            typer.Argument(help="list | show | apply"),
        ] = "list",
        name: Annotated[
            str | None,
            typer.Option("--name", "-n", help="Preset name for show/apply"),
        ] = None,
        json_out: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Hardcoded recommended setting packs → user.yaml (never secrets)."""
        import json

        from chancetime.utils.presets import apply_preset, get_preset, list_presets

        if action == "list":
            rows = list_presets()
            if json_out:
                typer.echo(json.dumps(rows, indent=2))
            else:
                for r in rows:
                    typer.echo(f"{r['name']:22}  {r['blurb']}")
            return
        if not name:
            typer.echo("show/apply need --name", err=True)
            raise typer.Exit(2)
        if action == "show":
            data = get_preset(name)
            typer.echo(json.dumps(data, indent=2, default=str))
            return
        if action == "apply":
            result = apply_preset(name)
            typer.echo(json.dumps(result, indent=2, default=str) if json_out else f"applied {name} → {result['path']}")
            return
        typer.echo(f"unknown action {action!r}", err=True)
        raise typer.Exit(2)



    @app.command("suggest-settings")
    def suggest_settings_cmd(
        account: Annotated[str, typer.Option("--account", "-a")] = "paper",
        config: Annotated[str | None, typer.Option("--config", "-c")] = None,
        apply_id: Annotated[
            str | None,
            typer.Option("--apply", help="Apply one suggestion id's patch to user.yaml"),
        ] = None,
        apply_all_actions: Annotated[
            bool,
            typer.Option("--apply-actions", help="Apply all severity=action patches"),
        ] = False,
        json_out: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Stats-based setting suggestions for a book (optional apply)."""
        import json
        import os

        from chancetime.utils.suggest import (
            merge_suggestion_patches,
            suggest_from_store,
            suggestions_to_dict,
        )
        from chancetime.utils.user_knobs import apply_user_overrides

        # Quiet logs so desktop JSON parse never sees structlog on stdout/stderr noise
        if json_out or os.environ.get("CHANCETIME_QUIET"):
            setup_logging("ERROR", json_logs=False)
        cfg = _load(config, account=account)
        store = StateStore(cfg.persistence.db_path, enabled=True)
        try:
            items = suggest_from_store(store, account=account)
            if apply_id:
                match = next((s for s in items if s.id == apply_id), None)
                if match is None or not match.patch:
                    typer.echo(f"no applyable suggestion {apply_id!r}", err=True)
                    raise typer.Exit(1)
                result = apply_user_overrides(match.patch)
                typer.echo(f"applied {apply_id} → {result['path']}")
                return
            if apply_all_actions:
                actions = [s for s in items if s.severity == "action" and s.patch]
                patch = merge_suggestion_patches(actions)
                if not patch:
                    typer.echo("no action patches")
                    return
                result = apply_user_overrides(patch)
                typer.echo(f"applied {len(actions)} action(s) → {result['path']}")
                return
            if json_out:
                typer.echo(json.dumps(suggestions_to_dict(items), indent=2, default=str))
            else:
                if not items:
                    typer.echo("(no suggestions)")
                for s in items:
                    typer.echo(f"[{s.severity}] {s.id}: {s.title}")
                    typer.echo(f"    {s.detail}")
                    if s.patch:
                        typer.echo(f"    patch={json.dumps(s.patch)}")
        finally:
            store.close()



    @app.command("readiness")
    def readiness_cmd(
        json_out: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Print live-readiness gate checklist (see docs/LIVE_READINESS.md)."""
        import json

        from chancetime.utils.paths import project_root

        doc = project_root() / "docs" / "LIVE_READINESS.md"
        checklist = [
            {"id": "doctor", "cmd": "chancetime doctor", "gate": "A"},
            {"id": "accounts", "cmd": "chancetime accounts", "gate": "A"},
            {"id": "paper_bag", "cmd": "chancetime run -c config/paper_bag.yaml --account paper_bag --max-polls 20", "gate": "A"},
            {"id": "history", "cmd": "chancetime list-history", "gate": "B"},
            {"id": "walk_forward", "cmd": "chancetime walk-forward --folds 2", "gate": "B"},
            {"id": "digest", "cmd": "chancetime digest --account paper", "gate": "C"},
            {"id": "export", "cmd": "chancetime export --account paper --year 2026", "gate": "C"},
            {"id": "suggest", "cmd": "chancetime suggest-settings --account paper", "gate": "C"},
            {"id": "presets", "cmd": "chancetime presets list", "gate": "D"},
            {"id": "live_smoke", "cmd": "chancetime live-smoke (after A-D; real money)", "gate": "E"},
            {"id": "sync", "cmd": "chancetime sync-positions", "gate": "E"},
        ]
        if json_out:
            typer.echo(
                json.dumps(
                    {
                        "doc": str(doc) if doc.is_file() else None,
                        "checklist": checklist,
                    },
                    indent=2,
                )
            )
            return
        typer.echo("Live readiness gates (full text: docs/LIVE_READINESS.md)\n")
        for c in checklist:
            typer.echo(f"  [{c['gate']}] {c['id']:12}  {c['cmd']}")
        if doc.is_file():
            typer.echo(f"\n→ {doc}")



    @app.command("user-config")
    def user_config_cmd(
        action: Annotated[
            str,
            typer.Argument(help="show | apply | snapshot"),
        ] = "show",
        json_file: Annotated[
            str | None,
            typer.Option("--file", "-f", help="JSON file of knobs snapshot (for apply)"),
        ] = None,
        raw_json: Annotated[
            str | None,
            typer.Option("--json", help="Inline JSON overrides or snapshot"),
        ] = None,
    ) -> None:
        """Read/write config/user.yaml (non-secrets only).

        * show — dump raw user.yaml
        * snapshot — flat UI-friendly knobs (defaults + user overlay)
        * apply — merge snapshot or nested overrides into user.yaml
        """
        import json
        from pathlib import Path

        from chancetime.utils.user_knobs import (
            apply_user_overrides,
            build_knobs_snapshot,
            load_user_overrides_file,
            snapshot_to_overrides,
        )

        if action == "show":
            data = load_user_overrides_file()
            typer.echo(json.dumps(data, indent=2, default=str))
            return
        if action == "snapshot":
            # Effective default.yaml + user.yaml (not hard-coded UI fallbacks)
            snap = build_knobs_snapshot()
            typer.echo(json.dumps(snap, indent=2, default=str))
            return
        if action == "apply":
            payload: dict
            if json_file:
                payload = json.loads(Path(json_file).read_text(encoding="utf-8"))
            elif raw_json:
                payload = json.loads(raw_json)
            else:
                typer.echo("apply needs --file or --json", err=True)
                raise typer.Exit(2)
            # Flat UI snapshot (has data_source) vs nested overrides (has bot/risk/…)
            if "data_source" in payload or (
                "poll_interval_seconds" in payload and "bot" not in payload
            ):
                payload = snapshot_to_overrides(payload)
            result = apply_user_overrides(payload)
            typer.echo(json.dumps(result, indent=2, default=str))
            return
        typer.echo(f"unknown action {action!r}; use show|snapshot|apply", err=True)
        raise typer.Exit(2)



