"""
In-zone candlestick pattern scanner (display only — no trade decisions).

Finds 1 / 2 / 3 / 4-candle patterns separately on H1 and M5.
A pattern is kept only when the confirmation candle overlaps an active
supply, demand, or FVG zone. Each hit includes meaning, typical outcome,
and market character (dhormo) — still not a trade signal.
"""
import math
from datetime import timezone, timedelta
import pandas as pd
import numpy as np
import config

# Bangladesh Standard Time (no DST)
_BD_TZ = timezone(timedelta(hours=6))


PATTERN_DETAILS = {
    # ── 1 candle ──────────────────────────────────────────────
    "Doji": {
        "family": "1-candle", "character": "INDECISION",
        "meaning": "Open and close are almost equal. Buyers and sellers are balanced.",
        "happens": "The next candle decides direction. A doji in a zone is a pause, then break or reject.",
        "dharma": "Wait / indecision. Trend energy is colliding, not committed.",
    },
    "Dragonfly Doji": {
        "family": "1-candle", "character": "REVERSAL",
        "meaning": "Sellers push price down, but close returns near the high — lows are rejected.",
        "happens": "In demand this often bounces. In supply it can be a trap / hanging-man warning.",
        "dharma": "Selling pressure fails. Buyers take control at the last moment.",
    },
    "Gravestone Doji": {
        "family": "1-candle", "character": "REVERSAL",
        "meaning": "Buyers push price up, but close returns near the low — highs are rejected.",
        "happens": "In supply this often drops. In demand it can be a failed breakout.",
        "dharma": "Buying pressure fails. Sellers take control at the last moment.",
    },
    "Long-Legged Doji": {
        "family": "1-candle", "character": "INDECISION",
        "meaning": "Long wicks both sides. Dual liquidity grab, no clear winner.",
        "happens": "High volatility. The next close outside the zone often starts the move.",
        "dharma": "Chop / battle until a close confirms the trend.",
    },
    "Hammer": {
        "family": "1-candle", "character": "REVERSAL",
        "meaning": "Long lower wick, small body near the high — lows rejected, buyers step in.",
        "happens": "At demand/support a bounce is common. Confirmation = next bullish close.",
        "dharma": "Sell-off fade. Absorption / demand defense.",
    },
    "Hanging Man": {
        "family": "1-candle", "character": "REVERSAL",
        "meaning": "Same shape as a hammer, but printed at supply / after a rally — sellers probing down.",
        "happens": "At supply this warns of a drop. Confirmation = next bearish close.",
        "dharma": "Distribution starting. Buyers weaken, sellers probe.",
    },
    "Inverted Hammer": {
        "family": "1-candle", "character": "REVERSAL",
        "meaning": "Long upper wick after a decline — buyers test higher, close stays low.",
        "happens": "At demand a weak bounce. Strength rises only if the next candle is bullish.",
        "dharma": "Early demand test. Not a full reversal until follow-through.",
    },
    "Shooting Star": {
        "family": "1-candle", "character": "REVERSAL",
        "meaning": "Long upper wick, body near the low — highs rejected, sellers step in.",
        "happens": "At supply/resistance a drop is common. Confirmation = next bearish close.",
        "dharma": "Rally fade. Supply defense / liquidity grab above.",
    },
    "Bullish Marubozu": {
        "family": "1-candle", "character": "CONTINUATION",
        "meaning": "Almost no wicks, open near low, close near high — one-way buying.",
        "happens": "Trend continuation, demand breakout, or supply eaten through.",
        "dharma": "Aggressive demand. Momentum, not indecision.",
    },
    "Bearish Marubozu": {
        "family": "1-candle", "character": "CONTINUATION",
        "meaning": "Almost no wicks, open near high, close near low — one-way selling.",
        "happens": "Trend continuation, supply breakdown, or demand eaten through.",
        "dharma": "Aggressive supply. Momentum dump.",
    },
    "Spinning Top": {
        "family": "1-candle", "character": "INDECISION",
        "meaning": "Small body, wicks both sides — tug of war, weak conviction.",
        "happens": "Range / pause. In a zone the next candle decides reject or break.",
        "dharma": "Balance / chop. Low energy.",
    },
    "High Wave": {
        "family": "1-candle", "character": "INDECISION",
        "meaning": "Very long wicks both sides — extreme indecision plus stop hunt.",
        "happens": "Spike then snap-back. Fake breakout risk is high.",
        "dharma": "Volatility expansion without direction.",
    },
    "Bullish Belt Hold": {
        "family": "1-candle", "character": "REVERSAL",
        "meaning": "Opens near the low, closes strong near the high — buyers control from the open.",
        "happens": "At demand a bounce. Follow-through can flip the trend.",
        "dharma": "Opening absorption then a demand drive.",
    },
    "Bearish Belt Hold": {
        "family": "1-candle", "character": "REVERSAL",
        "meaning": "Opens near the high, closes strong near the low — sellers control from the open.",
        "happens": "At supply a drop. Follow-through can flip the trend.",
        "dharma": "Opening rejection then a supply drive.",
    },

    # ── 2 candle ──────────────────────────────────────────────
    "Bullish Engulfing": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "A large green body fully covers the prior red body — sellers lose.",
        "happens": "At demand a strong bounce. Close above the zone supports continuation up.",
        "dharma": "Power shift: supply to demand. Aggressive reversal.",
    },
    "Bearish Engulfing": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "A large red body fully covers the prior green body — buyers lose.",
        "happens": "At supply a strong drop. Close below the zone supports continuation down.",
        "dharma": "Power shift: demand to supply. Aggressive reversal.",
    },
    "Piercing Line": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "After a red candle, green cuts more than 50% of the prior body but does not fully engulf.",
        "happens": "At demand a moderate bounce. Weaker than engulfing.",
        "dharma": "Partial demand recovery. Buyers try, not a full takeover.",
    },
    "Dark Cloud Cover": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "After a green candle, red cuts more than 50% of the prior body — cloud over the highs.",
        "happens": "At supply a moderate drop. Weaker than engulfing.",
        "dharma": "Partial supply recovery. Sellers try, not a full takeover.",
    },
    "Bullish Harami": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Small green inside a large red — selling slows (inside bar).",
        "happens": "Pause, then possible bounce. Confirm on a break of the inside bar high.",
        "dharma": "Momentum decay. Compression before reverse or continue.",
    },
    "Bearish Harami": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Small red inside a large green — buying slows (inside bar).",
        "happens": "Pause, then possible drop. Confirm on a break of the inside bar low.",
        "dharma": "Momentum decay. Compression.",
    },
    "Bullish Harami Cross": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Doji inside a large red — extreme indecision after a sell-off.",
        "happens": "Stronger reversal odds than a plain harami if the next candle is up.",
        "dharma": "Sellers exhausted. Market is deciding.",
    },
    "Bearish Harami Cross": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Doji inside a large green — extreme indecision after a rally.",
        "happens": "Stronger reversal odds than a plain harami if the next candle is down.",
        "dharma": "Buyers exhausted. Market is deciding.",
    },
    "Tweezer Bottom": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Two candles share the same low — double tap on demand.",
        "happens": "Support holds. A third candle up confirms the bounce.",
        "dharma": "Liquidity sweep then defend. Demand wall.",
    },
    "Tweezer Top": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Two candles share the same high — double tap on supply.",
        "happens": "Resistance holds. A third candle down confirms the drop.",
        "dharma": "Liquidity sweep then defend. Supply wall.",
    },
    "Bullish Kicking": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Bear marubozu then a gap-up bull marubozu — sudden power flip.",
        "happens": "Sharp rally. Impulse / gap-style move.",
        "dharma": "Violent regime change. Supply is gone.",
    },
    "Bearish Kicking": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Bull marubozu then a gap-down bear marubozu — sudden power flip.",
        "happens": "Sharp dump. Impulse / gap-style move.",
        "dharma": "Violent regime change. Demand is gone.",
    },
    "On Neck": {
        "family": "2-candle", "character": "CONTINUATION",
        "meaning": "After red, a small green closes near the prior low — weak bounce.",
        "happens": "Downtrend usually continues. Rally fails.",
        "dharma": "Bear flag / weak demand. Supply still in control.",
    },
    "In Neck": {
        "family": "2-candle", "character": "CONTINUATION",
        "meaning": "After red, green closes slightly above the prior close — still weak.",
        "happens": "Downtrend can continue. Not a reversal.",
        "dharma": "Minor covering, not a takeover.",
    },
    "Thrusting": {
        "family": "2-candle", "character": "CONTINUATION",
        "meaning": "After red, green enters less than 50% of the prior body — incomplete bounce.",
        "happens": "Downtrend often resumes. Weaker than a piercing line.",
        "dharma": "Failed recovery. Bears still dominant.",
    },
    "Bullish Meeting Lines": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Red then green with almost the same close — stall at one price.",
        "happens": "Demand stall. Next close up supports a bounce.",
        "dharma": "Equilibrium print. Decision bar is next.",
    },
    "Bearish Meeting Lines": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Green then red with almost the same close — stall at one price.",
        "happens": "Supply stall. Next close down supports a drop.",
        "dharma": "Equilibrium print. Decision bar is next.",
    },
    "Homing Pigeon": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Two red candles, second smaller and inside the first body — selling shrinks.",
        "happens": "Down move fades. At demand this is a bounce setup.",
        "dharma": "Bear exhaustion. Energy is dropping.",
    },
    "Descending Hawk": {
        "family": "2-candle", "character": "REVERSAL",
        "meaning": "Two green candles, second smaller and inside the first — buying shrinks.",
        "happens": "Up move fades. At supply this is a drop setup.",
        "dharma": "Bull exhaustion.",
    },

    # ── 3 candle ──────────────────────────────────────────────
    "Morning Star": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Large red, small indecision, then large green that cuts 50%+ of the first red.",
        "happens": "Classic demand bottom. Follow-through supports a move up.",
        "dharma": "Sell climax → pause → demand takeover. Strong reversal.",
    },
    "Evening Star": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Large green, small indecision, then large red that cuts 50%+ of the first green.",
        "happens": "Classic supply top. Follow-through supports a move down.",
        "dharma": "Buy climax → pause → supply takeover. Strong reversal.",
    },
    "Morning Doji Star": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Morning star with a doji in the middle — stronger indecision then flip.",
        "happens": "Respected bottom if the zone holds.",
        "dharma": "Exhaustion doji then demand explosion.",
    },
    "Evening Doji Star": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Evening star with a doji in the middle — stronger indecision then flip.",
        "happens": "Respected top if the zone holds.",
        "dharma": "Exhaustion doji then supply explosion.",
    },
    "Abandoned Baby Bullish": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Gap-down doji, then gap-up green — island reversal.",
        "happens": "Sharp bounce. Rare but strong.",
        "dharma": "Liquidity island. Sellers trapped below.",
    },
    "Abandoned Baby Bearish": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Gap-up doji, then gap-down red — island reversal.",
        "happens": "Sharp drop. Rare but strong.",
        "dharma": "Liquidity island. Buyers trapped above.",
    },
    "Three White Soldiers": {
        "family": "3-candle", "character": "CONTINUATION",
        "meaning": "Three consecutive strong green candles, each closing higher.",
        "happens": "Uptrend / demand breakout. If overbought, the 4th candle may pull back.",
        "dharma": "Controlled bullish auction. Strong demand.",
    },
    "Three Black Crows": {
        "family": "3-candle", "character": "CONTINUATION",
        "meaning": "Three consecutive strong red candles, each closing lower.",
        "happens": "Downtrend / supply breakdown. If oversold, the 4th candle may bounce.",
        "dharma": "Controlled bearish auction. Strong supply.",
    },
    "Three Inside Up": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Bullish harami, then a 3rd candle closes above the mother bar.",
        "happens": "Confirmed inside-bar reversal up.",
        "dharma": "Compression then expansion up. Demand wins the inside range.",
    },
    "Three Inside Down": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Bearish harami, then a 3rd candle closes below the mother bar.",
        "happens": "Confirmed inside-bar reversal down.",
        "dharma": "Compression then expansion down. Supply wins the inside range.",
    },
    "Three Outside Up": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Bullish engulfing, then a 3rd candle closes higher — engulf confirmed.",
        "happens": "Strong bounce continuation.",
        "dharma": "Power shift confirmed. Demand in control.",
    },
    "Three Outside Down": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Bearish engulfing, then a 3rd candle closes lower — engulf confirmed.",
        "happens": "Strong drop continuation.",
        "dharma": "Power shift confirmed. Supply in control.",
    },
    "Upside Tasuki Gap": {
        "family": "3-candle", "character": "CONTINUATION",
        "meaning": "Two greens with a gap up, 3rd red partly fills the gap but does not close it.",
        "happens": "Uptrend continues. Held gap acts as support.",
        "dharma": "Bull flag. Demand still dominant.",
    },
    "Downside Tasuki Gap": {
        "family": "3-candle", "character": "CONTINUATION",
        "meaning": "Two reds with a gap down, 3rd green partly fills the gap but does not close it.",
        "happens": "Downtrend continues. Held gap acts as resistance.",
        "dharma": "Bear flag. Supply still dominant.",
    },
    "Stick Sandwich": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Red + green + red, 1st and 3rd closes almost equal — sandwich lows.",
        "happens": "Demand defends. Next close up supports a bounce.",
        "dharma": "Double close support. Sellers cannot print a new close-low.",
    },
    "Advance Block": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Three greens but bodies shrink and upper wicks grow — tired rally.",
        "happens": "At supply: stall then drop. Not a healthy trend.",
        "dharma": "Bull exhaustion / distribution.",
    },
    "Deliberation": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Two strong greens then a small / spinning 3rd — rally hesitates.",
        "happens": "Pause or top. At supply this is a reversal warning.",
        "dharma": "Trend fatigue. A decision is coming.",
    },
    "Two Crows": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Strong green, small gap-up red, then another red into the first body.",
        "happens": "Uptrend cracks. Supply takes the highs.",
        "dharma": "Failed continuation. Distribution.",
    },
    "Unique Three River": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Long red, 2nd makes a lower-low hammer, 3rd small green above that low.",
        "happens": "Capitulation then tiny demand. Slow bottom.",
        "dharma": "Sell-climax washout. Weak but real demand.",
    },
    "Tri-Star Bullish": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Three dojis, middle one lower — extreme indecision at the lows.",
        "happens": "Rare bottom. The next impulse candle sets direction.",
        "dharma": "Deadlock then flip. Exhaustion.",
    },
    "Tri-Star Bearish": {
        "family": "3-candle", "character": "REVERSAL",
        "meaning": "Three dojis, middle one higher — extreme indecision at the highs.",
        "happens": "Rare top. The next impulse candle sets direction.",
        "dharma": "Deadlock then flip. Exhaustion.",
    },

    # ── 4 candle ──────────────────────────────────────────────
    "Bullish Three-Line Strike": {
        "family": "4-candle", "character": "CONTINUATION",
        "meaning": "Three reds down, 4th huge green closes above those three — strike.",
        "happens": "The down move is cancelled. Strong demand reclaim.",
        "dharma": "Bear trap then aggressive auction up.",
    },
    "Bearish Three-Line Strike": {
        "family": "4-candle", "character": "CONTINUATION",
        "meaning": "Three greens up, 4th huge red closes below those three — strike.",
        "happens": "The up move is cancelled. Strong supply reclaim.",
        "dharma": "Bull trap then aggressive auction down.",
    },
    "Concealing Baby Swallow": {
        "family": "4-candle", "character": "REVERSAL",
        "meaning": "Three falling bear candles, 4th fully engulfs the 3rd including the wick.",
        "happens": "Downtrend hides then reverses. At demand this can bounce.",
        "dharma": "Selling climax concealed then eaten. Rare bullish reversal.",
    },
    "Bullish 4-Bar Engulf": {
        "family": "4-candle", "character": "REVERSAL",
        "meaning": "Last green covers the high-low range of the prior 3 bars.",
        "happens": "Range break up. Zone reclaim.",
        "dharma": "Multi-bar supply absorbed in one print.",
    },
    "Bearish 4-Bar Engulf": {
        "family": "4-candle", "character": "REVERSAL",
        "meaning": "Last red covers the high-low range of the prior 3 bars.",
        "happens": "Range break down. Zone lost.",
        "dharma": "Multi-bar demand absorbed in one print.",
    },
    "Rising Window Hold": {
        "family": "4-candle", "character": "CONTINUATION",
        "meaning": "Gap up, two small pullback candles, 4th green holds the gap.",
        "happens": "Uptrend resumes. Pullback is a buy zone.",
        "dharma": "Bull continuation. Dip absorbed.",
    },
    "Falling Window Hold": {
        "family": "4-candle", "character": "CONTINUATION",
        "meaning": "Gap down, two small bounce candles, 4th red holds the gap.",
        "happens": "Downtrend resumes. Bounce is a sell zone.",
        "dharma": "Bear continuation. Rally absorbed.",
    },
}


def _details(name):
    d = PATTERN_DETAILS.get(name) or {
        "family": "—", "character": "INDECISION",
        "meaning": "Classic candlestick print.",
        "happens": "Wait for the next candle to confirm.",
        "dharma": "Watch the zone reaction.",
    }
    return dict(d)


def _truthy(v):
    try:
        if v is None:
            return False
        if isinstance(v, (float, np.floating)) and math.isnan(v):
            return False
        if pd.isna(v):
            return False
        return bool(v)
    except Exception:
        return False


def _f(v, default=0.0):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return float(default)
        if pd.isna(v):
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _ohlc(df, i):
    row = df.iloc[i]
    return _f(row.get("open")), _f(row.get("high")), _f(row.get("low")), _f(row.get("close"))


def _geom(o, h, l, c):
    body = abs(c - o)
    rng = max(h - l, 1e-9)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return body, rng, upper, lower, c > o, c < o


def _bar_time(df, i):
    t = df.iloc[i].get("time")
    try:
        ts = pd.Timestamp(t)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        ts = ts.tz_convert(_BD_TZ)
        return ts.strftime("%d %b %Y %I:%M %p") + " BD"
    except Exception:
        return str(i)


def _overlaps(c_low, c_high, z_low, z_high):
    return c_low <= z_high and c_high >= z_low


def _is_doji(body, rng):
    return body <= rng * 0.14


def _maru(body, rng, upper, lower):
    return body >= rng * 0.78 and upper <= rng * 0.10 and lower <= rng * 0.10


def extract_zones(df, digits=2, fvg_lookback=24):
    zones = []
    if df is None or len(df) == 0:
        return zones

    last = df.iloc[-1]
    rz_lo = _f(last.get("res_zone_low"))
    rz_hi = _f(last.get("res_zone_high"))
    sz_lo = _f(last.get("sup_zone_low"))
    sz_hi = _f(last.get("sup_zone_high"))

    if rz_hi > 0 and rz_hi >= rz_lo:
        if rz_hi == rz_lo:
            atr = max(_f(last.get("atr"), 5.0), 1.0)
            rz_lo = rz_hi - atr * 0.2
        zones.append({
            "kind": "SUPPLY", "label": "Resistance / Supply",
            "low": round(rz_lo, digits), "high": round(rz_hi, digits),
        })

    if sz_lo > 0 and sz_hi >= sz_lo:
        if sz_hi == sz_lo:
            atr = max(_f(last.get("atr"), 5.0), 1.0)
            sz_hi = sz_lo + atr * 0.2
        zones.append({
            "kind": "DEMAND", "label": "Support / Demand",
            "low": round(sz_lo, digits), "high": round(sz_hi, digits),
        })

    start = max(2, len(df) - int(fvg_lookback))
    for i in range(start, len(df)):
        row = df.iloc[i]
        if _truthy(row.get("fvg_bullish", False)):
            zlo = _f(df.iloc[i - 2].get("high"))
            zhi = _f(row.get("low"))
            if zhi > zlo:
                zones.append({
                    "kind": "FVG_DEMAND", "label": "Bullish FVG",
                    "low": round(zlo, digits), "high": round(zhi, digits),
                })
        if _truthy(row.get("fvg_bearish", False)):
            zlo = _f(row.get("high"))
            zhi = _f(df.iloc[i - 2].get("low"))
            if zhi > zlo:
                zones.append({
                    "kind": "FVG_SUPPLY", "label": "Bearish FVG",
                    "low": round(zlo, digits), "high": round(zhi, digits),
                })

    unique = []
    for z in zones:
        dup = False
        for u in unique:
            if z["kind"][:3] == u["kind"][:3] and abs(z["low"] - u["low"]) < 0.4 and abs(z["high"] - u["high"]) < 0.4:
                dup = True
                break
        if not dup:
            unique.append(z)
    return unique


def _instruction(bias, character, zone_kind, zone_label):
    kind = str(zone_kind or "")
    demand = kind in ("DEMAND", "FVG_DEMAND")
    supply = kind in ("SUPPLY", "FVG_SUPPLY")
    if bias == "BEARISH" and supply:
        return f"Sellers are defending {zone_label}. Instruction: rejection. Next close below the zone would confirm."
    if bias == "BULLISH" and demand:
        return f"Buyers are defending {zone_label}. Instruction: bounce. Next close above the zone would confirm."
    if bias == "BEARISH" and demand:
        return f"Sellers are attacking {zone_label}. Instruction: breakdown test. If the zone holds, this can be a trap."
    if bias == "BULLISH" and supply:
        return f"Buyers are attacking {zone_label}. Instruction: breakout test. If the zone holds, this can be a trap."
    if bias == "NEUTRAL":
        return f"No side has won yet inside {zone_label}. Instruction: wait for the next candle."
    if character == "CONTINUATION" and bias == "BULLISH":
        return f"Buying is still in force inside {zone_label}. Instruction: continuation up until the zone is lost."
    if character == "CONTINUATION" and bias == "BEARISH":
        return f"Selling is still in force inside {zone_label}. Instruction: continuation down until the zone is lost."
    return f"Watch the next candle inside {zone_label}. Display only — not a trade order."


def _zone_visit_indices(df, zone, last_i, max_lookback=24):
    """Consecutive candles from now back while they still touch this zone. Oldest first."""
    idxs = []
    start = max(0, last_i - int(max_lookback) + 1)
    for i in range(last_i, start - 1, -1):
        _o, h, l, _c = _ohlc(df, i)
        if _overlaps(l, h, zone["low"], zone["high"]):
            idxs.append(i)
        else:
            break
    idxs.reverse()
    return idxs


def _pick_current_zones(zones, close, high, low, atr):
    """Zone the live candle is in right now (close inside, else wick touch, else nearby)."""
    if not zones:
        return []
    inside = [z for z in zones if z["low"] <= close <= z["high"]]
    if inside:
        inside.sort(key=lambda z: (z["high"] - z["low"], abs(((z["low"] + z["high"]) / 2.0) - close)))
        return inside[:1]
    touch = [z for z in zones if _overlaps(low, high, z["low"], z["high"])]
    if touch:
        touch.sort(key=lambda z: min(abs(close - z["low"]), abs(close - z["high"])))
        return touch[:1]
    buf = max(float(atr) * 0.25, 0.8)
    near = []
    for z in zones:
        if (z["low"] - buf) <= close <= (z["high"] + buf):
            near.append(z)
    if near:
        near.sort(key=lambda z: min(abs(close - z["low"]), abs(close - z["high"])))
        return near[:1]
    return []


def _detect_at(df, i):
    """All 1/2/3/4-candle patterns whose confirmation bar is index i."""
    if i < 1 or i >= len(df):
        return []

    o, h, l, c = _ohlc(df, i)
    body, rng, upper, lower, bull, bear = _geom(o, h, l, c)
    po, ph, pl, pc = _ohlc(df, i - 1)
    pbody, prng, pupper, plower, pbull, pbear = _geom(po, ph, pl, pc)

    g2 = g3 = None
    if i >= 2:
        o2, h2, l2, c2 = _ohlc(df, i - 2)
        g2 = (o2, h2, l2, c2) + _geom(o2, h2, l2, c2)
    if i >= 3:
        o3, h3, l3, c3 = _ohlc(df, i - 3)
        g3 = (o3, h3, l3, c3) + _geom(o3, h3, l3, c3)

    found = []

    def add(name, bias, bars):
        found.append({"name": name, "bias": bias, "bars": bars})

    # ── 4-candle ──
    if g3 is not None:
        o3, h3, l3, c3, b3, r3, u3, lo3, bull3, bear3 = g3
        o2, h2, l2, c2, b2, r2, u2, lo2, bull2, bear2 = g2
        # Three-line strike bullish: 3 down then huge up engulfing all 3
        if bear3 and pbear and bear2 and bull:
            hi3 = max(h3, h2, ph)
            lo3b = min(l3, l2, pl)
            if c > hi3 and o <= min(c3, c2, pc) and body > max(b3, b2, pbody):
                add("Bullish Three-Line Strike", "BULLISH", 4)
        if bull3 and pbull and bull2 and bear:
            lo3b = min(l3, l2, pl)
            hi3 = max(h3, h2, ph)
            if c < lo3b and o >= max(c3, c2, pc) and body > max(b3, b2, pbody):
                add("Bearish Three-Line Strike", "BEARISH", 4)
        # Concealing baby swallow (simplified)
        if bear3 and bear2 and pbear and bull and c >= ph and o <= pl:
            add("Concealing Baby Swallow", "BULLISH", 4)
        # 4-bar engulf
        rng3h = max(h3, h2, ph)
        rng3l = min(l3, l2, pl)
        if bull and c >= rng3h and o <= rng3l:
            add("Bullish 4-Bar Engulf", "BULLISH", 4)
        if bear and c <= rng3l and o >= rng3h:
            add("Bearish 4-Bar Engulf", "BEARISH", 4)
        # Window hold (gap then 2 small then resume)
        if bull3 and c3 > h2 and (not bull2) and (not pbull) and bull and l >= min(c3, o3) * 0.999:
            if c > pc:
                add("Rising Window Hold", "BULLISH", 4)
        if bear3 and c3 < l2 and (not bear2) and (not pbear) and bear and h <= max(c3, o3) * 1.001:
            if c < pc:
                add("Falling Window Hold", "BEARISH", 4)

    # ── 3-candle ──
    if g2 is not None:
        o2, h2, l2, c2, b2, r2, u2, lo2, bull2, bear2 = g2
        mid_doji = _is_doji(pbody, prng)
        small_mid = pbody < b2 * 0.65

        if bear2 and bull and small_mid and c > ((o2 + c2) / 2.0) and c2 < o2:
            if mid_doji:
                add("Morning Doji Star", "BULLISH", 3)
            else:
                add("Morning Star", "BULLISH", 3)
            if l > h2 and ph < l2 and pl > h:
                add("Abandoned Baby Bullish", "BULLISH", 3)

        if bull2 and bear and small_mid and c < ((o2 + c2) / 2.0) and c2 > o2:
            if mid_doji:
                add("Evening Doji Star", "BEARISH", 3)
            else:
                add("Evening Star", "BEARISH", 3)
            if h < l2 and pl > h2 and ph < l:
                add("Abandoned Baby Bearish", "BEARISH", 3)

        if bull2 and pbull and bull and (c > pc > c2) and (o <= pc) and (po <= c2):
            add("Three White Soldiers", "BULLISH", 3)
            if b2 >= pbody >= body and upper >= pupper and pupper >= u2:
                add("Advance Block", "BEARISH", 3)
            if _is_doji(body, rng) or body < pbody * 0.45:
                add("Deliberation", "BEARISH", 3)

        if bear2 and pbear and bear and (c < pc < c2) and (o >= pc) and (po >= c2):
            add("Three Black Crows", "BEARISH", 3)

        # Three inside: harami + confirm
        if bear2 and pbull and pbody < b2 and pc < o2 and po > c2 and bull and c > o2:
            add("Three Inside Up", "BULLISH", 3)
        if bull2 and pbear and pbody < b2 and pc > o2 and po < c2 and bear and c < o2:
            add("Three Inside Down", "BEARISH", 3)

        # Three outside: engulf + confirm
        if bear2 and pbull and pc >= o2 and po <= c2 and pbody > b2 and bull and c > pc:
            add("Three Outside Up", "BULLISH", 3)
        if bull2 and pbear and pc <= o2 and po >= c2 and pbody > b2 and bear and c < pc:
            add("Three Outside Down", "BEARISH", 3)

        if bull2 and pbull and (min(c2, o2) > max(pc, po)) and bear and c < max(pc, po) and c > min(c2, o2):
            add("Upside Tasuki Gap", "BULLISH", 3)
        if bear2 and pbear and (max(c2, o2) < min(pc, po)) and bull and c > min(pc, po) and c < max(c2, o2):
            add("Downside Tasuki Gap", "BEARISH", 3)

        if bear2 and pbull and bear and abs(c - c2) <= rng * 0.12:
            add("Stick Sandwich", "BULLISH", 3)

        if bull2 and pbear and bear and c < ((o2 + c2) / 2.0) and po > c2:
            add("Two Crows", "BEARISH", 3)

        if bear2 and pbear and pl < l2 and plower >= pbody and bull and l > pl:
            add("Unique Three River", "BULLISH", 3)

        if _is_doji(b2, r2) and _is_doji(pbody, prng) and _is_doji(body, rng):
            if min(l2, pl, l) == pl or pl <= min(l2, l):
                add("Tri-Star Bullish", "BULLISH", 3)
            if max(h2, ph, h) == ph or ph >= max(h2, h):
                add("Tri-Star Bearish", "BEARISH", 3)

    # ── 2-candle ──
    if pbear and bull and c >= po and o <= pc and body > pbody:
        add("Bullish Engulfing", "BULLISH", 2)
    if pbull and bear and c <= po and o >= pc and body > pbody:
        add("Bearish Engulfing", "BEARISH", 2)

    if pbear and bull and o < pc and c > ((po + pc) / 2.0) and c < po and body > pbody * 0.45:
        add("Piercing Line", "BULLISH", 2)
    if pbull and bear and o > pc and c < ((po + pc) / 2.0) and c > po and body > pbody * 0.45:
        add("Dark Cloud Cover", "BEARISH", 2)

    if pbear and bull and o < pc and c <= (pc + (po - pc) * 0.25) and c > pl:
        add("On Neck", "BEARISH", 2)
    if pbear and bull and o < pc and pc < c < ((po + pc) / 2.0) and c < po:
        if body <= pbody * 0.7:
            add("In Neck", "BEARISH", 2)
            add("Thrusting", "BEARISH", 2)

    if pbear and bull and body < pbody and c < po and o > pc:
        add("Bullish Harami", "BULLISH", 2)
        if _is_doji(body, rng):
            add("Bullish Harami Cross", "BULLISH", 2)
    if pbull and bear and body < pbody and c > po and o < pc:
        add("Bearish Harami", "BEARISH", 2)
        if _is_doji(body, rng):
            add("Bearish Harami Cross", "BEARISH", 2)

    if abs(l - pl) <= max(rng, prng) * 0.08 and lower >= body * 0.8 and plower >= pbody * 0.5:
        add("Tweezer Bottom", "BULLISH", 2)
    if abs(h - ph) <= max(rng, prng) * 0.08 and upper >= body * 0.8 and pupper >= pbody * 0.5:
        add("Tweezer Top", "BEARISH", 2)

    if _maru(pbody, prng, pupper, plower) and _maru(body, rng, upper, lower):
        if pbear and bull and o > ph:
            add("Bullish Kicking", "BULLISH", 2)
        if pbull and bear and o < pl:
            add("Bearish Kicking", "BEARISH", 2)

    if pbear and bull and abs(c - pc) <= rng * 0.08:
        add("Bullish Meeting Lines", "BULLISH", 2)
    if pbull and bear and abs(c - pc) <= rng * 0.08:
        add("Bearish Meeting Lines", "BEARISH", 2)

    if pbear and bear and body < pbody and h <= ph and l >= pl:
        add("Homing Pigeon", "BULLISH", 2)
    if pbull and bull and body < pbody and h <= ph and l >= pl:
        add("Descending Hawk", "BEARISH", 2)

    # ── 1-candle ──
    if rng > 0:
        if _is_doji(body, rng):
            if lower >= rng * 0.55 and upper <= rng * 0.12:
                add("Dragonfly Doji", "BULLISH", 1)
            elif upper >= rng * 0.55 and lower <= rng * 0.12:
                add("Gravestone Doji", "BEARISH", 1)
            elif upper >= rng * 0.30 and lower >= rng * 0.30:
                add("Long-Legged Doji", "NEUTRAL", 1)
            else:
                add("Doji", "NEUTRAL", 1)
        elif lower >= body * 2.0 and upper <= body * 0.50 and lower >= rng * 0.48:
            add("Hammer", "BULLISH", 1)
            add("Hanging Man", "BEARISH", 1)
        elif upper >= body * 2.0 and lower <= body * 0.50 and upper >= rng * 0.48:
            add("Shooting Star", "BEARISH", 1)
            add("Inverted Hammer", "BULLISH", 1)
        elif _maru(body, rng, upper, lower):
            add("Bullish Marubozu" if bull else "Bearish Marubozu", "BULLISH" if bull else "BEARISH", 1)
            if bull and lower <= rng * 0.06:
                add("Bullish Belt Hold", "BULLISH", 1)
            if bear and upper <= rng * 0.06:
                add("Bearish Belt Hold", "BEARISH", 1)
        elif body <= rng * 0.32 and upper >= rng * 0.22 and lower >= rng * 0.22:
            add("Spinning Top", "NEUTRAL", 1)
            if upper >= rng * 0.35 and lower >= rng * 0.35:
                add("High Wave", "NEUTRAL", 1)

    seen = set()
    ordered = []
    for p in found:
        if p["name"] in seen:
            continue
        seen.add(p["name"])
        ordered.append(p)

    ordered.sort(key=lambda p: (-int(p["bars"]), p["name"]))
    return ordered[:6]


def scan_timeframe(df, timeframe, lookback=24, digits=2):
    empty = {
        "timeframe": timeframe,
        "zones": [],
        "current_zones": [],
        "candles": [],
        "candle_count": 0,
        "patterns": [],
        "count": 0,
        "status": "no_data",
    }
    if df is None or len(df) < 6:
        return empty

    zones = extract_zones(df, digits=digits)
    last_i = len(df) - 1
    _o, h, l, c = _ohlc(df, last_i)
    atr = max(_f(df.iloc[-1].get("atr"), 5.0), 1.0)
    current = _pick_current_zones(zones, c, h, l, atr)
    if not current:
        return {
            "timeframe": timeframe,
            "zones": zones,
            "current_zones": [],
            "candles": [],
            "candle_count": 0,
            "patterns": [],
            "count": 0,
            "status": "not_in_zone",
        }

    zone = current[0]
    visit = _zone_visit_indices(df, zone, last_i, max_lookback=lookback)
    if not visit:
        return {
            "timeframe": timeframe,
            "zones": current,
            "current_zones": current,
            "candles": [],
            "candle_count": 0,
            "patterns": [],
            "count": 0,
            "status": "not_in_zone",
        }

    number_of = {idx: n for n, idx in enumerate(visit, start=1)}
    candles = []
    for idx in visit:
        oo, hh, ll, cc = _ohlc(df, idx)
        candles.append({
            "n": number_of[idx],
            "time": _bar_time(df, idx),
            "open": round(oo, digits),
            "high": round(hh, digits),
            "low": round(ll, digits),
            "close": round(cc, digits),
            "forming": idx == last_i,
        })

    hits = []
    for i in visit:
        patterns = _detect_at(df, i)
        if not patterns:
            continue
        forming = (i == last_i)
        _oi, hi, li, ci = _ohlc(df, i)
        for pat in patterns:
            bars = int(pat["bars"])
            involved = list(range(i - bars + 1, i + 1))
            if any(j not in number_of for j in involved):
                continue
            name = pat["name"]
            bias = pat["bias"]
            kind = zone["kind"]
            if name == "Hammer" and kind in ("SUPPLY", "FVG_SUPPLY"):
                continue
            if name == "Hanging Man" and kind in ("DEMAND", "FVG_DEMAND"):
                continue
            if name == "Shooting Star" and kind in ("DEMAND", "FVG_DEMAND"):
                continue
            if name == "Inverted Hammer" and kind in ("SUPPLY", "FVG_SUPPLY"):
                continue

            nums = [number_of[j] for j in involved]
            if len(nums) == 1:
                combo = f"Candle #{nums[0]}"
            else:
                combo = "Candles " + " + ".join(f"#{n}" for n in nums)

            meta = _details(name)
            character = meta["character"]
            hits.append({
                "name": name,
                "bias": bias,
                "character": character,
                "type_line": f"{bias} {character}",
                "bars": bars,
                "candle_numbers": nums,
                "candle_combo": combo,
                "says": _instruction(bias, meta["character"], kind, zone["label"]),
                "meaning": meta["meaning"],
                "happens": meta["happens"],
                "timeframe": timeframe,
                "time": _bar_time(df, i),
                "close": round(ci, digits),
                "high": round(hi, digits),
                "low": round(li, digits),
                "zone_kind": kind,
                "zone_label": zone["label"],
                "zone_low": zone["low"],
                "zone_high": zone["high"],
                "forming": forming,
            })

    hits.reverse()
    hits.sort(key=lambda p: (0 if p["forming"] else 1, -int(p["bars"])))
    return {
        "timeframe": timeframe,
        "zones": current,
        "current_zones": current,
        "candles": candles,
        "candle_count": len(candles),
        "patterns": hits[:10],
        "count": min(len(hits), 10),
        "status": "in_zone",
    }


def _patterns_with_meta(df, i):
    out = []
    for p in _detect_at(df, i):
        meta = _details(p["name"])
        out.append({
            "name": p["name"],
            "bias": p.get("bias") or "NEUTRAL",
            "character": meta.get("character") or "INDECISION",
            "bars": p.get("bars") or 1,
        })
    return out


def tf_latest_reading(df, closed_only=True):
    """
    Directional patterns on this TF.
    Trade gates use the last CLOSED bar (forming candle flips every tick).
    MIXED if bullish and bearish both print on that same bar.
    """
    empty = {
        "bias": "NONE",
        "has_bull": False,
        "has_bear": False,
        "supports_buy": False,
        "supports_sell": False,
        "names": [],
        "summary": "NONE",
    }
    if df is None or len(df) < 5:
        return empty
    last_i = len(df) - 1
    idxs = (last_i - 1,) if closed_only else (last_i, last_i - 1)
    chosen = None
    for i in idxs:
        if i < 1:
            continue
        pats = _patterns_with_meta(df, i)
        directional = [p for p in pats if p["bias"] in ("BULLISH", "BEARISH")]
        if directional:
            chosen = directional
            break
    if not chosen:
        return empty

    has_bull = any(p["bias"] == "BULLISH" for p in chosen)
    has_bear = any(p["bias"] == "BEARISH" for p in chosen)
    if has_bull and has_bear:
        bias = "MIXED"
    elif has_bull:
        bias = "BULLISH"
    elif has_bear:
        bias = "BEARISH"
    else:
        bias = "NONE"

    # BUY: bullish reversal OR bullish continuation, and no bearish on the latest bar.
    # SELL: bearish reversal OR bearish continuation, and no bullish on the latest bar.
    names = [f"{p['name']} ({p['character']})" for p in chosen]
    return {
        "bias": bias,
        "has_bull": has_bull,
        "has_bear": has_bear,
        "supports_buy": has_bull and not has_bear,
        "supports_sell": has_bear and not has_bull,
        "names": names,
        "summary": " + ".join(names) if names else "NONE",
    }


def latest_bar_bias(df):
    """Bias of the latest candle pattern on this TF: BULLISH | BEARISH | MIXED | NONE."""
    return tf_latest_reading(df)["bias"]


def pattern_fill_veto(h1_df, m5_df, side, h1_closed_only=None, m5_closed_only=None):
    """
    Near-hit / fill veto only. Does NOT decide whether to place pending stops.
    BUY fill blocked if closed H1 or M5 is bearish.
    SELL fill blocked if closed H1 or M5 is bullish.
    NONE on a TF is OK (not a veto).
    """
    side_u = str(side).upper()
    want_buy = "BUY" in side_u
    h1_closed = bool(getattr(config, "H1_CONFIRM_CLOSED", True)) if h1_closed_only is None else bool(h1_closed_only)
    m5_closed = bool(getattr(config, "M5_CONFIRM_CLOSED", True)) if m5_closed_only is None else bool(m5_closed_only)
    h1 = tf_latest_reading(h1_df, closed_only=h1_closed)
    m5 = tf_latest_reading(m5_df, closed_only=m5_closed)
    if want_buy:
        blocked = h1["has_bear"] or m5["has_bear"]
        why = "bearish closed candle" if blocked else "ok"
    else:
        blocked = h1["has_bull"] or m5["has_bull"]
        why = "bullish closed candle" if blocked else "ok"
    return {
        "ok": not blocked,
        "h1": h1["bias"],
        "m5": m5["bias"],
        "reason": f"H1 {h1['summary']} | M5 {m5['summary']} ({why})",
    }


def h1_m5_pattern_gate(h1_df, m5_df, side, h1_closed_only=None, m5_closed_only=None):
    """
    Regular market entry confirmation.
    Trade decision uses M5 candle only.
    H1 candle is kept for UI visibility only (not used as trade gate).
    """
    side_u = str(side).upper()
    want_buy = "BUY" in side_u
    h1_closed = bool(getattr(config, "H1_CONFIRM_CLOSED", True)) if h1_closed_only is None else bool(h1_closed_only)
    m5_closed = bool(getattr(config, "M5_CONFIRM_CLOSED", True)) if m5_closed_only is None else bool(m5_closed_only)
    h1 = tf_latest_reading(h1_df, closed_only=h1_closed)
    m5 = tf_latest_reading(m5_df, closed_only=m5_closed)
    h1_mode = "closed" if h1_closed else "live"
    m5_mode = "closed" if m5_closed else "live"
    if want_buy:
        ok = m5["supports_buy"]
        need = f"M5 {m5_mode} candle bullish/continuation"
        if not ok:
            why = f"need {m5_mode} M5 bullish/continuation"
        else:
            why = "ok"
    else:
        ok = m5["supports_sell"]
        need = f"M5 {m5_mode} candle bearish/continuation"
        if not ok:
            why = f"need {m5_mode} M5 bearish/continuation"
        else:
            why = "ok"
    return {
        "ok": ok,
        "need": need,
        "h1": h1["bias"],
        "m5": m5["bias"],
        "h1_detail": h1["summary"],
        "m5_detail": m5["summary"],
        "h1_mode": h1_mode,
        "m5_mode": m5_mode,
        "reason": f"H1 {h1['summary']} | M5 {m5['summary']} ({why})",
    }


def scan_h1_and_m5(h1_df, m5_df, symbol="XAUUSD"):
    empty = {
        "note": "Display only — no trade decision",
        "h1": {"timeframe": "H1", "zones": [], "current_zones": [], "patterns": [], "count": 0, "status": "no_data"},
        "m5": {"timeframe": "M5", "zones": [], "current_zones": [], "patterns": [], "count": 0, "status": "no_data"},
    }
    try:
        is_gold = "XAU" in str(symbol).upper()
        digits = 2 if is_gold else 5
        return {
            "note": "Display only — no trade decision",
            "h1": scan_timeframe(h1_df, "H1", lookback=20, digits=digits),
            "m5": scan_timeframe(m5_df, "M5", lookback=32, digits=digits),
        }
    except Exception:
        return empty
