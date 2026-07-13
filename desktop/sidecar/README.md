# Chance Time CLI sidecar (optional bundled Python)

The desktop shell runs the bot/API by, in order:

1. `CHANCETIME_BIN` (absolute path to a CLI binary)
2. `.venv/bin/chancetime` (console script from `uv sync`)
3. `.venv/bin/python -m chancetime` (package `__main__.py`)
4. `uv run chancetime`
5. `desktop/sidecar/chancetime-cli` only if `CHANCETIME_USE_SIDECAR=1`

A stale sidecar previously shadowed the venv and kept serving the **old single DB**
(`data/chancetime.db`) without the PAPER/LIVE toggle.

For a **self-contained** install without requiring `uv` on PATH, build a one-file CLI:

```bash
# From repo root, with venv that has chancetime + dashboard deps
uv sync --extra dashboard
uv pip install pyinstaller
uv run pyinstaller \
  --onefile \
  --name chancetime-cli \
  --paths src \
  --collect-all chancetime \
  -c "from chancetime.main import app; app()"
# Or entrypoint:
#   uv run pyinstaller --onefile --name chancetime-cli $(uv run which chancetime)
```

Simpler entry (if `chancetime` console script exists):

```bash
cd ~/Projects/chancetime
uv sync --extra dashboard
uv pip install pyinstaller
cat > /tmp/ct_entry.py <<'PY'
from chancetime.main import app
if __name__ == "__main__":
    app()
PY
uv run pyinstaller --onefile --name chancetime-cli --paths src /tmp/ct_entry.py
cp dist/chancetime-cli desktop/sidecar/chancetime-cli
chmod +x desktop/sidecar/chancetime-cli
```

Then either set:

```bash
export CHANCETIME_BIN="$HOME/Projects/chancetime/desktop/sidecar/chancetime-cli"
```

or leave the binary at `desktop/sidecar/chancetime-cli` (auto-detected).

**Note:** PyInstaller one-files are large and platform-specific. Dev default remains `uv` / `.venv`.
