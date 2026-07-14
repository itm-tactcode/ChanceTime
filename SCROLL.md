# SCROLL — Chance Time strategy guide (the item bag)

**Chance Time** (`chancetime`) is a **paper-first** bot for **prediction markets** (mainly Kalshi and Polymarket US).

This document is written for **humans who are new to this style of trading**. You do not need a quant background. Read top-to-bottom once; then jump to the strategy you want to equip.

**Related docs**

| Doc | What it is |
|-----|------------|
| `README.md` | Install / first commands / GitHub layout |
| `AGENTS.md` | Local architecture notes for agents (gitignored; not on GitHub) |
| `PROGRESS.md` | Local changelog (gitignored; not on GitHub) |
| `docs/ORIENTATION.md` | Mental model of bot + desktop |
| `docs/SECURITY.md` | Secrets, bind exposure, pre-push checks |
| `docs/LIVE_READINESS.md` | Gates before tiny live |

> **Maintainers / agents:** When you add, rename, or change a strategy’s behavior or knobs, update **this file in the same session**. New strategies get a full section (not just a table row).

---

## 0. What is a prediction market? (novice start)

### 0.1 Sportsbook vs prediction market

On a typical sportsbook you bet *against the house*. Odds are set by the book; you usually cannot “sell” the ticket halfway through at a fair market price.

On a **prediction market** (Kalshi, Polymarket US, etc.):

- People trade **contracts** that pay **$1 if an event happens** and **$0 if it does not** (binary YES/NO markets).
- The **price of YES** is often read as “the market’s implied probability.”  
  Example: YES at **$0.40** ≈ “crowd thinks ~40% chance.”
- You can **buy** or **sell** (when there is liquidity) before the event resolves — more like a tiny option or stock than a locked-in sportsbook ticket.

### 0.2 YES and NO

| Action | Rough meaning |
|--------|----------------|
| Buy **YES** at 0.40 | You pay $0.40 now. If YES wins, you get $1 (profit $0.60). If NO wins, you lose $0.40. |
| Buy **NO** at 0.40 | Same math on the other side (often equivalent to “shorting YES”). |

**Max loss** on a long YES is what you paid. **Max gain** is `$1 − price`.

### 0.3 Fees, spreads, and “fake free money”

- **Mid price** = halfway between bid and ask (what many APIs show first).
- **Executable price** = what you actually pay if you buy now (usually the **ask**).
- Cross-venue “arb” that looks huge on **mids** often **disappears** once you pay both asks and fees.

That is why Chance Time’s arb path prefers **bid/ask (BBO)** and a **fee buffer**.

### 0.4 Paper vs live

| Mode | What happens |
|------|----------------|
| **Paper** (`PAPER_MODE=true`) | Bot simulates fills; no real money. Default and recommended while learning. |
| **Live** | Real orders via venue APIs. Requires risk acknowledgment and hard dollar caps. |

Logs: fill → **got item**; reject → **miss**. Fun flair; the risk engine is not random.

---

## 1. How Chance Time thinks (mental model)

```
markets (snapshots from venues or mock)
    → strategy.generate_signals()     # pure ideas only
    → risk filter                     # size / limits / families / cold strategies
    → execution (paper | live)        # fills
    → portfolio + SQLite + dashboard  # bookkeeping
```

| Layer | May place orders? | Job |
|-------|-------------------|-----|
| **Strategy** | **No** | Read markets, emit `Signal`s |
| **Risk** | No | Reject / size / circuit-break / family caps |
| **Execution** | Yes (gated) | Paper fill or live venue order |
| **Persistence** | No | Save fills, positions, strategy stats |

**Golden rule:** strategies never import execution. They only produce intents.  
**Second golden rule:** LLM/ML output is **advisory**. Coded risk decides if money moves.

### 1.1 What is a `Signal`?

Code: `src/chancetime/strategies/base.py`

| Field | Meaning in plain English |
|-------|--------------------------|
| `market_id` | Which contract (ticker or slug) |
| `platform` | `kalshi`, `polymarket`, `mock`, … |
| `side` | `yes` / `no` (rarely `flat`) |
| `strength` | 0–1 “how hard do I believe this?” (used in sizing) |
| `edge` | Strategy’s idea of advantage (often fair − market) |
| `fair_prob` | Strategy’s estimated true P(YES), if any |
| `market_prob` | What the market is showing (or exec price) |
| `size_usd` | Optional dollar notional; else default order size |
| `reason` | Human-readable sentence for logs and learning |

### 1.2 How do you equip a strategy?

1. Implement class under `strategies/`
2. Register in `build_strategies` (`strategies/__init__.py`)
3. Add YAML under `strategies:` in `config/default.yaml`
4. Document it **here**
5. Test + optional backtest

Config knobs live in YAML / `config/user.yaml`. **Secrets** stay in `.env` only.

---

## 2. Inventory (item bag)

| ID | Class | Status | One-line idea |
|----|--------|--------|----------------|
| `simple_edge` | `SimpleEdgeStrategy` | Live | Trade when price is far from a fair prior |
| `llm_calibrated` | `LLMCalibratedStrategy` | Live | Grok estimates fair; trade the gap (cost-capped) |
| `arb_cross` | `ArbCrossStrategy` | Live | Same event on two venues; buy cheap YES + hedge NO |
| `complement_arb` | `ComplementArbStrategy` | Live | Same market: buy YES+NO when ask sum &lt; $1 after fees (no LLM) |

**Universe profiles** (`data.profiles` + `strategies.<name>.universe`): each strategy gets its own market slice (shared HTTP, different filters/queries). Defaults: `broad` (simple_edge / mean_revert / ml_edge / price_buckets), `short_bbo` (complement_arb / tte_buckets), `dual_list` (arb_cross / pair_gap / match_quality), `llm_screen` (llm_calibrated / news_impulse).

**Log-only research** (enabled by default; **no orders**): write JSONL under `data/research/`:

| Strategy | File stem | What it logs |
|----------|-----------|--------------|
| `pair_gap_tracker` | `pair_gap-YYYYMMDD.jsonl` | Dual-list exec edge, BBO, TTE each poll |
| `tte_buckets` | `tte_buckets-…` | Mid/spread by hours-to-close bucket |
| `price_buckets` | `price_buckets-…` | Open mids by price band (resolve later offline) |
| `match_quality` | `match_quality-…` | Match score, long-TTE / year-mismatch flags |
| `mean_revert` | `MeanRevertStrategy` | Live | Fade short-term mid spikes/dumps |
| `news_impulse` | `NewsImpulseStrategy` | Scaffold | Grok re-prices after news text you provide |
| `ml_edge` | `MLEdgeStrategy` | Live (offline train) | sklearn model → fair; train with `train-ml` |

List equipped strategies anytime:

```bash
uv run chancetime strategies
uv run chancetime strategies --stats
```

---

## 3. `simple_edge` — “the market is wrong vs my prior”

**Code:** `strategies/simple_edge.py`  
**Config:** `strategies.simple_edge`

### 3.1 Story for novices

You pick a **prior** fair probability \(f\) (by default a blunt coin-flip \(f = 0.5\)).  
The market YES price is \(m\).

```
edge = f − m
```

| Situation | Trade idea |
|-----------|------------|
| Market at 0.20, prior 0.50 | YES looks “too cheap” → **buy YES** |
| Market at 0.85, prior 0.50 | YES looks “too expensive” → **buy NO** |
| \|edge\| small | Do nothing |

Also skip thin markets (`min_liquidity_usd`).

### 3.2 Priors (what “fair” means)

| `prior_mode` | Meaning |
|--------------|---------|
| `static` | Always use `default_fair_prob` (default 0.5) |
| `trailing_mean` | Fair = recent average mid for *this* market (fade moves) |
| `blend` | Mix static + trailing: `α·trail + (1−α)·static` (`blend_alpha`) |

**Honest limitation:** \(f = 0.5\) is a **toy**. Real events are not all 50/50. Use this strategy to learn the plumbing and to sweep parameters in the backtester — not as a money printer.

### 3.3 Knobs

| Param | Default | Effect |
|-------|---------|--------|
| `enabled` | true | Equip / unequip |
| `edge_threshold` | 0.08 | Min \|edge\| to fire |
| `min_liquidity_usd` | 100 | Skip thin books |
| `default_fair_prob` | 0.5 | Static prior |
| `prior_mode` | static | static / trailing_mean / blend |
| `blend_alpha` | 0.5 | Weight on trailing when blending |
| `history_window` / `min_history` | 5 / 3 | For trailing / blend |
| `weight` | 1.0 | Risk allocation multiplier |

### 3.4 Backtest recipe

```bash
uv run chancetime backtest -f backtests/fixtures/sample_series.csv --edge 0.08
uv run chancetime backtest -f backtests/fixtures/sample_series.csv --grid
```

---

## 4. `llm_calibrated` — “ask Grok for a careful probability”

**Code:** `strategies/llm_calibrated.py` + `llm/calibrate.py`  
**Config:** `strategies.llm_calibrated` (default **off** — costs money)

### 4.1 Story

Instead of assuming 0.5, you ask **Grok** for a calibrated P(YES) with confidence, using structured JSON and caching so you do not re-pay for the same market every poll.

```
edge = p_llm − m
```

Only trade if \|edge\| is large enough **and** confidence is above a floor.

### 4.2 Cost control (read this)

- Daily budget in config / env (`daily_budget_usd`)
- Disk cache with TTL
- Cache bust if mid moves a lot (`price_move_bust`)
- Optional news context file for better context without huge prompts
- Default model for the project: **`grok-4.5`**

### 4.3 When to equip

- You have an API key and a small budget you accept
- Markets where **text understanding** matters (rules, politics, nuanced events)
- **Not** every poll on hundreds of markets

### 4.4 Failure modes

- LLM hallucination → never auto-trust without risk caps  
- Spending the daily budget early  
- Overfitting prompts to last week’s news  

---

## 5. `arb_cross` — “same event, two shops, different prices”

**Code:** `strategies/arb_cross.py` + matching + BBO  
**Config:** `strategies.arb_cross`  
**CLI:** `chancetime scan-arb`, `chancetime markets`

### 5.1 Story for novices

Imagine the same question sold at two stores:

- Store Kalshi: YES ~ 0.39  
- Store Polymarket US: YES ~ 0.50  

If you can **buy YES cheap** and **buy NO expensive-side** so that total cost of a locked pair is under $1 after fees, you have a classic **cross-venue hedge** (theoretical locked edge).

In code we prefer **executable** prices:

```
cost ≈ YES_ask(cheap venue) + NO_ask(rich venue)
edge ≈ 1 − cost − fee_buffer
```

### 5.2 Matching is hard

Titles differ. Chance Time:

1. Normalizes titles (fuzzy score)  
2. Optional aliases file  
3. Optional LLM match (costs tokens)  
4. Deep discovery pages more of each catalog  

**Polymarket.com ≠ Polymarket US.** US slugs come from API / `chancetime markets "query"`, not international website paths.

### 5.3 Dual legs and risk

Hedge legs share an `arb_group_id`. Risk/execution try to approve and fill **both or neither** (paper). Live dual-leg is supported but **legging risk** remains (one leg fills, the other does not).

### 5.4 Knobs (high level)

| Param | Role |
|-------|------|
| `min_spread` + `fee_buffer` | How fat the edge must be |
| `min_match_score` | Fuzzy title threshold |
| `require_bbo` / `use_executable_prices` | Don’t trust empty 0.50 mids |
| `size_by_depth` / `max_leg_usd` | Don’t size bigger than the book |
| `emit_hedge_legs` | Second leg (NO on rich YES) |

### 5.5 Recipes

```bash
uv run chancetime scan-arb --source mock
uv run chancetime scan-arb --deep --bbo --limit 250
uv run chancetime markets "france" -v both
```

---

## 5b. `complement_arb` — “YES + NO cost less than a dollar on one market”

**Code:** `strategies/complement_arb.py`  
**Config:** `strategies.complement_arb`  
**No LLM** — pure bid/ask math (HFT-adjacent; edge is rare and short-lived).

### Story

On a binary market, buying **YES and NO** locks $1 at resolution. If you can pay:

```
yes_ask + no_ask + fees < 1
```

you have same-market **complement** arb (not the same as cross-venue arb).

Most books keep this gap closed. When it flashes, bots race. Chance Time paper-scans every poll so you can measure frequency — do not expect free lunch.

### Knobs

| Param | Role |
|-------|------|
| `min_edge` + `fee_buffer` | Required `1 - yes_ask - no_ask - fee_buffer` |
| `require_bbo` | Need real bid/ask (default on) |
| `max_hours_to_close` | Optional: only short-dated markets |
| `max_leg_usd` / `max_pair_usd` | Size caps |

### Data feed

Page-1 open markets are sports-heavy. Config `data.prefer_closing_within_hours` + `short_horizon_queries` expands the universe via venue search (still no LLM).

### Safety

- Dual legs share `arb_group_id` (both-or-neither paper fill)  
- Portfolio keys `market_id::yes` / `market_id::no` so both sides can open  
- Mock fixtures are **dropped** whenever any live market is in the feed  

---

## 6. `mean_revert` — “it just jumped; fade the move”

**Code:** `strategies/mean_revert.py`  
**Config:** `strategies.mean_revert` (default **off**)

### 6.1 Story

Markets often **overreact** for a few minutes (or a few polls of our bot).  
If the mid was ~0.34 for a while and suddenly prints 0.41 with no deeper reason, mean reversion says: **sell the spike** (buy NO) or **buy the dump** (buy YES).

```
move = mid − trailing_mean
if |move| ≥ threshold → fade
```

### 6.2 Needs history

On the **first** poll there is no history → no signals.  
After `min_history` snapshots, it can fire.  
Mock data **drifts** turnout over polls so paper multi-runs can exercise this without live feeds.

### 6.3 Failure modes

- Strong trends (elections, injury news) **punish** pure reversion  
- Poll interval too slow → you “mean-revert” after the news is already priced  
- Same market already open → risk says **miss / already_open**

### 6.4 Paper experiment

```bash
uv run chancetime run -c config/paper_bag.yaml --fresh-db --max-polls 8
uv run chancetime strategies -c config/paper_bag.yaml --stats
```

---

## 7. `news_impulse` — “headline just dropped”

**Code:** `strategies/news_impulse.py`  
**Config:** `strategies.news_impulse` (default **off**)

### 7.1 Story

You paste or file a short news blurb (`llm.news_context` / `news_context_file`).  
Grok estimates a new fair probability **in light of that news**.  
If fair and mid disagree enough, trade the gap — with confidence floor and **max LLM calls per poll**.

### 7.2 When it makes sense

- Scheduled events with known catalysts  
- You already have a cheap summary you trust  

### 7.3 When it is dangerous

- Garbage-in headlines  
- Paying Grok on every poll  
- Treating model tone as “alpha” without risk caps  

---

## 8. `ml_edge` — “a small model’s probability”

**Code:** `strategies/ml_edge.py` + `ml/train.py`  
**Train:** `chancetime train-ml`  
**Deps:** `uv sync --extra ml`  
**Artifact:** `models/ml_edge.joblib` (gitignored)

### 8.1 Story for novices

Classical ML here is **not** ChatGPT and **not** a neural net by default.

1. Offline, you train a **logistic regression** on historical bars labeled by how markets **resolved** (YES=1, NO=0).  
2. Online, the strategy only **scores** features → fair probability → edge vs mid.  
3. There is **no** continuous learning every second in the trading loop.

```bash
uv sync --extra ml
uv run chancetime train-ml -f backtests/fixtures/sample_series.csv
# reports train_acc and walk_forward holdout acc when possible
```

Then set `strategies.ml_edge.enabled: true` (e.g. in `paper_bag.yaml` or `user.yaml`).

### 8.2 Features (simple on purpose)

Mid, implied NO, liquidity, volume proxy, BBO flags/prices — same order train and live.

### 8.3 Failure modes

- Tiny fixtures → pretty in-sample accuracy, **weak** real edge  
- Regime change (2024 politics ≠ 2026 sports)  
- Label leakage if you train on post-resolve bars  

Walk-forward accuracy is printed so you do not only see “train_acc=0.80” and celebrate.

---

## 9. Risk layer extras (not a strategy, but part of the bag)

### 9.1 Take-profit / stop-loss

Configured as fractions of entry notional. Example: +30% / −25%.  
Applies to **open** positions when mids update each poll.

### 9.2 Event families

Titles are tagged with simple keywords into **sports / macro / crypto / politics / other**.  
`max_family_exposure_usd` limits how much notional you can stack in one family.

### 9.3 Cold strategies

If a strategy has enough fills **and** cumulative realized PnL is worse than a threshold, it is **auto-skipped** for new entries (`cold_min_fills`, `cold_max_realized_pnl`).

### 9.4 Weights

Each strategy has a `weight`. Risk multiplies size by weight. Weight `0` disables fills even if signals fire.

### 9.5 Free cash (no infinite paper leverage)

`available_cash ≈ cash_basis + realized − open_exposure`.  
With `enforce_cash: true`, risk **clips or rejects** orders so reserved notional never exceeds free cash (same idea as a live reject for insufficient funds).

### 9.6 Cost-aware edge (Phase 16)

Markets nickel-and-dime you: buy near the **ask**, mark near **mid**, plus fees.

Risk requires:

```text
|edge| − assumed_half_spread − assumed_fee  ≥  min_net_edge
```

Defaults: half-spread `0.005` (matches paper 50 bps absolute points), `min_net_edge` often `0.02–0.03`.  
Miss reason: `net_edge`. If you only win by 1¢ on a mid, you are not clearing the cost of admission.

### 9.7 Per-strategy open slots (Phase 16)

`max_open_per_strategy` (default 8, `0` = unlimited) is the **default** cap per strategy.  
Override per strategy with `strategies.<name>.max_open` (desktop Settings: **cap** next to **w**).  
Miss reason: `strategy_slots`.

### 9.8 Paper execution realism (Phase 17)

When the book has BBO:

- Buy YES at **yes_ask**, buy NO at **1 − yes_bid** (`use_bbo_paper`)
- Entry **fee** (`paper_fee_bps`) reduces contracts for the same cash
- **Depth** can clip size; **max_spread** rejects wide markets
- Logs show mid vs entry, fee, and MTM drag (why $5 becomes ~$4.76)

Without BBO, falls back to mid ± `paper_slippage_bps`.

### 9.9 Config truth (desktop / suggestions)

Effective knobs = `config/default.yaml` ← `config/user.yaml`.  
**Not** commented lines in `.env`.  
Control Settings + `suggest-settings` load via `snapshot_user_knobs()` / full config.  
Bot freezes knobs at **process start** — restart after YAML edits.

### 9.10 LLM tools (Phase 18)

Grok calibration can call xAI **server-side** tools:

- `web_search` — live web  
- `x_search` — live X posts  

Knobs: `llm.tools_enabled`, `web_search`, `x_search`, `calibrate_with_tools`.  
Will it help? **Yes for news/event markets**; less so for pure longshot noise. Still advisory — risk/execution decide.  
Without tools, Grok is training data + your `news_context` only.

### 9.11 Paper fees vs live

Simulated **paper** fills apply `paper_fee_bps` + BBO drag.  
**Live** orders do **not** re-apply that model — venues already embed fees/fills in their responses.

---

## 10. How to add a strategy (checklist)

1. Subclass `BaseStrategy` in `src/chancetime/strategies/<name>.py`.  
2. Emit only `Signal`s; pure helpers for unit tests.  
3. Register in `build_strategies` + YAML defaults.  
4. **Write a full section in this SCROLL** (knobs, story, failure modes).  
5. Tests + optional backtest.  
6. Update `PROGRESS.md`.

---

## 11. Source map

| Path | Role |
|------|------|
| `strategies/*` | Item bag |
| `data_layer/*` | Venues, matching, BBO, mock |
| `risk/*` | Portfolio, families, cold strategies |
| `execution/*` | Paper + live orders |
| `persistence/*` | SQLite book, export, live_book |
| `llm/*` | Grok client, calibrate, review |
| `ml/*` | Offline train |
| `main.py` | CLI: run, markets, live-*, strategies, train-ml, export |

---

## 12. Glossary

| Term | Plain meaning |
|------|----------------|
| Mid | Average of bid and ask |
| BBO | Best bid / best offer |
| Edge | Your estimated advantage vs the market |
| Notional | Dollar size of a position |
| IOC | Immediate-or-cancel order (fill now or cancel) |
| Paper | Simulated trading |
| Resolve | Event decides YES or NO; contract pays $1 or $0 |

---

**Not financial advice.** Prediction markets involve real risk of loss. Start paper, size tiny, and treat every strategy as an experiment until your own stats say otherwise.

---

## Path C — Tweet hybrid crypto Up/Down (intl Polymarket)

**Module:** `src/chancetime/crypto_updown/` · **Not** Polymarket US.  
**Status:** Phase 29 research (paper). **CLI:** `chancetime crypto run` (shadow) · `--paper-strategy` (paper fills).

This is the **one** primary Path C strategy — a 5-step loop adapted from public “short crypto Up/Down bot” writeups. It is **not** proven edge; treat as a lab experiment.

### The five steps

| Step | What | Code |
|------|------|------|
| 1 | Record open/external price at first sight of window; stream Coinbase spot | `CryptoUpDownBot._window_refs` |
| 2 | Direction (spot vs open), vol (recent returns), TTE, Poly liquidity (spread/BBO) | `TweetHybridStrategy.evaluate_market` |
| 3 | Own **P(Up)** from spot/open/vol/TTE (heuristic sigmoid) | `model_p_up` |
| 4 | Buy undervalued side if \|model − market mid\| ≥ edge; complete-set if ask_up+ask_down &lt; threshold | `decide_actions` phases `mispricing` / `complete_set` |
| 5 | Near end, add size on clear favorite (snipe) | phase `snipe` |

### Knobs (`TweetStrategyConfig`)

| Knob | Default | Meaning |
|------|---------|---------|
| `min_edge` | 0.06 | Min model vs market gap to take mispricing leg |
| `size_usd` / `snipe_size_usd` | 5 | Paper notional per leg |
| `complete_set_max_sum` | 0.995 | Buy both sides only if ask sum below this |
| `max_spread` | 0.12 | Skip if side spread too wide |
| `snipe_seconds` | 90 | Enter snipe zone under this TTE |
| `snipe_min_p` | 0.62 | Clear-favorite threshold |
| `max_usd_per_market_side` | 25 | Inventory cap per market side |

CLI: `--strategy-edge`, `--size`, `--paper-strategy` / `--shadow-strategy`.

### Path D relationship

- C **publishes** direction/model signals → `data/research/signals/`
- D **may** paper-follow with `exchange run --trade-signals`
- D is an **executor platform** that can later hold many crypto strategies (trend, etc.) and even grow larger than Path A’s bag — **after** C’s paper loop is honest

### Failure modes

- Model is **not** true probability — wrong vol/open → wrong edge  
- Complete-set rare when books are 1.01–1.02  
- Sniping into thin books = adverse selection  
- Spot vs ref resolution is a **proxy**, not always official Poly settlement  

### Commands

```bash
uv run chancetime crypto run --once                    # shadow eval + signals
uv run chancetime crypto run --max-polls 40 --interval 15 --paper-strategy
uv run chancetime crypto scorecard
```

