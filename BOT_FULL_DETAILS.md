# Money Maker Bot — Full Details

Complete documentation of the current **Money Maker** MT5 automated trading system (as implemented in this repo).

---

## 1. What this bot is

A Python + MetaTrader 5 expert-style bot for **XAUUSD (Gold)** that:

1. Connects to the local MT5 terminal (no password stored in code — uses open session)
2. Runs continuous multi-timeframe analysis (H1 / M15 / M5)
3. Maintains a **checklist score** for BUY and SELL
4. Operates **two engines** in parallel (togglable from dashboard / config):
   - **Pending Order Engine** — structural BUY_STOP / SELL_STOP triplets
   - **Regular Instant Market Engine** — high-score market entries
5. Serves a **live Flask dashboard** on `http://127.0.0.1:8000`

Primary capital goals: selective entries, zone-based SL/TP, circuit breakers, and a TP3 runner that only exits on SL or a clear opposite reversal signal.

---

## 2. Architecture

```
┌─────────────────┐     rates / ticks      ┌──────────────────┐
│  MetaTrader 5   │◄──────────────────────►│  mt5_interface.py │
│  (terminal)     │                        └────────┬─────────┘
└─────────────────┘                                 │
                                                    ▼
┌─────────────────┐   indicators/checklist  ┌──────────────────┐
│  strategy.py    │◄────────────────────────┤     main.py      │
└─────────────────┘                         │  bot_loop + API  │
                                            └────────┬─────────┘
┌─────────────────┐   lots / history        │
│ trade_tracker.py│◄────────────────────────┤
└─────────────────┘                         ▼
                                   ┌──────────────────┐
                                   │ templates/index  │
                                   │  Dashboard :8000 │
                                   └──────────────────┘
```

### Magic numbers (order tagging)

| Magic | Meaning |
|------:|---------|
| 100888 | Main BUY entry (TP1) |
| 100889 | Main SELL entry (TP1) |
| 100890 | TP3 reverse SELL_STOP (glued to Buy TP3 SL) |
| 100891 | TP3 reverse BUY_STOP (glued to Sell TP3 SL) |
| 100892 | TP3 BUY runner pending/position |
| 100893 | TP3 SELL runner pending/position |
| 100894 | TP2 BUY mid target (live-modified) |
| 100895 | TP2 SELL mid target (live-modified) |

---

## 3. Pending Order Engine (detail)

### 3.1 Dual entry mode

| Market regime | Pending behaviour |
|---------------|-------------------|
| **Ranging / consolidation** | Stops **outside** range + key structure (`RANGE BREAKOUT`) |
| **Smooth / trending** | Nearer M5/H1/MTF/OB structure stops (`SMC STRUCTURE`) — **not** breakout-only |

Regular instant market stays **blocked** while ranging; pending still works.

### 3.2 Mirror SL rule

For every pending pair:

- **BuyStop SL = SellStop entry price**
- **SellStop SL = BuyStop entry price**

When one side is filled, opposite pending syncs to the open position’s SL / entry.

### 3.3 Three legs (same entry, different TP)

| Leg | Role | TP source | Close behaviour |
|-----|------|-----------|-----------------|
| **TP1** | Quick / near | Near SMC / measured move | Normal broker TP |
| **TP2** | Mid structural | **support-and-resistance-mtf2** fractal resistance (BUY) / support (SELL), with structure-span projection if no print above/below | Broker TP; **live-modified** as MTF2 updates |
| **TP3** | Far runner | Far Fib/Gann/Hist reversal | Broker TP **cleared** after fill; closes only on **SL** or **opposite checklist ≥15/21**; virtual target always live-updated |

### 3.4 TP2 — support-and-resistance-mtf2

Uses Bill Williams fractal MTF zones already computed in `strategy.calculate_indicators`:

- `mtf_res_zone` / `mtf_sup_zone`
- `resistance` / `support`
- `last_high` / `last_low`
- Historical swing / fractal highs & lows

If price is breaking into new highs/lows with no further print above/below entry, TP2 uses an **MTF2 structure step** (`Next Res` / `Next Sup`) sized from MTF span, range height, ATR, and risk.

### 3.5 TP3 runner rules (strict)

1. **Always modify** the far virtual/pending target as structure updates  
2. **Do not close** on a fixed TP without a **reversal signal** (opposite checklist ≥ 15 and stronger than current side)  
3. **SL and reverse stop always stay together**  
   - Buy TP3 → `SELL_STOP` at SL (`MAGIC_REV_SELL`), reverse SL = buy entry  
   - Sell TP3 → `BUY_STOP` at SL (`MAGIC_REV_BUY`), reverse SL = sell entry  
4. Auto-repair must **never** re-attach a broker TP on TP3  
5. Dynamic RRR TP adjuster skips TP2/TP3 magics (pending engine owns those targets)

### 3.6 Pending lot sizing

- Base: `PENDING_BASE_LOT` (default **0.05**)
- Doubles on pending loss (martingale), capped by `PENDING_MAX_LOT`
- Resets toward base on win (see `trade_tracker`)

### 3.7 Circuit breakers (pending)

- **9 consecutive losses** → suspend pending stops for the day  
- **Daily ~10% drawdown** → can disable bot for the day (config / tracker)

---

## 4. Regular Instant Market Engine (detail)

| Rule | Value |
|------|-------|
| Lot | Fixed `REGULAR_BASE_LOT` (default **0.20**) — no martingale |
| Score | BUY or SELL checklist **≥ 15/21** and strictly stronger than the other side |
| Cooldown | **1 trade per H1 candle** |
| Ranging | **Blocked** 100% during consolidation |
| SL | Outside nearest H1 demand (BUY) / supply (SELL) + ATR buffer |
| TP | At opposite SMC / structure zones (soft RRR only if zone missing) |

Checklist rules include (among others): EMA/trend, RSI zones, MTF fractal S&R confluence, Fibo pivots, **!XPS AUTO FIB** golden zone (38.2–61.8%), Gann, OB, liquidity sweeps, etc. See `Strategy.get_checklist_status` in `strategy.py`.

---

## 5. Position management

Implemented mainly in `manage_open_positions` + `manage_tp3_runners` in `main.py`:

- Optional floating **profit cap** / **loss cap** (% of balance) — closes all + purges pendings  
- Auto-repair missing SL/TP on normal legs; **SL-only** repair for TP3  
- Optional breakeven / SMC structure trailing (config flags; often disabled for strict zone SL)  
- Dynamic TP adjustment for non-TP2/TP3 legs  
- TP3: strip TP, update virtual target, sync reverse stop, close on reversal only  

---

## 6. Dashboard

- URL: `http://127.0.0.1:8000`
- Live account equity/balance, checklist, market regime, open positions, logs  
- Engine ON/OFF badges for Pending / Regular  
- Chart / fib / structure overlays driven by API state from `main.py`

---

## 7. Config highlights (`config.py`)

| Key | Typical meaning |
|-----|-----------------|
| `SYMBOL` | `XAUUSD` |
| `ENABLE_PENDING_ENGINE` / `ENABLE_REGULAR_ENGINE` | Engine toggles |
| `PENDING_MODE` | `PMAX_RECOVERY` — 3-step no-loss pending (H1 zones outside + M5 C1/C2) |
| `PENDING_BASE_LOT` | `0.02` |
| `ENABLE_FLOATING_PROFIT_CAP` / `LOSS_CAP` | **OFF by default**; optional equity circuit breakers (+10% / -80% when enabled) |
| `MAX_DAILY_LOSS_PERCENT` / `MAX_TOTAL_LOSS_PERCENT` | **50%** daily pause / total emergency stop |
| `MAX_RECOVERY_STEPS` | **99** (keep recovering until flat) |
| `ATR_SL_MULTIPLIER` | SL distance helper |
| `POSITION_SPLIT_COUNT` | 3-leg split |
| `CONSECUTIVE_LOSS_LIMIT` | 9-loss pending breaker |

### SMC_PMAX_RECOVERY (pending) — short

1. **Step 1:** Valid H1 BuyStop/SellStop **outside** zones; M5 dual approach gate; on hit cancel opposite stops (no SL glue); TP1→BE; TP2/TP3 widen-only.  
2. **Step 2A profit reverse:** close + flip 3× base lot. **Step 2B loss:** larger recovery lot; reverse **no SL**; prior **SL** + cover reverse **TP** at 0-loss (cover = volume ≈ 2/3 of reverse legs, however many tickets that takes); when covered close priors+cover; 1 runner + zone TP.  
3. **Step 3 whipsaw:** if dual bias flips again while reverse is in loss → size new recovery lots to cover that loss; set recover **TP** + lose-side **SL** so all flatten ≈0. **No step 4.**

Manual trades, regular engine, and other safety flags remain as before.

Tune risk on a **demo** account first.

---

## 8. How to run (ops)

1. Start MT5 → login → enable **Algo Trading**  
2. `python main.py` from the project root  
3. Confirm log: `Connected to MT5 Account: ...`  
4. Open dashboard; verify pending stops appear when engines are ON and market is valid  

### Restart note
Killing an old `main.py` and starting a new one is normal after code changes. Exit code `4294967295` on Windows usually means the previous process was force-stopped.

---

## 9. Dependencies

```text
MetaTrader5
pandas
numpy
ta
flask
flask-cors
```

Optional: keep MT5 terminal on the same machine; remote VPS works if MT5 + Python run there.

---

## 10. What is intentionally not in git

- `bot.log`, `trade_tracker.json` (runtime)
- `.env` / credentials (none required for MT5 session auth)
- `scratch/`, `config_backup.py`
- `__pycache__/`

---

## 11. Related docs

- `TRADING_RULES.md` — original Bengali strategy narrative (may lag newest TP2/TP3 behaviour)
- This file (`BOT_FULL_DETAILS.md`) — **source of truth** for current pending TP2 (MTF2), TP3 runner + reverse stop, dual mode, and magic IDs

---

## 12. Disclaimer

Trading involves risk of loss. This software is provided for educational / private automation use. Past behaviour on demo (e.g. Exness trial) does not guarantee live results. Always validate on demo before real capital.
