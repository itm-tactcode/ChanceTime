# Chance Time Desktop (Tauri 2)

Personal **desktop app** around the Python bot + local FastAPI status UI.

### Product shape (why not two apps?)

| Layer | Role |
|-------|------|
| **Control tab** | Lifecycle: start/stop bot + API, knobs, logs, tray |
| **Monitor tab** | Embedded FastAPI UI (positions/fills/equity) via iframe |
| **Browser** | Optional only — same URL as Monitor |

Long-term we keep **one** desktop surface: Control + Monitor. FastAPI remains the data/HTML source so CLI/`chancetime dashboard` still works headless. We do **not** aim to duplicate the full portfolio UI twice.

- Secrets stay in project `.env` / `secrets/`
- Spawns `uv run chancetime …` from the repo root
- System tray: show window, optional browser open, start/stop, kill all
- Child logs → `data/desktop-logs/`

## Prerequisites

- Rust toolchain (`rustc`, `cargo`)
- Node.js 18+
- Project deps with dashboard extra:

```bash
cd ~/Projects/chancetime
uv sync --extra dashboard
```

### Linux system libraries (required to compile)

**Arch / CachyOS:**

```bash
sudo pacman -S --needed webkit2gtk-4.1 librsvg base-devel libayatana-appindicator
```

**Debian / Ubuntu:**

```bash
sudo apt install libwebkit2gtk-4.1-dev librsvg2-dev patchelf build-essential \
  libayatana-appindicator3-dev
```

- Without `webkit2gtk-4.1`, compile fails at `webkit2gtk-sys`.
- Without `libayatana-appindicator`, the app still runs in **window-only** mode (no system tray; close quits).

## Dev

```bash
cd ~/Projects/chancetime/desktop
npm install

# Resolve project root when the cwd is not the repo (recommended):
export CHANCETIME_ROOT="$HOME/Projects/chancetime"

npm run dev
```

The shell walks ancestors for `pyproject.toml` named `chancetime` if `CHANCETIME_ROOT` is unset.

## Build

```bash
cd desktop
export CHANCETIME_ROOT="$HOME/Projects/chancetime"
npm run build
# Artifacts under src-tauri/target/release/bundle/
```

## Commands (Rust ↔ UI)

| Command | Effect |
|---------|--------|
| `get_status` | bot/dashboard/port/tray, last messages, project root |
| `start_dashboard` / `stop_dashboard` | `chancetime dashboard` on `127.0.0.1:8787` (skips spawn if port open) |
| `start_bot` / `stop_bot` | long-running `chancetime run` (process group kill) |
| `kill_all` | stop both process groups |
| `open_dashboard` | open URL if port 8787 is listening |
| `get_logs` | tail `data/desktop-logs/{bot,dashboard}.*.log` |
| `get_user_knobs` / `save_user_knobs_cmd` | non-secret `config/user.yaml` edits |
| `set_paper_indicator` | UI badge only (does not rewrite secrets) |

### “Buttons only change indicators?”

They do real work. Check:

1. **Port 8787** row — should flip to **open** after Start dashboard  
2. **Bot logs / Dash logs** — live tails from `data/desktop-logs/`  
3. If Start dashboard fails with **address already in use** → Kill all (or free the port), then Start again  

Bot needs a few seconds before the first poll line appears (default poll 30s).

## Safety

- Default bot config is paper-oriented; live still requires `PAPER_MODE=false` + CLI risk ack for intentional live paths.
- Closing the window **hides to tray**; **Quit** from tray exits and kills children.
- This shell never writes API keys. Prefer `config/user.yaml` for non-secret knobs.

## Layout

```
desktop/
  package.json          # @tauri-apps/cli scripts
  ui/                   # static HTML/CSS/JS (withGlobalTauri)
  src-tauri/
    src/lib.rs          # process control + tray
    tauri.conf.json
    capabilities/
```
