# Packaging & signing (Phase 12)

## `dev.sh` vs `build-desktop.sh`

| Script | Purpose | Output |
|--------|---------|--------|
| **`desktop/dev.sh`** | Day-to-day development | Debug app with hot reload (`tauri dev`) |
| **`scripts/build-desktop.sh`** | Release packages | Optimized binary + `.deb` / `.rpm` |

You do **not** need `build-desktop.sh` to use the app. Use `./dev.sh` while iterating.

The release **binary** is enough to run without installing:

```bash
export CHANCETIME_ROOT="$HOME/Projects/chancetime"
./desktop/src-tauri/target/release/chancetime-desktop
```

## Build unsigned installers

**Prereqs:** Rust, Node 18+, WebKitGTK + ayatana on Linux, `uv sync --extra dashboard`.

```bash
# From repo root — default: deb + rpm only (AppImage off; linuxdeploy is flaky)
./scripts/build-desktop.sh

# Optional AppImage (needs FUSE + working linuxdeploy):
BUNDLE_TARGETS=appimage ./scripts/build-desktop.sh

# Optional PyInstaller CLI sidecar (no system uv at runtime):
WITH_SIDECAR=1 ./scripts/build-desktop.sh
```

Outputs (Linux typical):

- `desktop/src-tauri/target/release/chancetime-desktop` — main binary
- `desktop/src-tauri/target/release/bundle/deb/*.deb`
- `desktop/src-tauri/target/release/bundle/rpm/*.rpm`

`productName` is `ChanceTime` (no spaces) so packagers do not choke; the window title remains “Chance Time”.

macOS/Windows targets need those hosts (or CI matrix).

## Runtime resolution

The desktop app does **not** embed CPython by default. It runs:

1. `CHANCETIME_BIN` or `desktop/sidecar/chancetime-cli`
2. else `.venv/bin/python -m chancetime`
3. else `uv run chancetime`

Ship either a sidecar binary, a project tree with `.venv`, or document that `uv` is required.

## Signing (you provide certificates)

| Platform | Notes |
|----------|--------|
| **Linux** | AppImage/deb are usually **unsigned** for personal use. Optional: distro packaging + GPG. |
| **macOS** | Apple Developer ID + `codesign` / notarization. Set in `tauri.conf` / CI secrets. |
| **Windows** | Authenticode cert; configure `tauri` windows certificate thumbprint. |

Tauri docs: https://v2.tauri.app/distribute/

We do **not** commit secrets or certs. Personal builds: unsigned is fine.

## Paper vs live data

- Paper: `data/paper.db`
- Live: `data/live.db`

```bash
uv run chancetime migrate-books   # once, from legacy data/chancetime.db
```
