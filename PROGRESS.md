# Progress Log

Ongoing record of completed work on **Chance Time** (`chancetime`). **Append new entries at the top** (newest first). Keep entries factual: what landed, where, how to verify.

Agents: update this file when finishing a meaningful unit of work. Also sync the “Current status” and “Next plan” sections in `AGENTS.md`. Keep **`SCROLL.md`** current when strategies change.

---

## 2026-07-13 — GitHub prep: modularize CLI, security, docs

### Structure
- Extracted poll orchestrator → `src/chancetime/bot.py`
- Split 2.4k-line `main.py` into `src/chancetime/cli/` (`run`, `live`, `books`, `research`, `config_cmds`, `llm_cmds`)
- Stable entry remains `chancetime.main:app` (desktop / PyInstaller / `__main__`)

### Security (light)
- Dashboard refuses non-loopback bind without `--allow-remote`
- `.gitignore` hardened (`secrets/*` allowlist README, exports/history/digests keepouts)
- `docs/SECURITY.md`, `secrets/README.md`, root `LICENSE` (MIT)
- Honest README: paper-first, no EV claim

### Docs
- README rewrite (layout, status through Phase 20, publish checklist)
- AGENTS / ORIENTATION / LIVE_READINESS / VPS notes refreshed for current architecture
- Fixed `test_arb_cross` hybrid LLM test to use isolated `spend_path` (local `$50` ledger no longer fails suite)

```bash
uv run chancetime version
uv run chancetime --help | head
uv run pytest -q
# Before first push: git status — no .env / secrets/*.key / data/*.db
```

**Note:** Paper session evidence remains weak EV (selective flat / small simple_edge stop-outs). Showcase is the **stack**, not a performance claim.

---

## 2026-07-13 — Phase 18 tools/search + strategy quality

### xAI live tools
- Calibration uses **Responses API** with server-side `web_search` + `x_search` when `llm.tools_enabled` / `calibrate_with_tools`
- System prompt encourages tools for time-sensitive markets; still JSON-only structured output
- Real-key tool calls skip cache (`cache_when_tools: false`); mock still caches
- Falls back to plain chat if tools fail

### Fees
- **Paper only:** `_paper_fill` applies `paper_fee_bps` / BBO; **live** `_live_fill` never uses paper fee model (venue reports fill/fees)

### Strategy quality
- `simple_edge` default **blend** prior  
- LLM higher thresholds + `min_confidence_no_tools`  
- Arb `require_bbo` + fee buffer  
- Per-strategy `max_size_usd`

```bash
# Restart bot after config change
uv run chancetime check-config | head
uv run pytest tests/test_llm.py tests/test_portfolio.py -q
```

---

## 2026-07-13 — Phase 17 BBO paper + per-strategy cap UI

### Logs verified
- After restart: **8** simple_edge fills, then `strategy_slots` dominates; other strategies still 0 signals.

### Settings
- Strategy grid: **w** + **cap** (`max_open` per strategy) + global `max_open_per_strategy`
- `strategies.<name>.max_open` in user.yaml; `0` = unlimited

### Phase 17 execution
- `use_bbo_paper`, `paper_fee_bps`, `max_spread`, `size_by_depth`, depth clip, fee-reduced contracts
- Risk: `wide_spread` / BBO half-spread for net-edge when book attached

```bash
uv run pytest tests/test_portfolio.py -q
# Restart bot after Save settings
```

---

## 2026-07-13 — Phase 16: cost-aware edge, strategy slots, knobs truth

### Bugfix
- **Suggestions showed `16/10` bag full** because `suggest.py` imported non-existent `snapshot_user_knobs` and silently fell back to hard-coded `max_open=10` (same number as commented `.env` / UI fallback — looked like env, wasn’t).
- **`snapshot_user_knobs()`** now loads **effective** `default.yaml + user.yaml` via `load_config`.
- Desktop `user-config snapshot` uses that path; Control Settings no longer depends on stale 10.

### Risk (Phase 16)
- `min_net_edge`, `assumed_half_spread`, `assumed_fee` — reject signals that don’t clear costs  
- `max_open_per_strategy` — caps concurrent opens per strategy name  
- Free-cash + mid band already present  
- Miss reasons: `net_edge`, `strategy_slots`  
- `bot_start` logs session risk knobs (restart required after YAML edits)

### Docs
- **AGENTS.md**: Personal P&amp;L phases **16–22**; SaaS renumbered **23+**; Phase 13 dual-leg after personal micro-live  

```bash
uv run chancetime user-config snapshot | head
uv run chancetime suggest-settings --account paper --json
uv run pytest tests/test_portfolio.py tests/test_presets_suggest.py -q
# Restart bot to load new risk knobs
```

---

## 2026-07-13 — Orientation doc + Ops tab deferred loading

### Done

1. **`docs/ORIENTATION.md`** — plain-language map of bot/desktop/books + what presets/suggest actually do  
2. **Desktop tab switch** — paint first (`rAF` + timeout); Ops loads readiness/accounts/presets **sequentially after paint** with cache  
3. Settings knobs cached so re-open is instant until force reload  

---

## 2026-07-13 — Phases 14–15: live readiness, Ops UI, presets, suggestions

### Done

1. **`docs/LIVE_READINESS.md`** — gates A–F before dual-leg live  
2. **CLI:** `presets`, `suggest-settings`, `readiness`  
3. **Desktop Ops tab** — readiness, accounts, digest/export/sync, history, presets, suggestions  
4. **Control** — account dropdown + start bot with `--account`  
5. **AGENTS.md** phase table: 13 dual-leg (after readiness), 14–15 done, Path B → 16+  

```bash
uv run chancetime readiness
uv run chancetime presets list
uv run chancetime suggest-settings --account paper
cd desktop && ./dev.sh   # Ops tab
```

Note: `presets apply conservative_paper` may have written `config/user.yaml` during smoke test.

---

## 2026-07-13 — Phase 11 multi-account, digests, tax export

### Done

1. **Accounts** — `config/accounts.yaml.example`; built-in paper / live / paper_bag  
   - `chancetime accounts`  
   - `chancetime run --account paper`  
2. **Digest** — `chancetime digest --account paper [--send] [--json]` → `data/digests/`  
   - Telegram if `TELEGRAM_*` set  
   - Dashboard `GET /api/digest`, multi-book toggle (all accounts)  
3. **Export** — `export --account paper --year 2026` → fills/closed/summary CSVs with ISO timestamps, tax_year, proceeds/cost_basis/gain_loss  
4. Tests: `tests/test_phase11.py`

```bash
uv run chancetime accounts
uv run chancetime digest --account paper
uv run chancetime export --account live --year 2026
# cron: chancetime digest --account paper --send
```

---

## 2026-07-13 — Phase 10 complete + Settings select contrast

### UI

- Settings **Source** dropdown: forced dark `color-scheme`, option colors, custom chevron (WebKit was grey-on-white)

### Phase 10

1. Settings **Record markets/BBO each poll** → `history.enabled` in user.yaml  
2. `load_bars_from_history` multi-venue JSONL → bars  
3. `chancetime backtest --history data/history/….jsonl`  
4. `chancetime list-history`  
5. Tests extended in `test_phase10_history.py`

```bash
uv run chancetime list-history
uv run chancetime backtest --history data/history/markets-20260713.jsonl --venue kalshi
uv run chancetime walk-forward --folds 2
```

---

## 2026-07-13 — Phase 9 done + Phase 10 history/backtest realism

### Tray false positive

`desktop/dev.sh` now detects ayatana via **pkg-config** + `/usr/lib` paths (not only `ldconfig`).  
GTK may still print “libayatana-appindicator is deprecated” — tray works; ignore or migrate to glib later.

### Phase 9 closed

Doctor, shadow mode, Settings/`user-config`, dashboard write + scan-arb, VPS docs.

### Phase 10 started

1. **`history` config** + `MarketHistoryRecorder` JSONL under `data/history/`
2. **CLI:** `record-history`, `history-to-csv`, `walk-forward`
3. **Backtest:** BBO/depth columns, `cost_model_for_venue`, depth-aware fills
4. Tests: `tests/test_phase10_history.py`

```bash
# enable continuous recording in user.yaml:
# history: { enabled: true }
uv run chancetime record-history --source mock
uv run chancetime walk-forward --folds 2
uv run chancetime backtest --venue kalshi
```

---

## 2026-07-13 — Phase 9 start: doctor, shadow, Settings, scan-arb

### Done

1. **`chancetime doctor`** — secrets presence, key files, paper/live DBs, dashboard deps
2. **`bot.shadow_mode`** — signals + risk, zero fills (paper or live)
3. **`chancetime user-config`** show|snapshot|apply — whitelist non-secret knobs
4. **Desktop Settings tab** — risk, data source, LLM budget, strategy on/weight, shadow
5. **Dashboard** — `GET/POST /api/user-config`, `/api/doctor`, `/api/scan-arb` + UI sections
6. **`docs/VPS_AND_BACKUPS.md`**
7. Tests: `tests/test_phase9.py`

### Still Phase 9 (optional next)

- Paper replay of live session fills
- More Settings polish (per-strategy edge for all strats)

### Verify

```bash
uv run chancetime doctor
uv run chancetime user-config snapshot
# desktop: Settings → change poll/shadow → Save → restart bot
# Monitor: Run mock scan / Doctor in embedded page
```

---

## 2026-07-13 — Fix mixed paper/live Monitor (stale sidecar + migrate)

### Cause

1. Desktop preferred **`desktop/sidecar/chancetime-cli`** over `.venv` → old single-DB code kept writing/reading `data/chancetime.db`
2. Running dashboard never restarted after dual-book change
3. Migrate left `venue_sync` open positions on paper book

### Fix

1. Spawn order: venv/uv first; sidecar only with `CHANCETIME_USE_SIDECAR=1`
2. `migrate-books` moves live-ish positions (`venue_sync` / `live_*`) to live.db
3. Monitor shell **PAPER | LIVE** buttons (reload iframe with `?book=`)
4. Dashboard reads `?book=` / `#book=` on load

### Verify

```bash
uv run chancetime migrate-books --force
# Kill all in desktop, restart API — health must show "books" not single db_path
curl -s http://127.0.0.1:8787/api/health | jq .
```

---

## 2026-07-13 — Phase 12 paper/live books + packaging + Monitor fix

### Done

1. **Monitor** — toolbar is 2rem; Start API hidden when port live; placeholder never half-page
2. **Separate books** — `data/paper.db` (default paper), `data/live.db` (live_micro)
3. **`chancetime migrate-books`** — split legacy `chancetime.db` (live fills → live.db)
4. **Dashboard** — PAPER / LIVE toggle; APIs take `?book=`
5. **Spawn** — `CHANCETIME_BIN` → sidecar → `.venv/python -m chancetime` → `uv run`
6. **Packaging** — `scripts/build-desktop.sh`, `desktop/PACKAGING.md`, sidecar README
7. Tests: `tests/test_books_dashboard.py`; suite 71 passed

### Verify

```bash
uv run chancetime migrate-books   # once
# Restart desktop / API so Monitor shows PAPER|LIVE
cd desktop && ./dev.sh
```

---

## 2026-07-13 — Phase 12 Monitor UX + denser dashboard

### Done

1. **Monitor chrome** — ~2rem toolbar; Start API hidden when port is live; iframe fills rest of window
2. **Scrollbars** — thin styled bars (WebKit + Firefox) on Control + dashboard HTML
3. **Dashboard HTML** — positions/fills above equity chart; tighter padding; more fills (25)
4. **Auto-start API** on desktop launch (background, skips if 8787 already open)
5. Window default 1024×720, min size set

### Verify

Restart `./dev.sh` and **restart API** (Kill all → Monitor tab) so HTML reload picks up denser layout.

---

## 2026-07-13 — Phase 12 Control + Monitor tabs (embed, not dual app)

### Decision

Opening a separate browser for the same features is a smell once the desktop shell grows. **One desktop surface:**

- **Control** — bot/API lifecycle, knobs, logs  
- **Monitor** — iframe of local FastAPI (positions/fills)  
- Browser open remains optional  

FastAPI stays the shared backend (CLI + embed). No second portfolio UI rewrite.

### Done

1. Tabbed UI + CSP `frame-src` for `127.0.0.1:8787`
2. Monitor auto-starts API server when tab opens
3. Docs: product shape in `desktop/README.md` + AGENTS Phase 12

### Verify

```bash
cd desktop && ./dev.sh
# Control → Start bot; Monitor tab → portfolio view without external browser
```

---

## 2026-07-13 — Phase 12 desktop usable (process groups + knobs + logs)

### Done

1. **Process groups** (`setsid` + kill process group) so Stop kills `uv` and Python children
2. **Dashboard port probe** — status shows port 8787 open; refuse double-spawn if already bound
3. **Spawn health check** — if process dies immediately, UI shows stderr (e.g. address already in use)
4. **Log viewer** — `get_logs` for bot/dashboard tails from `data/desktop-logs/`
5. **user.yaml knobs** — poll interval + strategy enable flags from desktop UI
6. Confirmed earlier: buttons were working; bot paper-looped; dash failed only when port held by orphan

### Verify

```bash
cd desktop && ./dev.sh
# Start dashboard → Port 8787 open → Open dashboard
# Start bot → Bot logs show markets_fetched
# Save user.yaml knobs → restart bot
```

---

## 2026-07-13 — Phase 12 Tauri desktop scaffold (9–11 deferred)

### Done

1. **`desktop/`** Tauri 2 app (`com.chancetime.desktop`)
   - Rust: start/stop bot + dashboard via `uv run chancetime …`, tray menu, hide-on-close, kill-on-quit
   - Static UI (`ui/`) with `withGlobalTauri` invoke API
   - Child logs → `data/desktop-logs/`
   - `CHANCETIME_ROOT` or walk-up to `pyproject.toml` name `chancetime`
2. **Docs** — `desktop/README.md`, AGENTS Phase 12 scaffold + 9–11 deferred, root README
3. **Gitignore** — `desktop/node_modules`, `src-tauri/target`, `gen`, desktop-logs

### Blocked on host (compile)

```bash
# Arch / CachyOS (password required once):
sudo pacman -S --needed webkit2gtk-4.1 librsvg

cd desktop
export CHANCETIME_ROOT="$HOME/Projects/chancetime"
npm install
npm run dev
# or: cd src-tauri && cargo check
```

Without `webkit2gtk-4.1`, `cargo check` fails at `webkit2gtk-sys` pkg-config.

### Not yet (later Phase 12)

- First-run / `user.yaml` editor in UI
- Bundled Python (no system `uv`)
- Signed installers / auto-update

---

## 2026-07-13 — Finish phases 6–8 + SCROLL education + grok-4.5

### Done

1. **Default LLM model → `grok-4.5`** (default.yaml, config defaults, .env.example)
2. **Phase 6** — `dual_leg_live_enabled` gate on live dual-leg arb
3. **Phase 7** — train-ml walk-forward holdout acc; simple_edge `blend` prior; SCROLL novice deep-dive
4. **Phase 8** — event-family exposure caps; cold strategy auto-skip from SQLite stats
5. Tests: `tests/test_phase8_risk.py`; suite green

### Verify

```bash
uv run chancetime train-ml
uv run pytest -q
# paper bag clean run
uv run chancetime run -c config/paper_bag.yaml --fresh-db --max-polls 5
```

---

## 2026-07-13 — user.yaml, --fresh-db, AGENTS phase reorg

### Done

1. **Config layers** — `deep_merge` + `config/user.yaml` overlay; `save_user_config()` for future dashboard
2. **`.env.example`** — secrets-first; ops knobs documented as YAML/`user.yaml`
3. **`run --fresh-db`** — opt-in delete of configured SQLite book (not automatic)
4. **AGENTS.md** — skipped “later” items → phases 10–11; **Tauri desktop (12)**; dual-leg live (13); SaaS 14–15; **web/mobile (16)**; stocks 17; harden 18
5. Tests: `tests/test_config_user.py`

### Verify

```bash
uv run pytest -q
uv run chancetime run -c config/paper_bag.yaml --fresh-db --max-polls 1
cp config/user.yaml.example config/user.yaml   # optional
```

---

## 2026-07-13 — Paper bag + Phase 8 strategy stats

### Done

1. **`config/paper_bag.yaml`** — paper multi-strategy: simple_edge, arb_cross, mean_revert, ml_edge; separate `data/paper_bag.db`
2. **Mock mid drift** — turnout market moves after poll 3 so mean_revert can fire
3. **`strategy_stats` SQLite table** — signals/fills/notional/closed/realized per strategy
4. **`chancetime strategies --stats`** + dashboard **`GET /api/strategies`**
5. Bot poll wires fill/close into strategy counters

### Verify

```bash
uv run chancetime strategies -c config/paper_bag.yaml
uv run chancetime run --config config/paper_bag.yaml --max-polls 5
uv run chancetime strategies -c config/paper_bag.yaml --stats
```

---

## 2026-07-13 — Phase 7 continue: train-ml + AGENTS later-item audit

### Done

1. **`chancetime train-ml`** — sklearn logistic on resolved fixture → `models/ml_edge.joblib`
2. **`chancetime strategies`** — enabled flags + weights inventory
3. **`ml_edge`** loads dict artifact `{pipeline, feature_names}`
4. **AGENTS.md** — current status refresh; “Later” items marked done or still-later honestly
5. Tests: `tests/test_train_ml.py`

### Verify

```bash
uv sync --extra ml
uv run chancetime train-ml
uv run chancetime strategies
uv run pytest -q
```

---

## 2026-07-13 — Phase 6 complete + Phase 7 start

### Phase 6 finish

1. **`cancel-order`** CLI (Kalshi DELETE + Polymarket cancel paths)
2. **`sync-positions`** — pull venue positions into SQLite (replaces kalshi/pm local opens)
3. **`export`** — fills + closed trades CSV under `data/exports/`
4. Live smoke → dashboard already via `live_book`

### Phase 7 start

1. **`mean_revert`** strategy + tests
2. **`news_impulse`** scaffold (LLM + news_context)
3. **`ml_edge`** stub (joblib load if present)
4. Config YAML + SCROLL + weights registration

### Verify

```bash
uv run pytest -q
uv run chancetime sync-positions
uv run chancetime export
uv run chancetime status
# enable mean_revert in config, paper only:
# uv run chancetime run --once
```

---

## 2026-07-13 — Phase 6 start: micro live execution

### Done

1. **`execution/auth.py`** — Kalshi RSA-PSS + Polymarket US Ed25519 (base64 secret file)
2. **`live_kalshi.py` / `live_polymarket.py`** — balance + create order (IOC defaults)
3. **`ExecutionEngine`** — live path with risk ack + session/order USD caps
4. **CLI** — `live-ping`, `live-smoke`, `run --live --i-understand-this-spends-real-money`
5. **`config/live_micro.yaml`** — $5 order / $20 session caps; strategies off by default
6. **Tests** — `tests/test_live_phase6.py` (58+ suite)

### Human runbook (no auto live orders)

```bash
uv run chancetime live-ping
# pick liquid tickers/slugs, then:
uv run chancetime live-smoke --venue kalshi --kalshi-ticker TICKER --size 5 \
  --i-understand-this-spends-real-money
uv run chancetime live-smoke --venue polymarket --pm-slug SLUG --size 5 \
  --i-understand-this-spends-real-money
```

---

## 2026-07-13 — Phase 5: SQLite, metrics, dashboard, Docker

### Done

1. **`persistence/store.py`** — SQLite book: positions, fills, closed_trades, equity_snapshots, signal_stats; load on bot start / save each poll
2. **Config** — `persistence.*`, `dashboard.*` in YAML; `data/*.db` gitignored
3. **Metrics** — `monitoring/metrics.py` logs + stores equity / signal counts (LLM spend in extra)
4. **Dashboard** — FastAPI read-only UI + `/api/*`; CLI `chancetime dashboard` / `status`
5. **Docker** — `Dockerfile`, `docker-compose.yml` (bot, loop profile, ui profile)
6. **Extras** — `dashboard`, `ml` (sklearn reserved) in `pyproject.toml`
7. **Docs** — AGENTS Phase 5 done + §12 ML opinion; tests `test_persistence.py`

### Verify

```bash
uv sync --group dev --extra dashboard
uv run pytest -q && uv run mypy src/chancetime
uv run chancetime run --once
uv run chancetime status
uv run chancetime dashboard   # http://127.0.0.1:8787
```

---

## 2026-07-13 — Phase 4.5: pair BBO, depth sizing, dual-leg paper + roadmap

### Done

1. **Market BBO fields** — `yes_bid` / `yes_ask` / sizes / `has_bbo` on `Market`; helpers for YES/NO ask + depth USD
2. **Kalshi orderbook** — `fetch_orderbook` + `enrich_bbo_markets` (bids → implied YES ask via reciprocity)
3. **Polymarket US** — BBO enrich stores bid/ask/sizes; `enrich_bbo_markets` for pair-only
4. **`data_layer/bbo.py`** — `enrich_pairs_bbo` only hits legs in matched pairs
5. **`arb_cross`** — executable edge `1 − yes_ask − no_ask − fee_buffer`; depth sizing; `arb_group_id`; `require_bbo`
6. **Risk** — atomic arb group approve; **execution** dual-leg paper + hard caps (`max_arb_pairs_per_poll`, pair/notional/leg caps)
7. **CLI** — `scan-arb --bbo` / `--require-bbo`
8. **Docs** — `AGENTS.md` phases 4.5–13 + personal vs SaaS notes; `SCROLL.md` arb rewrite
9. **Tests** — `tests/test_arb_bbo_dual_leg.py` (+ suite green)

### Verify

```bash
uv run ruff check src tests && uv run mypy src/chancetime && uv run pytest -q
uv run chancetime scan-arb --source mock
# live: uv run chancetime scan-arb --deep --bbo --limit 200
```

---

## 2026-07-13 — Lint / mypy / suite clean

### Done

1. **Ruff** — SIM103 inline parlay condition (`kalshi.py`); E501 wrap `SYSTEM_MATCH` (`match_venues.py`); drop unused `pair_markets` import (`main.py`)
2. **mypy** — rename score/signal loop vars in `scan-arb` so `s: float` no longer shadows `Signal`
3. **Verify** — ruff, pytest 42, mypy clean; mock scan-arb ok; live `--source both` shows subject+event PM titles, 0 pairs (demo Kalshi vs PM season winners)

### Verify

```bash
uv run ruff check src tests && uv run ruff format src tests
uv run pytest -q && uv run mypy src/chancetime
uv run chancetime scan-arb --source mock
```

---

## 2026-07-13 — Fix suite after Phase 4 dual-mock

### Done

1. **`normalize_title`** — currency/number collapse (`$100,000` ↔ `100000 USD`) so BTC mock pair scores ≥0.72
2. **Ruff RUF002** — EN dash → hyphen in matching/polymarket_us docstrings
3. **`test_mock_list_markets`** — expects dual-venue mock fixtures (kalshi/pm/mock), not first-row MOCK only

### Verify

```bash
uv run ruff check src tests && uv run pytest -q && uv run mypy src/chancetime
uv run chancetime scan-arb --source mock
```

---

## 2026-07-13 — Phase 20 research loop + Settings UI knobs

### Settings UI
- Desktop Settings: Phase 19 knobs (deploy %, cluster, time-to-event, hot-reload, discovery, LLM calls/poll)
- `user_knobs.snapshot_user_knobs` / `snapshot_to_overrides` wired for all new fields

### Phase 20
1. **`chancetime scorecard`** — per-strategy/family edge after estimated fees; gate PASS/HOLD
2. **`walk-forward`** — costs on by default (`--zero-cost` to disable)
3. **Digest** embeds scorecard block
4. **Dashboard** `/api/scorecard` + “Edge after cost” panel

### Verify
```bash
uv run pytest tests/test_phase20_scorecard.py -q
uv run chancetime scorecard --account paper
uv run chancetime walk-forward --folds 2
```

---

## 2026-07-13 — Phase 19 portfolio sophistication

### Done
1. **`max_deploy_pct`** — cap open notional as % of cash_basis (miss: `deploy_cap`)
2. **`cluster_key` + `max_cluster_exposure_usd`** — series/period clusters (e.g. all KXNBA-27)
3. **Time-to-event** — `min_hours_to_close` / `max_days_to_close`; Kalshi/PM `close_time` parse
4. **`bot.hot_reload_risk`** — re-read risk + strategy caps/weights each poll (user.yaml true)
5. **`llm_calibrated.max_llm_calls_per_poll: 2`** (was 3)

### Verify
```bash
uv run pytest tests/test_phase19_portfolio.py tests/test_phase8_risk.py -q
uv run pytest -q
```

---

## 2026-07-13 — Mid-band LLM match adjudication

### Idea
Fuzzy/structural auto-accepts high scores; **mid-band only** (default 0.40–`min_match_score`)
gets a **tiny** Grok yes/no (`llm_adjudicate_candidates`) instead of dumping whole catalogs.

### Config (`arb_cross`)
- `use_llm_match: true` (enabled in user.yaml)
- `llm_match_band_low: 0.40`
- `min_match_score: 0.72` (auto floor / band high)
- `llm_match_min_confidence: 0.75`
- `llm_match_max_each: 24` (max candidates per call)
- `llm_bulk_fallback: false` (heavy full-list match off)

### Notes
- Edge thresholds unchanged; this only improves **same-event** pairing.
- Logs: `llm_adjudicate_done` / `llm_adjudicate_pair`

---

## 2026-07-13 — Real dual-list arb discovery (v2)

### Problem
`source=both` open books rarely overlap (Kalshi tennis props vs PM baseball futures).
Even when dual-listed contracts existed, title shapes differed so fuzzy scores stuck ~0.4
(`Will Cleveland win Pro Basketball Finals?` vs `Cleveland Cavaliers - 2027 NBA Champion`).

### Done
1. **Dual-venue search** in `deep_discover` — same queries hit Kalshi *and* Polymarket
2. **Kalshi series map** — `KXNBA`, `KXBTCMAXY`, `KXFED`, `KXMLBWS`, … via `search_markets`
3. **Structural matching** — entity + event-family + year/strike (NBA teams, BTC ladders)
4. **Rejects false pairs** — different teams; Fed *level* vs Fed *decision*
5. **Bot path** — every `data.discovery_every_polls` (default 5), refresh discovery when
   `source=both` + `arb_cross.deep_discovery`; merges into market pool + caches pairs
6. **Aliases** — bot/strategy load `config/arb_aliases.json` automatically
7. Edge thresholds **unchanged** (`min_spread` + `fee_buffer` + `require_bbo`)

### Verified live
```bash
uv run chancetime scan-arb --source both --deep --limit 120 --debug
# pairs=37 (NBA champs + BTC year-high ladders), top_score=1.0
# arb_signals=0 at thr=0.04+fee=0.03 — real pairs, no free money after costs
uv run pytest -q  # 98 passed
```

---

## 2026-07-13 — Arb deep discovery (prod-first)

### Done

1. **Kalshi** — cursor pagination + `mve_filter=exclude`; dollar mid prices; better titles (`yes_sub_title` / rules)
2. **Polymarket** — multi-page list + **`search_markets(query)`** for discovery
3. **`arb_discovery.deep_discover`** — deep open books + default query set (WS, Fed, MVP, …)
4. **CLI** `scan-arb --deep [--limit N] [--save-aliases] [-q query]`
5. **Aliases file** `config/arb_aliases.json`
6. Skip empty-book placeholder mids (~0.50 with no depth)

### Verified live

```bash
uv run chancetime scan-arb --deep --limit 120 --debug
# Found 14+ MLB All-Star MVP dual listings; huge spreads often = thin Kalshi mid
```

### Notes

- Prefer **prod** for real books; paper until signed dual-venue orders exist.
- Large “arb” vs 0.50 Kalshi may be empty books — inspect bid/ask before believing free money.

---

## 2026-07-13 — Phase 4 follow-up: title fix + LLM match

### Done

1. **Polymarket titles** — use `title` (subject) + `question` (event), not question alone
2. **Kalshi** — skip multi-leg/parlay blobs (`KXMVE*`, concatenated yes/no legs); over-fetch then filter
3. **PM pagination** — fill requested limit across pages
4. **LLM venue match** `llm/match_venues.py` — opt-in Grok pairing; merge with fuzzy
5. **CLI** `scan-arb --debug` (sample titles + top scores), `--llm-match`, `--limit`

### Why 0 pairs on first live run

Kalshi open page ≠ Polymarket first page (props/parlays vs season winners); demo Kalshi ≠ PM US prod; PM title bug hid team names.

### Verify

```bash
uv run pytest -q
uv run chancetime scan-arb --source mock
uv run chancetime scan-arb --source both --debug --limit 80
# optional:
uv run chancetime scan-arb --source both --llm-match --limit 50
# .env: KALSHI_ENV=prod
```

---

## 2026-07-13 — Phase 4: cross-venue matching + arb_cross

### Done

1. **Polymarket US client** — gateway markets + BBO enrich
2. **Matching** + **`arb_cross`** + composite `both` + mock dual-list + `scan-arb`
3. SCROLL / AGENTS / config updated

---

## 2026-07-13 — Phase 3 positions/weights/alerts + P1/P2 optionals

### Done

**Phase 3**
1. **Portfolio** `risk/portfolio.py` — open / reduce / close, MTM, realized PnL
2. **RiskEngine** — strategy weights, signal de-dupe by edge×strength, TP/SL exits
3. **Alerts** `monitoring/alerts.py` — log always; optional Telegram (`TELEGRAM_BOT_TOKEN` + `CHAT_ID`)
4. **Bot loop** — manage positions each poll, alert on fills/exits/halts/budget low

**Phase 1 optionals**
1. **`prior_mode: trailing_mean`** on `simple_edge` (fade vs own recent mean)
2. **Partial fills** in backtester via liquidity participation (`CostModel`)

**Phase 2 optionals**
1. **News context** (`llm.news_context` / `news_context_file`)
2. **Cache bust** on large mid move (`price_move_bust`)
3. **Batch calibrate** (`calibrate_batch` + CLI `--batch`)

### Verify

```bash
uv run pytest -q
uv run chancetime run --once
uv run chancetime backtest --prior trailing_mean --edge 0.05
uv run chancetime calibrate --batch
```

### Notes

- Telegram optional; without token, LogAlerter only.
- Next: Phase 4 cross-platform arb / better Polymarket US market list.

---

## 2026-07-13 — Phase 2 LLM calibration + official venue docs

### Done

1. **AGENTS.md venue docs (critical)**
   - Kalshi: https://docs.kalshi.com/welcome
   - Polymarket **US**: https://docs.polymarket.us/api-reference/introduction  
   - Explicitly **not** international CLOB (`docs.polymarket.com` / wallet) for MVP
   - Hosts: `api.polymarket.us` (auth), `gateway.polymarket.us` (public)
2. **LLM Phase 2**
   - `llm/schemas.py`, `calibrate.py`, `review.py`
   - Strategy `llm_calibrated` (opt-in in YAML; screened + per-poll call cap + budget)
   - Disk cache via `llm_cache/`; post-trade review on bot stop
   - CLI: `chancetime llm-smoke`, `chancetime calibrate`
3. **SCROLL.md** full section for `llm_calibrated`
4. Public Polymarket US client base → `gateway.polymarket.us`

### Verify

```bash
uv run pytest -q
uv run chancetime llm-smoke          # real Grok if XAI_API_KEY set
uv run chancetime calibrate --yes 0.42 -m "Will the Fed cut rates?"
# equip LLM strategy: strategies.llm_calibrated.enabled: true in config/default.yaml
uv run chancetime run --once
```

### Notes

- Keep `llm_calibrated.enabled: false` until you want to spend credits each poll.
- User funded ~$5 on console.x.ai; daily cap still defaults to $5 in config.

---

## 2026-07-13 — Phase 1 backtester + SCROLL.md + repo rename

### Done

1. **Backtesting** (`src/chancetime/backtesting/`)
   - CSV loader, fee/slippage `CostModel`, event-driven `BacktestEngine`
   - Settle on `resolve` (yes/no); EOD MTM for open; PnL / hit rate / max drawdown
   - Fixture: `backtests/fixtures/sample_series.csv`
   - CLI: `chancetime backtest [-f ...] [--edge] [--grid] [--fee-bps] [--slip-bps]`
   - Param grid helper for `simple_edge` thresholds
2. **SCROLL.md** — strategy-first educational doc (item bag, `simple_edge` depth, how to add strategies)
3. **AGENTS.md** — Phase 1 done; Phase 2 next; SCROLL keep-current rule
4. **Repo directory** renamed `~/Projects/trading-bot` → `~/Projects/chancetime`

### Verify

```bash
cd ~/Projects/chancetime   # or trading-bot if rename not yet done
uv run pytest -q
uv run chancetime backtest --grid
uv run chancetime backtest --edge 0.08
```

### Notes

- Fixture is synthetic; real venue history still needed for serious eval.
- `simple_edge` prior remains 0.5 (documented limitations in SCROLL).
- Polymarket US key material may be shorter than Kalshi RSA PEM; still stored as file path.

---

## 2026-07-12 — Polymarket US credentials (Kalshi-shaped account API)

### Done

1. **Clarified product split**
   - **Polymarket US** (`docs.polymarket.us` / `api.polymarket.us`): account + UUID + RSA file (like Kalshi)
   - International Polygon/CLOB: not wired
2. **Config**
   - `POLYMARKET_API_KEY` (UUID)
   - `POLYMARKET_PRIVATE_KEY_PATH` (canonical) + `POLYMARKET_API_SECRET` as path alias
   - Shared `resolve_private_key_path()` helper
3. **Client skeleton** `data_layer/polymarket_us.py` — public market list best-effort; PEM load ready; no trading/signing yet
4. **Docs / check-config** updated; tests for path alias

### Verify

```bash
# .env (example)
# POLYMARKET_API_KEY=<uuid>
# POLYMARKET_API_SECRET=./secrets/polymarket.key   # or POLYMARKET_PRIVATE_KEY_PATH=...

uv run pytest tests/test_kalshi_paths.py -q
uv run chancetime check-config
```

---

## 2026-07-12 — Kalshi secrets: private key file path (not inline PEM)

### Done

1. **Env / config**
   - `KALSHI_PRIVATE_KEY_PATH` (canonical) → resolved `Path` on `AppConfig`
   - Legacy `KALSHI_API_SECRET` accepted as **same path** (not inline PEM)
   - Rejects env values that look like pasted PEM (`BEGIN PRIVATE KEY`)
2. **Helpers** `utils/paths.py` — `project_root`, `resolve_path`, `load_text_secret`
3. **KalshiClient** takes `api_key_id` + `private_key_path`; can `load_private_key_pem()` (signing still TODO)
4. **Docs** `.env.example`, README, AGENTS; `check-config` shows path + file exists flag
5. **Tests** `tests/test_kalshi_paths.py`

### Verify

```bash
# .env
# KALSHI_API_KEY=<uuid>
# KALSHI_PRIVATE_KEY_PATH=./secrets/kalshi.key

uv run pytest tests/test_kalshi_paths.py -q
uv run chancetime check-config   # kalshi_private_key_file_exists / credentials_configured
```

### Notes

- Authenticated request signing (RSA-PSS headers) still not implemented; market list remains public.

---

## 2026-07-12 — Rebrand: Chance Time + mini-game slogans

### Done

1. **Rename** package/CLI `trading_bot` / `trading-bot` → **`chancetime`**
   - `src/chancetime/`, `pyproject.toml` entrypoint `chancetime`, bot config name `chance-time`
   - Docs: `README.md`, `AGENTS.md`, `.env.example`
2. **Flair** (`src/chancetime/flair.py`)
   - Fill → **got item** (paper: `got item (paper)`)
   - Reject / risk block → **miss**
   - Wired into execution + risk log events
3. **Tests** `tests/test_flair.py` for slogans + fill/reject notes

### Verify

```bash
uv sync --extra dev
uv run pytest -q
uv run chancetime run --once   # expect slogan=chance time on start; got_item on paper fills
```

### Notes

- Repo directory may still be `trading-bot` on disk; product name is Chance Time / `chancetime`.
- Phase 1 (backtester) still next.

---

## 2026-07-11 — Phase 0: project bootstrap (skeleton + paper loop)

### Done

1. **Project scaffolding**
   - Package layout under `src/chancetime/` (`data_layer`, `strategies`, `llm`, `risk`, `execution`, `backtesting`, `monitoring`, `utils`)
   - `pyproject.toml` (hatchling, Python ≥3.12, core + dev extras, `chancetime` CLI entry)
   - `.gitignore`, `.env.example`, `config/default.yaml`, `README.md`
2. **Config + logging**
   - `utils/config.py` — dotenv + YAML + env overrides → Pydantic `AppConfig` (`PAPER_MODE` default true)
   - `utils/logging.py` — structlog setup
3. **LLM module (cost-aware stub)**
   - `llm/client.py` — async `GrokClient` via OpenAI-compatible `https://api.x.ai/v1`; mock path when no `XAI_API_KEY`
   - Daily spend tracker + `DailyBudgetExceeded`; rough $/token estimates; call logging
   - `llm/cache.py` — in-memory (+ optional disk) TTL cache
   - `llm/prompts.py` — short calibration / post-trade prompt stubs
4. **Data layer**
   - Normalized `Market` model; `MockMarketClient` for offline paper runs
   - `KalshiClient` skeleton (public `GET /markets`, best-effort normalize; no orders)
5. **Strategies / risk / execution**
   - `strategies/base.py` — `BaseStrategy`, `Signal`, `Side`
   - `strategies/simple_edge.py` — threshold vs naive 0.5 prior + liquidity filter
   - `risk/engine.py` — max size / open positions / daily loss / consecutive-error halt
   - `execution/engine.py` — paper fills (mid ± slippage); **live path rejects** (not implemented)
6. **Main loop**
   - `main.py` — Typer CLI: `run [--once|--max-polls]`, `check-config`, `version`
   - Async poll: fetch → strategies → risk → paper execute
7. **Tests**
   - `tests/test_*.py` — config, mock data, simple_edge, LLM mock/cache/budget, risk/execution, single-poll bot loop

### Verify

```bash
# from project root (uv recommended)
uv sync --extra dev
uv run pytest -q
uv run chancetime check-config
uv run chancetime run --once
uv run ruff check src tests
```

### Notes / follow-ups

- Polymarket client not started (Kalshi skeleton only + mock).
- Official `xai_sdk` optional extra not required; OpenAI-compatible client used.
- Simple-edge fair prior is 0.5 (placeholder); LLM calibration not wired into signals yet.
- No historical backtester yet → **Phase 1**.

---
