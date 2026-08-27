# MoneyMoney — MT5 Automated Trading Bot

Private repository for the **MoneyMoney** MetaTrader 5 trading bot: dual-engine pending + regular entries, SMC/MTF zones, live TP2/TP3 management, Flask live dashboard.

---

## Quick start

### Requirements
- Windows PC with **MetaTrader 5** installed and logged in
- Python 3.10+ recommended
- Broker symbol (default): **XAUUSD** / `XAUUSDm` (Exness-compatible)

### Install
```bash
pip install MetaTrader5 pandas numpy ta flask flask-cors
```

### Run
1. Open MT5 terminal and enable **Algo Trading**
2. From project folder:
```bash
python main.py
```
3. Open dashboard: [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## Project layout

| File | Role |
|------|------|
| `main.py` | Bot loop, pending engine, regular engine, position management, Flask API |
| `strategy.py` | Indicators, checklist scoring, SL/TP zone math (SMC / MTF2 / Fib / Gann) |
| `mt5_interface.py` | MT5 connect, market/pending orders, modify SL/TP, close |
| `config.py` | Symbol, lots, risk caps, engine toggles |
| `trade_tracker.py` | Trade history, pending lot martingale, circuit breaker |
| `templates/index.html` | Live dashboard UI |
| `TRADING_RULES.md` | Original strategy rules (Bengali) |
| `BOT_FULL_DETAILS.md` | Full technical + operational documentation (this repo’s main doc) |

---

## Engines (summary)

### 1) Pending Order Engine
- Places **3× BUY_STOP** and **3× SELL_STOP** (TP1 / TP2 / TP3)
- **Dual mode:** ranging → range breakout stops; smooth/trend → nearer SMC structure stops
- **Mirror SL:** BuyStop SL = SellStop entry | SellStop SL = BuyStop entry
- **TP1:** near SMC / measured zone  
- **TP2:** `support-and-resistance-mtf2` fractal Res/Sup (live-modified)  
- **TP3:** far reversal target; **no broker TP close** without opposite checklist reversal; SL + reverse stop always paired

### 2) Regular Instant Market Engine
- Score ≥ **15/21** on checklist, 1 trade per H1 bar
- Blocked while market is ranging
- Fixed lot (no pending-style martingale)

---

## Safety

- Floating profit/loss caps (config)
- Daily drawdown / 9-loss pending circuit breaker
- Do not commit live account passwords — MT5 uses the already-logged terminal session

---

## License / privacy

**Private repository.** Do not make public without removing any account-specific notes or tracker dumps.
