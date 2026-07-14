# US crypto venues for Chance Time (Path D research)

**Not financial or legal advice.** Product availability changes by entity, state, and account type — verify in-app and on official docs before coding live paths.

**Out of scope for now:** Deribit (and Deribit-powered intl options stacks aimed at non-US eligibility). Do not plan Path D around Deribit.

Personal-stack question: *If intl Polymarket CLOB Up/Down is hard (wallet, geo, ToS), can we still trade short-horizon crypto direction ideas on a US-friendly API — and should we add a plain crypto module anyway?*

**Answer in short:** Yes — separate `crypto_exchange` module. Strong US bot candidates: **Coinbase Advanced**, **Robinhood Crypto API** (official, US crypto customers), **Kraken Pro**, plus **Crypto.com CDNA** if you want short-horizon option-like products. None are a 1:1 substitute for Polymarket 5m Up/Down.

---

## Path C vs Path D — link signals, not engines

| | Path C (`crypto_updown`) | Path D (`crypto_exchange`, planned) |
|--|--------------------------|-------------------------------------|
| Venue | Intl Polymarket CLOB | Coinbase / Robinhood / Kraken / Crypto.com (US-eligible) |
| Payoff | Event contract Up/Down | Spot (first), later futures / short-horizon derivatives |
| Auth | Wallet + CLOB L2 (live) | Exchange API keys |
| Default role | **Discover + price binaries + emit signals** | **Execute directional risk on US rails** |

### Do C and D need to be linked?

**Yes optionally — as a one-way signal bus, not a merged bot.**

Your example is the right *product* idea, wrong *coupling*:

> Poly window ends in 5m, book says “Up will hit,” we can’t/won’t buy the CLOB — so Path D buys a short-horizon call (or just spot/futures) on that coin.

That is a **cross-module strategy** (“PM-implied direction → exchange fill”), not a reason to merge C and D into one process or one DB.

| Layer | Responsibility |
|-------|----------------|
| **C** | Gamma/CLOB scan, window clock, BBO, complete-set metrics, optional paper fills on Poly |
| **Signal bus** | Typed events: `{asset, window_end, p_up_implied, strike_or_open_ref, confidence, source=poly_updown, ts}` — JSONL and/or in-process queue |
| **D** | Own market data, risk, paper/live exchange orders; **may** subscribe to C signals |
| **Hub** | Sum equities only — no trading logic |

```
  crypto_updown (C)  ──publish──►  signals (JSONL / bus)
                                         │
                                         ▼ optional consumer
  crypto_exchange (D)  ◄── also has own strategies (spot TA, etc.)
         │
         ▼
  hub portfolio (read-only sum of books)
```

**Rules:**

1. **C never places Coinbase/Robinhood orders.** D never places CLOB orders.  
2. **D must work with C offline** (own strategies). C must work with D offline (research/paper Poly).  
3. **Fallback routing** (“would buy Poly Up → buy exchange Up exposure”) is an explicit strategy flag, not hard-wired.  
4. Fail closed: stale Poly book, missing strike/open reference, or no tradable D product → **no D order**.  
5. Paper first: log “would have bought call/spot from Poly signal” before live.

### Important payoff reality check

Polymarket crypto **Up/Down** is usually “did spot finish above the window open / reference?” — a **binary** with known max loss/gain per share.

A **call option** needs strike, expiry, IV, and premium. A 5-minute binary is **not** the same as:

- ATM weekly call (wrong horizon), or  
- Spot long (unlimited downside relative to binary), or  
- Futures (funding, leverage).

So “consensus says Up” is a **direction signal**. Mapping it to D means choosing an **instrument approximation**:

| Intent | D instrument (US-friendly) | Notes |
|--------|----------------------------|--------|
| Direction only, simplest | Spot buy/sell | Robinhood or Coinbase API; no strike match |
| Levered direction | US crypto futures (Coinbase CFM / Kraken if unlocked) | Not binary loss |
| Closer to short binary | Crypto.com **UpDowns / Strike** (CDNA) | Best onshore analogy; confirm API automation |
| Listed call at Poly reference | Rare / often unavailable on short tenors for US retail crypto | Don’t design Phase 31 around this |

Late in a 5m window, if Poly is 95¢ Up **and spot is already through the reference**, you often don’t need Poly at all — **spot vs open** is enough. Poly shines when the book encodes info (or mispricing) **beyond** the last trade.

Also: if Poly already prices Up at 95¢, “following consensus” into D is **not free edge** — you need a reason D is underpricing that same view (or you’re just taking correlated directional risk).

---

## Venue comparison (mid-2026 snapshot)

### 1. Coinbase Advanced Trade — **default full-desk integration**

| | |
|--|--|
| Docs | https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/overview |
| SDK | Official Python: `coinbase-advanced-py` |
| Spot | Yes — mature REST + WebSocket |
| US derivatives | CFTC-regulated **US futures** via CFM |
| Auth | CDP API key (scoped) |
| Fit | Best general automation surface for spot + US futures |

### 2. Robinhood Crypto Trading API — **official US crypto bot API**

| | |
|--|--|
| Docs | https://docs.robinhood.com/ (Crypto Trading API) |
| Support | https://robinhood.com/us/en/support/articles/crypto-api/ |
| Credentials | Desktop: https://robinhood.com/account/crypto |
| What it does | Market data, account/holdings, **place crypto orders** programmatically (US Robinhood Crypto customers) |
| Versions | v1 orders; **v2** fee-tier orders (volume-based fee tiers) |
| Stocks/options API | **Not** the same — official public retail API is **crypto**, not equities/options trading |
| Fit | Strong if you already bank on RH; simpler product set (spot crypto). Good Path D **spot** backend alternative or second venue |

**Note:** Official docs + API credentials portal make this a real first-class candidate — earlier “poor bot API” takes referred to unofficial app scrapers / lack of equity API, not this crypto product.

### 3. Crypto.com Exchange + CDNA — **short-horizon derivative candidate**

| | |
|--|--|
| Exchange API | https://exchange-docs.crypto.com/ |
| US derivatives | **UpDown Options** / **Strike Options** via CDNA (CFTC); US membership required |
| Fit | Closest *regulated US* feel to short binary-ish exposure for the “Poly signal → option-like fill” story — **if** order APIs cover the product you want |

### 4. Kraken Pro — **alternative / multi-venue**

| | |
|--|--|
| Docs | https://docs.kraken.com/api/ |
| Fit | Mature spot (+ futures depending on account). Solid second venue. |

### 5. Out of scope / deprioritized

| Venue | Note |
|-------|------|
| **Deribit** | Steer clear for this project for now (geo / eligibility). |
| Binance.US | Spot possible; thinner set; churn |
| Gemini | Clean US exchange; smaller set |
| Webull crypto | Weaker bot story than RH official crypto API |
| CME / broker BTC options | Different stack; stretch |

---

## Recommended integration order (personal)

1. **Path C paper** — tweet hybrid + scorecard (Phase 29).  
2. **Path D** — Coinbase public spot paper (**done**).  
3. **Path D multi-venue (Phase 31–32):** implement adapters for **all four**:
   - Coinbase Advanced (private orders)
   - Robinhood Crypto API
   - Kraken Pro
   - Crypto.com Exchange (+ CDNA UpDowns research)
4. **Optional link** — C publishes signals; D paper-consumes.  
5. Futures / CDNA UpDowns after spot path is boring.  
6. No Deribit. Alpaca stocks remain stretch.

---

## Guardrails (same as other modules)

- Separate DB: `data/crypto_exchange_paper.db` (never mix with `paper.db` or `crypto_paper.db`).  
- `PAPER_MODE` / risk ack before live.  
- Fail closed on missing BBO / stale data / missing signal fields.  
- Log every order intent with venue + product type + optional `signal_id` from C.  
- Cross-module strategies must declare max notional and correlation to open C inventory (avoid double-betting the same BTC move without a cap).

---

## Sources to re-check before coding

- Coinbase Advanced: https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/overview  
- Robinhood Crypto API: https://docs.robinhood.com/ · support article on crypto API  
- Crypto.com UpDowns: https://help.crypto.com/en/articles/6983814-about-updown-options  
- Kraken API: https://docs.kraken.com/api/  

Update this file when a venue’s product matrix changes materially.
