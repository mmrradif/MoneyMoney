# 🤖 MoneyMoney Trading Bot - System Operating Rules

## 📌 1. Identity & Magic Number Protection
- **Bot Name**: MoneyMoney
- **Magic Range**: 100988 – 100999 (100% isolated from Money Maker 1008xx and Money Generator)
- **Symbol**: XAUUSD (Gold)
- **Timeframe**: M1 (1-Minute chart for signals, structure, TP, and loss recovery)
- **Dashboard Port**: 8020 (http://127.0.0.1:8020)

---

## ⚡ 2. Entry Signal Rules (M1 Confluence)
Trade entries require 100% alignment across 3 indicators on the **M1 timeframe**:
1. **PMAX (Profit Maximizer)**: M1 trend direction (Bullish / Bearish).
2. **HalfTrend**: M1 trend filter confirming PMAX.
3. **Candlestick Pattern Gate**: Closed M1 candle pattern (Engulfing, Pinbar, Marubozu, etc.) in trend direction.

---

## 🎯 3. Lot Size & Engine Settings
- **Base Lot Size**: **0.06**
- **Regular Engine**: **OFF** (Only Pending Order & Loss Recovery Engine active)
- **Max Open Entry Trades**: 1 main position at a time

---

## 🔄 4. Loss Recovery Mode (20 Pips Target)
- **Recovery Take-Profit Target**: **20 Pips** (2.0 price move on Gold).
- **BufferUSD**: .00 profit pad added to breakeven price (covers spread + commission + small net profit).
- **Recovery Horizon**: Dynamic martingale/hedging legs configured to exit all recovery legs at +20 pips net profit.

---

## 🛡️ 5. Absolute Trade Isolation Rules
1. **Zero Manual Trade Interaction**:
   - The bot **NEVER** sets, modifies, or cancels SL/TP on manual trades (magic == 0).
2. **Zero Foreign Bot Conflict**:
   - MoneyMoney **NEVER** modifies, closes, or cancels pending orders or running trades of **Money Generator** or **Money Maker**.
3. **Circuit Breakers**:
   - **Floating Loss Cap**: 80% default account floating loss limit.
   - **Daily Circuit Breaker**: Pauses bot if 50% daily loss limit or 9 consecutive losses hit.
