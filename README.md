# Chance Time (`chancetime`)

Paper-first **prediction-market** bot for **Kalshi** and **Polymarket US**, with a deterministic Python core and optional **Grok (xAI)** intelligence.

Named after the mini-game. The risk engine is not random.

> **Not financial advice.** Default mode is **paper trading**. Live orders require explicit risk acknowledgment and hard dollar caps. This project is a research / ops lab — it does **not** claim positive expected value out of the box.

Mini-game flair in logs: fill → **got item** · risk reject → **miss**

---

## Status (2026-07-13)

| Area | State |
|------|--------|
| Phases **0–12** | Ops stack: multi-book, digests, history, desktop shell |
| Phases **16–20** | Cost-aware risk, BBO paper, LLM tools, portfolio filters, scorecard |
| Live path | Signed Kalshi + Polymarket US; micro caps; dual-leg gated |
| Default strategies | Selective / often flat after harden — **edge not proven** |
| GitHub readiness | Structure + docs cleanup; keep secrets local (see [Security](#security)) |

Full history: [`PROGRESS.md`](PROGRESS.md) · agent rules: [`AGENTS.md`](AGENTS.md) · strategies: [`SCROLL.md`](SCROLL.md)

---

## Features

- **Pluggable strategies** — `simple_edge`, `llm_calibrated`, `arb_cross`, `mean_revert`, `news_impulse`, `ml_edge`
- **Risk engine** — position / family / cluster caps, free-cash, net-edge after costs, circuit breaker, cold strategies
- **Paper execution** — BBO-aware fills, paper fees; separate SQLite books (`paper` / `live`)
- **Cross-venue discovery** — structural + optional LLM mid-band match; fee-aware arb gates
- **LLM** — Grok calibrate / review / rare news brief; durable daily spend ledger; tool call limits
- **Research** — backtest, walk-forward (costs on), scorecard, market history JSONL
- **Ops** — digests, tax-oriented CSV export, presets, suggest-settings, doctor, readiness
- **UI** — FastAPI dashboard (loopback) + optional Tauri desktop control shell

---

## Requirements

- Python **3.12+**
- [uv](https://docs.astral.sh/uv/) (recommended)

## Quick start

```bash
git clone <your-fork-url> chancetime && cd chancetime
cp .env.example .env
# Keep PAPER_MODE=true. Add keys only when you need live/public signed calls.

uv sync --group dev

# Mock paper poll (no venue keys)
uv run chancetime run --once

# Continuous paper loop
uv run chancetime run

# Sanity
uv run chancetime doctor
uv run chancetime check-config
uv run pytest -q
```

**Common commands**

```bash
uv run chancetime status                  # SQLite book summary
uv run chancetime strategies --stats
uv run chancetime scorecard --account paper
uv run chancetime scan-arb --source mock
uv run chancetime backtest --grid
uv run chancetime dashboard               # http://127.0.0.1:8787
```

**Desktop (optional)** — Linux needs `webkit2gtk-4.1`; see [`desktop/README.md`](desktop/README.md).

```bash
cd desktop && npm install && CHANCETIME_ROOT="$(pwd)/.." npm run dev
```

**Docker (paper)**

```bash
docker compose run --rm bot
docker compose --profile ui up dashboard
```

If you moved the project and `uv run` fails with stale shebangs: `rm -rf .venv && uv sync --group dev`.

---

## Project layout

```text
src/chancetime/
  bot.py              # Poll loop orchestrator (data → strategies → risk → exec)
  cli/                # Typer commands (run, books, research, live, config, llm)
  data_layer/         # Kalshi, Polymarket US, mock, matching, BBO, history
  strategies/         # Pluggable BaseStrategy implementations
  risk/               # Portfolio, families, cold strategies, filter_signals
  execution/          # Paper + signed live clients
  llm/                # Grok client, cache, calibrate, news brief, spend
  persistence/        # SQLite books, export, sync
  backtesting/        # Event engine, walk-forward, fees
  monitoring/         # Alerts, digest, scorecard, poll metrics
  dashboard/          # Optional FastAPI status UI
  utils/              # Config, accounts, presets, knobs, doctor
config/               # default.yaml + examples (user.yaml gitignored)
desktop/              # Tauri 2 shell
docs/                 # Orientation, live readiness, VPS, security
tests/
```

Config layers:

| Source | Purpose |
|--------|---------|
| `.env` | Secrets + `PAPER_MODE` only |
| `secrets/*.key` | Venue private keys (gitignored) |
| `config/default.yaml` | Non-secret defaults |
| `config/user.yaml` | Local overrides (gitignored) |
| `config/accounts.yaml` | Named books (gitignored; example provided) |

---

## Configuration & venues

Copy `.env.example` → `.env`. Both US venues use **API Key ID (UUID)** + **private key PEM file**:

```bash
KALSHI_API_KEY=your-uuid
KALSHI_PRIVATE_KEY_PATH=./secrets/kalshi.key
KALSHI_ENV=prod   # or demo

POLYMARKET_API_KEY=your-uuid
POLYMARKET_PRIVATE_KEY_PATH=./secrets/polymarket.key
```

Docs: [Kalshi](https://docs.kalshi.com/welcome) · [Polymarket US](https://docs.polymarket.us/api-reference/introduction)  
International Polymarket (Polygon CLOB) is **not** wired here.

---

## Safety

1. **`PAPER_MODE=true` by default**
2. Live: `--live --i-understand-this-spends-real-money` + execution caps
3. LLM output is **advisory** — risk + execution decide
4. Durable LLM budget / tool limits (see `config/default.yaml` → `llm`)
5. Dashboard defaults to **loopback**; use `--allow-remote` only on trusted nets

Details: [`docs/SECURITY.md`](docs/SECURITY.md) · live gates: [`docs/LIVE_READINESS.md`](docs/LIVE_READINESS.md)

---

## Documentation map

| Doc | Audience |
|-----|----------|
| [`docs/ORIENTATION.md`](docs/ORIENTATION.md) | Mental model (bot / desktop / books) |
| [`SCROLL.md`](SCROLL.md) | Strategy guide for humans |
| [`AGENTS.md`](AGENTS.md) | Architecture + phases for coding agents |
| [`PROGRESS.md`](PROGRESS.md) | Changelog of what landed |
| [`docs/VPS_AND_BACKUPS.md`](docs/VPS_AND_BACKUPS.md) | Deploy notes |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Secrets & exposure hygiene |
| [`desktop/README.md`](desktop/README.md) | Tauri shell |

---

## Publishing to GitHub

```bash
# From a clean machine state — never force-add secrets
git init
git add .
git status   # confirm .env, secrets/*.key, data/*.db are NOT listed
git commit -m "Initial public Chance Time snapshot"
# create empty repo on GitHub, then:
git remote add origin git@github.com:YOU/chancetime.git
git push -u origin main
```

If keys ever hit a remote, **rotate them immediately**.

---

## License

[MIT](LICENSE) — use at your own risk; prediction markets and live trading can lose money.
