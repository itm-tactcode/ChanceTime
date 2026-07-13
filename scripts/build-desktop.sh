#!/usr/bin/env bash
# Build Chance Time desktop (Tauri *release* packages).
# For day-to-day development use:  desktop/dev.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Project root: $ROOT"
export CHANCETIME_ROOT="$ROOT"

# Bundle targets: deb+rpm by default (AppImage needs fuse/linuxdeploy and is flaky).
# Override:  BUNDLE_TARGETS=appimage ./scripts/build-desktop.sh
#            BUNDLE_TARGETS=all ./scripts/build-desktop.sh
TARGETS="${BUNDLE_TARGETS:-deb,rpm}"

if [[ "${WITH_SIDECAR:-0}" == "1" ]]; then
  echo "==> Building Python sidecar (PyInstaller)…"
  uv sync --extra dashboard
  uv pip install pyinstaller
  ENTRY="$(mktemp /tmp/ct_entry.XXXXXX.py)"
  cat > "$ENTRY" <<'PY'
from chancetime.main import app
if __name__ == "__main__":
    app()
PY
  uv run pyinstaller --noconfirm --onefile --name chancetime-cli --paths src "$ENTRY"
  mkdir -p desktop/sidecar
  cp -f dist/chancetime-cli desktop/sidecar/chancetime-cli
  chmod +x desktop/sidecar/chancetime-cli
  rm -f "$ENTRY"
  echo "    sidecar: desktop/sidecar/chancetime-cli"
fi

echo "==> npm install + tauri build (targets: $TARGETS)"
cd desktop
if [[ ! -d node_modules ]]; then
  npm install
fi

# Prefer explicit targets so a flaky AppImage does not fail the whole build
npx tauri build --bundles "$TARGETS"

echo ""
echo "==> Artifacts"
echo "  Binary:  desktop/src-tauri/target/release/chancetime-desktop"
echo "  Bundles: desktop/src-tauri/target/release/bundle/"
ls -la src-tauri/target/release/bundle/*/ 2>/dev/null | head -40 || true
echo ""
echo "Run the release binary (no installer needed):"
echo "  CHANCETIME_ROOT=$ROOT ./desktop/src-tauri/target/release/chancetime-desktop"
echo ""
echo "Or install the .deb (Debian/Ubuntu; Arch can use rpm or just the binary)."
echo "Day-to-day dev (hot reload):  cd desktop && ./dev.sh"
echo "Signing: see desktop/PACKAGING.md"
