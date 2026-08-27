import pandas as pd
import numpy as np
import ta
import logging
import config
import warnings
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)
from candlestick_patterns import h1_m5_pattern_gate, tf_latest_reading


class Strategy:
    @staticmethod
    def calculate_dynamic_rrr(m1_df, m15_df, m5_df, signal):
        """
        100% Fully Dynamic Market-Structure-Based Risk-to-Reward Ratio (RRR):
        Calculates exact RRR dynamically based on:
        1. Distance to next Key SMC Liquidity Target (Key Resistance / BSL for BUY, Support / SSL for SELL)
        2. Average True Range (ATR) & Volatility Expansion Ratio
        3. Multi-timeframe trend Alignment Ratio (M1 + M15 + M5 Confluence)
        """
        if m1_df is None or m15_df is None or m5_df is None or len(m5_df) == 0:
            return 2.0

        last_m5 = m5_df.iloc[-1]
        last_m15 = m15_df.iloc[-1]
        last_h1 = m1_df.iloc[-1]

        close_p = float(last_m5['close'])
        atr_val = float(last_m5['atr']) if 'atr' in last_m5 and pd.notna(last_m5['atr']) and last_m5['atr'] > 0 else (close_p * 0.002)

        # Calculate SL Distance based on 1.2x ATR
        sl_dist = atr_val * 1.2

        # 1. Structure Target Distance (Distance to Major Swing High / Resistance for BUY, Swing Low / Support for SELL)
        if signal == 'BUY':
            target_p = float(last_m15['last_high']) if pd.notna(last_m15['last_high']) and last_m15['last_high'] > close_p else (close_p + (sl_dist * 2.5))
            target_dist = max(sl_dist * 1.2, target_p - close_p)
        else:
            target_p = float(last_m15['last_low']) if pd.notna(last_m15['last_low']) and last_m15['last_low'] < close_p else (close_p - (sl_dist * 2.5))
            target_dist = max(sl_dist * 1.2, close_p - target_p)

        # 2. Raw Structure RRR = Distance to Target / Risk SL Distance
        raw_rrr = target_dist / sl_dist if sl_dist > 0 else 2.0

        # 3. Volatility & Multi-Timeframe Alignment Adjustments
        h1_lh = last_h1['last_high'] if pd.notna(last_h1['last_high']) and last_h1['last_high'] is not None else last_h1['close']
        h1_bull = last_h1['close'] >= h1_lh
        trend_alignment = (signal == 'BUY' and h1_bull) or (signal == 'SELL' and not h1_bull)

        if trend_alignment:
            raw_rrr *= 1.25  # Expand target RRR during strong trend alignment
        if bool(last_m5.get('vol_spike', False)):
            raw_rrr *= 1.15  # Expand target RRR during high volume expansion

        # Clamp RRR to a realistic & safe range (Min 1.20 to Max 4.50)
        final_rrr = round(max(1.20, min(4.50, raw_rrr)), 2)
        logging.info(f"[DYNAMIC RRR ALGORITHM] Signal: {signal} | Target Dist: {target_dist:.2f} | SL Dist: {sl_dist:.2f} | Dynamic Computed RRR = 1:{final_rrr}")
        return final_rrr

    @staticmethod
    def calculate_manual_smc_sl_tp(m1_df, m5_df, signal, entry_price):
        """
        Calculate M1 Timeframe SMC Structure SL and TP for Manual Trades.
        - BUY: SL below M1 SMC Demand Zone / M1 Support OB / M1 Swing Low; TP at M1 SMC Supply Zone / M1 Resistance OB.
        - SELL: SL above M1 SMC Supply Zone / M1 Resistance OB / M1 Swing High; TP at M1 SMC Demand Zone / M1 Support OB.
        """
        entry_price = float(entry_price)
        if entry_price <= 0:
            return 0.0, 0.0

        last_h1 = m1_df.iloc[-1] if (m1_df is not None and len(m1_df) > 0) else {}
        last_m5 = m5_df.iloc[-1] if (m5_df is not None and len(m5_df) > 0) else last_h1

        def _f(row, key, default=0.0):
            try:
                v = row.get(key, default) if hasattr(row, 'get') else default
                return float(v) if pd.notna(v) else float(default)
            except Exception:
                return float(default)

        h1_atr = _f(last_h1, 'atr', _f(last_m5, 'atr', 3.0) * 2.5)
        if h1_atr < 1.0:
            h1_atr = 5.0

        is_gold = "XAU" in str(config.SYMBOL).upper()
        digits = 2 if is_gold else 5
        sl_buffer = max(h1_atr * 0.30, 2.0 if is_gold else h1_atr * 0.30)

        # True M1 SMC Demand / Support Structure Candidates (below price)
        h1_demands = []
        for key in ['mtf_sup_zone', 'last_low', 'support', 'valid_low']:
            v = _f(last_h1, key, 0.0)
            if 0 < v < (entry_price - sl_buffer):
                h1_demands.append(v)

        if 'swing_low' in m1_df.columns:
            for v in m1_df['swing_low'].dropna().tail(30).tolist():
                try:
                    val = float(v)
                    if 0 < val < (entry_price - sl_buffer):
                        h1_demands.append(val)
                except Exception:
                    pass

        # True M1 SMC Supply / Resistance Structure Candidates (above price)
        h1_supplies = []
        for key in ['mtf_res_zone', 'last_high', 'resistance', 'valid_high']:
            v = _f(last_h1, key, 0.0)
            if v > (entry_price + sl_buffer):
                h1_supplies.append(v)

        if 'swing_high' in m1_df.columns:
            for v in m1_df['swing_high'].dropna().tail(30).tolist():
                try:
                    val = float(v)
                    if val > (entry_price + sl_buffer):
                        h1_supplies.append(val)
                except Exception:
                    pass

        if signal == 'BUY':
            if h1_demands:
                h1_demand_zone = max(h1_demands)
                sl_price = h1_demand_zone - sl_buffer
            else:
                sl_price = entry_price - (h1_atr * 2.0)

            risk = max(10.0 if is_gold else 0.0010, entry_price - sl_price)
            min_tp_dist = max(risk * 1.5, 15.0 if is_gold else risk * 1.5)

            valid_supplies = [s for s in h1_supplies if s >= entry_price + min_tp_dist]
            if valid_supplies:
                tp_price = min(valid_supplies)
            else:
                tp_price = entry_price + min_tp_dist

        else:  # SELL
            if h1_supplies:
                h1_supply_zone = min(h1_supplies)
                sl_price = h1_supply_zone + sl_buffer
            else:
                sl_price = entry_price + (h1_atr * 2.0)

            risk = max(10.0 if is_gold else 0.0010, sl_price - entry_price)
            min_tp_dist = max(risk * 1.5, 15.0 if is_gold else risk * 1.5)

            valid_demands = [d for d in h1_demands if d <= entry_price - min_tp_dist]
            if valid_demands:
                tp_price = max(valid_demands)
            else:
                tp_price = entry_price - min_tp_dist

        return round(sl_price, digits), round(tp_price, digits)

    @staticmethod
    def calculate_valid_zone_sl_tp(m1_df, m5_df, signal, entry_price):
        """
        Regular market entry SL/TP:
        - SL OUTSIDE nearest valid M1 demand (BUY) / supply (SELL) zone
        - TP AT valid opposite SMC / structure zones (not before zone)
        """
        entry_price = float(entry_price)
        last_m5 = m5_df.iloc[-1] if (m5_df is not None and len(m5_df) > 0) else {}
        last_h1 = m1_df.iloc[-1] if (m1_df is not None and len(m1_df) > 0) else last_m5

        def _f(row, key, default):
            try:
                v = row.get(key, default) if hasattr(row, 'get') else default
                return float(v) if pd.notna(v) else float(default)
            except Exception:
                return float(default)

        m5_atr = _f(last_m5, 'atr', 5.0)
        h1_atr = _f(last_h1, 'atr', m5_atr * 3.0)
        if h1_atr < m5_atr * 1.5:
            h1_atr = max(h1_atr, m5_atr * 3.0)

        is_gold = "XAU" in str(config.SYMBOL).upper()
        min_risk = max(h1_atr * 1.2, 20.0 if is_gold else h1_atr * 1.2)
        max_risk = max(h1_atr * 4.0, 80.0 if is_gold else h1_atr * 4.0)
        sl_buf = h1_atr * 0.60

        def _pick_tps(named_levels, ascending=True, min_gap_mult=0.30):
            """Pick up to 3 TPs exactly AT valid zones."""
            levels = [(n, float(z)) for n, z in named_levels if z and float(z) > 0]
            levels.sort(key=lambda x: x[1], reverse=not ascending)
            picked, notes = [], []
            for n, z in levels:
                if ascending and z <= entry_price + h1_atr * 0.30:
                    continue
                if (not ascending) and z >= entry_price - h1_atr * 0.30:
                    continue
                if picked and abs(z - picked[-1]) < h1_atr * min_gap_mult:
                    continue
                picked.append(z)
                notes.append(f"@ {n}")
                if len(picked) >= 3:
                    break
            return picked, notes

        if signal == 'BUY':
            candidates = [
                ('M1 Swing Low', _f(last_h1, 'last_low', entry_price - min_risk)),
                ('M1 MTF Demand', _f(last_h1, 'mtf_sup_zone', entry_price - min_risk)),
                ('M1 Support', _f(last_h1, 'support', entry_price - min_risk)),
                ('Gann 90 Support', _f(last_h1, 'gann_sq9_90_dn', entry_price - min_risk)),
                ('M5 Support', _f(last_m5, 'support', entry_price - min_risk)),
            ]
            zones = [(n, z) for n, z in candidates if (entry_price - max_risk) <= z < (entry_price - h1_atr * 0.35)]
            zones.sort(key=lambda x: x[1], reverse=True)
            if zones:
                zone_name, zone_lvl = zones[0]
                sl_price = zone_lvl - sl_buf
            else:
                zone_name, zone_lvl = ('M1 ATR', entry_price - min_risk)
                sl_price = entry_price - min_risk

            risk = entry_price - sl_price
            if risk < min_risk:
                sl_price = entry_price - min_risk
                risk = min_risk
            elif risk > max_risk:
                sl_price = entry_price - max_risk
                risk = max_risk

            # TP AT opposite supply / SMC zones (priority order)
            named = [
                ('M1 Swing High', _f(last_h1, 'last_high', 0)),
                ('M1 MTF Supply', _f(last_h1, 'mtf_res_zone', 0)),
                ('M1 Resistance OB', _f(last_h1, 'resistance', 0)),
                ('Fib 1.618 Ext', _f(last_h1, 'fib_1618', 0)),
                ('Fib 1000', _f(last_h1, 'fib_1000', 0)),
                ('Gann 90', _f(last_h1, 'gann_sq9_90_up', 0)),
                ('Gann 180', _f(last_h1, 'gann_sq9_180_up', 0)),
                ('Gann 360', _f(last_h1, 'gann_sq9_360_up', 0)),
                ('Fibo R1', _f(last_m5, 'fibo_r1', 0)),
                ('Fibo R2', _f(last_m5, 'fibo_r2', 0)),
                ('Fibo R4', _f(last_m5, 'fibo_r4', 0)),
                ('Pivot R1', _f(last_m5, 'pivot_r1', 0)),
                ('Pivot R2', _f(last_m5, 'pivot_r2', 0)),
                ('M5 Resistance', _f(last_m5, 'resistance', 0)),
            ]
            tps, notes = _pick_tps(named, ascending=True)
            while len(tps) < 3:
                z = entry_price + risk * (1.5 + 0.5 * len(tps))
                if tps and z <= tps[-1] + h1_atr * 0.25:
                    z = tps[-1] + h1_atr * 0.50
                tps.append(z)
                notes.append('@ RRR structural')
            tp1, tp2, tp3 = tps[0], tps[1], tps[2]
            tp2 = max(tp2, tp1 + h1_atr * 0.25)
            tp3 = max(tp3, tp2 + h1_atr * 0.25)

            logging.info(
                f"[REGULAR SL/TP BUY] Entry={entry_price:.2f} | SL OUTSIDE {zone_name}@{zone_lvl:.2f} -> SL={sl_price:.2f} "
                f"(risk={risk:.1f}) | TP1={tp1:.2f} ({notes[0]}) TP2={tp2:.2f} ({notes[1]}) TP3={tp3:.2f} ({notes[2]})"
            )
        else:
            candidates = [
                ('M1 Swing High', _f(last_h1, 'last_high', entry_price + min_risk)),
                ('M1 MTF Supply', _f(last_h1, 'mtf_res_zone', entry_price + min_risk)),
                ('M1 Resistance', _f(last_h1, 'resistance', entry_price + min_risk)),
                ('Gann 90 Resistance', _f(last_h1, 'gann_sq9_90_up', entry_price + min_risk)),
                ('M5 Resistance', _f(last_m5, 'resistance', entry_price + min_risk)),
            ]
            zones = [(n, z) for n, z in candidates if (entry_price + h1_atr * 0.35) < z <= (entry_price + max_risk)]
            zones.sort(key=lambda x: x[1])
            if zones:
                zone_name, zone_lvl = zones[0]
                sl_price = zone_lvl + sl_buf
            else:
                zone_name, zone_lvl = ('M1 ATR', entry_price + min_risk)
                sl_price = entry_price + min_risk

            risk = sl_price - entry_price
            if risk < min_risk:
                sl_price = entry_price + min_risk
                risk = min_risk
            elif risk > max_risk:
                sl_price = entry_price + max_risk
                risk = max_risk

            named = [
                ('M1 Swing Low', _f(last_h1, 'last_low', 0)),
                ('M1 MTF Demand', _f(last_h1, 'mtf_sup_zone', 0)),
                ('M1 Support OB', _f(last_h1, 'support', 0)),
                ('Fib 000', _f(last_h1, 'fib_000', 0)),
                ('Gann 90', _f(last_h1, 'gann_sq9_90_dn', 0)),
                ('Gann 180', _f(last_h1, 'gann_sq9_180_dn', 0)),
                ('Gann 360', _f(last_h1, 'gann_sq9_360_dn', 0)),
                ('Fibo S1', _f(last_m5, 'fibo_s1', 0)),
                ('Fibo S2', _f(last_m5, 'fibo_s2', 0)),
                ('Fibo S4', _f(last_m5, 'fibo_s4', 0)),
                ('Pivot S1', _f(last_m5, 'pivot_s1', 0)),
                ('Pivot S2', _f(last_m5, 'pivot_s2', 0)),
                ('M5 Support', _f(last_m5, 'support', 0)),
            ]
            tps, notes = _pick_tps(named, ascending=False)
            while len(tps) < 3:
                z = entry_price - risk * (1.5 + 0.5 * len(tps))
                if tps and z >= tps[-1] - h1_atr * 0.25:
                    z = tps[-1] - h1_atr * 0.50
                tps.append(z)
                notes.append('@ RRR structural')
            tp1, tp2, tp3 = tps[0], tps[1], tps[2]
            tp2 = min(tp2, tp1 - h1_atr * 0.25)
            tp3 = min(tp3, tp2 - h1_atr * 0.25)

            logging.info(
                f"[REGULAR SL/TP SELL] Entry={entry_price:.2f} | SL OUTSIDE {zone_name}@{zone_lvl:.2f} -> SL={sl_price:.2f} "
                f"(risk={risk:.1f}) | TP1={tp1:.2f} ({notes[0]}) TP2={tp2:.2f} ({notes[1]}) TP3={tp3:.2f} ({notes[2]})"
            )

        digits = 2 if is_gold else 5
        return round(sl_price, digits), round(tp1, digits), round(tp2, digits), round(tp3, digits)

    @staticmethod
    def calculate_pending_triggers(m1_df, m5_df, curr_ask, curr_bid, m15_df=None):
        """
        Pending BUY_STOP / SELL_STOP prices.

        Uses the CURRENT session box (M5/M15 + last 12 M1 bars), not the 50-bar
        M1 extreme (that was ~$160 wide). Last 5-bar fractal is also ignored
        when it sits inside noise.

        Band vs live price (gold): about $14–$38 each side.
        """
        last_m5 = m5_df.iloc[-1] if (m5_df is not None and len(m5_df) > 0) else {}
        last_h1 = m1_df.iloc[-1] if (m1_df is not None and len(m1_df) > 0) else last_m5
        last_m15 = m15_df.iloc[-1] if (m15_df is not None and len(m15_df) > 0) else last_m5

        def _f(row, key, default):
            try:
                v = row.get(key, default) if hasattr(row, 'get') else default
                return float(v) if pd.notna(v) else float(default)
            except Exception:
                return float(default)

        def _tail_hl(df, n):
            if df is None or len(df) == 0 or 'high' not in getattr(df, 'columns', []):
                return None, None
            try:
                closed = df.iloc[:-1] if len(df) > 1 else df
                return float(closed['high'].tail(n).max()), float(closed['low'].tail(n).min())
            except Exception:
                return None, None

        is_gold = "XAU" in str(config.SYMBOL).upper()
        digits = 2 if is_gold else 5
        curr_ask = float(curr_ask)
        curr_bid = float(curr_bid)

        m5_atr = max(_f(last_m5, 'atr', 5.0), 0.50)
        h1_atr = _f(last_h1, 'atr', m5_atr * 3.0)
        if h1_atr < m5_atr * 1.5:
            h1_atr = max(h1_atr, m5_atr * 3.0)

        min_from_price = max(h1_atr * 0.50, 14.0 if is_gold else m5_atr * 2.0)
        max_from_price = max(h1_atr * 1.60, 38.0 if is_gold else h1_atr * 1.60)
        if max_from_price < min_from_price + 8.0:
            max_from_price = min_from_price + 8.0
        min_span = max(h1_atr * 1.20, 28.0 if is_gold else h1_atr * 1.20)
        buf = max(h1_atr * 0.35, 10.0 if is_gold else h1_atr * 0.35)

        h1_high = _f(last_h1, 'last_high', curr_ask + min_from_price)
        h1_low = _f(last_h1, 'last_low', curr_bid - min_from_price)
        mtf_res = _f(last_h1, 'mtf_res_zone', h1_high)
        mtf_sup = _f(last_h1, 'mtf_sup_zone', h1_low)

        m5_hi, m5_lo = _tail_hl(m5_df, 40)
        m15_hi, m15_lo = _tail_hl(m15_df, 20)
        h1_hi, h1_lo = _tail_hl(m1_df, 12)
        m5_rmax = _f(last_m5, 'range_max', m5_hi or curr_ask)
        m5_rmin = _f(last_m5, 'range_min', m5_lo or curr_bid)

        # Current box = nearest recent highs/lows (ignore stale 50-bar M1 extremes)
        near_highs = [v for v in (m5_hi, m15_hi, h1_hi, m5_rmax, h1_high, mtf_res) if v and v > curr_ask]
        near_lows = [v for v in (m5_lo, m15_lo, h1_lo, m5_rmin, h1_low, mtf_sup) if v and 0 < v < curr_bid]
        range_top = min(near_highs) if near_highs else curr_ask + min_from_price
        range_bot = max(near_lows) if near_lows else curr_bid - min_from_price

        try:
            is_ranging = bool(last_m5.get('is_consolidation', False))
        except Exception:
            is_ranging = False

        buy_floor = curr_ask + min_from_price
        buy_ceil = curr_ask + max_from_price
        sell_ceil = curr_bid - min_from_price
        sell_floor = curr_bid - max_from_price

        pending_mode = getattr(config, 'PENDING_MODE', 'HYBRID_LIMIT_BREAKOUT').upper()

        # 1. Breakout Trigger Prices (Buy Stop / Sell Stop)
        breakout_buy_cands = [v for v in (h1_high, mtf_res, range_top) if buy_floor <= v <= buy_ceil]
        breakout_sell_cands = [v for v in (h1_low, mtf_sup, range_bot) if sell_floor <= v <= sell_ceil]
        breakout_buy_trig = min(breakout_buy_cands) if breakout_buy_cands else (curr_ask + min_from_price)
        breakout_sell_trig = max(breakout_sell_cands) if breakout_sell_cands else (curr_bid - min_from_price)
        if is_ranging:
            breakout_buy_trig = range_top + buf
            breakout_sell_trig = range_bot - buf

        breakout_buy_trig = min(max(float(breakout_buy_trig), buy_floor), buy_ceil)
        breakout_sell_trig = max(min(float(breakout_sell_trig), sell_ceil), sell_floor)
        if (breakout_buy_trig - breakout_sell_trig) < min_span:
            mid = (curr_ask + curr_bid) / 2.0
            half = min_span / 2.0
            breakout_buy_trig = min(max(mid + half, buy_floor), buy_ceil)
            breakout_sell_trig = max(min(mid - half, sell_ceil), sell_floor)

        # 2. SMC Limit Pullback Prices (Buy Limit / Sell Limit at Support/Demand & Resistance/Supply OB)
        discount_buy = mtf_sup if (0 < mtf_sup < curr_ask) else (range_bot if (0 < range_bot < curr_ask) else (curr_ask - min_from_price))
        discount_sell = mtf_res if (mtf_res > curr_bid) else (range_top if (range_top > curr_bid) else (curr_bid + min_from_price))
        limit_buy_trig = min(discount_buy, curr_ask - (buf if buf > 0.5 else 1.0))
        limit_sell_trig = max(discount_sell, curr_bid + (buf if buf > 0.5 else 1.0))

        if is_ranging:
            buy_trig = breakout_buy_trig
            sell_trig = breakout_sell_trig
            mode = "RANGE BREAKOUT"
        else:
            buy_trig = limit_buy_trig
            sell_trig = limit_sell_trig
            mode = "HYBRID: 1 BREAKOUT STOP + 3 LIMIT PULLBACK"

        return {
            "buy_trig": round(buy_trig, digits),
            "sell_trig": round(sell_trig, digits),
            "breakout_buy_trig": round(breakout_buy_trig, digits),
            "breakout_sell_trig": round(breakout_sell_trig, digits),
            "limit_buy_trig": round(limit_buy_trig, digits),
            "limit_sell_trig": round(limit_sell_trig, digits),
            "min_from_price": float(min_from_price),
            "min_span": float(min_span),
            "mode": mode,
            "is_ranging": is_ranging,
            "pending_mode": pending_mode
        }

    @staticmethod
    def calculate_pending_zone_sl_tp(m1_df, m5_df, signal, entry_price, opposite_entry=None):
        """
        Pending breakout SL/TP:
        - SL = opposite pending ENTRY (BuyStop SL = SellStop entry, SellStop SL = BuyStop entry)
        - TP = AT valid SMC / structure zones (swing, OB, MTF, fib, gann, pivot)
        """
        entry_price = float(entry_price)
        last_m5 = m5_df.iloc[-1] if (m5_df is not None and len(m5_df) > 0) else {}
        last_h1 = m1_df.iloc[-1] if (m1_df is not None and len(m1_df) > 0) else last_m5

        def _f(row, key, default):
            try:
                v = row.get(key, default) if hasattr(row, 'get') else default
                return float(v) if pd.notna(v) else float(default)
            except Exception:
                return float(default)

        m5_atr = _f(last_m5, 'atr', 5.0)
        h1_atr = _f(last_h1, 'atr', m5_atr * 3.0)
        if h1_atr < m5_atr * 1.5:
            h1_atr = max(h1_atr, m5_atr * 3.0)

        is_gold = "XAU" in str(config.SYMBOL).upper()
        min_risk = max(h1_atr * 1.5, 25.0 if is_gold else h1_atr * 1.5)

        def _hist_levels(dfs, cols):
            out = []
            for df in dfs:
                if df is None or len(df) == 0:
                    continue
                for col in cols:
                    if col not in df.columns:
                        continue
                    for v in df[col].dropna().tail(80).tolist():
                        try:
                            out.append(float(v))
                        except Exception:
                            pass
            return out

        # Priority: real SMC first, then fib/gann/pivot, hist swings, then measured-move only as filler
        SMC_PRIORITY = {
            'M1 Swing High': 0, 'M1 Swing Low': 0,
            'M1 MTF Supply': 1, 'M1 MTF Demand': 1,
            'M1 Resistance OB': 2, 'M1 Support OB': 2,
            'M5 Resistance': 3, 'M5 Support': 3,
            'Hist Supply': 4, 'Hist Demand': 4,
            'Fib 1.618 Ext': 5, 'Fib 1000': 5, 'Fib 000': 5,
            'Fibo R1': 6, 'Fibo R2': 6, 'Fibo R4': 6,
            'Fibo S1': 6, 'Fibo S2': 6, 'Fibo S4': 6,
            'Gann 90': 7, 'Gann 180': 7, 'Gann 360': 7,
            'Pivot R1': 8, 'Pivot R2': 8, 'Pivot S1': 8, 'Pivot S2': 8,
            'Measured Move 1.0': 9, 'Measured Move 1.5': 10, 'Measured Move 2.0': 11,
        }

        def _rank(name):
            return SMC_PRIORITY.get(name, 20)

        if signal == 'BUY':
            sl_price, _ = Strategy.calculate_manual_smc_sl_tp(m1_df, m5_df, 'BUY', entry_price)
            zone_name = 'M1 Valid Swing Low (Unified Structure)'
            if opposite_entry is not None and float(opposite_entry) < entry_price:
                sl_price = float(opposite_entry)
                zone_name = 'Opposite SellStop entry (mirror SL)'

            risk = entry_price - sl_price
            if risk < min_risk:
                sl_price = entry_price - min_risk
                risk = min_risk
                zone_name = 'Min M1 ATR risk floor'

            named = [
                ('M1 Resistance OB', _f(last_h1, 'resistance', 0)),
                ('M1 Swing High', _f(last_h1, 'last_high', 0)),
                ('M1 MTF Supply', _f(last_h1, 'mtf_res_zone', 0)),
                ('M5 Resistance', _f(last_m5, 'resistance', 0)),
                ('Gann 90', _f(last_h1, 'gann_sq9_90_up', 0)),
                ('Gann 180', _f(last_h1, 'gann_sq9_180_up', 0)),
                ('Gann 360', _f(last_h1, 'gann_sq9_360_up', 0)),
                ('Fibo R1', _f(last_m5, 'fibo_r1', 0)),
                ('Fibo R2', _f(last_m5, 'fibo_r2', 0)),
                ('Fibo R4', _f(last_m5, 'fibo_r4', 0)),
                ('Pivot R1', _f(last_m5, 'pivot_r1', 0)),
                ('Pivot R2', _f(last_m5, 'pivot_r2', 0)),
                ('Fib 1.618 Ext', _f(last_h1, 'fib_1618', 0)),
                ('Fib 1000', _f(last_h1, 'fib_1000', 0)),
            ]
            for lvl in _hist_levels([m1_df, m5_df], ['swing_high', 'mtf_res_zone', 'resistance', 'last_high']):
                named.append(('Hist Supply', lvl))

            rmax = _f(last_m5, 'range_max', _f(last_h1, 'range_max', entry_price))
            rmin = _f(last_m5, 'range_min', _f(last_h1, 'range_min', entry_price - h1_atr * 2))
            range_h = max(abs(rmax - rmin), h1_atr * 1.5, risk * 0.35)
            named.extend([
                ('Measured Move 1.0', entry_price + range_h * 1.00),
                ('Measured Move 1.5', entry_price + range_h * 1.50),
                ('Measured Move 2.0', entry_price + range_h * 2.00),
            ])

            min_gap = max(risk * 1.0, 15.0 if is_gold else risk * 1.0)
            above = [(n, round(t, 2)) for n, t in named if t >= entry_price + min_gap]
            # Prefer SMC/valid zones: sort by price, but when clustering prefer higher priority name
            above.sort(key=lambda x: (x[1], _rank(x[0])))
            dedup = []
            for n, t in above:
                if not dedup or abs(t - dedup[-1][1]) >= max(h1_atr * 1.0, 10.0 if is_gold else h1_atr * 1.0):
                    dedup.append((n, t))
                elif _rank(n) < _rank(dedup[-1][0]):
                    dedup[-1] = (n, t)
            above = dedup

            buy_tps, buy_notes = [], []
            for n, z in above:
                if buy_tps and z <= buy_tps[-1] + max(risk * 0.5, 10.0 if is_gold else risk * 0.5):
                    continue
                buy_tps.append(z)
                buy_notes.append(f"zone {n}@{z:.2f}")
                if len(buy_tps) >= 2:
                    break

            while len(buy_tps) < 2:
                z = entry_price + max(risk * (1.0 + 0.5 * len(buy_tps)), (15.0 if is_gold else 15.0) * (len(buy_tps) + 1))
                if buy_tps:
                    z = max(z, buy_tps[-1] + max(risk * 0.5, 15.0 if is_gold else risk * 0.5))
                buy_tps.append(z)
                buy_notes.append(f"zone Measured@{z:.2f}")

            tp1, tp2 = buy_tps[0], buy_tps[1]
            tp1_note, tp2_note = buy_notes[0], buy_notes[1]

            # TP2 = Strictly M1 SMC Structure Zone (M1 Swing High, M1 Res, M1 MTF) with M1 ATR Minimum Gap ($15.0+ points)
            h1_mtf_res = _f(last_h1, 'mtf_res_zone', entry_price)
            h1_mtf_sup = _f(last_h1, 'mtf_sup_zone', entry_price - h1_atr * 3)
            mtf_span = max(abs(h1_mtf_res - h1_mtf_sup), h1_atr * 3.0, range_h, risk * 1.2)

            mtf2_named = [
                ('M1 MTF Res', h1_mtf_res),
                ('M1 Resistance OB', _f(last_h1, 'resistance', 0)),
                ('M1 Swing High', _f(last_h1, 'last_high', 0)),
                ('M1 Next Zone', entry_price + mtf_span),
            ]
            if m1_df is not None and len(m1_df) > 0:
                if 'swing_high' in m1_df.columns:
                    for v in m1_df['swing_high'].dropna().tail(40).tolist():
                        mtf2_named.append(('M1 Hist Swing High', float(v)))
                for lvl in _hist_levels([m1_df], ['mtf_res_zone', 'resistance', 'last_high']):
                    mtf2_named.append(('M1 Hist Res', lvl))

            min_tp2 = max(tp1 + max(h1_atr * 1.5, 15.0 if is_gold else 0.0015), entry_price + mtf_span * 0.75)
            mtf2_ok = [(n, round(z, 2)) for n, z in mtf2_named if z >= min_tp2]
            mtf2_ok.sort(key=lambda x: x[1])
            if mtf2_ok:
                tp2_n, tp2 = mtf2_ok[0]
                tp2_note = f"{tp2_n}@{tp2:.2f}"
            else:
                tp2 = round(min_tp2, 2)
                tp2_note = f"M1 Measured Res@{tp2:.2f}"
            tp2 = max(float(tp2), min_tp2)

            # TP3 = FAR next major reversal zone (Fib/Gann/Hist) — live-updated runner
            import math
            sq = math.sqrt(max(entry_price, 1.0))
            hist_highs = _hist_levels([m1_df], ['swing_high', 'resistance', 'last_high'])
            major_high = max(hist_highs) if hist_highs else 0.0
            far_named = [
                ('Fib 2.618 Ext', rmin + range_h * 2.618),
                ('Fib 3.618 Ext', rmin + range_h * 3.618),
                ('Gann 360', (sq + 1.0) ** 2),
                ('Gann 720', (sq + 2.0) ** 2),
                ('Hist Major High', major_high),
                ('Measured Move 2.5', entry_price + range_h * 2.50),
                ('Measured Move 3.0', entry_price + range_h * 3.00),
                ('Measured Move 4.0', entry_price + range_h * 4.00),
            ]
            max_far = entry_price + max(range_h * 5.0, h1_atr * 12.0, risk * 4.0)
            far_ok = [(n, round(z, 2)) for n, z in far_named if (tp2 + h1_atr * 1.0) < z <= max_far]
            struct = [(n, z) for n, z in far_ok if n.startswith(('Fib', 'Gann', 'Hist'))]
            pick_pool = struct if struct else far_ok
            if pick_pool:
                pick_pool.sort(key=lambda x: x[1], reverse=True)
                tp3_n, tp3 = pick_pool[0]
            else:
                tp3 = max(tp2 + h1_atr * 3.0, entry_price + range_h * 3.0, entry_price + risk * 3.0)
                tp3_n = 'Forced Far Reversal'
            tp3 = max(float(tp3), tp2 + h1_atr * 1.5)
            tp3_note = f"reversal {tp3_n}@{tp3:.2f}"

            logging.info(
                f"[PENDING SL/TP BUY] Entry={entry_price:.2f} | SL={sl_price:.2f} (= {zone_name}) "
                f"(risk={risk:.1f}) | TP1={tp1:.2f} ({tp1_note}) TP2={tp2:.2f} ({tp2_note}) TP3={tp3:.2f} ({tp3_note})"
            )

        else:  # SELL
            sl_price, _ = Strategy.calculate_manual_smc_sl_tp(m1_df, m5_df, 'SELL', entry_price)
            zone_name = 'M1 Valid Swing High (Unified Structure)'
            if opposite_entry is not None and float(opposite_entry) > entry_price:
                sl_price = float(opposite_entry)
                zone_name = 'Opposite BuyStop entry (mirror SL)'

            risk = sl_price - entry_price
            if risk < min_risk:
                sl_price = entry_price + min_risk
                risk = min_risk
                zone_name = 'Min M1 ATR risk floor'

            named = [
                ('M1 Support OB', _f(last_h1, 'support', 0)),
                ('M1 Swing Low', _f(last_h1, 'last_low', 0)),
                ('M1 MTF Demand', _f(last_h1, 'mtf_sup_zone', 0)),
                ('M5 Support', _f(last_m5, 'support', 0)),
                ('Gann 90', _f(last_h1, 'gann_sq9_90_dn', 0)),
                ('Gann 180', _f(last_h1, 'gann_sq9_180_dn', 0)),
                ('Gann 360', _f(last_h1, 'gann_sq9_360_dn', 0)),
                ('Fibo S1', _f(last_m5, 'fibo_s1', 0)),
                ('Fibo S2', _f(last_m5, 'fibo_s2', 0)),
                ('Fibo S4', _f(last_m5, 'fibo_s4', 0)),
                ('Pivot S1', _f(last_m5, 'pivot_s1', 0)),
                ('Pivot S2', _f(last_m5, 'pivot_s2', 0)),
                ('Fib 000', _f(last_h1, 'fib_000', 0)),
            ]
            for lvl in _hist_levels([m1_df, m5_df], ['swing_low', 'mtf_sup_zone', 'support', 'last_low']):
                named.append(('Hist Demand', lvl))

            rmax = _f(last_m5, 'range_max', _f(last_h1, 'range_max', entry_price + h1_atr * 2))
            rmin = _f(last_m5, 'range_min', _f(last_h1, 'range_min', entry_price))
            range_h = max(abs(rmax - rmin), h1_atr * 1.5, risk * 0.35)
            named.extend([
                ('Measured Move 1.0', entry_price - range_h * 1.00),
                ('Measured Move 1.5', entry_price - range_h * 1.50),
                ('Measured Move 2.0', entry_price - range_h * 2.00),
            ])
            min_gap = max(risk * 1.0, 15.0 if is_gold else risk * 1.0)
            below = [(n, round(t, 2)) for n, t in named if 0 < t <= entry_price - min_gap]
            below.sort(key=lambda x: (-x[1], _rank(x[0])))
            dedup = []
            for n, t in below:
                if not dedup or abs(t - dedup[-1][1]) >= max(h1_atr * 1.0, 10.0 if is_gold else h1_atr * 1.0):
                    dedup.append((n, t))
                elif _rank(n) < _rank(dedup[-1][0]):
                    dedup[-1] = (n, t)
            below = dedup

            sell_tps, sell_notes = [], []
            for n, z in below:
                if sell_tps and z >= sell_tps[-1] - max(risk * 0.5, 10.0 if is_gold else risk * 0.5):
                    continue
                sell_tps.append(z)
                sell_notes.append(f"zone {n}@{z:.2f}")
                if len(sell_tps) >= 2:
                    break

            while len(sell_tps) < 2:
                z = entry_price - max(risk * (1.0 + 0.5 * len(sell_tps)), (15.0 if is_gold else 15.0) * (len(sell_tps) + 1))
                if sell_tps:
                    z = min(z, sell_tps[-1] - max(risk * 0.5, 15.0 if is_gold else risk * 0.5))
                sell_tps.append(z)
                sell_notes.append(f"zone Measured@{z:.2f}")

            tp1, tp2 = sell_tps[0], sell_tps[1]
            tp1_note, tp2_note = sell_notes[0], sell_notes[1]

            # TP2 = Strictly M1 SMC Structure Zone (M1 Swing Low, M1 Sup, M1 MTF) with M1 ATR Minimum Gap ($15.0+ points)
            h1_mtf_res = _f(last_h1, 'mtf_res_zone', entry_price + h1_atr * 3)
            h1_mtf_sup = _f(last_h1, 'mtf_sup_zone', entry_price)
            mtf_span = max(abs(h1_mtf_res - h1_mtf_sup), h1_atr * 3.0, range_h, risk * 1.2)

            mtf2_named = [
                ('M1 MTF Sup', h1_mtf_sup),
                ('M1 Support OB', _f(last_h1, 'support', 0)),
                ('M1 Swing Low', _f(last_h1, 'last_low', 0)),
                ('M1 Next Sup Zone', entry_price - mtf_span),
            ]
            if m1_df is not None and len(m1_df) > 0:
                if 'swing_low' in m1_df.columns:
                    for v in m1_df['swing_low'].dropna().tail(40).tolist():
                        mtf2_named.append(('M1 Hist Swing Low', float(v)))
                for lvl in _hist_levels([m1_df], ['mtf_sup_zone', 'support', 'last_low']):
                    mtf2_named.append(('M1 Hist Sup', lvl))

            max_tp2 = min(tp1 - max(h1_atr * 1.5, 15.0 if is_gold else 0.0015), entry_price - mtf_span * 0.75)
            mtf2_ok = [(n, round(z, 2)) for n, z in mtf2_named if 0 < z <= max_tp2]
            mtf2_ok.sort(key=lambda x: x[1], reverse=True)
            if mtf2_ok:
                tp2_n, tp2 = mtf2_ok[0]
                tp2_note = f"{tp2_n}@{tp2:.2f}"
            else:
                tp2 = round(max_tp2, 2)
                tp2_note = f"M1 Measured Sup@{tp2:.2f}"
            tp2 = min(float(tp2), max_tp2)

            # TP3 = FAR next major reversal zone
            import math
            sq = math.sqrt(max(entry_price, 1.0))
            hist_lows = _hist_levels([m1_df], ['swing_low', 'support', 'last_low'])
            major_low = min(hist_lows) if hist_lows else 0.0
            far_named = [
                ('Fib 2.618 Ext', rmax - range_h * 2.618),
                ('Fib 3.618 Ext', rmax - range_h * 3.618),
                ('Gann 360', max(0.01, (sq - 1.0) ** 2)),
                ('Gann 720', max(0.01, (sq - 2.0) ** 2)),
                ('Hist Major Low', major_low),
                ('Measured Move 2.5', entry_price - range_h * 2.50),
                ('Measured Move 3.0', entry_price - range_h * 3.00),
                ('Measured Move 4.0', entry_price - range_h * 4.00),
            ]
            min_far = entry_price - max(range_h * 5.0, h1_atr * 12.0, risk * 4.0)
            far_ok = [(n, round(z, 2)) for n, z in far_named if min_far <= z < (tp2 - h1_atr * 1.0)]
            struct = [(n, z) for n, z in far_ok if n.startswith(('Fib', 'Gann', 'Hist')) and z > 0]
            pick_pool = struct if struct else [(n, z) for n, z in far_ok if z > 0]
            if pick_pool:
                pick_pool.sort(key=lambda x: x[1])  # farthest down = smallest
                tp3_n, tp3 = pick_pool[0]
            else:
                tp3 = min(tp2 - h1_atr * 3.0, entry_price - range_h * 3.0, entry_price - risk * 3.0)
                tp3_n = 'Forced Far Reversal'
            tp3 = min(float(tp3), tp2 - h1_atr * 1.5)
            tp3_note = f"reversal {tp3_n}@{tp3:.2f}"

            logging.info(
                f"[PENDING SL/TP SELL] Entry={entry_price:.2f} | SL={sl_price:.2f} (= {zone_name}) "
                f"(risk={risk:.1f}) | TP1={tp1:.2f} ({tp1_note}) TP2={tp2:.2f} ({tp2_note}) TP3={tp3:.2f} ({tp3_note})"
            )

        digits = 2 if is_gold else 5
        return round(sl_price, digits), round(tp1, digits), round(tp2, digits), round(tp3, digits)

    # ─── SMC_PMAX_RECOVERY helpers (M1 zones outside; M5 PMAX+HalfTrend gates) ───

    @staticmethod
    def _smc_f(row, key, default=0.0):
        try:
            v = row.get(key, default) if hasattr(row, 'get') else default
            return float(v) if pd.notna(v) else float(default)
        except Exception:
            return float(default)

    @staticmethod
    def zone_edge_buffer(m1_df, broker_stops=0.0):
        last = m1_df.iloc[-1] if (m1_df is not None and len(m1_df) > 0) else {}
        atr = Strategy._smc_f(last, 'atr', 5.0)
        frac = float(getattr(config, 'SMC_ZONE_EDGE_ATR_FRAC', 0.15))
        is_gold = "XAU" in str(config.SYMBOL).upper()
        return max(atr * frac, float(broker_stops or 0), 2.0 if is_gold else atr * frac)

    @staticmethod
    def price_outside_zone(side, zone_low, zone_high, buffer, for_entry=True):
        """Place price strictly outside [zone_low, zone_high]. side: BUY_STOP|SELL_STOP|BUY_SL|SELL_SL|BUY_TP|SELL_TP."""
        zl, zh, buf = float(zone_low), float(zone_high), float(buffer)
        s = str(side).upper()
        if s in ('BUY_STOP', 'SELL_SL', 'SELL_TP'):
            return zh + buf
        if s in ('SELL_STOP', 'BUY_SL', 'BUY_TP'):
            return zl - buf
        return zh + buf if for_entry else zl - buf

    @staticmethod
    def assert_outside_zone(price, zone_low, zone_high, eps=1e-9):
        p, zl, zh = float(price), float(zone_low), float(zone_high)
        if zl > zh:
            zl, zh = zh, zl
        return not (zl - eps <= p <= zh + eps)

    @staticmethod
    def get_validated_breakout_levels(m1_df, curr_ask, curr_bid, broker_stops=0.0):
        """
        M1-only validated resistance/support OUTSIDE zone edges.
        Rejects inducement/invalid highs/lows when valid_* is available.
        """
        is_gold = "XAU" in str(config.SYMBOL).upper()
        digits = 2 if is_gold else 5
        ask, bid = float(curr_ask), float(curr_bid)
        buf = Strategy.zone_edge_buffer(m1_df, broker_stops)
        last = m1_df.iloc[-1] if (m1_df is not None and len(m1_df) > 0) else {}

        vh = Strategy._smc_f(last, 'valid_high', 0.0)
        vl = Strategy._smc_f(last, 'valid_low', 0.0)
        lh = Strategy._smc_f(last, 'last_high', ask + buf * 4)
        ll = Strategy._smc_f(last, 'last_low', bid - buf * 4)
        mtf_r = Strategy._smc_f(last, 'mtf_res_zone', lh)
        mtf_s = Strategy._smc_f(last, 'mtf_sup_zone', ll)
        atr = max(Strategy._smc_f(last, 'atr', 5.0), 1.0)

        # Prefer validated; fall back to MTF/last only if valid missing (still outside band)
        res_core = vh if vh > 0 else (mtf_r if mtf_r > ask else lh)
        if res_core <= ask:
            res_core = ask + max(atr * 0.5, buf * 3)
        # Reject if equal to marked invalid and no valid
        inv_h = Strategy._smc_f(last, 'invalid_high', 0.0)
        if vh <= 0 and inv_h > 0 and abs(res_core - inv_h) < buf:
            res_core = ask + max(atr * 0.8, buf * 4)

        sup_core = vl if vl > 0 else (mtf_s if 0 < mtf_s < bid else ll)
        if sup_core <= 0 or sup_core >= bid:
            sup_core = bid - max(atr * 0.5, buf * 3)
        inv_l = Strategy._smc_f(last, 'invalid_low', 0.0)
        if vl <= 0 and inv_l > 0 and abs(sup_core - inv_l) < buf:
            sup_core = bid - max(atr * 0.8, buf * 4)

        res_zone_high = Strategy._smc_f(last, 'res_zone_high', res_core)
        res_zone_low = Strategy._smc_f(last, 'res_zone_low', res_core - atr * 0.2)
        if res_zone_high < res_core:
            res_zone_high = res_core
        if res_zone_low > res_zone_high:
            res_zone_low = res_zone_high - atr * 0.2

        sup_zone_low = Strategy._smc_f(last, 'sup_zone_low', sup_core)
        sup_zone_high = Strategy._smc_f(last, 'sup_zone_high', sup_core + atr * 0.2)
        if sup_zone_low > sup_core:
            sup_zone_low = sup_core
        if sup_zone_high < sup_zone_low:
            sup_zone_high = sup_zone_low + atr * 0.2

        buy_stop = Strategy.price_outside_zone('BUY_STOP', res_zone_low, res_zone_high, buf)
        sell_stop = Strategy.price_outside_zone('SELL_STOP', sup_zone_low, sup_zone_high, buf)
        if buy_stop <= ask:
            buy_stop = ask + max(buf * 2, 2.0 if is_gold else buf)
        if sell_stop >= bid:
            sell_stop = bid - max(buf * 2, 2.0 if is_gold else buf)

        # Hard reject if still inside zone
        if not Strategy.assert_outside_zone(buy_stop, res_zone_low, res_zone_high):
            buy_stop = res_zone_high + buf
        if not Strategy.assert_outside_zone(sell_stop, sup_zone_low, sup_zone_high):
            sell_stop = sup_zone_low - buf

        return {
            "buy_stop": round(buy_stop, digits),
            "sell_stop": round(sell_stop, digits),
            "res_zone": (round(res_zone_low, digits), round(res_zone_high, digits)),
            "sup_zone": (round(sup_zone_low, digits), round(sup_zone_high, digits)),
            "buffer": float(buf),
            "h1_atr": float(atr),
            "m1_atr": float(atr),
            "valid_high": float(vh),
            "valid_low": float(vl),
        }

    @staticmethod
    def m5_pmax_halftrend_status(m5_df):
        """M5 PMAX + HalfTrend (Mannu) live status for UI/gates."""
        out = {
            "pmax": "FLAT",
            "halftrend": "FLAT",
            "dual": "MIXED",
            "pmax_bullish": False,
            "pmax_bearish": False,
            "halftrend_bullish": False,
            "halftrend_bearish": False,
        }
        if m5_df is None or len(m5_df) == 0:
            return out
        last = m5_df.iloc[-1]
        pmax_b = bool(last.get('pmax_bullish', False)) if hasattr(last, 'get') else False
        pmax_s = bool(last.get('pmax_bearish', False)) if hasattr(last, 'get') else False
        ht_b = bool(last.get('mannu_matrix_bullish', False)) if hasattr(last, 'get') else False
        ht_s = bool(last.get('mannu_matrix_bearish', False)) if hasattr(last, 'get') else False
        if not pmax_b and not pmax_s:
            try:
                pmax_b = float(last.get('close', 0)) > float(last.get('pmax', 0))
                pmax_s = not pmax_b
            except Exception:
                pass
        if not ht_b and not ht_s:
            try:
                t = float(last.get('mannu_matrix_trend', 0))
                # Existing indicator: trend 0 = bullish, 1 = bearish
                ht_b, ht_s = (t == 0), (t == 1)
            except Exception:
                pass
        out["pmax_bullish"] = pmax_b
        out["pmax_bearish"] = pmax_s
        out["halftrend_bullish"] = ht_b
        out["halftrend_bearish"] = ht_s
        out["pmax"] = "BUY" if pmax_b and not pmax_s else ("SELL" if pmax_s and not pmax_b else ("BUY" if pmax_b else ("SELL" if pmax_s else "FLAT")))
        out["halftrend"] = "BUY" if ht_b and not ht_s else ("SELL" if ht_s and not ht_b else ("BUY" if ht_b else ("SELL" if ht_s else "FLAT")))
        # C1 = PMax, C2 = HalfTrend
        out["c1"] = out["pmax"]
        out["c2"] = out["halftrend"]
        require = getattr(config, 'SMC_REQUIRE_DUAL_TREND', True)
        if require:
            if pmax_b and ht_b:
                out["dual"] = "BUY"
            elif pmax_s and ht_s:
                out["dual"] = "SELL"
            else:
                out["dual"] = "MIXED"
        else:
            if pmax_b or ht_b:
                out["dual"] = "BUY"
            elif pmax_s or ht_s:
                out["dual"] = "SELL"
            else:
                out["dual"] = "MIXED"
        return out

    @staticmethod
    def trend_bias_pmax_halftrend(m5_df):
        """M5-only dual bias: BUY | SELL | MIXED."""
        return Strategy.m5_pmax_halftrend_status(m5_df).get("dual", "MIXED")

    @staticmethod
    def m5_hit_confirm_status(m1_df, m5_df):
        """C1 (PMAX) + C2 (HalfTrend) + C3 (Closed Candle) reading for UI and hit-check."""
        out = dict(Strategy.m5_pmax_halftrend_status(m5_df))
        h1_closed = bool(getattr(config, "M1_CONFIRM_CLOSED", True))
        m5_closed = bool(getattr(config, "M5_CONFIRM_CLOSED", True))
        h1c = tf_latest_reading(m1_df, closed_only=h1_closed)
        m5c = tf_latest_reading(m5_df, closed_only=m5_closed)
        buy_g = h1_m5_pattern_gate(m1_df, m5_df, "BUY", h1_closed_only=h1_closed, m5_closed_only=m5_closed)
        sell_g = h1_m5_pattern_gate(m1_df, m5_df, "SELL", h1_closed_only=h1_closed, m5_closed_only=m5_closed)
        candle = m5c.get("bias") or "NONE"
        out["candle"] = candle
        out["c3"] = candle
        out["c1"] = out.get("pmax")
        out["c2"] = out.get("halftrend")
        out["candle_h1"] = h1c.get("bias") or "NONE"
        out["candle_m5"] = candle
        out["candle_detail"] = m5c.get("summary") or "NONE"
        out["candle_h1_detail"] = h1c.get("summary") or "NONE"
        out["h1_closed_mode"] = h1_closed
        out["m5_closed_mode"] = m5_closed
        out["buy_hit_ok"] = (
            out.get("pmax") == "BUY" and out.get("halftrend") == "BUY" and bool(buy_g.get("ok"))
        )
        out["sell_hit_ok"] = (
            out.get("pmax") == "SELL" and out.get("halftrend") == "SELL" and bool(sell_g.get("ok"))
        )
        return out

    @staticmethod
    def entry_is_outside_zones(m1_df, price, curr_ask, curr_bid, side="BUY", broker_stops=0.0, require_breakout=False):
        """True when price is not inside M1 supply/demand zone body.
        require_breakout=True (stops): BUY above supply high, SELL below demand low."""
        lvls = Strategy.get_validated_breakout_levels(m1_df, curr_ask, curr_bid, broker_stops)
        res = lvls.get("res_zone") or (0.0, 0.0)
        sup = lvls.get("sup_zone") or (0.0, 0.0)
        p = float(price)
        inside_res = not Strategy.assert_outside_zone(p, res[0], res[1])
        inside_sup = not Strategy.assert_outside_zone(p, sup[0], sup[1])
        if inside_res or inside_sup:
            where = "supply" if inside_res else "demand"
            return False, f"price {p:.2f} is inside the {where} zone"
        s = str(side).upper()
        if require_breakout:
            if "BUY" in s and p <= float(res[1]):
                return False, f"BUY must be outside/above supply zone high {res[1]:.2f} (now {p:.2f})"
            if "SELL" in s and p >= float(sup[0]):
                return False, f"SELL must be outside/below demand zone low {sup[0]:.2f} (now {p:.2f})"
        return True, "outside zone"

    @staticmethod
    def should_confirm_or_modify_stop(side, price, stop_price, m1_df, m5_df, broker_stops=0.0):
        """
        Near-hit: fill only if PMAX + HalfTrend + candle pattern ALL agree on this side.
        Far from stop → KEEP (pending stays parked).
        Near stop:
          BUY:  PMAX BUY AND HalfTrend BUY AND M5 bullish/continuation (M1 not bearish)
          SELL: PMAX SELL AND HalfTrend SELL AND M5 bearish/continuation (M1 not bullish)
          Else → MODIFY farther outside zone so it cannot fill.
        """
        st = Strategy.m5_pmax_halftrend_status(m5_df)
        bias = st.get("dual", "MIXED")
        pmax = st.get("pmax") or "FLAT"
        ht = st.get("halftrend") or "FLAT"
        side_u = str(side).upper()
        want = 'BUY' if 'BUY' in side_u else 'SELL'
        atr = Strategy._smc_f(m1_df.iloc[-1] if len(m1_df) else {}, 'atr', 5.0)
        approach = max(atr * float(getattr(config, 'SMC_APPROACH_ATR_FRAC', 0.5)), float(broker_stops or 0), 1.0)
        dist = abs(float(price) - float(stop_price))
        candle = h1_m5_pattern_gate(m1_df, m5_df, want)
        extra = {
            "pmax": pmax,
            "halftrend": ht,
            "bias": bias,
            "candle_ok": bool(candle.get("ok")),
            "candle": candle.get("reason"),
        }
        pmax_ok = pmax == want
        ht_ok = ht == want
        candle_ok = bool(candle.get("ok"))
        if pmax_ok and ht_ok and candle_ok:
            # All 3 signals aligned: do not add any artificial delay/chasing.
            return {"action": "KEEP", "new_price": float(stop_price), **extra}
        # Not fully confirmed and still far from stop -> keep parked.
        if dist > approach:
            return {"action": "KEEP", "new_price": float(stop_price), **extra}

        lvls2 = Strategy.get_validated_breakout_levels(m1_df, float(price), float(price), broker_stops)
        buf = float(lvls2.get("buffer") or 1.0)
        new_p = float(lvls2['buy_stop'] if want == 'BUY' else lvls2['sell_stop'])
        if want == 'BUY':
            rz = lvls2.get("res_zone") or (new_p - 5, new_p)
            new_p = Strategy.price_outside_zone('BUY_STOP', rz[0], rz[1], buf)
            new_p = max(new_p, float(price) + max(approach, 2.0), rz[1] + buf)
        else:
            sz = lvls2.get("sup_zone") or (new_p, new_p + 5)
            new_p = Strategy.price_outside_zone('SELL_STOP', sz[0], sz[1], buf)
            new_p = min(new_p, float(price) - max(approach, 2.0), sz[0] - buf)
        digits = 2 if 'XAU' in str(getattr(config, 'SYMBOL', '')).upper() else 5
        return {"action": "MODIFY", "new_price": round(new_p, digits), **extra}

    @staticmethod
    def calc_smc_recovery_lot(loss_usd, target_move, money_per_lot_per_move, original_leg_lot, min_lot=0.01, lot_step=0.01):
        """Per-leg lot so 2 reverse legs cover loss_usd over target_move. Never undersize vs original on real loss."""
        loss = abs(float(loss_usd))
        move = max(float(target_move), 0.01)
        mpl = max(float(money_per_lot_per_move), 1e-9)
        # 2 legs must cover full loss + slight profit buffer (~8–10%)
        raw = (loss * 1.10 / 2.0) / mpl
        mult = float(getattr(config, 'SMC_MAX_RECOVERY_LOT_MULT', 5.0))
        cap = max(float(original_leg_lot) * mult, float(min_lot))
        max_pend = float(getattr(config, 'PENDING_MAX_LOT', cap))
        cap = min(cap, max_pend)
        stepped = max(float(min_lot), round(raw / float(lot_step)) * float(lot_step))
        # Same-lot hedge cannot flatten — require strictly larger than original when loss > 0
        if loss > 0:
            stepped = max(stepped, round((float(original_leg_lot) + float(lot_step)) / float(lot_step)) * float(lot_step))
        return round(min(stepped, cap), 2)

    @staticmethod
    def calc_smc_neutralize_lot(open_net_pnl, target_move, money_per_lot_per_move, n_legs=3, min_lot=0.01, lot_step=0.01):
        """Size each of n_legs so combined move offsets open_net_pnl toward flat."""
        need = abs(float(open_net_pnl))
        mpl = max(float(money_per_lot_per_move), 1e-9)
        raw = (need / max(int(n_legs), 1)) / mpl
        max_pend = float(getattr(config, 'PENDING_MAX_LOT', 0.80))
        stepped = max(float(min_lot), round(raw / float(lot_step)) * float(lot_step))
        return round(min(stepped, max_pend), 2)


    @staticmethod
    def calculate_indicators(df):
        """Calculates Smart Money Concepts (SMC) + Price Action + Volume Analysis"""
        df = df.copy()
        
        # 1. Advanced SMC & Bill Williams Fractal Market Structure (support-and-resistance-mtf2.ex4 Rule)
        window = 5
        df['swing_high'] = df['high'].rolling(window=window*2+1, center=True).apply(lambda x: x[window] if x[window] == max(x) else float('nan'), raw=True)
        df['swing_low'] = df['low'].rolling(window=window*2+1, center=True).apply(lambda x: x[window] if x[window] == min(x) else float('nan'), raw=True)
        
        # Bill Williams 5-Bar MTF Fractal Support & Resistance Zone Rules
        df['fractal_high'] = (df['high'] > df['high'].shift(1)) & (df['high'] > df['high'].shift(2)) & (df['high'] > df['high'].shift(-1)) & (df['high'] > df['high'].shift(-2))
        df['fractal_low'] = (df['low'] < df['low'].shift(1)) & (df['low'] < df['low'].shift(2)) & (df['low'] < df['low'].shift(-1)) & (df['low'] < df['low'].shift(-2))
        df['mtf_res_zone'] = df['high'].where(df['fractal_high']).ffill()
        df['mtf_sup_zone'] = df['low'].where(df['fractal_low']).ffill()

        df['last_high'] = df['swing_high'].combine_first(df['mtf_res_zone']).ffill()
        df['last_low'] = df['swing_low'].combine_first(df['mtf_sup_zone']).ffill()

        # 2. SMC Inducement (IDM) & Liquidity Trap Detection
        # Inducement occurs when minor swing points get swept before the major structure break
        df['inducement_buy'] = df['low'] < df['swing_low'].shift(2)   # Internal Liquidity Sweep (IDM Buy)
        df['inducement_sell'] = df['high'] > df['swing_high'].shift(2) # Internal Liquidity Sweep (IDM Sell)

        # 3. Valid High & Valid Low: ONLY after liquidity/IDM sweep — else NaN (not same as last_*)
        swept_for_high = df['inducement_buy'] | (df['low'].rolling(10).min() < df['last_low'])
        swept_for_low = df['inducement_sell'] | (df['high'].rolling(10).max() > df['last_high'])
        df['valid_high'] = df['last_high'].where(swept_for_high)
        df['valid_low'] = df['last_low'].where(swept_for_low)
        df['invalid_high'] = df['last_high'].where(~swept_for_high)
        df['invalid_low'] = df['last_low'].where(~swept_for_low)

        # Zone bands around last valid structure (body = ATR fringe; entries must sit OUTSIDE)
        _atr_tmp = df['high'].rolling(14).max() - df['low'].rolling(14).min()
        _atr_tmp = _atr_tmp.replace(0, np.nan).ffill().fillna(5.0)
        _half = (_atr_tmp * 0.20).clip(lower=1.0)
        df['res_zone_high'] = df['valid_high'].fillna(df['last_high'])
        df['res_zone_low'] = df['res_zone_high'] - _half
        df['sup_zone_low'] = df['valid_low'].fillna(df['last_low'])
        df['sup_zone_high'] = df['sup_zone_low'] + _half

        # 4. Break of Structure (BOS) vs Change of Character (CHoCH)
        # BOS = Trend continuation break of Valid High/Low with candle BODY close
        # CHoCH = First counter-trend structural shift breaking prior swing level

        df['bos_bullish'] = (df['close'] > df['valid_high'].shift(1)) & (df['close'].shift(1) <= df['valid_high'].shift(1))
        df['bos_bearish'] = (df['close'] < df['valid_low'].shift(1)) & (df['close'].shift(1) >= df['valid_low'].shift(1))
        
        df['choch_bullish'] = (df['close'] > df['last_high'].shift(1)) & (df['close'].shift(2) < df['last_low'].shift(2))
        df['choch_bearish'] = (df['close'] < df['last_low'].shift(1)) & (df['close'].shift(2) > df['last_high'].shift(2))

        # 5. Smart Money Volume Analysis (Volume Spike > 1.3x 20 MA)
        df['vol_ma'] = df['real_volume'].rolling(window=20).mean() if 'real_volume' in df and df['real_volume'].sum() > 0 else df['tick_volume'].rolling(window=20).mean()
        vol_col = 'real_volume' if 'real_volume' in df and df['real_volume'].sum() > 0 else 'tick_volume'
        df['vol_spike'] = df[vol_col] > (df['vol_ma'] * 1.3)

        # 6. Valid Fair Value Gap (FVG) & Order Block (OB) Validation
        df['fvg_bullish'] = df['low'] > df['high'].shift(2)
        df['fvg_bearish'] = df['high'] < df['low'].shift(2)

        df['ob_bullish'] = (df['close'].shift(1) < df['open'].shift(1)) & (df['close'] > df['high'].shift(1)) & df['fvg_bullish']
        df['ob_bearish'] = (df['close'].shift(1) > df['open'].shift(1)) & (df['close'] < df['low'].shift(1)) & df['fvg_bearish']
        
        # 6. Premium vs Discount Zone Equilibrium Analysis
        df['range_max'] = df['high'].rolling(window=50).max()
        df['range_min'] = df['low'].rolling(window=50).min()
        df['equilibrium'] = (df['range_max'] + df['range_min']) / 2.0
        df['is_discount'] = df['close'] < df['equilibrium']  # Best for SMC BUY
        df['is_premium'] = df['close'] > df['equilibrium']   # Best for SMC SELL

        # 6b. XPS AUTO FIBONACCI RETRACEMENT & EXTENSION LOGIC (PeriodsBack = 50)
        fib_diff = (df['range_max'] - df['range_min']).replace(0, 0.0001)
        df['fib_000'] = df['range_min']
        df['fib_236'] = df['range_min'] + (fib_diff * 0.236)
        df['fib_382'] = df['range_min'] + (fib_diff * 0.382)
        df['fib_500'] = df['equilibrium']
        df['fib_618'] = df['range_min'] + (fib_diff * 0.618)  # Golden Ratio Zone
        df['fib_786'] = df['range_min'] + (fib_diff * 0.786)
        df['fib_1000'] = df['range_max']
        df['fib_1618'] = df['range_min'] + (fib_diff * 1.618) # Golden Extension Target

        # XPS Fib Confluence Signals: Price within Golden Zone (38.2% to 61.8%)
        df['fib_golden_zone_buy'] = (df['close'] >= df['fib_382']) & (df['close'] <= df['fib_618']) & df['is_discount']
        df['fib_golden_zone_sell'] = (df['close'] >= df['fib_382']) & (df['close'] <= df['fib_618']) & df['is_premium']

        # 7. Liquidity Sweep (SSL / BSL Inducement Sweep)
        df['ssl_sweep'] = df['low'] < df['last_low'].shift(1)  # Sell-Side Liquidity Sweep
        df['bsl_sweep'] = df['high'] > df['last_high'].shift(1) # Buy-Side Liquidity Sweep

        # 8. Dynamic Support & Resistance Pivot Levels & Standard Pivot Points
        df['support'] = df['low'].rolling(window=30).min()
        df['resistance'] = df['high'].rolling(window=30).max()
        df['near_support'] = df['close'] <= (df['support'] * 1.002) # Within 0.2% of Key Support
        df['near_resistance'] = df['close'] >= (df['resistance'] * 0.998) # Within 0.2% of Key Resistance

        # Standard Pivot Points Calculation (Classic Daily/Window Pivot)
        prev_high = df['high'].shift(1)
        prev_low = df['low'].shift(1)
        prev_close = df['close'].shift(1)
        p_range = (prev_high - prev_low).replace(0, 0.0001)
        
        df['pivot'] = (prev_high + prev_low + prev_close) / 3.0
        df['pivot_r1'] = (2.0 * df['pivot']) - prev_low
        df['pivot_s1'] = (2.0 * df['pivot']) - prev_high
        df['pivot_r2'] = df['pivot'] + (prev_high - prev_low)
        df['pivot_s2'] = df['pivot'] - (prev_high - prev_low)
        df['pivot_r3'] = prev_high + 2.0 * (df['pivot'] - prev_low)
        df['pivot_s3'] = prev_low - 2.0 * (prev_high - df['pivot'])

        # 8b. FIBONACCI PIVOTS V3 ALGORITHM (FiboPiv_v3.ex4 Rule)
        df['fibo_pivot'] = df['pivot']
        df['fibo_r1'] = df['fibo_pivot'] + (0.382 * p_range)
        df['fibo_r2'] = df['fibo_pivot'] + (0.618 * p_range)  # Golden Ratio Target
        df['fibo_r3'] = df['fibo_pivot'] + (1.000 * p_range)
        df['fibo_r4'] = df['fibo_pivot'] + (1.618 * p_range)  # Golden Expansion Target

        df['fibo_s1'] = df['fibo_pivot'] - (0.382 * p_range)
        df['fibo_s2'] = df['fibo_pivot'] - (0.618 * p_range)  # Golden Ratio Target
        df['fibo_s3'] = df['fibo_pivot'] - (1.000 * p_range)
        df['fibo_s4'] = df['fibo_pivot'] - (1.618 * p_range)  # Golden Expansion Target

        # 8c. W.D. GANN SQUARE OF 9 REVERSAL MATRIX (Gann_SQ9_2.ex4 Rule)
        # Price Angle Matrix = (sqrt(Price) + Angle / 360)^2
        sqrt_close = np.sqrt(df['close'].replace(0, 0.0001))
        df['gann_sq9_90_up'] = (sqrt_close + (90.0 / 360.0)) ** 2   # 90-degree Resistance Level
        df['gann_sq9_180_up'] = (sqrt_close + (180.0 / 360.0)) ** 2 # 180-degree Reversal Level
        df['gann_sq9_360_up'] = (sqrt_close + (360.0 / 360.0)) ** 2 # 360-degree Target Expansion

        df['gann_sq9_90_dn'] = np.maximum(0.01, (sqrt_close - (90.0 / 360.0)) ** 2)   # 90-degree Support Level
        df['gann_sq9_180_dn'] = np.maximum(0.01, (sqrt_close - (180.0 / 360.0)) ** 2) # 180-degree Reversal Level
        df['gann_sq9_360_dn'] = np.maximum(0.01, (sqrt_close - (360.0 / 360.0)) ** 2) # 360-degree Target Expansion


        # 9. Fast M5 Trend Origin & Moving Averages (EMA 9, EMA 21, EMA 50, EMA 200)
        df['ema9'] = ta.trend.ema_indicator(df['close'], window=9)
        df['ema21'] = ta.trend.ema_indicator(df['close'], window=21)
        df['ema50'] = ta.trend.ema_indicator(df['close'], window=50)
        df['ema200'] = ta.trend.ema_indicator(df['close'], window=200)
        
        df['ema_fast_bullish'] = (df['ema9'] > df['ema21']) & (df['close'] > df['ema9'])
        df['ema_fast_bearish'] = (df['ema9'] < df['ema21']) & (df['close'] < df['ema9'])
        
        df['ema_bullish'] = (df['ema50'] > df['ema200']) | df['ema_fast_bullish']
        df['ema_bearish'] = (df['ema50'] < df['ema200']) | df['ema_fast_bearish']

        # 10. SMC Breaker Block & Mitigation Block Analysis
        df['breaker_bullish'] = (df['high'] > df['last_high'].shift(1)) & (df['close'] < df['last_low'].shift(1)) & (df['close'] > df['open'])
        df['breaker_bearish'] = (df['low'] < df['last_low'].shift(1)) & (df['close'] > df['last_high'].shift(1)) & (df['close'] < df['open'])
        df['mitigation_bullish'] = (df['low'] >= df['support']) & (df['close'] > df['open']) & (df['close'] > df['close'].shift(1))
        df['mitigation_bearish'] = (df['high'] <= df['resistance']) & (df['close'] < df['open']) & (df['close'] < df['close'].shift(1))

        # 11. MACD Momentum & Divergence Crossover
        df['macd'] = ta.trend.macd(df['close'])
        df['macd_signal'] = ta.trend.macd_signal(df['close'])
        df['macd_bullish'] = df['macd'] > df['macd_signal']
        df['macd_bearish'] = df['macd'] < df['macd_signal']

        # 12. RSI Momentum, ADX Trend Strength & Dynamic ATR Volatility Filter
        df['rsi'] = ta.momentum.rsi(df['close'], window=14)
        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
        df['atr_ma'] = df['atr'].rolling(window=20).mean()
        df['atr_expansion'] = df['atr'] > df['atr_ma'] # True volatility expansion filter

        # ADX + Bollinger Bandwidth + EMA Convergence Calculation for Strict Ranging Market Protection
        adx_ind = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        df['adx'] = adx_ind.adx()
        
        # Bollinger Bands Squeeze Filter
        bb_ind = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bbw'] = (bb_ind.bollinger_hband() - bb_ind.bollinger_lband()) / bb_ind.bollinger_mavg()
        df['is_bb_squeeze'] = df['bbw'] < 0.004  # Tight volatility compression / range squeeze

        # EMA 50 & 200 Convergence (Flat choppy market)
        df['ema_flat'] = (abs(df['ema50'] - df['ema200']) / df['close']) < 0.0008

        # Comprehensive Multi-Indicator Ranging Market Filter:
        df['is_consolidation'] = (df['adx'] < 22) | df['is_bb_squeeze'] | df['ema_flat']
        df['is_trending_market'] = ~df['is_consolidation']


        # 13. Profit Maximizer (PMax) Indicator Calculation Algorithm
        # Source = (High + Low)/2, ATR Length = 10, ATR Multiplier = 3.0, Moving Average = EMA 10
        src = (df['high'] + df['low']) / 2.0
        pmax_atr = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=10)
        mavg = ta.trend.ema_indicator(src, window=10)

        long_stop = mavg - (3.0 * pmax_atr)
        short_stop = mavg + (3.0 * pmax_atr)

        long_stop_arr = long_stop.to_numpy()
        short_stop_arr = short_stop.to_numpy()
        mavg_arr = mavg.to_numpy()

        n = len(df)
        pmax_arr = [0.0] * n
        dir_arr = [1] * n

        for i in range(1, n):
            # Update Long Stop Trailing
            if mavg_arr[i] > long_stop_arr[i-1]:
                long_stop_arr[i] = max(long_stop_arr[i], long_stop_arr[i-1])
            
            # Update Short Stop Trailing
            if mavg_arr[i] < short_stop_arr[i-1]:
                short_stop_arr[i] = min(short_stop_arr[i], short_stop_arr[i-1])

            # Determine PMax Direction Trend (1 = Bullish, -1 = Bearish)
            prev_dir = dir_arr[i-1]
            if prev_dir == -1 and mavg_arr[i] > short_stop_arr[i-1]:
                curr_dir = 1
            elif prev_dir == 1 and mavg_arr[i] < long_stop_arr[i-1]:
                curr_dir = -1
            else:
                curr_dir = prev_dir

            dir_arr[i] = curr_dir
            pmax_arr[i] = long_stop_arr[i] if curr_dir == 1 else short_stop_arr[i]

        df['pmax'] = pmax_arr
        df['pmax_mavg'] = mavg_arr
        df['pmax_bullish'] = [d == 1 for d in dir_arr]
        df['pmax_bearish'] = [d == -1 for d in dir_arr]

        # 14. MANNU MATRIX (HalfTrend Amplitude 3, Channel Dev 2, ATR 100) Indicator Algorithm
        amplitude = 3
        chan_dev = 2.0
        atr100_window = min(100, max(2, n - 1))
        atr100 = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=atr100_window).fillna(0.0) / 2.0
        
        df_high = df['high'].to_numpy()
        df_low = df['low'].to_numpy()
        df_close = df['close'].to_numpy()

        ht_trend = [0] * n
        ht_next_trend = [0] * n
        ht_max_low = [df_low[0]] * n
        ht_min_high = [df_high[0]] * n
        ht_up = [0.0] * n
        ht_down = [0.0] * n

        high_ma = ta.trend.sma_indicator(df['high'], window=amplitude).fillna(df['high']).to_numpy()
        low_ma = ta.trend.sma_indicator(df['low'], window=amplitude).fillna(df['low']).to_numpy()

        for i in range(1, n):
            start_idx = max(0, i - amplitude + 1)
            high_price = np.max(df_high[start_idx:i+1])
            low_price = np.min(df_low[start_idx:i+1])

            prev_next_trend = ht_next_trend[i-1]
            prev_trend = ht_trend[i-1]
            prev_max_low = ht_max_low[i-1]
            prev_min_high = ht_min_high[i-1]

            if prev_next_trend == 1:
                curr_max_low = max(low_price, prev_max_low)
                # TradingView parity: use high MA in this branch.
                if high_ma[i] < curr_max_low and df_close[i] < df_low[i-1]:
                    curr_trend = 1
                    curr_next_trend = 0
                    curr_min_high = high_price
                else:
                    curr_trend = prev_trend
                    curr_next_trend = prev_next_trend
                    curr_min_high = prev_min_high
            else:
                curr_min_high = min(high_price, prev_min_high)
                if low_ma[i] > curr_min_high and df_close[i] > df_high[i-1]:
                    curr_trend = 0
                    curr_next_trend = 1
                    curr_max_low = low_price
                else:
                    curr_trend = prev_trend
                    curr_next_trend = prev_next_trend
                    curr_max_low = prev_max_low

            ht_trend[i] = curr_trend
            ht_next_trend[i] = curr_next_trend
            ht_max_low[i] = curr_max_low
            ht_min_high[i] = curr_min_high

            if curr_trend == 0:
                if i > 0 and ht_trend[i-1] != 0:
                    ht_up[i] = ht_down[i-1] if i > 0 else curr_max_low
                else:
                    ht_up[i] = max(curr_max_low, ht_up[i-1]) if i > 0 else curr_max_low
                ht_down[i] = ht_down[i-1] if i > 0 else 0.0
            else:
                if i > 0 and ht_trend[i-1] != 1:
                    ht_down[i] = ht_up[i-1] if i > 0 else curr_min_high
                else:
                    ht_down[i] = min(curr_min_high, ht_down[i-1]) if i > 0 else curr_min_high
                ht_up[i] = ht_up[i-1] if i > 0 else 0.0

        df = df.copy()
        df['mannu_matrix_trend'] = ht_trend
        df['mannu_matrix_bullish'] = [t == 0 for t in ht_trend]
        df['mannu_matrix_bearish'] = [t == 1 for t in ht_trend]


        # 15. SuperTrend V Volume-Weighted ATR Trend Algorithm
        # Volume Spread & Volume-Weighted Price Trend (VPT)
        vol_series = df['volume'] if 'volume' in df.columns else (df['tick_volume'] if 'tick_volume' in df.columns else pd.Series(1, index=df.index))
        hilow = (df['high'] - df['low']) * 100.0
        hilow_clean = hilow.replace(0, 0.0001)
        openclose = (df['close'] - df['open']) * 100.0
        vol = vol_series / hilow_clean
        spreadvol = openclose * vol
        vpt_val = (spreadvol + spreadvol.cumsum()).fillna(0)
        
        # SuperTrend Multiplier = 1, Period = 10
        st_atr10 = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=10)
        up_lev = vpt_val - (1.0 * st_atr10)
        dn_lev = vpt_val + (1.0 * st_atr10)

        up_trend_st = [0.0] * n
        dn_trend_st = [0.0] * n
        st_trend_arr = [1] * n

        up_lev_arr = up_lev.to_numpy()
        dn_lev_arr = dn_lev.to_numpy()

        for i in range(1, n):
            if df_close[i-1] > up_trend_st[i-1]:
                up_trend_st[i] = max(up_lev_arr[i], up_trend_st[i-1])
            else:
                up_trend_st[i] = up_lev_arr[i]

            if df_close[i-1] < dn_trend_st[i-1]:
                dn_trend_st[i] = min(dn_lev_arr[i], dn_trend_st[i-1])
            else:
                dn_trend_st[i] = dn_lev_arr[i]

            if df_close[i] > dn_trend_st[i-1]:
                st_trend_arr[i] = 1
            elif df_close[i] < up_trend_st[i-1]:
                st_trend_arr[i] = -1
            else:
                st_trend_arr[i] = st_trend_arr[i-1]

        df['supertrend_v_bullish'] = [t == 1 for t in st_trend_arr]
        df['supertrend_v_bearish'] = [t == -1 for t in st_trend_arr]

        return df

    @staticmethod
    def generate_signal(m1_df, m15_df, m5_df):
        """
        Smart Money Concepts (SMC) High Win-Rate Execution:
        1. Higher Timeframe (M1 + M15): Structural Bias (Bullish/Bearish BOS & Market Structure).
        2. Lower Timeframe (M5): Order Block Sweep + Fair Value Gap (FVG) + Institutional Volume Spike.
        """
        m1_df = Strategy.calculate_indicators(m1_df)
        m15_df = Strategy.calculate_indicators(m15_df)
        m5_df = Strategy.calculate_indicators(m5_df)

        last_h1 = m1_df.iloc[-1]
        last_m15 = m15_df.iloc[-1]
        last_m5 = m5_df.iloc[-1]
        prev_m5 = m5_df.iloc[-2]

        h1_lh = last_h1['last_high'] if pd.notna(last_h1['last_high']) else last_h1['high']
        h1_ll = last_h1['last_low'] if pd.notna(last_h1['last_low']) else last_h1['low']
        m15_lh = last_m15['last_high'] if pd.notna(last_m15['last_high']) else last_m15['high']
        m15_ll = last_m15['last_low'] if pd.notna(last_m15['last_low']) else last_m15['low']

        # Clean Structural Breakout Bias (Only True on genuine structural breakouts/breakdowns)
        h1_bullish = bool((last_h1['close'] > h1_lh) or last_h1['bos_bullish'])
        h1_bearish = bool((last_h1['close'] < h1_ll) or last_h1['bos_bearish'])
        if h1_bullish and h1_bearish:
            if last_h1['close'] >= last_h1['open']: h1_bearish = False
            else: h1_bullish = False

        m15_bullish = bool((last_m15['close'] > m15_lh) or last_m15['bos_bullish'])
        m15_bearish = bool((last_m15['close'] < m15_ll) or last_m15['bos_bearish'])
        if m15_bullish and m15_bearish:
            if last_m15['close'] >= last_m15['open']: m15_bearish = False
            else: m15_bullish = False

        # CONSOLIDATION / RANGING MARKET PROTECTION:
        # Block trade entry if ADX < 20 (weak, flat, choppy ranging market)
        if bool(last_m5.get('is_consolidation', False)):
            adx_val = float(last_m5.get('adx', 0)) if pd.notna(last_m5.get('adx', 0)) else 0.0
            logging.info(f"[CONSOLIDATION BLOCK] Market is ranging (ADX: {adx_val:.1f} < 20). Trade entry blocked until trend breakout!")
            return None, 0, 0, False

        m5_bullish_candle = bool(last_m5['close'] >= last_m5['open'])
        curr_price = float(last_m5['close'])

        # 1. EVALUATE BUY LOGIC ACROSS ALL 20 COMPREHENSIVE RULES
        buy_score = 0
        if h1_bullish: buy_score += 1
        if m15_bullish: buy_score += 1
        if last_m5['ema_bullish']: buy_score += 1
        if last_m5['ema_fast_bullish']: buy_score += 1
        if last_m5['pmax_bullish']: buy_score += 1
        if last_m5['mannu_matrix_bullish']: buy_score += 1
        if pd.notna(last_m5.get('mtf_sup_zone')) and curr_price >= last_m5.get('mtf_sup_zone', 0): buy_score += 1
        if curr_price < (curr_price + (last_m5.get('atr', 2.0) * 3.0)): buy_score += 1
        if pd.notna(last_m5.get('fibo_pivot')) and curr_price >= (last_m5.get('fibo_pivot', 0) - last_m5.get('atr', 2.0)): buy_score += 1
        if pd.notna(last_m5.get('gann_sq9_90_dn')) and curr_price >= last_m5.get('gann_sq9_90_dn', 0): buy_score += 1
        if last_m5['is_discount'] or last_m5['near_support']: buy_score += 1
        if last_m5['ssl_sweep']: buy_score += 1
        if last_m5['fvg_bullish']: buy_score += 1
        if last_m5['ob_bullish']: buy_score += 1
        if last_m5['breaker_bullish'] or last_m5['mitigation_bullish']: buy_score += 1
        if last_m5['supertrend_v_bullish']: buy_score += 1
        if last_m5['macd_bullish']: buy_score += 1
        if (last_m5['vol_spike'] or last_m5['atr_expansion']) and m5_bullish_candle: buy_score += 1
        if last_m5['rsi'] < 45: buy_score += 1
        if last_m5['near_support'] or (abs(curr_price - last_m5['pivot']) <= last_m5['atr']): buy_score += 1

        # 2. EVALUATE SELL LOGIC ACROSS ALL 20 COMPREHENSIVE RULES
        sell_score = 0
        if h1_bearish: sell_score += 1
        if m15_bearish: sell_score += 1
        if last_m5['ema_bearish']: sell_score += 1
        if last_m5['ema_fast_bearish']: sell_score += 1
        if last_m5['pmax_bearish']: sell_score += 1
        if last_m5['mannu_matrix_bearish']: sell_score += 1
        if pd.notna(last_m5.get('mtf_res_zone')) and curr_price <= last_m5.get('mtf_res_zone', 999999): sell_score += 1
        if curr_price > (curr_price - (last_m5.get('atr', 2.0) * 3.0)): sell_score += 1
        if pd.notna(last_m5.get('fibo_pivot')) and curr_price <= (last_m5.get('fibo_pivot', 0) + last_m5.get('atr', 2.0)): sell_score += 1
        if pd.notna(last_m5.get('gann_sq9_90_up')) and curr_price <= last_m5.get('gann_sq9_90_up', 999999): sell_score += 1
        if last_m5['is_premium'] or last_m5['near_resistance']: sell_score += 1
        if last_m5['bsl_sweep']: sell_score += 1
        if last_m5['fvg_bearish']: sell_score += 1
        if last_m5['ob_bearish']: sell_score += 1
        if last_m5['breaker_bearish'] or last_m5['mitigation_bearish']: sell_score += 1
        if last_m5['supertrend_v_bearish']: sell_score += 1
        if last_m5['macd_bearish']: sell_score += 1
        if (last_m5['vol_spike'] or last_m5['atr_expansion']) and not m5_bullish_candle: sell_score += 1
        if last_m5['rsi'] > 55: sell_score += 1
        if last_m5['near_resistance'] or (abs(curr_price - last_m5['pivot']) <= last_m5['atr']): sell_score += 1

        # Track Mandatory matched count out of 10 for BUY
        buy_mandatory_score = 0
        if h1_bullish: buy_mandatory_score += 1
        if m15_bullish: buy_mandatory_score += 1
        if last_m5['ema_bullish']: buy_mandatory_score += 1
        if last_m5['ema_fast_bullish']: buy_mandatory_score += 1
        if last_m5['pmax_bullish']: buy_mandatory_score += 1
        if last_m5['mannu_matrix_bullish']: buy_mandatory_score += 1
        if pd.notna(last_m5.get('mtf_sup_zone')) and curr_price >= last_m5.get('mtf_sup_zone', 0): buy_mandatory_score += 1
        if curr_price < (curr_price + (last_m5.get('atr', 2.0) * 3.0)): buy_mandatory_score += 1
        if pd.notna(last_m5.get('fibo_pivot')) and curr_price >= (last_m5.get('fibo_pivot', 0) - last_m5.get('atr', 2.0)): buy_mandatory_score += 1
        if pd.notna(last_m5.get('gann_sq9_90_dn')) and curr_price >= last_m5.get('gann_sq9_90_dn', 0): buy_mandatory_score += 1

        # Track Mandatory matched count out of 10 for SELL
        sell_mandatory_score = 0
        if h1_bearish: sell_mandatory_score += 1
        if m15_bearish: sell_mandatory_score += 1
        if last_m5['ema_bearish']: sell_mandatory_score += 1
        if last_m5['ema_fast_bearish']: sell_mandatory_score += 1
        if last_m5['pmax_bearish']: sell_mandatory_score += 1
        if last_m5['mannu_matrix_bearish']: sell_mandatory_score += 1
        if pd.notna(last_m5.get('mtf_res_zone')) and curr_price <= last_m5.get('mtf_res_zone', 999999): sell_mandatory_score += 1
        if curr_price > (curr_price - (last_m5.get('atr', 2.0) * 3.0)): sell_mandatory_score += 1
        if pd.notna(last_m5.get('fibo_pivot')) and curr_price <= (last_m5.get('fibo_pivot', 0) + last_m5.get('atr', 2.0)): sell_mandatory_score += 1
        if pd.notna(last_m5.get('gann_sq9_90_up')) and curr_price <= last_m5.get('gann_sq9_90_up', 999999): sell_mandatory_score += 1

        atr_pips = last_m5['atr'] if pd.notna(last_m5['atr']) else 10.0

        # Trigger Trade signal if score >= 15 out of 20 AND ALL 10 MANDATORY RULES ARE MATCHED!
        if buy_score >= 15 and buy_mandatory_score == 10 and buy_score > sell_score:
            return 'BUY', last_m5['close'], atr_pips, bool(last_m5['supertrend_v_bullish'])
        elif sell_score >= 15 and sell_mandatory_score == 10 and sell_score > buy_score:
            return 'SELL', last_m5['close'], atr_pips, bool(last_m5['supertrend_v_bearish'])

        return None, 0, 0, False



    @staticmethod
    def get_waiting_reason(m1_df, m15_df, m5_df):
        """
        Human-readable SMC confirmation status showing exact missing rules and what bot is waiting for.
        """
        m1_df = Strategy.calculate_indicators(m1_df)
        m15_df = Strategy.calculate_indicators(m15_df)
        m5_df = Strategy.calculate_indicators(m5_df)

        last_h1 = m1_df.iloc[-1]
        last_m15 = m15_df.iloc[-1]
        last_m5 = m5_df.iloc[-1]

        if bool(last_m5.get('is_consolidation', False)):
            adx_val = float(last_m5.get('adx', 0)) if pd.notna(last_m5.get('adx', 0)) else 0.0
            return f"⏸️ CONSOLIDATION / RANGING MARKET (ADX: {adx_val:.1f} < 20). Waiting for market trend expansion!"

        h1_lh = last_h1['last_high'] if pd.notna(last_h1['last_high']) and last_h1['last_high'] is not None else last_h1['close']
        h1_ll = last_h1['last_low'] if pd.notna(last_h1['last_low']) and last_h1['last_low'] is not None else last_h1['close']
        m15_lh = last_m15['last_high'] if pd.notna(last_m15['last_high']) and last_m15['last_high'] is not None else last_m15['close']

        h1_bullish = last_h1['close'] >= h1_lh or last_h1['bos_bullish']
        m15_bullish = last_m15['close'] >= m15_lh or last_m15['bos_bullish']

        buy_score = 0
        if h1_bullish: buy_score += 1
        if m15_bullish: buy_score += 1
        if last_m5['ema_bullish']: buy_score += 1
        if last_m5['ema_fast_bullish']: buy_score += 1
        if last_m5['pmax_bullish']: buy_score += 1
        if last_m5['mannu_matrix_bullish']: buy_score += 1
        if last_m5['is_discount'] or last_m5['near_support']: buy_score += 1
        if last_m5['ssl_sweep']: buy_score += 1
        if last_m5['fvg_bullish']: buy_score += 1
        if last_m5['ob_bullish']: buy_score += 1
        if last_m5['breaker_bullish'] or last_m5['mitigation_bullish']: buy_score += 1
        if last_m5['supertrend_v_bullish']: buy_score += 1
        if last_m5['macd_bullish']: buy_score += 1
        if last_m5['vol_spike'] or last_m5['atr_expansion']: buy_score += 1
        if last_m5['rsi'] < 45: buy_score += 1

        missing_rules = []
        if not last_m5['pmax_bullish']: missing_rules.append("QUANTUM MAXIMUM TREND 🔴")
        if not last_m5['mannu_matrix_bullish']: missing_rules.append("ALPHA MATRIX SIGNAL 🔴")

        if buy_score >= 8 and missing_rules:
            return f"⚠️ SCORE {buy_score}/15 REACHED, BUT WAITING FOR MANDATORY CONFIRMATIONS: {', '.join(missing_rules)}. Trade will open immediately when these match!"
        elif buy_score < 8:
            needed = 8 - buy_score
            return f"⏳ SCANNING: Current score is {buy_score}/15. Waiting for {needed} more rule(s) to hit 8/15 entry threshold!"
        
        return "⏳ Monitoring quad-timeframe alignment..."

    @staticmethod
    def check_upcoming_forecast(m1_df, m15_df, m5_df):
        """
        Smart Money Concepts Forecast Detection.
        """
        m1_df = Strategy.calculate_indicators(m1_df)
        m5_df = Strategy.calculate_indicators(m5_df)

        last_h1 = m1_df.iloc[-1]
        last_m5 = m5_df.iloc[-1]

        h1_lh = last_h1['last_high'] if pd.notna(last_h1['last_high']) and last_h1['last_high'] is not None else last_h1['close']
        h1_ll = last_h1['last_low'] if pd.notna(last_h1['last_low']) and last_h1['last_low'] is not None else last_h1['close']

        h1_bullish = last_h1['close'] >= h1_lh
        h1_bearish = last_h1['close'] <= h1_ll

        if h1_bullish and last_m5['rsi'] < 45:
            return {"has_forecast": True, "signal": "BUY (Liquidity Sweep)", "symbol": config.SYMBOL}

        if h1_bearish and last_m5['rsi'] > 55:
            return {"has_forecast": True, "signal": "SELL (Order Block)", "symbol": config.SYMBOL}

        return {"has_forecast": False, "signal": None, "symbol": config.SYMBOL}

    @staticmethod
    def get_checklist_status(m1_df, m15_df, m5_df):
        """
        Calculates exact status for 8 Comprehensive SMC Strategy Rules (Matched / Pending).
        """
        m1_df = Strategy.calculate_indicators(m1_df)
        m15_df = Strategy.calculate_indicators(m15_df)
        m5_df = Strategy.calculate_indicators(m5_df)

        last_h1 = m1_df.iloc[-1]
        last_m15 = m15_df.iloc[-1]
        last_m5 = m5_df.iloc[-1]

        h1_lh = last_h1['last_high'] if pd.notna(last_h1['last_high']) else last_h1['high']
        h1_ll = last_h1['last_low'] if pd.notna(last_h1['last_low']) else last_h1['low']
        m15_lh = last_m15['last_high'] if pd.notna(last_m15['last_high']) else last_m15['high']
        m15_ll = last_m15['last_low'] if pd.notna(last_m15['last_low']) else last_m15['low']

        # Clean Structural Breakout Bias
        h1_bullish = bool((last_h1['close'] > h1_lh) or last_h1['bos_bullish'])
        h1_bearish = bool((last_h1['close'] < h1_ll) or last_h1['bos_bearish'])
        if h1_bullish and h1_bearish:
            if last_h1['close'] >= last_h1['open']: h1_bearish = False
            else: h1_bullish = False

        m15_bullish = bool((last_m15['close'] > m15_lh) or last_m15['bos_bullish'])
        m15_bearish = bool((last_m15['close'] < m15_ll) or last_m15['bos_bearish'])
        if m15_bullish and m15_bearish:
            if last_m15['close'] >= last_m15['open']: m15_bearish = False
            else: m15_bullish = False

        vol_spike = bool(last_m5['vol_spike'])
        
        # Dual Timeframe (M5 & M1) SMC Zone Alignment
        is_discount = bool(last_m5['is_discount'] or (last_h1 is not None and last_h1.get('is_discount', False)))
        is_premium = bool(last_m5['is_premium'] or (last_h1 is not None and last_h1.get('is_premium', False)))
        eq_price = round(float(last_m5['equilibrium']), 2) if pd.notna(last_m5['equilibrium']) else round(float(last_m5['close']), 2)

        ssl_sweep = bool(last_m5['ssl_sweep'] or (last_h1 is not None and last_h1.get('ssl_sweep', False)))
        bsl_sweep = bool(last_m5['bsl_sweep'] or (last_h1 is not None and last_h1.get('bsl_sweep', False)))
        sweep_active = ssl_sweep or bsl_sweep

        fvg_bullish_mtf = bool(last_m5['fvg_bullish'] or (last_h1 is not None and last_h1.get('fvg_bullish', False)))
        fvg_bearish_mtf = bool(last_m5['fvg_bearish'] or (last_h1 is not None and last_h1.get('fvg_bearish', False)))
        
        ob_bullish_mtf = bool(last_m5['ob_bullish'] or (last_h1 is not None and last_h1.get('ob_bullish', False)))
        ob_bearish_mtf = bool(last_m5['ob_bearish'] or (last_h1 is not None and last_h1.get('ob_bearish', False)))

        breaker_bullish_mtf = bool(last_m5['breaker_bullish'] or last_m5['mitigation_bullish'] or (last_h1 is not None and (last_h1.get('breaker_bullish', False) or last_h1.get('mitigation_bullish', False))))
        breaker_bearish_mtf = bool(last_m5['breaker_bearish'] or last_m5['mitigation_bearish'] or (last_h1 is not None and (last_h1.get('breaker_bearish', False) or last_h1.get('mitigation_bearish', False))))


        rsi_val = round(float(last_m5['rsi']), 1)
        rsi_in_zone = bool((rsi_val < 42) or (rsi_val > 58))

        h1_aligned = bool(h1_bullish or h1_bearish)
        m15_aligned = bool(m15_bullish or m15_bearish)

        curr_price = float(last_m5['close'])
        atr_pips = float(last_m5['atr']) if pd.notna(last_m5['atr']) else 10.0
        m5_swing_low = float(last_m5['last_low']) if pd.notna(last_m5['last_low']) else curr_price - (atr_pips * 1.5)
        m5_swing_high = float(last_m5['last_high']) if pd.notna(last_m5['last_high']) else curr_price + (atr_pips * 1.5)

        # SAFE SMC STRUCTURAL ZONE ALIGNMENT (WITH SAFETY BUFFER OUTSIDE ZONES):
        # 1. BUY_STOP is placed SAFELY ABOVE Higher High (HH) / Key Resistance Zone + Safety Buffer (Prevents fake breakouts)
        # 2. SELL_STOP is placed SAFELY BELOW Lower Low (LL) / Key Support Zone - Safety Buffer
        zone_buffer = max(atr_pips * 0.5, 1.5 if "XAU" in config.SYMBOL else 10.0)
        
        resistance_zone = float(last_h1['last_high']) if pd.notna(last_h1['last_high']) else (float(last_m15['resistance']) if pd.notna(last_m15['resistance']) else curr_price + (atr_pips * 2.0))
        support_zone = float(last_h1['last_low']) if pd.notna(last_h1['last_low']) else (float(last_m15['support']) if pd.notna(last_m15['support']) else curr_price - (atr_pips * 2.0))

        buy_trigger = round(resistance_zone, 2)
        sell_trigger = round(support_zone, 2)

        # STRICT SMC ZONE & ORDER BLOCK SL/TP ANCHORING ENGINE:
        # Stop Loss (SL) is strictly placed safely OUTSIDE the SMC Demand OB / Swing Low (for BUY) or Supply OB / Swing High (for SELL) + Buffer.
        # Take Profit (TP) is strictly anchored at the opposite SMC Supply Block / Liquidity Sweep Zone / Resistance Target.
        
        # UNIFIED SINGLE SL ENGINE (Strictly identical SL for all BUY/BUY_STOP and SELL/SELL_STOP orders):
        # 1. ALL BUY & BUY_STOP orders share the SINGLE SL below M1 Valid Swing Low / Demand Zone
        # 2. ALL SELL & SELL_STOP orders share the SINGLE SL above M1 Valid Swing High / Supply Zone
        h1_atr_val = float(last_h1['atr']) if pd.notna(last_h1['atr']) else 5.0
        sl_buf_unified = max(h1_atr_val * 0.30, 2.0 if "XAU" in config.SYMBOL else h1_atr_val * 0.30)

        h1_valid_low = float(last_h1['last_low']) if pd.notna(last_h1['last_low']) else (float(last_m15['support']) if pd.notna(last_m15['support']) else curr_price - (atr_pips * 2.0))
        h1_valid_high = float(last_h1['last_high']) if pd.notna(last_h1['last_high']) else (float(last_m15['resistance']) if pd.notna(last_m15['resistance']) else curr_price + (atr_pips * 2.0))

        buy_sl = round(h1_valid_low - sl_buf_unified, 2)
        sell_sl = round(h1_valid_high + sl_buf_unified, 2)

        # TP targets at opposite structural / SMC zones
        demand_ob = float(last_m5['support']) if pd.notna(last_m5['support']) else m5_swing_low
        supply_ob = float(last_m5['resistance']) if pd.notna(last_m5['resistance']) else m5_swing_high
        buy_tp_target = max(supply_ob, curr_price + (abs(curr_price - buy_sl) * 1.5))
        buy_tp = round(buy_tp_target, 2)

        sell_tp_target = min(demand_ob, curr_price - (abs(sell_sl - curr_price) * 1.5))
        sell_tp = round(sell_tp_target, 2)

        # Separate SMC BUY Strategy Checklist (16 Comprehensive Rules with Priority Tags)
        buy_checklist = [
            {
                "id": "buy_h1_struct",
                "name": "1. M1 Bullish Structure Bias (BOS)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(h1_bullish),
                "detail": f"M1: {'BULLISH (BOS) ✅' if h1_bullish else 'WAITING M1 BULLISH BOS ⏳'}",
                "target_info": f"High Breakout Target: ${h1_lh:.2f}"
            },
            {
                "id": "buy_m15_struct",
                "name": "2. M15 Trend Structure Alignment (BOS)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(m15_bullish),
                "detail": f"M15 Structure: {'BULLISH ALIGNED ✅' if m15_bullish else 'WAITING M15 BULLISH ⏳'}",
                "target_info": "M15 Market Structure Break (BOS)"
            },
            {
                "id": "buy_ema_trend",
                "name": "3. Long-Term Moving Average Filter (EMA 50 > EMA 200)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_m5['ema50'] > last_m5['ema200']),
                "detail": f"Macro MA Filter: {'BULLISH TREND (EMA 50 > 200) ✅' if (last_m5['ema50'] > last_m5['ema200']) else 'WAITING BULLISH EMA (50 > 200) ⏳'}",
                "target_info": f"EMA50: ${last_m5['ema50']:.2f} | EMA200: ${last_m5['ema200']:.2f}"
            },
            {
                "id": "buy_ema_fast",
                "name": "4. Fast Velocity Moving Average Start (EMA 9 > EMA 21)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_m5['ema_fast_bullish']),
                "detail": f"Micro MA Crossover: {'FAST VELOCITY ORIGIN (EMA 9 > 21) ✅' if last_m5['ema_fast_bullish'] else 'WAITING FAST EMA CROSSOVER ⏳'}",
                "target_info": f"EMA9: ${last_m5['ema9']:.2f} | EMA21: ${last_m5['ema21']:.2f}"
            },
            {
                "id": "buy_pmax",
                "name": "5. QUANTUM MAXIMUM TREND & VELOCITY OVERLAY",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_m5['pmax_bullish']),
                "detail": f"QUANTUM MAX & VELOCITY: {'BULLISH TREND ✅' if last_m5['pmax_bullish'] else 'WAITING QUANTUM BULLISH ⏳'}",
                "target_info": f"Quantum Stop: ${last_m5['pmax']:.2f}"
            },
            {
                "id": "buy_mannu_matrix",
                "name": "6. ALPHA MATRIX SIGNAL",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_m5['mannu_matrix_bullish']),
                "detail": f"ALPHA MATRIX: {'BULLISH SIGNAL ✅' if last_m5['mannu_matrix_bullish'] else 'WAITING ALPHA BULLISH ⏳'}",
                "target_info": "Alpha Matrix Bullish Confirmation"
            },
            {
                "id": "buy_mtf_sr_rule",
                "name": "7. support-and-resistance-mtf2 Fractal Zone Confluence",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(pd.notna(last_m5.get('mtf_sup_zone')) and curr_price >= last_m5.get('mtf_sup_zone', 0)),
                "detail": f"MTF Fractal S&R: {'AT VALID FRACTAL SUPPORT ZONE ✅' if (pd.notna(last_m5.get('mtf_sup_zone')) and curr_price >= last_m5.get('mtf_sup_zone', 0)) else 'WAITING FRACTAL SUPPORT ⏳'}",
                "target_info": f"Fractal Support: ${last_m5.get('mtf_sup_zone', 0):.2f}"
            },
            {
                "id": "buy_gold999_rule",
                "name": "8. GOLD999D1 Daily Range Expansion Target Alignment",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(curr_price < (curr_price + (last_m5.get('atr', 2.0) * 3.0))),
                "detail": "GOLD999D1 Expansion: BULLISH DAILY EXPANSION TARGETS ACTIVE ✅",
                "target_info": "Daily Range Expansion TP Targets Active"
            },
            {
                "id": "buy_fibopiv_rule",
                "name": "9. FiboPiv_v3 Fibonacci Pivot Golden Ratio Confluence",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(pd.notna(last_m5.get('fibo_pivot')) and curr_price >= (last_m5.get('fibo_pivot', 0) - last_m5.get('atr', 2.0))),
                "detail": f"FiboPiv_v3: {'ABOVE FIBONACCI PIVOT GOLDEN ZONE ✅' if (pd.notna(last_m5.get('fibo_pivot')) and curr_price >= (last_m5.get('fibo_pivot', 0) - last_m5.get('atr', 2.0))) else 'WAITING FIBO PIVOT RECOVERY ⏳'}",
                "target_info": f"Fibo Pivot: ${last_m5.get('fibo_pivot', 0):.2f}"
            },
            {
                "id": "buy_gann_sq9_rule",
                "name": "10. Gann_SQ9_2 W.D. Gann Square of 9 M1 Matrix Confluence",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(pd.notna(last_h1.get('gann_sq9_90_dn')) and curr_price >= last_h1.get('gann_sq9_90_dn', 0)),
                "detail": f"Gann SQ9 Matrix (M1): {'ABOVE GANN 90-DEGREE M1 SUPPORT MATRIX ✅' if (pd.notna(last_h1.get('gann_sq9_90_dn')) and curr_price >= last_h1.get('gann_sq9_90_dn', 0)) else 'WAITING M1 GANN MATRIX ⏳'}",
                "target_info": f"Gann 90° Level (M1): ${last_h1.get('gann_sq9_90_dn', 0):.2f}"
            },

            {
                "id": "buy_xps_fib_rule",
                "name": "11. !XPS AUTO FIB.ex4 M1 Golden Zone Retest (38.2% - 61.8%)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_h1.get('fib_golden_zone_buy', False) or (pd.notna(last_h1.get('fib_382')) and curr_price >= last_h1.get('fib_382', 0) and curr_price <= last_h1.get('fib_618', 999999))),
                "detail": f"!XPS AUTO FIB (M1): {'AT M1 GOLDEN RETRACEMENT ZONE (38.2% - 61.8%) ✅' if (last_h1.get('fib_golden_zone_buy', False) or (pd.notna(last_h1.get('fib_382')) and curr_price >= last_h1.get('fib_382', 0) and curr_price <= last_h1.get('fib_618', 999999))) else 'WAITING M1 FIB GOLDEN RETRACEMENT ⏳'}",
                "target_info": "M1 Golden Zone Retracement (38.2% - 61.8%)"
            },



            {
                "id": "buy_discount_supp",
                "name": "12. Discount Zone Retest (Equilibrium)",
                "category": "🟡 CORE ZONE",
                "matched": bool(is_discount),
                "detail": f"Zone: {'DISCOUNT ZONE (BELOW EQUILIBRIUM) ✅' if is_discount else 'WAITING DISCOUNT ZONE ⏳'}",
                "target_info": f"Support: ${last_m5['support']:.2f} | Eq: ${eq_price:.2f}"
            },
            {
                "id": "buy_ssl_sweep",
                "name": "13. SSL Liquidity Sweep (Inducement)",
                "category": "🟡 CORE ZONE",
                "matched": bool(ssl_sweep),
                "detail": f"Liquidity: {'SSL SWEEP COMPLETED ✅' if ssl_sweep else 'WAITING SSL SWEEP ⏳'}",
                "target_info": f"Sweep Target: ${h1_ll:.2f}"
            },
            {
                "id": "buy_fvg",
                "name": "14. Fair Value Gap (FVG) Retest (M5 / M1)",
                "category": "🟡 CORE ZONE",
                "matched": bool(fvg_bullish_mtf),
                "detail": f"FVG Zone: {'BULLISH FVG ACTIVE (M5/M1) ✅' if fvg_bullish_mtf else 'WAITING BULLISH FVG ⏳'}",
                "target_info": f"FVG Imbalance Area: ~${curr_price:.2f}"
            },
            {
                "id": "buy_ob",
                "name": "15. Institutional Order Block (OB) Sweep (M5 / M1)",
                "category": "🟡 CORE ZONE",
                "matched": bool(ob_bullish_mtf),
                "detail": f"OB Zone: {'BULLISH ORDER BLOCK ACTIVE (M5/M1) ✅' if ob_bullish_mtf else 'WAITING BULLISH OB ⏳'}",
                "target_info": f"Demand Area: ~${curr_price:.2f}"
            },
            {
                "id": "buy_breaker_mit",
                "name": "16. Breaker / Mitigation Block Retest (M5 / M1)",
                "category": "🟡 CORE ZONE",
                "matched": bool(breaker_bullish_mtf),
                "detail": f"Block: {'BREAKER / MITIGATION BLOCK ACTIVE (M5/M1) ✅' if breaker_bullish_mtf else 'WAITING BULLISH BREAKER ⏳'}",
                "target_info": "Institutional Order Mitigation"
            },

            {
                "id": "buy_supertrend_v",
                "name": "17. VELOCITY VOL-TREND",
                "category": "🔵 MOMENTUM CATALYST",
                "matched": bool(last_m5['supertrend_v_bullish']),
                "detail": f"VELOCITY VOL-TREND: {'BULLISH TREND ✅' if last_m5['supertrend_v_bullish'] else 'WAITING BULLISH VELOCITY ⏳'}",
                "target_info": "Volume-Weighted ATR Trend Filter"
            },
            {
                "id": "buy_macd",
                "name": "18. MACD Momentum Crossover (Bullish)",
                "category": "🔵 MOMENTUM CATALYST",
                "matched": bool(last_m5['macd_bullish']),
                "detail": f"MACD: {'BULLISH CROSSOVER ✅' if last_m5['macd_bullish'] else 'WAITING BULLISH MACD ⏳'}",
                "target_info": "MACD > Signal Line Confirmation"
            },
            {
                "id": "buy_vol",
                "name": "19. Institutional Volume & Bullish ATR Expansion",
                "category": "🔵 MOMENTUM CATALYST",
                "matched": bool((vol_spike or last_m5['atr_expansion']) and last_m5['close'] >= last_m5['open']),
                "detail": f"Volume/ATR: {'BULLISH VOL EXPANSION 🚀' if ((vol_spike or last_m5['atr_expansion']) and last_m5['close'] >= last_m5['open']) else 'WAITING BULLISH VOL ⏳'}",
                "target_info": "Smart Money Volatility Injection"
            },
            {
                "id": "buy_rsi",
                "name": "20. RSI Momentum Recovery (<45)",
                "category": "🔵 MOMENTUM CATALYST",
                "matched": bool(rsi_val < 45),
                "detail": f"RSI Status: {'OVERSOLD / DIPPING ✅' if rsi_val < 45 else 'WAITING RSI < 45 ⏳'} (RSI: {rsi_val})",
                "target_info": "Buy Entry: RSI < 45"
            },
            {
                "id": "buy_sr_pivot",
                "name": "21. Dynamic Support & Central Pivot Level Confluence",
                "category": "🟡 CORE ZONE",
                "matched": bool(last_m5['near_support'] or (abs(curr_price - last_m5['pivot']) <= last_m5['atr'])),
                "detail": f"S/R & Pivot: {'AT KEY SUPPORT / PIVOT ZONE ✅' if (last_m5['near_support'] or (abs(curr_price - last_m5['pivot']) <= last_m5['atr'])) else 'WAITING KEY SUPPORT RECOVERY ⏳'}",
                "target_info": f"Support: ${last_m5['support']:.2f} | Pivot: ${last_m5['pivot']:.2f}"
            }

        ]

        # Separate SMC SELL Strategy Checklist (16 Comprehensive Rules with Priority Tags)
        sell_checklist = [
            {
                "id": "sell_h1_struct",
                "name": "1. M1 Bearish Structure Bias (CHoCH)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(h1_bearish),
                "detail": f"M1: {'BEARISH (CHoCH) ✅' if h1_bearish else 'WAITING M1 BEARISH CHoCH ⏳'}",
                "target_info": f"Low Breakdown Target: ${h1_ll:.2f}"
            },
            {
                "id": "sell_m15_struct",
                "name": "2. M15 Trend Structure Alignment (CHoCH)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(m15_bearish),
                "detail": f"M15 Structure: {'BEARISH ALIGNED ✅' if m15_bearish else 'WAITING M15 BEARISH ⏳'}",
                "target_info": "M15 Market Structure Break (CHoCH)"
            },
            {
                "id": "sell_ema_trend",
                "name": "3. Long-Term Moving Average Filter (EMA 50 < EMA 200)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_m5['ema50'] < last_m5['ema200']),
                "detail": f"Macro MA Filter: {'BEARISH TREND (EMA 50 < 200) ✅' if (last_m5['ema50'] < last_m5['ema200']) else 'WAITING BEARISH EMA (50 < 200) ⏳'}",
                "target_info": f"EMA50: ${last_m5['ema50']:.2f} | EMA200: ${last_m5['ema200']:.2f}"
            },
            {
                "id": "sell_ema_fast",
                "name": "4. Fast Velocity Moving Average Start (EMA 9 < EMA 21)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_m5['ema_fast_bearish']),
                "detail": f"Micro MA Crossover: {'FAST VELOCITY ORIGIN (EMA 9 < 21) ✅' if last_m5['ema_fast_bearish'] else 'WAITING FAST EMA CROSSOVER ⏳'}",
                "target_info": f"EMA9: ${last_m5['ema9']:.2f} | EMA21: ${last_m5['ema21']:.2f}"
            },
            {
                "id": "sell_pmax",
                "name": "5. QUANTUM MAXIMUM TREND & VELOCITY OVERLAY",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_m5['pmax_bearish']),
                "detail": f"QUANTUM MAX & VELOCITY: {'BEARISH TREND ✅' if last_m5['pmax_bearish'] else 'WAITING QUANTUM BEARISH ⏳'}",
                "target_info": f"Quantum Stop: ${last_m5['pmax']:.2f}"
            },
            {
                "id": "sell_mannu_matrix",
                "name": "6. ALPHA MATRIX SIGNAL",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_m5['mannu_matrix_bearish']),
                "detail": f"ALPHA MATRIX: {'BEARISH SIGNAL ✅' if last_m5['mannu_matrix_bearish'] else 'WAITING ALPHA BEARISH ⏳'}",
                "target_info": "Alpha Matrix Bearish Confirmation"
            },
            {
                "id": "sell_mtf_sr_rule",
                "name": "7. support-and-resistance-mtf2 Fractal Zone Confluence",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(pd.notna(last_m5.get('mtf_res_zone')) and curr_price <= last_m5.get('mtf_res_zone', 999999)),
                "detail": f"MTF Fractal S&R: {'AT VALID FRACTAL RESISTANCE ZONE ✅' if (pd.notna(last_m5.get('mtf_res_zone')) and curr_price <= last_m5.get('mtf_res_zone', 999999)) else 'WAITING FRACTAL RESISTANCE ⏳'}",
                "target_info": f"Fractal Resistance: ${last_m5.get('mtf_res_zone', 0):.2f}"
            },
            {
                "id": "sell_gold999_rule",
                "name": "8. GOLD999D1 Daily Range Expansion Target Alignment",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(curr_price > (curr_price - (last_m5.get('atr', 2.0) * 3.0))),
                "detail": "GOLD999D1 Expansion: BEARISH DAILY EXPANSION TARGETS ACTIVE ✅",
                "target_info": "Daily Range Expansion TP Targets Active"
            },
            {
                "id": "sell_fibopiv_rule",
                "name": "9. FiboPiv_v3 Fibonacci Pivot Golden Ratio Confluence",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(pd.notna(last_m5.get('fibo_pivot')) and curr_price <= (last_m5.get('fibo_pivot', 0) + last_m5.get('atr', 2.0))),
                "detail": f"FiboPiv_v3: {'BELOW FIBONACCI PIVOT GOLDEN ZONE ✅' if (pd.notna(last_m5.get('fibo_pivot')) and curr_price <= (last_m5.get('fibo_pivot', 0) + last_m5.get('atr', 2.0))) else 'WAITING FIBO PIVOT RECOVERY ⏳'}",
                "target_info": f"Fibo Pivot: ${last_m5.get('fibo_pivot', 0):.2f}"
            },
            {
                "id": "sell_gann_sq9_rule",
                "name": "10. Gann_SQ9_2 W.D. Gann Square of 9 M1 Matrix Confluence",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(pd.notna(last_h1.get('gann_sq9_90_up')) and curr_price <= last_h1.get('gann_sq9_90_up', 999999)),
                "detail": f"Gann SQ9 Matrix (M1): {'BELOW GANN 90-DEGREE M1 RESISTANCE MATRIX ✅' if (pd.notna(last_h1.get('gann_sq9_90_up')) and curr_price <= last_h1.get('gann_sq9_90_up', 999999)) else 'WAITING M1 GANN MATRIX ⏳'}",
                "target_info": f"Gann 90° Level (M1): ${last_h1.get('gann_sq9_90_up', 0):.2f}"
            },

            {
                "id": "sell_xps_fib_rule",
                "name": "11. !XPS AUTO FIB.ex4 M1 Golden Zone Retest (38.2% - 61.8%)",
                "category": "🔴 MAIN MANDATORY",
                "matched": bool(last_h1.get('fib_golden_zone_sell', False) or (pd.notna(last_h1.get('fib_382')) and curr_price >= last_h1.get('fib_382', 0) and curr_price <= last_h1.get('fib_618', 999999))),
                "detail": f"!XPS AUTO FIB (M1): {'AT M1 GOLDEN RETRACEMENT ZONE (38.2% - 61.8%) ✅' if (last_h1.get('fib_golden_zone_sell', False) or (pd.notna(last_h1.get('fib_382')) and curr_price >= last_h1.get('fib_382', 0) and curr_price <= last_h1.get('fib_618', 999999))) else 'WAITING M1 FIB GOLDEN RETRACEMENT ⏳'}",
                "target_info": "M1 Golden Zone Retracement (38.2% - 61.8%)"
            },
            {
                "id": "sell_premium_res",
                "name": "12. Premium Zone Retest (Equilibrium)",

                "category": "🟡 CORE ZONE",
                "matched": bool(is_premium),
                "detail": f"Zone: {'PREMIUM ZONE (ABOVE EQUILIBRIUM) ✅' if is_premium else 'WAITING PREMIUM ZONE ⏳'}",
                "target_info": f"Resistance: ${last_m5['resistance']:.2f} | Eq: ${eq_price:.2f}"
            },
            {
                "id": "sell_bsl_sweep",
                "name": "13. BSL Liquidity Sweep (Inducement)",
                "category": "🟡 CORE ZONE",
                "matched": bool(bsl_sweep),
                "detail": f"Liquidity: {'BSL SWEEP COMPLETED ✅' if bsl_sweep else 'WAITING BSL SWEEP ⏳'}",
                "target_info": f"Sweep Target: ${h1_lh:.2f}"
            },
            {
                "id": "sell_fvg",
                "name": "14. Fair Value Gap (FVG) Retest (M5 / M1)",
                "category": "🟡 CORE ZONE",
                "matched": bool(fvg_bearish_mtf),
                "detail": f"FVG Zone: {'BEARISH FVG ACTIVE (M5/M1) ✅' if fvg_bearish_mtf else 'WAITING BEARISH FVG ⏳'}",
                "target_info": f"FVG Imbalance Area: ~${curr_price:.2f}"
            },
            {
                "id": "sell_ob",
                "name": "15. Institutional Order Block (OB) Sweep (M5 / M1)",
                "category": "🟡 CORE ZONE",
                "matched": bool(ob_bearish_mtf),
                "detail": f"OB Zone: {'BEARISH ORDER BLOCK ACTIVE (M5/M1) ✅' if ob_bearish_mtf else 'WAITING BEARISH OB ⏳'}",
                "target_info": f"Supply Area: ~${curr_price:.2f}"
            },
            {
                "id": "sell_breaker_mit",
                "name": "16. Breaker / Mitigation Block Retest (M5 / M1)",
                "category": "🟡 CORE ZONE",
                "matched": bool(breaker_bearish_mtf),
                "detail": f"Block: {'BEARISH BREAKER / MITIGATION BLOCK ACTIVE (M5/M1) ✅' if breaker_bearish_mtf else 'WAITING BEARISH BREAKER ⏳'}",
                "target_info": "Institutional Order Mitigation"
            },

            {
                "id": "sell_supertrend_v",
                "name": "17. VELOCITY VOL-TREND",
                "category": "🔵 MOMENTUM CATALYST",
                "matched": bool(last_m5['supertrend_v_bearish']),
                "detail": f"VELOCITY VOL-TREND: {'BEARISH TREND ✅' if last_m5['supertrend_v_bearish'] else 'WAITING BEARISH VELOCITY ⏳'}",
                "target_info": "Volume-Weighted ATR Trend Filter"
            },
            {
                "id": "sell_macd",
                "name": "18. MACD Momentum Crossover (Bearish)",
                "category": "🔵 MOMENTUM CATALYST",
                "matched": bool(last_m5['macd_bearish']),
                "detail": f"MACD: {'BEARISH CROSSOVER ✅' if last_m5['macd_bearish'] else 'WAITING BEARISH MACD ⏳'}",
                "target_info": "MACD < Signal Line Confirmation"
            },
            {
                "id": "sell_vol",
                "name": "19. Institutional Volume & Bearish ATR Expansion",
                "category": "🔵 MOMENTUM CATALYST",
                "matched": bool((vol_spike or last_m5['atr_expansion']) and last_m5['close'] < last_m5['open']),
                "detail": f"Volume/ATR: {'BEARISH VOL EXPANSION 🚀' if ((vol_spike or last_m5['atr_expansion']) and last_m5['close'] < last_m5['open']) else 'WAITING BEARISH VOL ⏳'}",
                "target_info": "Smart Money Volatility Injection"
            },
            {
                "id": "sell_rsi",
                "name": "20. RSI Momentum Reversal (>55)",
                "category": "🔵 MOMENTUM CATALYST",
                "matched": bool(rsi_val > 55),
                "detail": f"RSI Status: {'OVERBOUGHT / REVERSAL ✅' if rsi_val > 55 else 'WAITING RSI > 55 ⏳'} (RSI: {rsi_val})",
                "target_info": "Sell Entry: RSI > 55"
            },
            {
                "id": "sell_sr_pivot",
                "name": "21. Dynamic Resistance & Central Pivot Level Confluence",
                "category": "🟡 CORE ZONE",
                "matched": bool(last_m5['near_resistance'] or (abs(curr_price - last_m5['pivot']) <= last_m5['atr'])),
                "detail": f"S/R & Pivot: {'AT KEY RESISTANCE / PIVOT ZONE ✅' if (last_m5['near_resistance'] or (abs(curr_price - last_m5['pivot']) <= last_m5['atr'])) else 'WAITING KEY RESISTANCE REJECTION ⏳'}",
                "target_info": f"Resistance: ${last_m5['resistance']:.2f} | Pivot: ${last_m5['pivot']:.2f}"
            }
        ]


        buy_matched = sum(1 for c in buy_checklist if c["matched"])
        buy_pct = round((buy_matched / len(buy_checklist)) * 100, 1)

        sell_matched = sum(1 for c in sell_checklist if c["matched"])
        sell_pct = round((sell_matched / len(sell_checklist)) * 100, 1)

        adx_val = round(float(last_m5.get('adx', 0)), 1) if pd.notna(last_m5.get('adx', 0)) else 0.0
        is_consolidation = bool(last_m5.get('is_consolidation', False))
        is_bb_squeeze = bool(last_m5.get('is_bb_squeeze', False))
        ema_flat = bool(last_m5.get('ema_flat', False))
        reasons = []
        if adx_val < 22:
            reasons.append(f"ADX {adx_val} < 22")
        if is_bb_squeeze:
            reasons.append("BB Squeeze")
        if ema_flat:
            reasons.append("EMA Flat")
        regime = "RANGING" if is_consolidation else "SMOOTH"
        regime_detail = (" + ".join(reasons) + " → trades blocked") if is_consolidation else f"ADX {adx_val} trending — entries allowed"

        return {
            "buy_checklist": buy_checklist,
            "buy_matched_count": buy_matched,
            "buy_total_count": len(buy_checklist),
            "buy_percentage": buy_pct,
            "sell_checklist": sell_checklist,
            "sell_matched_count": sell_matched,
            "sell_total_count": len(sell_checklist),
            "sell_percentage": sell_pct,
            "market_regime": {
                "regime": regime,
                "is_ranging": is_consolidation,
                "adx": adx_val,
                "bb_squeeze": is_bb_squeeze,
                "ema_flat": ema_flat,
                "detail": regime_detail
            },
            "structure_levels": {
                "h1_high": round(float(h1_lh), 2),
                "bull_bos": round(float(h1_lh), 2),
                "m15_high": round(float(m15_lh), 2),
                "eq_val": round(float(eq_price), 2),
                "m15_low": round(float(m15_ll), 2),
                "bear_choch": round(float(m15_ll), 2),
                "h1_low": round(float(h1_ll), 2),
                "discount_val": round(float(eq_price), 2)
            },
            "price_targets": {
                "buy_trigger_price": buy_trigger,
                "buy_sl": buy_sl,
                "buy_tp": buy_tp,
                "sell_trigger_price": sell_trigger,
                "sell_sl": sell_sl,
                "sell_tp": sell_tp
            }
        }
