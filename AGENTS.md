# AGENTS.md — Chance Time (`chancetime`)

**Project / display name:** Chance Time  
**Package / CLI:** `chancetime`  
**Primary Language:** Python 3.12+  
**Target Platforms (MVP):** Kalshi + Polymarket US (prediction markets)  
**Later:** Alpaca for stocks/options  
**LLM Provider:** Grok API (xAI) via official `xai_sdk` or OpenAI-compatible client  
**Core Philosophy:** Modularity, safety-first (paper trading), rigorous backtesting, cost-controlled intelligence, human-in-the-loop oversight where needed.  
**Flair:** Mini-game slogans in logs — fill → `got item`, reject → `miss` (see `src/chancetime/flair.py`). Named after the mini-game; the risk engine is not random.

## Official venue API docs (use these — not generic “international Polymarket”)

Agents: **do not** invent venue APIs from memory or from the international Polygon/CLOB stack when integrating US products.

| Venue | What we use | Primary docs | Auth model |
|-------|-------------|--------------|------------|
| **Kalshi** | US prediction exchange API | https://docs.kalshi.com/welcome · [API keys](https://docs.kalshi.com/getting_started/api_keys) · [Environments](https://docs.kalshi.com/getting_started/api_environments) | Account-based: **API Key ID (UUID)** + **private key file**. **Demo and prod credentials are separate** (demo.kalshi.co vs kalshi.com). Public `GET /markets` needs no key; `KALSHI_ENV` only selects the host. Use `mve_filter=exclude` to drop combos. |
| **Polymarket US** | CFTC-regulated **US** product | https://docs.polymarket.us/api-reference/introduction | Account-based (same *shape* as Kalshi): UUID + private key file. **Not** wallet/CLOB. |
| Polymarket international | **Path C** module `crypto_updown/` — not mixed into US bot | https://docs.polymarket.com (Gamma / CLOB / relayer) | Wallet L1 + CLOB L2 for live later; Phase 28 is public data + paper only |

**Polymarket US hosts (from US docs):**

- Authenticated trading: `https://api.polymarket.us`
- Public market data gateway: `https://gateway.polymarket.us`
- Developer keys UI: https://polymarket.us/developer

**Kalshi starting points:** welcome → API keys → demo env → OpenAPI specs on docs.kalshi.com.

When docs and this file disagree on paths, **prefer the official docs** and update the client + this section.

## Progress log
**Always read and update [`PROGRESS.md`](PROGRESS.md)** when completing work:
- Append a dated entry (newest first) describing what landed, paths, and how to verify.
- Keep entries concise; do not rewrite history—correct in a new note if needed.

## Educational doc: SCROLL.md (mandatory keep-current)
**[`SCROLL.md`](SCROLL.md)** is the in-repo **strategy scroll** (item bag): how strategies work, knobs, failure modes, backtest recipes.

**Whenever you add/rename/change a strategy or its config knobs, update `SCROLL.md` in the same session.** New strategies get a full section (not just a table row).

## Current status (as of 2026-07-14)

| Component | Path | Status |
|-----------|------|--------|
| Package layout | `bot.py` + `cli/*` + domain packages under `src/chancetime/` | **Modular (GitHub prep)** |
| Venue docs | Kalshi + Polymarket **US** official URLs | **Done** |
| LLM | calibrate, cache, review, news brief, durable budget, rare tools | **Done** |
| Strategies | simple_edge, llm_calibrated, arb_cross, mean_revert, news_impulse, ml_edge | **Phase 7** (edge unproven) |
| Live trading | signed dual-venue; micro caps; dual-leg gated | **Phase 6** |
| Persistence | SQLite multi-book; sync-positions; export; digests | **Done** |
| Config layers | `default.yaml` + `user.yaml` + secrets `.env` | **Done** |
| Risk | free-cash, mid band, cost-aware edge, deploy %, clusters, TTE | **Phases 16–19** |
| Paper execution | BBO fills, paper fees, depth/spread gates | **Phase 17** |
| Research | walk-forward costs-on, scorecard, history JSONL | **Phase 20** |
| Dashboard | FastAPI loopback; equity / free-cash / scorecard | **Done** |
| Desktop shell | `desktop/` Tauri 2 | **Phase 12 usable** |
| Security docs | `docs/SECURITY.md`, hardened `.gitignore` | **Done** |
| Personal edge path (US venues) | Phases **21–22** (micro live after gates) | **Optional** — US dual-list arb unproven |
| Research loggers | pair_gap / tte / price / match_quality → `data/research/` | **On** |
| Short-horizon crypto Up/Down | **Path C** — `crypto_updown/` + multi-module Home hub | **Phase 28 paper infra done**; 29–30 gated |
| US crypto exchange / futures | **Path D** — `crypto_exchange/` paper + C signals | **Phase 31 paper infra done**; live gated |
| Stocks (Alpaca) | Separate module | **Stretch** |
| Consumer SaaS / web / mobile | Former Path B | **Stretch only** — not on the active roadmap |

**Honesty for agents:** Paper evidence on Kalshi ↔ Polymarket **US** dual-list / complement arb does **not** support marketing positive EV. Prefer flat / selective over overtrading. Never commit secrets. International short crypto binaries are a **different product** (Path C) — do not bolt them onto `polymarket_us`. SaaS is **not** a near-term goal.

**Smoke (bot):** `uv run chancetime strategies --stats` · `uv run chancetime run --account paper --max-polls 2`  
**Smoke (desktop):** `cd desktop && CHANCETIME_ROOT=$PWD/.. npm run dev`  
**Config truth:** effective knobs = `default.yaml` ← `user.yaml`. **Restart bot** after YAML edits (unless `hot_reload_risk`).

## Product paths (personal-first; SaaS is stretch)

Chance Time is a **personal multi-module trading desk**. Each market surface gets its own package, DB, and guardrails. Consumer SaaS is **out of the active plan** (see Stretch at bottom of roadmap).

| Path | What | Keys | Fit for now |
|------|------|------|-------------|
| **A. US prediction markets** | Kalshi + Polymarket US | Local `.env` / secrets | **Primary stack exists** |
| **C. Global Poly crypto Up/Down** | Intl CLOB binaries + spot | Paper now; wallet later | **Phase 28 done**; strategy research next |
| **D. US crypto exchange** | Spot executor now; multi-strategy crypto desk later | Exchange API keys | **Paper infra done** — C signal sink + future strategy host |
| **E. Stocks** | Alpaca equities/options | Alpaca keys | **Stretch** |
| ~~**B. Consumer SaaS**~~ | Multi-tenant product | OAuth / BYO keys | **Stretch only** — not phased |

Path C/D are **not** flags on the US prediction-market bot.

## Next plan of action

### Phase 0 — Bootstrap — **done**

1. ~~Directory structure, `pyproject.toml`, `.env.example`, `.gitignore`~~
2. ~~Config loader, LLM wrapper, mock/Kalshi data, BaseStrategy, paper loop, tests, README~~
3. ~~`PROGRESS.md` + status sync~~

### Phase 1 — Backtesting — **done**

1. ~~Event-driven backtester (fees, slippage, settle on resolve)~~
2. ~~CSV fixture + grid; partial liquidity; trailing_mean prior~~

### Phase 2 — LLM — **done**

1. ~~Calibration + disk cache + budget; post-trade review; news/bust/batch~~

### Phase 3 — Risk, positions, alerts — **done**

1. ~~Portfolio lifecycle; weights; TP/SL; Telegram optional; SQLite book~~

### Phase 4 / 4.5 — Cross-venue arb — **done**

1. ~~Matching, deep discovery, BBO, executable edge, dual-leg paper~~

### Phase 5 — Ops polish — **done**

1. ~~SQLite, Docker, metrics, FastAPI dashboard, CSV export, live fills in DB~~
2. ~~Config layers: `default.yaml` + `config/user.yaml` + secrets `.env`~~
3. ~~`run --fresh-db` opt-in clean slate~~

### Phase 6 — Small live — **done**

1. ~~Dual-venue signed live; micro caps; live-smoke; sync/cancel/export~~
2. ~~Dual-leg live path gated (`dual_leg_live_enabled` + risk ack + caps)~~
3. Prefer paper dual-leg + tiny single-venue live before large dual live

### Phase 7 — Strategy bag — **done** (2026-07-13)

1. ~~mean_revert, news_impulse, ml_edge + train-ml, paper_bag.yaml~~
2. ~~Walk-forward holdout accuracy on train-ml~~
3. ~~simple_edge priors: static / trailing_mean / **blend**~~
4. ~~SCROLL educational rewrite for novices~~

### Phase 8 — Multi-strategy intelligence — **done** (2026-07-13)

1. ~~Per-strategy stats (`strategy_stats`, `strategies --stats`, `/api/strategies`)~~
2. ~~Event-family exposure caps (`max_family_exposure_usd`)~~
3. ~~Cold strategy auto-skip (`cold_min_fills` / `cold_max_realized_pnl`)~~
4. Session LLM review exists; **daily** Telegram digest → Phase 11

### Phase 9 — Path A pro tooling — **done** (2026-07-13)

1. ~~`chancetime doctor`~~; ~~`shadow_mode`~~; paper replay of live sessions → later if needed
2. ~~VPS deploy + backups doc~~
3. ~~Desktop Settings + `user-config` + dashboard write path~~
4. ~~Dashboard scan-arb + doctor~~

### Phase 10 — History & realism — **done** (2026-07-13)

1. ~~Market/BBO JSONL recorder + Settings toggle + `list-history` / `history-to-csv`~~
2. ~~BBO/depth fills; venue fee schedules; multi-venue `load_bars_from_history`~~
3. ~~`backtest --history` replay; walk-forward SimpleEdge~~

### Phase 11 — Multi-account & digests — **done** (2026-07-13)

1. ~~Named books (`config/accounts.yaml.example`, `--account`, `chancetime accounts`)~~
2. ~~Daily digest CLI + Telegram (`chancetime digest --send`) + `/api/digest`~~
3. ~~Tax export polish (ISO ts, year filter, proceeds/cost/gain, summary CSV)~~

### Phase 12 — Desktop app (personal) — **usable**

See desktop section below. Ops tab ships live-readiness tools (Phase 14).

### Phase 13 — Dual-leg live automation — **planned** *(after Phase 14 gates)*

1. Live arb both venues with legging timeout/cancel  
2. Position sync reconciliation after every dual fill  
3. Hard skew / max unhedged time circuit breakers  

### Phase 14 — Live readiness & ops UX — **done** (2026-07-13)

1. ~~`docs/LIVE_READINESS.md` testing playbook~~  
2. ~~CLI → desktop Ops: accounts, digest, export, history, sync, readiness~~  
3. ~~Kill-switch / doctor / account selector on Control~~  

### Phase 15 — Recommended settings — **done** (2026-07-13)

1. ~~Hardcoded presets (`chancetime presets`, Ops Apply)~~  
2. ~~Stats-based `suggest-settings` (optional apply; no LLM for size/edge)~~  
3. LLM remains review/narrative only for live decisions  

### Phase 12 detail — Desktop shell

Ship Chance Time as a **desktop shell** around the existing Python bot + local FastAPI dashboard.

| Item | Status |
|------|--------|
| **Tauri 2** under `desktop/` | **Done** |
| Control / **Ops** / Settings / Monitor tabs | **Done** |
| Process control, tray, dual paper/live books | **Done** |
| Sidecar resolve (venv first; PyInstaller optional) | **Done** |
| Signed installers | Documented; needs your certs |

**Architecture note:** Do not ship two full UIs. Desktop = control plane + embedded monitor. FastAPI stays the shared status backend for desktop iframe, browser, and CLI.

See [`desktop/README.md`](desktop/README.md). Set `CHANCETIME_ROOT` if the app is not launched from the monorepo tree.

---

## Personal P&L track (Path A) — priority after Phase 15

Goal: fewer, higher-quality paper→micro-live trades that clear **spread + fees**, with honest accounting. Not “more strategies on.”

### Phase 16 — Cost-aware risk + strategy slots — **done** (2026-07-13)

1. ~~Free-cash hard cap (`enforce_cash`, `available_cash`)~~  
2. ~~Mid band (`min_yes_mid` / `max_yes_mid`) + simple_edge price band~~  
3. ~~**Cost-aware signal filter:** `min_net_edge`, `assumed_half_spread`, `assumed_fee`~~  
4. ~~**Per-strategy open caps:** `max_open_per_strategy` + per-strategy `max_open` (Settings **cap** next to **w**)~~  
5. ~~Suggestions/Control knobs use **effective** `load_config` (`snapshot_user_knobs`)~~  
6. ~~`bot_start` logs frozen session risk knobs; YAML changes need restart~~  

### Phase 17 — Execution realism (paper = live haircut) — **done** (2026-07-13)

1. ~~Paper fills from **BBO** (`use_bbo_paper`: pay ask / buy NO at 1−bid)~~  
2. ~~Paper fees (`paper_fee_bps` / `paper_fee_venue`); contracts after fee~~  
3. ~~Logs: `mid`, `entry`, `fee_usd`, `mtm_value`, `mtm_drag_pct`, `px_src`~~  
4. ~~Size by depth (`size_by_depth`, `liquidity_participation`); reject `wide_spread` / `thin_book`~~

### Phase 18 — Strategy quality (edge generation) — **done** (2026-07-13)

1. ~~Default simple_edge `prior_mode: blend` (static only for research)~~  
2. ~~LLM calibrated: higher edge/confidence floors; `min_confidence_no_tools`~~  
3. ~~**xAI tools:** `web_search` + `x_search` via Responses API; system prompt encourages use~~  
4. ~~Arb_cross: `require_bbo: true`, higher `fee_buffer`; ml_edge still needs `uv sync --extra ml` + model~~  
5. ~~Per-strategy `max_size_usd` size budgets + existing open **cap**~~

### Phase 19 — Portfolio sophistication — **done** (2026-07-13)

1. ~~Max deploy % of bankroll (`risk.max_deploy_pct`) in addition to free-cash~~  
2. ~~Better family/correlation clustering (`cluster_key` + `max_cluster_exposure_usd`; series tickers)~~  
3. ~~Time-to-event filters (`min_hours_to_close` / `max_days_to_close`; venue `close_time`)~~  
4. ~~Optional hot-reload (`bot.hot_reload_risk`) for risk + strategy caps/weights each poll~~

### Phase 20 — Research loop — **done** (2026-07-13)

1. ~~Walk-forward costs **on by default** (`walk-forward --venue default`; `--zero-cost` opt-out)~~  
2. ~~Per-strategy / family edge-after-cost (`chancetime scorecard`, digest append)~~  
3. ~~Shadow mode + digest remain paper→live gate tools (presets / digest include scorecard)~~  
4. ~~Dashboard `/api/scorecard` + Monitor “Edge after cost” panel~~

### Phase 21 — Micro live (personal)

1. Tiny live after Phase 20 gates (reuse LIVE_READINESS)  
2. Dual-leg live automation remains **Phase 13** (after readiness)  
3. Position sync reconciliation; kill switch muscle memory  

### Phase 22 — Personal hardening

1. VPS/deploy polish, backups, monitoring alerts  
2. Tax export + digests as routine  
3. Operator runbook: restart after YAML, clear-book, cold strategies  

### Phase 13 — Dual-leg live automation — **planned** *(after Phase 21 readiness)*

1. Live arb both venues with legging timeout/cancel  
2. Position sync reconciliation after every dual fill  
3. Hard skew / max unhedged time circuit breakers  

---

## Path C — Short-horizon crypto Up/Down (international Polymarket)

**Motivation (2026-07-14):** US dual-list / complement arb shows little executable edge at poll latency. Public accounts in **short crypto Up/Down** (e.g. 5m/15m binaries) are a different market structure: external spot feed + CLOB microstructure + complete-set + late sniping. Strategy details matter, but **you cannot paper-test them without infrastructure** (WS books, spot, paper fills, history). Build **module-first**, strategy-second.

**Architecture rule:** Separate package surface — e.g. `src/chancetime_crypto/` or `src/chancetime/crypto_updown/` — **not** mixed into `data_layer/polymarket_us.py`. Own config, paper book, risk, fail-closed (no “if data missing, guess”).

**Strategy sketch (research target, not committed alpha):**

1. Record window open spot; stream external price (Binance/etc.)  
2. Evaluate direction, vol, time remaining, CLOB liquidity  
3. Model own \(P(\mathrm{Up})\) / \(P(\mathrm{Down})\)  
4. Buy undervalued side; accumulate opposite if Up+Down asks &lt; 1 (complete-set)  
5. Near resolution, lean into clear favorite (sniping) — with inventory limits  

**Honest constraints:** Competitive; not institutional µs HFT; capacity and edge decay; paper until scorecard green.

### Phase 28 — Path C infrastructure (paper) — **done** (2026-07-14)

1. ~~**Data:** Gamma Up/Down discovery; CLOB public books; Coinbase spot (Binance often geo-blocked)~~  
2. ~~**Clock:** `window_end` / `seconds_remaining` on models~~  
3. ~~**Paper:** dual-side inventory; complete-set optional; **fail closed** without BBO/spot~~  
4. ~~**Persistence:** `data/crypto_paper.db` + `data/research/crypto_updown/scan-*.jsonl`~~  
5. ~~**CLI:** `chancetime crypto scan|run|status|hub`~~  
6. ~~**Hub:** multi-module home + `/api/hub` + `/api/modules` combined equity~~  
7. ~~**Desktop:** Home screen cards → US desk / Crypto Up/Down / Exchange~~  
8. ~~**Optional:** CLOB market WS (`--ws`); window-open quality (`near_open` / `mid_window_join`); HYPE/BNB via CoinGecko/Kraken fallbacks~~  

**Exit criteria:** Poll loop logs real books+spot with zero invented prices — **met** for Phase 28. Strategy edge → Phase 29.

### Phase 29 — Path C strategy research (paper only) — **done enough to paper** (2026-07-14)

**Canonical strategy (tweet hybrid) — one strategy, five steps:**

1. **Open print:** at each window, record external asset price; stream spot continuously  
2. **State:** direction (spot vs open), vol, time remaining, Polymarket liquidity (spread/BBO)  
3. **Own P(Up):** heuristic model from spot/open/vol/TTE (research grade — not claimed true cal)  
4. **Mispricing + complete-set:** buy undervalued side when model vs mid edge ≥ threshold; if Up+Down asks &lt; ~1, buy both  
5. **Snipe:** near expiry, add size on the clear favorite (model/market agree), inventory-capped  

**Code:** `TweetHybridStrategy` · `--paper-strategy` / shadow · resolve-aware `crypto scorecard` · kill switches (stale spot, daily loss, wide spread skip) · desktop Control + **Monitor** tabs.

**Exit criteria:** Multi-day paper + `go_nogo` on scorecard — **human runs** before Phase 30. Official Poly resolution feed still optional upgrade over spot-vs-ref proxy.

### Path D note (executor, multi-strategy later)

D is the **US crypto rails module** (spot now; futures/options later). Near term: consume C signals + simple paper. **Later** D can host *many* crypto strategies (trend, mean-revert, funding, etc.) independent of event contracts — possibly a larger strategy bag than Path A once Coinbase/RH/Alpaca depth exists. **One step at a time:** prove C paper loop + D paper executor first.

### Phase 30 — Path C micro-live (optional) — **gated**

1. Wallet / CLOB auth in secrets (never in US `.env` mix-ups)  
2. Tiny size caps; same risk-ack pattern as Phase 6  
3. Only after Phase 29 scorecard green + human approval  

---

## Path D — US crypto exchange module (planned; personal)

**Motivation:** If intl Polymarket CLOB is awkward (wallet/geo/ToS), short-horizon *direction* ideas can still be researched via **US-eligible crypto products** (spot, regulated futures, and in some cases short-horizon option-like products). Also useful as a plain **spot / futures** desk independent of prediction markets.

**Architecture rule:** New package e.g. `src/chancetime/crypto_exchange/` + `data/crypto_exchange_paper.db` — **not** mixed into `crypto_updown` (intl CLOB) or `polymarket_us`. Same pattern: paper-first, fail-closed, hub card on Home.

**Venue notes (summary — full writeup `docs/CRYPTO_VENUES.md`):**

| Venue | US retail fit | API quality | Closest to “Up/Down binary” |
|-------|---------------|-------------|------------------------------|
| **Coinbase Advanced** | Strong: spot + CFTC US futures (CFM) | Excellent REST/WS + official Python SDK | Spot/futures direction, **not** 5m binaries |
| **Robinhood Crypto API** | Official US crypto trading API | Market data + place crypto orders (v1/v2 fee tiers) | Spot only via this API — not equity options |
| **Crypto.com + CDNA** | UpDowns / Strike Options (CFTC) | Exchange API; confirm product automation | **Closest US product** to short binary-ish exposure |
| **Kraken Pro** | Spot + US futures (verify account) | Mature REST/WS/FIX | Directional futures/spot |

**Out of scope for now:** Deribit.

**Honesty:** Short-dated listed options / UpDowns are **not** the same payoff as Polymarket 5m Up/Down (different fees, barriers, knockouts, settlement). Use as *related* research, not a drop-in arb.

### C ↔ D linkage (signals, not a merged module)

Path C and Path D stay **separate packages + DBs**. They may share a **signal bus** later:

- **C publishes** (scan/JSONL/event): asset, window end, implied \(P(\mathrm{Up})\), reference/open level, BBO health.  
- **D optionally consumes** that to size spot/futures/UpDown exposure when Poly is hard to trade or as a second expression of the same view.  
- **Hub only sums equity** — no order routing.

Do **not** make D’s exchange client import CLOB trading code, or C place Robinhood/Coinbase orders. Cross play is an explicit strategy with its own risk caps (avoid doubling the same BTC bet blindly). Full writeup: `docs/CRYPTO_VENUES.md` § “Path C vs Path D”.

### Phase 31 — Path D paper infra — **done (spot/Coinbase)**; multi-venue **planned**

1. ~~Coinbase public spot feed + paper book + `crypto_exchange_paper.db`~~  
2. ~~Hub + desktop Exchange Control/Monitor + `chancetime exchange *`~~  
3. ~~C→D signal bus + optional `--trade-signals`~~  
4. **Target venues for D (all four — not all built yet):**  

| Venue | Role in D | Status |
|-------|-----------|--------|
| **Coinbase Advanced** | Spot (+ later US futures) | Public ticker paper **done**; private orders later |
| **Robinhood Crypto API** | Spot orders (official US crypto API) | Stub / keys in `.env.example` |
| **Kraken Pro** | Spot (+ US futures if unlocked) | Planned adapter |
| **Crypto.com (+ CDNA)** | Spot + UpDowns/Strike research | Planned adapter |

5. No live until paper scorecard + human ack. **Do not** use Deribit.

### Phase 32 — Path D strategy / multi-venue / micro-live — **gated**

1. Wire RH / Kraken / Crypto.com clients behind same paper book interface  
2. Own strategies (trend / mean-revert) + Poly-implied consumer  
3. Micro live with hard caps; correlation cap vs open Path C inventory  

---

## 1. Project Goals & Success Criteria
- Build a **flexible, pluggable strategy system** so new strategies can be added/configured without major refactoring.
- Hybrid architecture: Deterministic/reliable Python core (execution, risk, position management) + Grok LLM for intelligence (news synthesis, probability calibration, idea generation, post-trade review).
- Strong emphasis on **backtesting** with realistic slippage/fees/fill simulation.
- **Safety first**: Every trading action must respect a global `PAPER_MODE` flag. Never place real orders until extensively tested.
- Cost control on Grok API: Target **<$5/day total spend** (or a small % of bot P&L once live). Prefer fast/cheap models. Cache aggressively. Only call LLM when it adds clear value.
- Clean, well-documented, testable codebase that a human (or future agents) can easily extend.
- Start simple → iterate. MVP = data ingestion + one simple strategy + paper trading loop + basic LLM integration.

---

## 2. Recommended Tech Stack & Tools
- **Python 3.12+** (use `uv` or Poetry for dependency management — prefer `uv` for speed).
- Core libs: `asyncio`, `aiohttp`, `pandas`, `numpy`, `pydantic` (v2), `python-dotenv`, `structlog` (or rich logging).
- Backtesting: `vectorbt` (or custom event-driven simulator for order-book realism in prediction markets).
- LLM: Official `xai_sdk` (preferred) or `openai` client pointing to `https://api.x.ai/v1`. Use structured outputs (Pydantic models + JSON mode).
- Unified prediction market access: `pmxt` (if mature and well-maintained — "CCXT for prediction markets") or direct SDKs (`kalshi-python`, Polymarket US SDK).
- Stocks later: `alpaca-py`.
- Other: `typer` CLI, FastAPI local dashboard, `pytest` + hypothesis.
- Desktop (Phase 12): **Tauri 2** shell around local FastAPI + bot sidecar (prefer over Electron).
- Crypto exchange later: Coinbase Advanced SDK / Kraken REST (see Path D). Stocks stretch: `alpaca-py`.
- Dev tools: `ruff`, `mypy`, `pre-commit`.
- Deployment: Docker + compose; VPS optional.

**Do not** introduce unnecessary complexity early (no Kubernetes, no heavy ORMs, etc.).

---

## 3. Secrets & Configuration Management (IMPORTANT)
**Recommendation (decided by Grok):**  
Start simple and secure for development:

1. Use **`python-dotenv`** + a **`.env`** file (gitignored).
2. Create `.env.example` with all required keys as placeholders/comments.
3. In production / VPS: Inject via environment variables or Docker secrets. Never commit real keys.
4. For extra user convenience/security (Proton Pass, etc.): The human can store the master `XAI_API_KEY` (and future exchange keys) in Proton Pass / a password manager. They manually copy the values into the local `.env` file when setting up. A small helper script can be added later to pull from a secure local vault if desired (e.g., using `keyring` or `pass`).

**Never hardcode any API keys or secrets in source code.**

Required env vars (examples):
- `XAI_API_KEY` — Grok API key (starts with `xai-`)
- `KALSHI_API_KEY` — Kalshi **API Key ID** (UUID), not the RSA file
- `KALSHI_PRIVATE_KEY_PATH` — path to RSA private key PEM (e.g. `./secrets/kalshi.key`); keep full PEM with BEGIN/END **in the file**. Legacy alias: `KALSHI_API_SECRET` (also a path, never inline PEM)
- `POLYMARKET_API_KEY` — Polymarket **US** API Key ID (UUID) from polymarket.us/developer
- `POLYMARKET_PRIVATE_KEY_PATH` — RSA PEM path (e.g. `./secrets/polymarket.key`); alias `POLYMARKET_API_SECRET` as path. **Not** international Polygon wallet keys.
- `PAPER_MODE=true` (or `false` for live — default to true)
- Trading params, risk limits, etc. (many should live in config YAML/JSON too)

Put secret files under `secrets/` (gitignored). The agent must always read secrets via `os.getenv` / path loaders after `load_dotenv()` — never hardcode PEMs.

---

## 4. Grok API / LLM Usage Rules & Cost Control (CRITICAL)
- **Primary client**: Prefer the official `xai_sdk` (`from xai_sdk import Client, AsyncClient`).
- **Default model: `grok-4.5`** (stronger reasoning; still keep daily budget + cache). Switch to cheaper/faster models only if spend approaches the cap.
- **Strict budget target**: Keep total daily Grok API spend well under **$5/day**. Once the bot is making consistent profits, aim for LLM cost << 5–10% of gross P&L.
- Techniques the agent **must** implement:
  - Structured outputs (Pydantic models) to reduce token waste.
  - Aggressive caching (in-memory + simple file/DB cache for repeated news/sentiment).
  - Call LLM only on **valuable triggers** (new market creation, significant price move, scheduled review, news event) — not on every polling cycle.
  - Batch prompts where possible.
  - Log every call: prompt summary, model used, input/output tokens, estimated cost, timestamp.
  - Add a simple cost tracker / daily cap (raise exception or switch to cheaper model / disable non-essential calls if nearing limit).
  - Prefer shorter context and lower `max_tokens` where sufficient.
- For probability calibration / sentiment: Use well-engineered prompts with few-shot examples and explicit instructions for calibrated outputs (e.g., "Give a probability between 0 and 1 with reasoning...").
- Post-trade review: Use LLM asynchronously to analyze closed positions and suggest strategy improvements (this can be batched daily).

The agent should propose and implement a `llm/` module with a `GrokClient` wrapper that enforces these rules.

---

## 5. High-Level Architecture (Follow This)
```
src/chancetime/
  data_layer/        # Ingestion: markets, order books, trades, news, resolutions
      ├── models.py / base.py / mock.py / kalshi.py
      └── polymarket.py / news.py (later)
  strategies/        # Pluggable strategies (inherit from BaseStrategy)
      ├── base.py
      ├── simple_edge.py        # e.g., prob diff threshold
      ├── arb_scanner.py        # cross-platform (later)
      └── llm_enhanced.py       # uses Grok for signals (later)
  llm/               # Grok wrapper + prompts + caching + cost tracking
      ├── client.py
      ├── prompts.py
      └── cache.py
  risk/              # Position sizing, limits, circuit breakers
  execution/         # Order placement, fill tracking (paper + live gate)
  backtesting/       # Historical replay + realistic simulation (Phase 1)
  monitoring/        # Alerts, dashboard hooks, metrics
  utils/             # Config loading, logging helpers
  bot.py             # Async poll loop (data → strategies → risk → exec)
  cli/               # Typer command groups
  main.py            # Thin entry: re-exports app + Bot
config/              # YAML for params, strategy allocation
tests/ & backtests/
```

**Key Design Rules**:
- Strategies are independent and composable.
- Everything is async-friendly.
- Clear separation between "signal generation" and "execution/risk".
- Global config + per-strategy overrides.
- All trading actions go through a central `ExecutionEngine` that respects `PAPER_MODE`.

---

## 6. Coding Standards & Best Practices
- **Formatting/Linting**: Use `ruff` (format + lint). Run on every change.
- **Typing**: Use type hints everywhere. `mypy --strict` friendly.
- **Async**: Prefer `asyncio` + async/await. Use `aiohttp` for HTTP where SDKs allow.
- **Logging**: Structured logging (`structlog`). Log decisions, LLM calls, orders, errors with context.
- **Error Handling**: Never let an exception in one strategy kill the whole bot. Use try/except + circuit breakers.
- **Testing**: Unit tests for pure functions. Integration tests for data layers. Backtests as the primary validation for strategies.
- **Documentation**: Docstrings on all public functions/classes. Update this AGENTS.md when architecture changes significantly.
- **Git**: Small, focused commits. Good commit messages. Feature branches if collaborating.
- **Safety**: Any function that can place real orders must check `PAPER_MODE` and have clear warnings in comments.

---

## 7. Initial Bootstrap Tasks (Do These First)
When starting or resetting the project, the agent should:

1. Ensure the directory structure exists (create `src/`, subfolders, `tests/`, `config/`, `backtests/` if missing).
2. Create `pyproject.toml` (or `requirements.txt` + `setup.py` if simpler) with core dependencies and dev tools.
3. Create `.env.example` with all placeholder keys and example values/comments.
4. Add `.gitignore` (standard Python + `.env`, `__pycache__`, `.venv`, logs, backtest outputs, etc.).
5. Implement a minimal `utils/config.py` that loads `.env` + YAML config.
6. Create a basic `llm/client.py` wrapper around `xai_sdk` that:
   - Loads `XAI_API_KEY`
   - Provides async `chat()` and structured output helpers
   - Logs token usage + rough cost estimate
   - Has a simple daily spend tracker stub
7. Implement a skeleton data fetcher (e.g., list active markets on Kalshi or Polymarket US using available SDKs or `pmxt`).
8. Create `strategies/base.py` with a `BaseStrategy` abstract class defining `generate_signals()`, `manage_risk()`, etc.
9. Build a minimal main loop skeleton (`main.py` or `bot.py`) that:
   - Loads config
   - Initializes LLM client and data clients
   - Runs an async polling loop (paper mode by default)
   - Prints/logs status
10. Add a `README.md` with high-level setup instructions (human will fill in account creation steps).
11. Write a simple test or backtest stub to verify the skeleton works.

After bootstrap, propose the next concrete milestone (e.g., "Implement first simple probability-edge strategy + backtester").

---

## 8. Phased Development Roadmap (Suggested Order)

| Phase | Theme | Status |
|-------|--------|--------|
| 0–6 | Bootstrap → live micro | **Done** |
| 7 | Strategy bag expansion | **Done** |
| 8 | Multi-strategy intelligence | **Done** |
| 9 | Path A pro tooling + `user.yaml` UI | **Done** |
| 10 | History recorder + realistic backtests | **Done** |
| 11 | Multi-account + daily digests | **Done** |
| 12 | **Desktop app (Tauri)** | **Usable** |
| 13 | Dual-leg live automation | Planned (after readiness) |
| 14 | **Live readiness + Ops UI** | **Done** |
| 15 | **Presets + stats suggestions** | **Done** |
| 16–20 | Risk / BBO paper / portfolio / scorecard | **Done** (see PROGRESS) |
| 28 | Path C paper infra (intl Poly crypto) | **Done** |
| 29 | Path C signals + ref/resolution research | **In progress** (signals + ref + scorecard CLI) |
| 30 | Path C micro-live | Gated |
| 31 | Path D paper exchange + C signal consumer | **Done** |
| 32 | Path D strategies / micro-live | Gated |
| — | Stocks (Alpaca), SaaS, web/mobile | **Stretch only** |

**Ongoing** — Paper bag experiments, Path C/D research, prompt/ML iteration. **Not** multi-tenant SaaS.

### Config vs secrets (product rule)

| Store | Contents |
|-------|----------|
| **`.env`** | Secrets only: API keys, key file paths, `PAPER_MODE`, optional Telegram |
| **`config/default.yaml`** | Shipped defaults |
| **`config/user.yaml`** | Local overrides (poll, risk, strategies) — gitignored; dashboard writes here |
| **SQLite `data/*.db`** | Portfolio, fills, strategy stats |

Merge: `--config` YAML ← `user.yaml` ← secrets from env. Ops knobs should not require `.env`.

---

## 9. Important Constraints & Warnings
- **No real money trading** until the human explicitly approves after extensive paper + small-size live testing.
- Always respect platform ToS and rate limits.
- Tax/reporting implications: The bot should log everything needed for P&L tracking.
- LLM hallucinations: Never blindly trust LLM output for execution. Use it for signals/ideas only; final decisions go through coded risk/execution layers.
- Cost discipline: If daily LLM spend approaches the limit, the agent must suggest throttling or cheaper alternatives.
- Prefer paper/demo and mocks until dual-venue execution is proven.
- **Do not** build multi-tenant SaaS scaffolding unless the human explicitly reopens that stretch goal.

---

## 10. How to Interact with This File
- Update this AGENTS.md whenever the architecture, stack, or major conventions change.
- When the human gives new high-level direction, re-read this file first.
- Before writing significant new code, propose the plan/structure in comments or a short response so the human can confirm.
- Prioritize working, tested, safe code over cleverness.
- Keep **`SCROLL.md`** and **`PROGRESS.md`** current with strategy and milestone changes.

---

## 11. Product strategy (personal desk)

**Not legal advice.** Automating trades on prediction markets and crypto derivatives has ToS, tax, and (if you ever trade for others) regulatory implications. This project is **you trading for yourself**.

**What we build:** single-tenant modules (US PM, Path C intl binaries, Path D US crypto exchange, later stocks) with shared hub portfolio, separate DBs, paper-first.

**What we do not build (active plan):** multi-tenant SaaS, customer key custody, subscription trading-for-others, mobile client product.

**Stretch only (if ever):** self-hosted license, signals-only feed, or web UI that never holds third-party trading keys — still needs legal review before money changes hands. See historical notes in old PROGRESS entries; no numbered SaaS phases remain.

---

## 12. Classical ML strategies (opinion / later)

**Yes, worth adding eventually** as another pluggable strategy — **after** you have labeled history (resolutions + features), not as a Phase 6 blocker.

| Approach | When | Deps | Notes |
|----------|------|------|--------|
| **Rules / edges** (current) | Now | none | Interpretable, backtest-clean |
| **sklearn** (logistic, GBDT, calibration) | Phase 7+ | `chancetime[ml]` extra | Prefer first; small models, joblib dump, CPU-only |
| **LLM sentiment / news** | Triggers only | Grok budget | Not continuous fine-tuning |
| **PyTorch / TensorFlow** | Only if sklearn plateaus | heavy | Overkill for binary mids + sparse labels; hurts installable packaging |

**Design if/when we add `ml_edge`:**
1. Train **offline** (CLI `chancetime train-ml`) on recorded bars + resolutions — not live weight SGD every poll.
2. Artifact: `models/ml_edge.joblib` (+ feature schema version). Bot loads read-only and emits `Signal`s like any strategy.
3. Optional **background retrain** on a schedule (daily), not continuous in the hot path.
4. Features: mid, spread, depth, time-to-close, venue, trailing vol — start simple; avoid leakage (no post-resolution labels in train fold).
5. Keep Torch/TF out of core deps forever unless a specific deep model earns its keep.

**SQLite choice (Phase 5 → 12):** separate books — `data/paper.db` (paper bot) and `data/live.db` (live). Monitor toggles books. Legacy `data/chancetime.db` → `uv run chancetime migrate-books`.

---

**Let's build this responsibly and intelligently.** Current focus: **Phase 6** (tiny live, still gated) or more arb quality / history recording; classical ML is optional Phase 7+.

Human handles venue KYC and real keys in `.env`. Focus on code quality, safety, and the hybrid intelligence layer.