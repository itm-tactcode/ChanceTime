"""Path helpers for project root and secret files."""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Resolve project root (directory containing pyproject.toml / config/)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() or (parent / "config").is_dir():
            return parent
    return Path.cwd()


def resolve_path(path: str | Path, *, root: Path | None = None) -> Path:
    """Resolve a path relative to project root (or absolute / ~)."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (root or project_root()) / p
    return p.resolve()


def load_text_secret(path: str | Path, *, root: Path | None = None) -> str:
    """Read a secret file (e.g. PEM). Raises FileNotFoundError if missing."""
    resolved = resolve_path(path, root=root)
    if not resolved.is_file():
        raise FileNotFoundError(f"Secret file not found: {resolved}")
    return resolved.read_text(encoding="utf-8")


def resolve_private_key_path(
    raw: str | None,
    *,
    root: Path | None = None,
    venue: str = "exchange",
    example_path: str = "./secrets/key.key",
    preferred_env: str = "PRIVATE_KEY_PATH",
) -> Path | None:
    """Parse an env value as a PEM file path (never inline key material).

    Returns None if raw is empty. Raises ValueError if the value looks like
    a pasted PEM body instead of a path.
    """
    if raw is None or not str(raw).strip():
        return None
    path_str = str(raw).strip()
    if "BEGIN" in path_str and "PRIVATE KEY" in path_str:
        raise ValueError(
            f"{venue} private key must be a file path (e.g. {example_path}), "
            f"not the PEM contents. Put the PEM under secrets/ and set "
            f"{preferred_env}={example_path}"
        )
    return resolve_path(path_str, root=root)
