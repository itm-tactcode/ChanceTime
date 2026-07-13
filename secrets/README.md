# Secrets directory

**Never commit private keys or API secrets.**

This folder is gitignored except for this README.

## Expected files (local only)

| File | Purpose |
|------|---------|
| `kalshi.key` | Kalshi RSA private key PEM (`KALSHI_PRIVATE_KEY_PATH`) |
| `polymarket.key` | Polymarket US private key PEM (`POLYMARKET_PRIVATE_KEY_PATH`) |

## Setup

```bash
cp .env.example .env
# Put PEMs here, then point paths in .env:
#   KALSHI_PRIVATE_KEY_PATH=./secrets/kalshi.key
#   POLYMARKET_PRIVATE_KEY_PATH=./secrets/polymarket.key
chmod 600 secrets/*.key .env
```

API **key IDs** (UUIDs) live in `.env`, not in these files.

See also: [`docs/SECURITY.md`](../docs/SECURITY.md).
