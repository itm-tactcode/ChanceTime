# Security notes (Chance Time)

Personal trading stack. **Not** multi-tenant SaaS. Treat every machine that holds keys as a production wallet host.

## Threat model (light)

| Asset | Risk if leaked |
|-------|----------------|
| `.env` / API key IDs + PEMs under `secrets/` | Venue account takeover, real orders, balance theft |
| Telegram bot token | Spam / alert hijack |
| `XAI_API_KEY` | LLM bill drain |
| SQLite books (`data/*.db`) | P&L / strategy disclosure (not auth material) |
| Dashboard on LAN | Read of books; balance routes if keys loaded |

## Controls already in place

1. **Paper by default** — `PAPER_MODE=true`; live needs `--i-understand-this-spends-real-money` + caps.
2. **Secrets only in env / files** — never in YAML knobs or `user.yaml`.
3. **`.gitignore`** — `.env`, `secrets/*`, DBs, history, digests, exports, LLM spend/cache.
4. **`check-config` / `doctor`** — redact secret values; report presence only.
5. **Dashboard bind** — default `127.0.0.1`; non-loopback requires `--allow-remote` (still **no auth**).
6. **User knobs API** — writes non-secret YAML only; not intended for public internet.
7. **LLM daily budget** — durable spend ledger; tools rate-limited.

## Before first `git push`

```bash
# Confirm no secrets staged
git status
git check-ignore -v .env secrets/kalshi.key data/paper.db
# Optional: scan working tree (requires gitleaks or similar)
# gitleaks detect --source . --no-git
```

**If a key was ever committed:** rotate it on the venue / xAI immediately; treat history as burned.

## Operator hygiene

- `chmod 600 .env secrets/*.key`
- Prefer loopback dashboard + Tauri desktop over VPS public ports
- On VPS: firewall deny 8787 from WAN; reverse proxy + auth if you must expose UI
- Separate **demo** vs **prod** Kalshi keys; never paste PEMs into chat/issues
- Rotate keys after machine compromise or accidental log paste

## What this is *not*

- No multi-user auth / RBAC
- No encryption at rest for SQLite
- No formal pen-test
- Desktop shell can spawn local CLI processes — assume a local-user threat model

## Reporting

This is a personal open-source-style project. If you find a vulnerability in a public fork, open a private report to the maintainer rather than filing a public issue with exploit details.
