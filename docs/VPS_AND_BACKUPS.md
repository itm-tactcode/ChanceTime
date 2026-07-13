# VPS deploy & backups (Phase 9)

Personal stack — not multi-tenant SaaS.

**Security:** Do not expose the FastAPI dashboard (`:8787`) to the public internet. It has **no authentication** and can load venue credentials for balance routes. Prefer SSH tunnel or VPN. See `docs/SECURITY.md`.

## Suggested layout on a small VPS

```text
/opt/chancetime/          # git clone or rsync
  .env                    # secrets only (chmod 600)
  secrets/*.key
  config/user.yaml
  data/paper.db
  data/live.db
  .venv/
```

```bash
# Install
sudo apt update  # or distro equivalent
curl -LsSf https://astral.sh/uv/install.sh | sh
cd /opt/chancetime
uv sync --extra dashboard
cp .env.example .env   # fill secrets
uv run chancetime doctor
```

## Run under systemd (paper bot)

`/etc/systemd/system/chancetime-bot.service`:

```ini
[Unit]
Description=Chance Time paper bot
After=network.target

[Service]
Type=simple
User=chancetime
WorkingDirectory=/opt/chancetime
Environment=CHANCETIME_ROOT=/opt/chancetime
ExecStart=/opt/chancetime/.venv/bin/chancetime run -c config/default.yaml
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Optional dashboard (localhost only; tunnel with SSH):

```ini
ExecStart=/opt/chancetime/.venv/bin/chancetime dashboard --host 127.0.0.1 --port 8787
```

```bash
ssh -L 8787:127.0.0.1:8787 user@vps
```

## Backups

Daily cron (example):

```bash
#!/bin/bash
set -euo pipefail
STAMP=$(date +%Y%m%d)
DEST=/var/backups/chancetime/$STAMP
mkdir -p "$DEST"
cp -a /opt/chancetime/data/*.db "$DEST/" 2>/dev/null || true
cp -a /opt/chancetime/config/user.yaml "$DEST/" 2>/dev/null || true
# Do NOT copy .env to unencrypted offsite without care
find /var/backups/chancetime -maxdepth 1 -mtime +14 -type d -exec rm -rf {} +
```

Restore: stop bot → copy `paper.db` / `live.db` back → start bot.

## Desktop on VPS

Usually unnecessary. Prefer CLI + SSH tunnel to dashboard. Desktop packaging is for your local machine (`scripts/build-desktop.sh`).
