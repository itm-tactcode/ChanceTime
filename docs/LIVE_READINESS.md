# Live readiness playbook (pre–Phase 21 / 13)

**Goal:** Earn the right to trade tiny live size through *your* paper history and ops hygiene—not vibes.

Not financial advice. Default remains **paper**.

**As of 2026-07-13:** Phase 20 research tools (scorecard, costs-on walk-forward) are in. Personal **Phase 21** is tiny live after these gates; dual-leg automation remains **Phase 13** after that.

## Gate checklist

Do these in order. Do not skip to dual-leg live (Phase 13) until the “Tiny live smoke” section is boring.

### A. Paper foundation (1–3 days)

- [ ] `chancetime doctor` clean (or only known warnings)
- [ ] Account books exist: `chancetime accounts`
- [ ] Paper bag multi-strategy:  
  `chancetime run -c config/paper_bag.yaml --account paper_bag --max-polls 20`
- [ ] Settings: **Record markets/BBO each poll** on for at least one session
- [ ] `chancetime list-history` shows files growing
- [ ] Desktop: Kill all stops bot + API (verify with `pgrep -af chancetime`)

### B. History evidence

- [ ] `chancetime walk-forward --folds 2` on fixture (sanity)
- [ ] `chancetime backtest --history data/history/markets-….jsonl --venue kalshi` (or mock)
- [ ] **Promotion rule (suggested):** no live size increase unless  
  - walk-forward mean test PnL ≥ 0 **and**  
  - paper bag has ≥ 20 fills across strategies **and**  
  - max daily loss circuit never hit in last paper week

### C. Ops loop (make it habit)

- [ ] Daily: `chancetime digest --account paper` (optionally `--send`)
- [ ] Weekly: `chancetime export --account paper --year YYYY`
- [ ] After any live smoke: `chancetime sync-positions` + export live book
- [ ] Desktop Ops tab: doctor, digest, export, history list all used once

### D. Live config dry-run (still paper)

- [ ] Apply preset **live_micro_dry** (paper_mode true, live caps in YAML for review)
- [ ] Or run live_micro settings with `PAPER_MODE=true` and shadow off only for paper fills
- [ ] Confirm account `live` DB stays empty while dry-running paper

### E. Tiny live smoke (real money, single venue, micro)

Only after A–D:

- [ ] `PAPER_MODE` deliberate; risk-ack flags on CLI
- [ ] `chancetime live-smoke` (or existing micro path) with hard caps
- [ ] `sync-positions` + `export --account live`
- [ ] Digest for live book
- [ ] **No dual-leg** until Phase 13 gates are written and tested in paper dual-leg first

### F. Kill / halt drills

- [ ] Force strategy error path / cold skip → no cascade crash
- [ ] Hit max daily loss in paper → halt, no new fills
- [ ] Desktop Kill all mid-poll → processes gone

## Recommended settings philosophy

1. **Presets** (hardcoded) — `chancetime presets` / UI Apply  
2. **Stats suggestions** — from `strategy_stats` + closed PnL; never auto-apply  
3. **LLM** — post-trade narrative only; not live size/edge authority  

See `chancetime suggest-settings` and desktop **Suggest** panel.

## Commands cheat sheet

```bash
uv run chancetime doctor
uv run chancetime accounts
uv run chancetime presets
uv run chancetime suggest-settings --account paper
uv run chancetime readiness          # checklist echo
uv run chancetime digest --account paper --send
uv run chancetime export --account paper --year 2026
uv run chancetime list-history
uv run chancetime walk-forward --folds 2
uv run chancetime backtest --history data/history/markets-YYYYMMDD.jsonl
```
