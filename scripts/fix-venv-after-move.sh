#!/usr/bin/env bash
# Recreate .venv after the project directory was renamed/moved.
# Stale shebangs (old absolute paths) break `uv run pytest`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "Recreating venv in $ROOT"
rm -rf .venv
if command -v uv >/dev/null 2>&1; then
  uv sync --group dev || uv sync --extra dev
  uv run pytest -q
else
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -e ".[dev]"
  python -m pytest -q
fi
echo "OK — pytest should work now."
