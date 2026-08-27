# 🤖 Antigravity Forex Trading Bot - Trading Rules & Strategy Guide

এই ডকুমেন্টে আমাদের বটের সম্পূর্ণ ট্রেডিং অ্যালগরিদম, অ্যানালাইসিস পদ্ধতি, রিস্ক ম্যানেজমেন্ট এবং ট্রেড নেওয়ার নিয়ম সহজ ভাষায় বাংলায় ব্যাখ্যা করা হয়েছে।

---

## 🎯 ১. বটের প্রধান উদ্দেশ্য (Primary Directives)
- **Account Capital Preservation (মূলধন সুরক্ষা)**: অ্যাকাউন্ট কখনো জিরো (0) বা ওয়াশ হতে পারবে না। 
- **Selective Trading (কম ট্রেড, নিখুঁত এন্ট্রি)**: বট সারাদিন যত্রতত্র ট্রেড নিবে না। কেবল হাই-প্রোবাবিলিটি কনফার্মেশন পেলেই ট্রেড নিবে।
- **Dynamic Risk Control**: প্রতি ট্রেডে এক্সেস রিস্ক নিবে না।

---

## 📊 ২. টেকনিক্যাল অ্যানালাইসিস ও ট্রেড এন্ট্রির নিয়ম (Strategy Rules)

বটটি **Multi-Timeframe Analysis** (একাধিক টাইমফ্রেমে একসাথে অ্যানালাইসিস) ব্যবহার করে ট্রেড সিদ্ধান্ত নেয়।

### নিয়ম ১: ট্রেন্ড ফিল্টারিং (Higher Timeframe - H1)
বট প্রথমে **H1 (১ ঘন্টার)** ক্যান্ডেলস্টিক চার্টে মার্কেটের মূল দিক (Trend) নির্ধারণ করে:
- **EMA 50** যদি **EMA 200** এর **উপরে** থাকে 👉 মার্কেট **Uptrend (বাই ট্রেন্ড)**।
- **EMA 50** যদি **EMA 200** এর **নিচে** থাকে 👉 মার্কেট **Downtrend (সেল ট্রেন্ড)**।

> ⚠️ **রুল**: Uptrend থাকলে বট কেবল **BUY** ট্রেড খুঁজবে (কোন Sell নিবে না)। Downtrend থাকলে বট কেবল **SELL** ট্রেড খুঁজবে (কোন Buy নিবে না)।

---

### নিয়ম ২: নিখুঁত এন্ট্রি টাইমিং (Lower Timeframe - M15)
ট্রেন্ড কনফার্ম হওয়ার পর বট **M15 (১৫ মিনিটের)** ক্যান্ডেলস্টিক চার্টে পুলব্যাক (Pullback) ফিল্টার করে:

#### 🟢 BUY এন্ট্রির শর্ত:
1. H1 চার্টে মার্কেট Uptrend এ থাকতে হবে।
2. M15 চার্টে RSI Indicator আগের ক্যান্ডেলে **35 এর নিচে (Oversold Area)** থেকে রিকভার করে **35 এর উপরে** ক্রস করবে।
3. এই ২টি শর্ত মিললেই বট সাথে সাথে **BUY** এন্ট্রি প্লেস করবে।

#### 🔴 SELL এন্ট্রির শর্ত:
1. H1 চার্টে মার্কেট Downtrend এ থাকতে হবে।
2. M15 চার্টে RSI Indicator আগের ক্যান্ডেলে **65 এর উপরে (Overbought Area)** থেকে ড্রপ করে **65 এর নিচে** ক্রস করবে।
3. এই ২টি শর্ত মিললেই বট সাথে সাথে **SELL** এন্ট্রি প্লেস করবে।

---

## 🛡️ ৩. রিস্ক ও মানি ম্যানেজমেন্ট রুলস (Zone & Multi-Position Rules)

অ্যাকাউন্ট ব্যালেন্স নিরাপদ রাখতে বট কড়া গাণিতিক হিসাব ও জোন রুল মেনে চলে:

| রুল (Rule) | ভ্যালু / নিয়ম | বিবরণ (Explanation) |
|---|---|---|
| **Valid Zone Stop Loss** | **S&R / OB Boundary** | অনর্থক এন্ট্রি প্রাইসে Break Even করা হবে না। SL সবসময় **Valid Support/Resistance, Order Block, বা Swing High/Low**-এর বাইরে স্পাইক/লিকুইডিটি সুইপ প্রোটেকশনসহ বানিয়ান দূরত্বে বসবে। |
| **MTF Fractal S&R Rules** | **support-and-resistance-mtf2** | M15, H1, H4-এর 5-bar Bill Williams Fractals দিয়ে ডাইনামিক Support ও Resistance জোন গণনা করে SL/TP নির্ধারণ করে। |
| **Gold Daily Target Rules** | **GOLD999D1** | XAUUSD (Gold)-এর Daily ATR Volatility Expansion অনুযায়ী TP1 (Must-Hit), TP2 (Must-Hit), এবং TP3 (Daily Expansion Target) হিসাব করে। |
| **Fibonacci Pivot Rules** | **FiboPiv_v3** | Previous Day Range-এর ০.৩৮২, ০.৬১৮ (Golden Ratio), এবং ১.৬১৮ (Golden Expansion) রেসিও থেকে নির্ভুল TP ও S&R লেভেল ক্যালকুলেট করে। |
| **Gann Square of 9 Matrix** | **Gann_SQ9_2** | W.D. Gann-এর Square of 9 ম্যাট্রিক্স ($90^\circ, 180^\circ, 360^\circ$ Price Angles) দিয়ে গাণিতিক রিভার্সাল ও টিপি জোন নির্ধারণ করে। |
| **Ranging Market Protection** | **Range Bounds Guard** | Consolidation / Ranging মার্কেটের ভেতর বট কোনো Buy Stop বা Sell Stop বসাবে না। রেঞ্জ ব্রেক করে জোনের সম্পূর্ণ **বাইরে** পেন্ডিং অর্ডার বসবে, রেঞ্জ যতই বড় হোক না কেন। |
| **3-Split Order System** | **3 Positions** | ১টি সিগন্যালে ৩টি পজিশন চালু হবে: <br>1. **TP1 (Must Hit):** কনজারভেটিভ প্রফিট টার্গেট। <br>2. **TP2 (Must Hit):** স্ট্রাকচারাল কি-লেভেল প্রফিট। <br>3. **TP3 (Runner):** ট্রেন্ড ফলো করার জন্য প্রসারিত টার্গেট। |
| **Continuous Trend Riding & Reversal** | **Min 1 Active Trade** | ৩ নম্বর পজিশনটি বন্ধ হলে বা SL হিট করলে সাথে সাথে বিপরীতমুখী **Reversal Buy Stop / Sell Stop** ট্রিগার হয়ে ট্রেন্ড অনুযায়ী অন্তত ১টি ট্রেড রানিং রাখবে। |
| **1-Hour (H1) Signal Cooldown** | **H1 Candle Bar Guard** | একই ১-ঘন্টার (H1) ক্যান্ডেল বারে একাধিকবার স্কোরের তারতম্য (১০ ➔ ৯ ➔ ১০) হলেও সর্বমোট সর্বোচ্চ ১টি ইনস্ট্যান্ট ট্রেড প্লেস হবে। পরবর্তী ট্রেডের জন্য নতুন H1 ক্যান্ডেল ও স্ট্রাকচার প্রয়োজন হবে। |
| **Daily 9-Loss Circuit Breaker** | **9 Consecutive Losses** | ১ দিনে টানা ৯টি ট্রেড (৩টি সাইকেল × ৩ পজিশন) লস হলে বট পেন্ডিং Stop Orders নেওয়া বন্ধ করবে এবং সাধারণ **0.01 Lot Regular Mode**-এ স্যুইচ করবে। |




---

## ⛔ ৪. যা যা বট কখনোই করবে না (Strict Bans)
- ❌ **No Arbitrary Breakeven Shift**: এন্ট্রি প্রাইসে অহেতুক Break Even টেনে স্পাইকে ট্রেড ক্লোজ করবে না।
- ❌ **No Ranging Entry/Stops**: রেঞ্জিং মার্কেটের মাঝখানে কোনো ট্রেড বা স্টপ অর্ডার বসাবে না।
- ❌ **No Martingale**: ট্রেডে লস হলে লট সাইজ দ্বিগুণ বা বাড়িয়ে লস কভারের চেষ্টা করবে না।
- ❌ **No High Exposure**: অ্যাকাউন্টের অতিরিক্ত মার্জিন ব্যবহার করে হাই-লটে ট্রেড করবে না।


---

## 💻 ৫. বট রান ও টেস্ট করার ফাইলসমূহ

- **[TRADING_RULES.md](file:///d:/Money%20Maker/TRADING_RULES.md)**: এই ডকুমেন্টেশন ফাইল।
- **[config.py](file:///d:/Money%20Maker/config.py)**: রিস্ক এবং ইন্ডিকেটর সেটিংসের ফাইল।
- **[strategy.py](file:///d:/Money%20Maker/strategy.py)**: টেকনিক্যাল অ্যানালাইসিস কোড।
- **[mt5_interface.py](file:///d:/Money%20Maker/mt5_interface.py)**: MT5 টার্মিনাল কানেক্টর ও রিস্ক ক্যালকুলেটর।
- **[main.py](file:///d:/Money%20Maker/main.py)**: মূল অটোমেটিক ট্রেডিং বট রানার ফাইল।

### বট চালানোর কমান্ড:
```bash
python main.py
```
