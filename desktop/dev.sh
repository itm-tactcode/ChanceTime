#!/usr/bin/env bash
# Dev launcher for Chance Time desktop shell.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export CHANCETIME_ROOT="$ROOT"
cd "$(dirname "$0")"

if ! pkg-config --exists webkit2gtk-4.1 2>/dev/null; then
  echo "Missing webkit2gtk-4.1. On Arch/CachyOS:"
  echo "  sudo pacman -S --needed webkit2gtk-4.1 librsvg libayatana-appindicator"
  exit 1
fi

# Tray library: pkg-config is the reliable check (ldconfig can fail in some envs).
# Note: runtime may still log "libayatana-appindicator is deprecated" — that is OK
# (GTK stack deprecation notice; tray still works). Prefer libayatana-appindicator-glib later.
has_tray=0
if pkg-config --exists ayatana-appindicator3-0.1 2>/dev/null; then
  has_tray=1
elif [[ -e /usr/lib/libayatana-appindicator3.so || -e /usr/lib/libayatana-appindicator3.so.1 ]]; then
  has_tray=1
elif [[ -e /usr/lib64/libayatana-appindicator3.so.1 ]]; then
  has_tray=1
elif ldconfig -p 2>/dev/null | grep -qE 'libayatana-appindicator3\.so|libappindicator3\.so'; then
  has_tray=1
fi
if [[ "$has_tray" -eq 0 ]]; then
  echo "Note: libayatana-appindicator not detected — tray may be unavailable (window-only)."
  echo "  sudo pacman -S --needed libayatana-appindicator"
fi

if [[ ! -d node_modules ]]; then
  npm install
fi
exec npm run dev
