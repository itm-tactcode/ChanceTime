# Where you are (Chance Time mental model)

**One sentence:** A paper-first bot watches prediction markets, scores edges with strategies, and can place tiny live orders only when you force it—with a desktop app and CLI to run, watch, and tune it.

**Reality check:** Having a sophisticated stack is not the same as having edge. Expect long flat periods when gates are honest. See `docs/SECURITY.md` before exposing anything to a network.

## The pieces

```
┌─────────────────────────────────────────────────────────┐
│  DESKTOP (Tauri)                                        │
│  Control  → start/stop bot + API                        │
│  Ops      → digests, export, presets, suggestions       │
│  Settings → knobs written to config/user.yaml           │
│  Monitor  → portfolio UI (paper vs live books)          │
└───────────────┬─────────────────────────────────────────┘
                │ spawns local CLI
                ▼
┌─────────────────────────────────────────────────────────┐
│  CLI  (chancetime.*)                                    │
│  chancetime.bot.Bot  — poll loop                        │
│  chancetime.cli.*    — commands (status, scorecard, …)  │
│  every poll: markets → strategies → risk → fills        │
│  optional: history JSONL, shadow (no fills)             │
└───────────────┬─────────────────────────────────────────┘
                │ reads/writes
                ▼
┌─────────────────────────────────────────────────────────┐
│  data/paper.db   paper trading book                     │
│  data/live.db    live fills / sync                      │
│  data/history/   market snapshots for backtests         │
│  config/user.yaml  your overrides (not secrets)         │
│  .env + secrets/   API keys only (never commit)         │
└─────────────────────────────────────────────────────────┘
```

## What those commands you ran did

| Command | What it actually did |
|---------|----------------------|
| `readiness` | **Printed a checklist** of what *you* should do before live. No trading. |
| `presets list` | Listed **named setting packs** (conservative, shadow, …). |
| `presets apply conservative_paper` | **Wrote** those knobs into `config/user.yaml` (smaller size, simple_edge only, history on). **Next bot start** will use them. |
| `suggest-settings` | **Read paper.db stats** and *suggested* changes (e.g. boost simple_edge weight). Did **not** change config until `--apply`. |
| `--apply boost_simple_edge` | **Merged** weight 1.2 for simple_edge into `user.yaml`. |

Nothing placed live orders. Nothing touched API keys.

## What is *not* happening

- The bot is **not** always running—only when you Start bot / `chancetime run`.
- Presets/suggestions are **not** live trading advice engines; they edit local YAML.
- Monitor is a **viewer** of SQLite books, not a broker.

## Recommended weekly loop (keep it simple)

1. **Paper bag** a while with history on (`Settings` or preset).  
2. **Ops → Digest** once a day.  
3. **Ops → Suggestions** glance; apply only if you agree.  
4. **walk-forward / backtest --history** when you have a few days of JSONL.  
5. Only then consider **tiny** live smoke (see `docs/LIVE_READINESS.md`).

## If you feel lost, run only this

```bash
cd ~/Projects/chancetime
uv run chancetime doctor
uv run chancetime run --account paper --max-polls 5   # short paper session
uv run chancetime digest --account paper
```

Desktop: **Control → Start API → Start bot (account paper)** → **Monitor** to watch.

Phases **21+** (micro live) and **13** (dual-leg live automation) are **later**, after scorecard / readiness gates feel boring.

More docs: `SCROLL.md` (strategies) · `docs/LIVE_READINESS.md` · `docs/SECURITY.md` · `PROGRESS.md`
