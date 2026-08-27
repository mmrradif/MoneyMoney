import time
import sys
import os
import logging
import threading
import pandas as pd
from flask import Flask, render_template, jsonify, make_response, request
from flask_cors import CORS
import MetaTrader5 as mt5
import config
from daily_logger import setup_daily_logger
from mt5_interface import MT5Interface
from strategy import Strategy
import trade_tracker
import smc_recovery
from candlestick_patterns import scan_h1_and_m5, h1_m5_pattern_gate

# UTF-8 Logging Configuration for Windows Console & Daily Entry/Exit Logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
setup_daily_logger()


# Global State for HTML Dashboard
# Magic IDs: TP1 entry, TP2 mid (live-modified), TP3 runner (no broker TP-close), reverse stop glued to TP3 SL
MAGIC_MAIN_BUY = 100988
MAGIC_MAIN_SELL = 100989
MAGIC_REV_SELL = 100990   # SellStop at Buy TP3 SL
MAGIC_REV_BUY = 100991    # BuyStop at Sell TP3 SL
MAGIC_TP3_BUY = 100992
MAGIC_TP3_SELL = 100993
MAGIC_TP2_BUY = 100994
MAGIC_TP2_SELL = 100995
MAGIC_BREAKOUT_BUY = 100996
MAGIC_BREAKOUT_SELL = 100997
ENTRY_BUY_MAGICS = (MAGIC_MAIN_BUY, MAGIC_TP2_BUY, MAGIC_TP3_BUY, MAGIC_BREAKOUT_BUY, 100998, 100999)
ENTRY_SELL_MAGICS = (MAGIC_MAIN_SELL, MAGIC_TP2_SELL, MAGIC_TP3_SELL, MAGIC_BREAKOUT_SELL, 100900, 100901)
TP3_MAGICS = (MAGIC_TP3_BUY, MAGIC_TP3_SELL)
TP2_MAGICS = (MAGIC_TP2_BUY, MAGIC_TP2_SELL)
REV_MAGICS = (MAGIC_REV_SELL, MAGIC_REV_BUY)

bot_state = {
    "is_running": True,
    "account": {"login": None, "server": None, "name": None, "balance": 0.0, "equity": 0.0},
    "market": {"trend": "SCANNING", "rsi": 0.0, "atr": 0.0},
    "positions": [],
    "forecast": {"has_forecast": False, "signal": None, "symbol": "XAUUSD"},
    "checklist": {"checklist": [], "matched_count": 0, "total_count": 5, "percentage": 0.0},
    "symbol": "XAUUSD",
    "logs": [],
    "tp3_virtual_targets": {},
    "smc_recovery": {},
    "smc_recovery_ui": {},
    "m5_trend_status": {"c1": "—", "c2": "—", "c3": "NONE", "pmax": "—", "halftrend": "—", "dual": "MIXED", "candle": "NONE"},
    "candlestick_patterns": {
        "note": "Display only — no trade decision",
        "h1": {"timeframe": "H1", "zones": [], "patterns": [], "count": 0},
        "m5": {"timeframe": "M5", "zones": [], "patterns": [], "count": 0},
    },
}

def add_log(msg):
    try:
        logging.info(msg.encode('ascii', errors='ignore').decode('ascii'))
    except Exception:
        pass
    bot_state["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_state["logs"]) > 50:
        bot_state["logs"].pop(0)

if getattr(sys, 'frozen', False):
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    template_folder = os.path.join(base_dir, 'templates')
    app = Flask(__name__, template_folder=template_folder)
else:
    app = Flask(__name__, template_folder='templates')

app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
CORS(app)


from flask import make_response

@app.route('/')
def index():
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0, post-check=0, pre-check=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/api/status')
def get_status():
    try:
        if mt5.terminal_info() is None:
            mt5_client = MT5Interface()
            mt5_client.initialize()
            
        acc_info = mt5.account_info()
        if acc_info:
            acc_dict = {
                "login": acc_info.login,
                "server": acc_info.server,
                "name": acc_info.name,
                "balance": acc_info.balance,
                "equity": acc_info.equity,
                "currency": acc_info.currency
            }
            prev_acc = bot_state.get("account", {})
            if isinstance(prev_acc, dict) and "periodic_pnl" in prev_acc:
                acc_dict["periodic_pnl"] = prev_acc["periodic_pnl"]
            bot_state["account"] = acc_dict
            
        bot_state["closed_trades_history"] = trade_tracker.get_enriched_trade_history()
        
        curr_bal = acc_dict.get("balance", 1000.0) if 'acc_dict' in locals() else 1000.0
        bot_state["active_close_rules"] = {
            "enable_profit_rule": getattr(config, 'ENABLE_FLOATING_PROFIT_CAP', False),
            "max_profit_pct": getattr(config, 'FLOATING_MAX_PROFIT_PERCENT', 10.0),
            "enable_loss_rule": getattr(config, 'ENABLE_FLOATING_LOSS_CAP', True),
            "max_loss_pct": getattr(config, 'FLOATING_MAX_LOSS_PERCENT', 80.0) or 80.0,
            "profit_multiplier": getattr(config, 'SMC_RECOVERY_PROFIT_MULTIPLIER', 1.1),
            "base_lot": getattr(config, 'PENDING_BASE_LOT', 0.02),
            "max_daily_loss_pct": getattr(config, 'MAX_DAILY_LOSS_PERCENT', 50.0),
            "consecutive_loss_limit": getattr(config, 'CONSECUTIVE_LOSS_LIMIT', 9),
            "pending_9_loss_active": trade_tracker.is_circuit_breaker_active(),
            "daily_10pct_drawdown_hit": trade_tracker.is_daily_drawdown_limit_reached(curr_bal),
            "today_net_pnl": trade_tracker.get_today_net_pnl(),
            "pending_next_lot": trade_tracker.get_next_pending_lot(),
            "pending_mode": getattr(config, 'PENDING_MODE', 'PMAX_RECOVERY'),
            "h1_confirm_closed": bool(getattr(config, 'H1_CONFIRM_CLOSED', True)),
            "m5_confirm_closed": bool(getattr(config, 'M5_CONFIRM_CLOSED', True)),
            "smc_recovery": bot_state.get("smc_recovery_ui") or {},
            "m5_trend": bot_state.get("m5_trend_status") or {},
            **trade_tracker.get_lot_status()
        }

        mt5_client = MT5Interface()
        mt5_client.connected = True  # reuse already-initialized terminal in this process
        is_open, mkt_msg = mt5_client.is_market_open(config.SYMBOL)
        bot_state["market_open_status"] = {"is_open": is_open, "msg": mkt_msg}

        # Real MT5 AutoTrading (Algo Trading) button — separate from in-app ENGINE pause
        algo_on, algo_msg = mt5_client.get_algo_trading_status()
        bot_state["mt5_algo_trading"] = {"enabled": algo_on, "msg": algo_msg}

        # Dual-engine enable flags + live effective status for UI ON/OFF badges
        pending_enabled = bool(getattr(config, 'ENABLE_PENDING_ENGINE', True))
        regular_enabled = bool(getattr(config, 'ENABLE_REGULAR_ENGINE', True))
        pending_9 = bot_state["active_close_rules"]["pending_9_loss_active"]
        bot_running = bool(bot_state.get("is_running", True))
        regime = (bot_state.get("market_regime") or {})
        is_ranging = bool(regime.get("is_ranging", False))
        bot_state["engine_modes"] = {
            "pending_enabled": pending_enabled,
            "regular_enabled": regular_enabled,
            "pending_status": (
                "OFF" if not pending_enabled else
                "STANDBY" if not is_open else
                "PAUSED" if not bot_running else
                "SUSPENDED" if pending_9 else
                "BREAKOUT" if is_ranging else
                "STRUCTURE"
            ),
            "regular_status": (
                "OFF" if not regular_enabled else
                "STANDBY" if not is_open else
                "PAUSED" if not bot_running else
                "BLOCKED" if is_ranging else
                "ON"
            ),
            "block_reason": regime.get("detail") if is_ranging else None
        }

        # Calculate current dynamic Lot Size for display in manual order popups
        try:
            h1_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, 250)
            m5_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, 250)
            if h1_rates is not None and not h1_rates.empty and m5_rates is not None and not m5_rates.empty:
                h1_calc = Strategy.calculate_indicators(h1_rates)
                m5_calc = Strategy.calculate_indicators(m5_rates)
                bot_state["m5_trend_status"] = Strategy.m5_hit_confirm_status(h1_calc, m5_calc)
                bot_state["candlestick_patterns"] = scan_h1_and_m5(h1_calc, m5_calc, config.SYMBOL)
                info = mt5.symbol_info(config.SYMBOL)
                price = info.ask if (info and info.ask > 0) else 0.0
                if price > 0:
                    sl_price, _ = Strategy.calculate_manual_smc_sl_tp(h1_calc, m5_calc, 'BUY', price)
                    sl_distance = max(0.01, abs(price - sl_price))
                    bot_state["calculated_lot"] = mt5_client.calculate_lot_size(config.SYMBOL, sl_distance)
                else:
                    bot_state["calculated_lot"] = 0.01
            else:
                bot_state["calculated_lot"] = 0.01
        except Exception:
            bot_state["calculated_lot"] = 0.01

    except Exception as e:
        logging.error(f"Error fetching trade status: {e}")
    resp = make_response(jsonify(bot_state))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/api/set_symbol', methods=['POST'])
def set_symbol():
    try:
        req_data = request.get_json(silent=True) or {}
        new_symbol = req_data.get('symbol', 'XAUUSD').upper()
        
        valid_symbol = None
        for sym in [new_symbol, new_symbol + 'm', new_symbol + 'c', new_symbol + 'k', new_symbol + 't', new_symbol + 'tm', new_symbol + '_i']:
            if mt5.symbol_select(sym, True):
                info = mt5.symbol_info(sym)
                if info is not None:
                    valid_symbol = sym
                    break
                    
        if not valid_symbol:
            all_symbols = mt5.symbols_get()
            if all_symbols:
                for s in all_symbols:
                    if new_symbol in s.name.upper():
                        if mt5.symbol_select(s.name, True):
                            valid_symbol = s.name
                            break

        if not valid_symbol:
            valid_symbol = new_symbol
            mt5.symbol_select(valid_symbol, True)

        config.SYMBOL = valid_symbol
        config.save_user_settings()
        bot_state["symbol"] = new_symbol
        bot_state["active_symbol"] = valid_symbol
        bot_state["last_executed_signal"] = None

        # Pre-fetch MT5 rates immediately to prime chart and checklist
        mt5_client = MT5Interface()
        h1_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, num_bars=250)
        m15_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, num_bars=250)
        m5_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, num_bars=250)

        if h1_rates is not None and m15_rates is not None and m5_rates is not None:
            forecast = Strategy.check_upcoming_forecast(h1_rates, m15_rates, m5_rates)
            waiting_reason = Strategy.get_waiting_reason(h1_rates, m15_rates, m5_rates)
            checklist_info = Strategy.get_checklist_status(h1_rates, m15_rates, m5_rates)
            checklist_info["active_signal"] = None
            forecast["waiting_reason"] = waiting_reason
            bot_state["forecast"] = forecast
            bot_state["checklist"] = checklist_info
            h1_calc = Strategy.calculate_indicators(h1_rates)
            m5_calc = Strategy.calculate_indicators(m5_rates)
            bot_state["candlestick_patterns"] = scan_h1_and_m5(h1_calc, m5_calc, valid_symbol)
            bot_state["m5_trend_status"] = Strategy.m5_hit_confirm_status(h1_calc, m5_calc)

        add_log(f"🔀 Active trading pair switched to: {valid_symbol}")
        return jsonify({"status": "success", "symbol": valid_symbol})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/shutdown', methods=['POST'])
def shutdown_bot_app():
    import os
    add_log("🛑 Shutdown request received from Web Dashboard. Closing Bot Engine...")
    def _exit_later():
        time.sleep(1)
        os._exit(0)
    threading.Thread(target=_exit_later, daemon=True).start()
    return jsonify({"status": "success", "message": "Bot Engine is shutting down..."})

@app.route('/api/candles')
def get_candles():
    try:
        mt5_client = MT5Interface()
        valid_symbol = None
        for sym in [config.SYMBOL, config.SYMBOL + 'm', config.SYMBOL + 'c', config.SYMBOL + 'k']:
            if mt5.symbol_select(sym, True):
                info = mt5.symbol_info(sym)
                if info is not None:
                    valid_symbol = sym
                    break
        if not valid_symbol:
            valid_symbol = config.SYMBOL

        rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, num_bars=100)
        if rates is None or rates.empty:
            return jsonify({"status": "error", "message": "Failed to fetch rates"}), 400
        
        df = Strategy.calculate_indicators(rates)
        
        # Calculate EMA 50 & EMA 200 for chart drawing
        import ta
        df['ema_fast'] = ta.trend.ema_indicator(df['close'], window=50)
        df['ema_slow'] = ta.trend.ema_indicator(df['close'], window=200)

        candles = []
        smc_zones = []

        for i in range(len(df)):
            row = df.iloc[i]
            # Convert time to Unix timestamp integer
            t = int(row['time'].timestamp()) if hasattr(row['time'], 'timestamp') else (int(row['time']) if 'time' in row else i)
            candles.append({
                "time": t,
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row['tick_volume']) if 'tick_volume' in row else float(row.get('real_volume', 0)),
                "ema50": float(row['ema_fast']) if pd.notna(row['ema_fast']) else None,
                "ema200": float(row['ema_slow']) if pd.notna(row['ema_slow']) else None
            })

            # Detect Order Blocks & FVG for drawing on chart
            if i >= 2:
                if row.get('fvg_bullish', False):
                    smc_zones.append({
                        "type": "FVG_BULLISH",
                        "time": t,
                        "price_low": float(df.iloc[i-2]['high']),
                        "price_high": float(row['low']),
                        "label": "Bullish FVG Zone"
                    })
                elif row.get('fvg_bearish', False):
                    smc_zones.append({
                        "type": "FVG_BEARISH",
                        "time": t,
                        "price_low": float(row['high']),
                        "price_high": float(df.iloc[i-2]['low']),
                        "label": "Bearish FVG Zone"
                    })

            if row.get('bos_bullish', False):
                smc_zones.append({
                    "type": "BOS_BULLISH",
                    "time": t,
                    "price": float(row['close']),
                    "label": "BOS Breakout ▲"
                })
            elif row.get('bos_bearish', False):
                smc_zones.append({
                    "type": "CHoCH_BEARISH",
                    "time": t,
                    "price": float(row['close']),
                    "label": "CHoCH Reversal ▼"
                })

        # Latest XPS Auto Fibonacci levels calculation for current chart view
        last_row = df.iloc[-1]
        fib_levels = {
            "0.000": float(last_row['fib_000']),
            "0.236": float(last_row['fib_236']),
            "0.382": float(last_row['fib_382']),
            "0.500": float(last_row['fib_500']),
            "0.618": float(last_row['fib_618']),
            "0.786": float(last_row['fib_786']),
            "1.000": float(last_row['fib_1000']),
            "1.618": float(last_row['fib_1618'])
        }

        h1_high = float(df['high'].max())
        h1_low = float(df['low'].min())
        m15_high = float(df['high'].tail(20).max())
        m15_low = float(df['low'].tail(20).min())
        eq_val = (h1_high + h1_low) / 2.0

        structure_levels = {
            "h1_high": h1_high,
            "bull_bos": h1_high,
            "m15_high": m15_high,
            "eq_val": eq_val,
            "m15_low": m15_low,
            "bear_choch": m15_low,
            "h1_low": h1_low,
            "discount_val": eq_val
        }

        return jsonify({
            "status": "success",
            "symbol": valid_symbol,
            "candles": candles,
            "smc_zones": smc_zones,
            "fib_levels": fib_levels,
            "structure_levels": structure_levels
        })
    except Exception as e:
        import traceback
        logging.error(f"Error in get_candles: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/order/buy', methods=['POST'])
def manual_buy():
    try:
        mt5_client = MT5Interface()
        req_data = request.get_json(silent=True) or {}
        order_count = int(req_data.get('count', 1))
        order_count = max(1, min(100, order_count))
        
        # Dynamically use currently selected pair from bot_state or config
        target_sym = req_data.get('symbol') or bot_state.get('active_symbol') or config.SYMBOL
        valid_symbol = None
        for sym in [target_sym, target_sym + 'm', target_sym + 'c', target_sym + 'k']:
            if mt5.symbol_select(sym, True):
                info = mt5.symbol_info(sym)
                if info is not None:
                    valid_symbol = sym
                    break
                    
        if valid_symbol is None:
            err_msg = f"Symbol '{target_sym}' not found on broker account. Please check Market Watch."
            add_log(f"[ERROR] {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 400
        
        h1_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, 250)
        m5_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, 250)
        if h1_rates is None or h1_rates.empty or m5_rates is None or m5_rates.empty:
            err_msg = f"Failed to fetch market rates for {valid_symbol}"
            add_log(f"[ERROR] {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 400
            
        symbol_info = mt5.symbol_info(valid_symbol)
        h1_calc = Strategy.calculate_indicators(h1_rates)
        m5_calc = Strategy.calculate_indicators(m5_rates)
        price = symbol_info.ask
        
        sl_price, tp_price = Strategy.calculate_manual_smc_sl_tp(h1_calc, m5_calc, 'BUY', price)

        # Inherit SL & TP from existing active bot BUY positions if running
        existing_pos = mt5_client.get_open_positions()
        same_side_pos = [p for p in (existing_pos or []) if "XAU" in str(getattr(p, 'symbol', '')).upper() and p.type == mt5.POSITION_TYPE_BUY]
        if same_side_pos:
            bot_sl = [float(p.sl) for p in same_side_pos if float(getattr(p, 'sl', 0) or 0) > 0]
            bot_tp = [float(p.tp) for p in same_side_pos if float(getattr(p, 'tp', 0) or 0) > 0]
            if bot_sl:
                sl_price = bot_sl[0]
            if bot_tp:
                tp_price = bot_tp[0]

        manual_lot = req_data.get('lot')
        if manual_lot is not None and float(manual_lot) > 0:
            lot_size = round(float(manual_lot), 2)
        else:
            # Isolated manual: user lot only — never scale from bot recovery / pending losses
            lot_size = round(float(getattr(config, 'PENDING_BASE_LOT', 0.02)), 2)

        executed_orders = []
        for i in range(order_count):
            res_buy = mt5_client.open_order(valid_symbol, mt5.ORDER_TYPE_BUY, lot_size, sl_price, tp_price, magic=smc_recovery.MAGIC_MANUAL)
            if res_buy:
                ticket = res_buy.order if hasattr(res_buy, 'order') else 0
                open_reason = f"Manual BUY (isolated) on {valid_symbol} | Order {i+1}/{order_count} | SL: ${sl_price:.2f} | TP: ${tp_price:.2f}"
                trade_tracker.record_open_trade(ticket, valid_symbol, "BUY", lot_size, price, sl_price, tp_price, open_reason, ["Manual Dashboard Trigger", "ISOLATED_FROM_BOT"])
                executed_orders.append(ticket)
        
        if executed_orders:
            msg = f"Instant BUY ({len(executed_orders)} Orders on {valid_symbol}) Executed! Price: {price} | SL: ${sl_price} | TP: ${tp_price} | Lot: {lot_size}"
            add_log(f"[MANUAL BUY] 🚀 {msg}")
            return jsonify({"status": "success", "message": msg, "orders": executed_orders})
        else:
            err_msg = f"Broker rejected BUY orders for {valid_symbol}. Check margin/balance."
            add_log(f"[ERROR] {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 450
    except Exception as e:
        err_msg = f"System Error: {str(e)}"
        add_log(f"[ERROR] {err_msg}")
        return jsonify({"status": "error", "message": err_msg}), 500

@app.route('/api/order/sell', methods=['POST'])
def manual_sell():
    try:
        mt5_client = MT5Interface()
        req_data = request.get_json(silent=True) or {}
        order_count = int(req_data.get('count', 1))
        order_count = max(1, min(100, order_count))
        
        # Dynamically use currently selected pair from bot_state or config
        target_sym = req_data.get('symbol') or bot_state.get('active_symbol') or config.SYMBOL
        valid_symbol = None
        for sym in [target_sym, target_sym + 'm', target_sym + 'c', target_sym + 'k']:
            if mt5.symbol_select(sym, True):
                info = mt5.symbol_info(sym)
                if info is not None:
                    valid_symbol = sym
                    break
                    
        if valid_symbol is None:
            err_msg = f"Symbol '{target_sym}' not found on broker account. Please check Market Watch."
            add_log(f"[ERROR] {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 400
        
        h1_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, 250)
        m5_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, 250)
        if h1_rates is None or h1_rates.empty or m5_rates is None or m5_rates.empty:
            err_msg = f"Failed to fetch market rates for {valid_symbol}"
            add_log(f"[ERROR] {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 400
            
        symbol_info = mt5.symbol_info(valid_symbol)
        h1_calc = Strategy.calculate_indicators(h1_rates)
        m5_calc = Strategy.calculate_indicators(m5_rates)
        price = symbol_info.bid
        
        sl_price, tp_price = Strategy.calculate_manual_smc_sl_tp(h1_calc, m5_calc, 'SELL', price)

        # Inherit SL & TP from existing active bot SELL positions if running
        existing_pos = mt5_client.get_open_positions()
        same_side_pos = [p for p in (existing_pos or []) if "XAU" in str(getattr(p, 'symbol', '')).upper() and p.type == mt5.POSITION_TYPE_SELL]
        if same_side_pos:
            bot_sl = [float(p.sl) for p in same_side_pos if float(getattr(p, 'sl', 0) or 0) > 0]
            bot_tp = [float(p.tp) for p in same_side_pos if float(getattr(p, 'tp', 0) or 0) > 0]
            if bot_sl:
                sl_price = bot_sl[0]
            if bot_tp:
                tp_price = bot_tp[0]

        manual_lot = req_data.get('lot')
        if manual_lot is not None and float(manual_lot) > 0:
            lot_size = round(float(manual_lot), 2)
        else:
            # Isolated manual: user lot only — never scale from bot recovery / pending losses
            lot_size = round(float(getattr(config, 'PENDING_BASE_LOT', 0.02)), 2)
        
        executed_orders = []
        for i in range(order_count):
            res_sell = mt5_client.open_order(valid_symbol, mt5.ORDER_TYPE_SELL, lot_size, sl_price, tp_price, magic=smc_recovery.MAGIC_MANUAL)
            if res_sell:
                ticket = res_sell.order if hasattr(res_sell, 'order') else 0
                open_reason = f"Manual SELL (isolated) on {valid_symbol} | Order {i+1}/{order_count} | SL: ${sl_price:.2f} | TP: ${tp_price:.2f}"
                trade_tracker.record_open_trade(ticket, valid_symbol, "SELL", lot_size, price, sl_price, tp_price, open_reason, ["Manual Dashboard Trigger", "ISOLATED_FROM_BOT"])
                executed_orders.append(ticket)
        
        if executed_orders:
            msg = f"Instant SELL ({len(executed_orders)} Orders on {valid_symbol}) Executed! Price: {price} | SL: ${sl_price} | TP: ${tp_price} | Lot: {lot_size}"
            add_log(f"[MANUAL SELL] 🚀 {msg}")
            return jsonify({"status": "success", "message": msg, "orders": executed_orders})
        else:
            err_msg = f"Broker rejected SELL orders for {valid_symbol}. Check margin/balance."
            add_log(f"[ERROR] {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 450
    except Exception as e:
        err_msg = f"System Error: {str(e)}"
        add_log(f"[ERROR] {err_msg}")
        return jsonify({"status": "error", "message": err_msg}), 500

@app.route('/api/config/close_rules', methods=['POST'])
def set_close_rules():
    try:
        req_data = request.get_json(silent=True) or {}

        if req_data.get('base_lot') is not None and float(req_data.get('base_lot')) > 0:
            lot_val = round(float(req_data.get('base_lot')), 2)
            config.PENDING_BASE_LOT = lot_val

        if 'enable_profit_rule' in req_data:
            config.ENABLE_FLOATING_PROFIT_CAP = bool(req_data.get('enable_profit_rule'))
        if req_data.get('max_profit_pct') is not None and float(req_data.get('max_profit_pct')) > 0:
            config.FLOATING_MAX_PROFIT_PERCENT = float(req_data.get('max_profit_pct'))

        if req_data.get('profit_multiplier') is not None and float(req_data.get('profit_multiplier')) > 0:
            mult_val = float(req_data.get('profit_multiplier'))
            config.SMC_RECOVERY_PROFIT_MULTIPLIER = mult_val
            config.REWARD_TO_RISK_RATIO = mult_val
            config.REWARD_RISK_RATIO = mult_val

        # Loss shield: UI-only — set positive % to enable; disable/0/null removes static value
        if 'enable_loss_rule' in req_data or 'max_loss_pct' in req_data:
            enable_loss = bool(req_data.get('enable_loss_rule', True))
            max_loss_pct = req_data.get('max_loss_pct', None)
            if (not enable_loss) or max_loss_pct is None or float(max_loss_pct) <= 0:
                config.ENABLE_FLOATING_LOSS_CAP = False
                config.FLOATING_MAX_LOSS_PERCENT = None
            else:
                config.ENABLE_FLOATING_LOSS_CAP = True
                config.FLOATING_MAX_LOSS_PERCENT = float(max_loss_pct)

        config.save_user_settings()

        rules = bot_state.get("active_close_rules") or {}
        rules["base_lot"] = config.PENDING_BASE_LOT
        rules["pending_base_lot"] = config.PENDING_BASE_LOT
        rules["enable_profit_rule"] = config.ENABLE_FLOATING_PROFIT_CAP
        rules["enable_loss_rule"] = config.ENABLE_FLOATING_LOSS_CAP
        rules["max_profit_pct"] = config.FLOATING_MAX_PROFIT_PERCENT
        rules["max_loss_pct"] = config.FLOATING_MAX_LOSS_PERCENT
        rules["profit_multiplier"] = config.SMC_RECOVERY_PROFIT_MULTIPLIER
        bot_state["active_close_rules"] = rules

        profit_status = f"+{config.FLOATING_MAX_PROFIT_PERCENT}%" if config.ENABLE_FLOATING_PROFIT_CAP else "DISABLED ❌"
        loss_pct = getattr(config, 'FLOATING_MAX_LOSS_PERCENT', None)
        loss_status = f"-{loss_pct}%" if config.ENABLE_FLOATING_LOSS_CAP and loss_pct else "DISABLED ❌"
        mult_status = f"{config.SMC_RECOVERY_PROFIT_MULTIPLIER}x"
        base_lot_status = f"{config.PENDING_BASE_LOT} Lot"

        add_log(f"⚙️ CUSTOM RULES UPDATED: Base Lot = {base_lot_status} | Profit Cap = {profit_status} | Loss Shield = {loss_status} | Target = {mult_status}")
        return jsonify({
            "status": "success",
            "message": f"Settings updated! Base Lot: {base_lot_status}, Profit Target: {mult_status}, Loss Shield: {loss_status}",
            "base_lot": config.PENDING_BASE_LOT,
            "enable_profit_rule": config.ENABLE_FLOATING_PROFIT_CAP,
            "enable_loss_rule": config.ENABLE_FLOATING_LOSS_CAP,
            "max_profit_pct": config.FLOATING_MAX_PROFIT_PERCENT,
            "max_loss_pct": config.FLOATING_MAX_LOSS_PERCENT,
            "profit_multiplier": config.SMC_RECOVERY_PROFIT_MULTIPLIER
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/clear_close_rules', methods=['POST'])
def clear_close_rules():
    try:
        config.ENABLE_FLOATING_PROFIT_CAP = False
        config.ENABLE_FLOATING_LOSS_CAP = False
        config.FLOATING_MAX_LOSS_PERCENT = None  # remove UI-set loss % (not static)
        config.save_user_settings()
        bot_state["is_running"] = True
        bot_state["bot_status_msg"] = "RUNNING"
        # Immediately sync UI state so Floating Profit Cap cannot stay green after clear
        rules = bot_state.get("active_close_rules") or {}
        rules["enable_profit_rule"] = False
        rules["enable_loss_rule"] = False
        rules["max_loss_pct"] = 0
        bot_state["active_close_rules"] = rules
        add_log("🧹 CLOSE RULES CLEARED & BOT RE-ENABLED: Floating Profit/Loss Caps DISABLED. Bot active!")
        return jsonify({
            "status": "success",
            "message": "Close rules cleared — Floating Profit/Loss Caps DISABLED!",
            "enable_profit_rule": False,
            "enable_loss_rule": False,
            "max_loss_pct": None
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/config/candle_mode', methods=['POST'])
def set_candle_mode():
    """
    Toggle candle confirmation mode:
    - H1 is fixed LIVE (not configurable from UI)
    - M5_CONFIRM_CLOSED default True; False means live M5 candle mode
    """
    try:
        req_data = request.get_json(silent=True) or {}
        # H1 mode is fixed to LIVE by user requirement.
        config.H1_CONFIRM_CLOSED = False
        if 'm5_confirm_closed' in req_data:
            config.M5_CONFIRM_CLOSED = bool(req_data.get('m5_confirm_closed'))
        config.save_user_settings()

        h1_mode = "LIVE"
        m5_mode = "CLOSED" if bool(getattr(config, 'M5_CONFIRM_CLOSED', True)) else "LIVE"
        add_log(f"🕯️ CANDLE MODE UPDATED: H1={h1_mode} | M5={m5_mode}")
        return jsonify({
            "status": "success",
            "h1_confirm_closed": False,
            "m5_confirm_closed": bool(getattr(config, 'M5_CONFIRM_CLOSED', True)),
            "message": f"Candle mode updated: H1={h1_mode}, M5={m5_mode}"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/toggle_engine', methods=['POST'])
def toggle_engine():
    """Toggle Pending or Regular engine ON/OFF independently."""
    try:
        req_data = request.get_json(silent=True) or {}
        engine = str(req_data.get('engine', '')).lower()
        if engine == 'pending':
            config.ENABLE_PENDING_ENGINE = not bool(getattr(config, 'ENABLE_PENDING_ENGINE', True))
            state = "ON" if config.ENABLE_PENDING_ENGINE else "OFF"
            config.save_user_settings()
            add_log(f"🎯 PENDING ORDER ENGINE toggled → {state}")
            return jsonify({"status": "success", "engine": "pending", "enabled": config.ENABLE_PENDING_ENGINE})
        if engine == 'regular':
            config.ENABLE_REGULAR_ENGINE = not bool(getattr(config, 'ENABLE_REGULAR_ENGINE', True))
            state = "ON" if config.ENABLE_REGULAR_ENGINE else "OFF"
            config.save_user_settings()
            add_log(f"⚡ REGULAR INSTANT ENGINE toggled → {state}")
            return jsonify({"status": "success", "engine": "regular", "enabled": config.ENABLE_REGULAR_ENGINE})
        return jsonify({"status": "error", "message": "engine must be 'pending' or 'regular'"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/reset_circuit_breaker', methods=['POST'])
def reset_circuit_breaker():
    try:
        bot_state["is_running"] = True
        bot_state["bot_status_msg"] = "RUNNING"
        add_log("🔄 CIRCUIT BREAKER RESET: Daily 10% Drawdown & 9-Loss limits cleared! Engines reactivated.")
        return jsonify({"status": "success", "message": "Circuit breaker reset! All engines reactivated."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/order/pending', methods=['POST'])
def place_pending():
    try:
        mt5_client = MT5Interface()
        req_data = request.get_json(silent=True) or {}
        order_type_str = req_data.get('type', 'BUY_STOP').upper()
        target_sym = req_data.get('symbol') or bot_state.get('active_symbol') or config.SYMBOL

        valid_symbol = None
        for sym in [target_sym, target_sym + 'm', target_sym + 'c', target_sym + 'k']:
            if mt5.symbol_select(sym, True):
                info = mt5.symbol_info(sym)
                if info is not None:
                    valid_symbol = sym
                    break

        if valid_symbol is None:
            valid_symbol = config.SYMBOL

        h1_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, num_bars=100)
        m15_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, num_bars=100)
        m5_rates = mt5_client.fetch_rates(valid_symbol, mt5.TIMEFRAME_M1, num_bars=100)

        if m5_rates is None or len(m5_rates) == 0:
            return jsonify({"status": "error", "message": f"Failed to fetch market rates for {valid_symbol}"}), 400

        m5_calc = Strategy.calculate_indicators(m5_rates)
        curr_price = float(m5_calc.iloc[-1]['close'])
        atr_pips = float(m5_calc.iloc[-1]['atr']) if 'atr' in m5_calc.iloc[-1] and pd.notna(m5_calc.iloc[-1]['atr']) else 10.0

        min_sl_dist = atr_pips * 2.0
        if "XAU" in valid_symbol: min_sl_dist = max(min_sl_dist, 5.0)
        elif "BTC" in valid_symbol: min_sl_dist = max(min_sl_dist, 350.0)

        if order_type_str == 'BUY_STOP':
            trigger_p = round(curr_price + (atr_pips * 1.0), 2)
            sl_p = round(trigger_p - min_sl_dist, 2)
            tp_p = round(trigger_p + (min_sl_dist * 2.2), 2)
        else:
            trigger_p = round(curr_price - (atr_pips * 1.0), 2)
            sl_p = round(trigger_p + min_sl_dist, 2)
            tp_p = round(trigger_p - (min_sl_dist * 2.2), 2)

        lot_size = mt5_client.calculate_lot_size(valid_symbol, min_sl_dist)
        res = mt5_client.place_pending_order(valid_symbol, order_type_str, lot_size, trigger_p, sl_p, tp_p, magic=777888)

        if res:
            msg = f"Pending {order_type_str} Order Placed! Trigger: ${trigger_p} | SL: ${sl_p} | TP: ${tp_p} | Lot: {lot_size}"
            add_log(f"[PENDING ORDER] ⚡ {msg}")
            return jsonify({"status": "success", "message": msg, "order_id": res.order})
        else:
            err_msg = f"Failed to place Pending {order_type_str} on MT5."
            add_log(f"[ERROR] {err_msg}")
            return jsonify({"status": "error", "message": err_msg}), 450
    except Exception as e:
        err_msg = f"Pending Order System Error: {str(e)}"
        add_log(f"[ERROR] {err_msg}")
        return jsonify({"status": "error", "message": err_msg}), 500

@app.route('/api/order/close_all', methods=['POST'])
def close_all():
    try:
        req_data = request.get_json(silent=True) or {}
        max_close = req_data.get('count', None)
        if max_close is not None:
            max_close = int(max_close)

        mt5_client = MT5Interface()
        positions = mt5_client.get_open_positions()
        if not positions:
            return jsonify({"status": "info", "message": "No open positions to close"})
        
        closed_count = 0
        for pos in positions:
            if max_close is not None and closed_count >= max_close:
                break
            symbol = pos.symbol
            ticket = pos.ticket
            pos_type = pos.type # 0 for BUY, 1 for SELL
            volume = pos.volume
            
            close_price = mt5.symbol_info_tick(symbol).bid if pos_type == 0 else mt5.symbol_info_tick(symbol).ask
            order_type = mt5.ORDER_TYPE_SELL if pos_type == 0 else mt5.ORDER_TYPE_BUY
            
            mt5_req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": order_type,
                "position": ticket,
                "price": close_price,
                "deviation": 20,
                "magic": getattr(config, 'MAGIC_NUMBER', 100988),
                "comment": "Manual Close All",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            res = mt5.order_send(mt5_req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                closed_count += 1
                trade_tracker.record_close_trade(ticket, close_price, "✋ Manual Close: Closed manually from Dashboard", "MANUAL", profit=getattr(pos, 'profit', 0.0))
                
        add_log(f"[MANUAL CLOSE ALL] Closed {closed_count} open position(s)")
        return jsonify({"status": "success", "message": f"Closed {closed_count} position(s)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/order/close_single', methods=['POST'])
def close_single():
    try:
        req_data = request.get_json(silent=True) or {}
        ticket = req_data.get('ticket', None)
        if not ticket:
            return jsonify({"status": "error", "message": "Ticket number missing"}), 400

        ticket = int(ticket)
        mt5_client = MT5Interface()
        positions = mt5_client.get_open_positions()
        if not positions:
            return jsonify({"status": "info", "message": "No open positions found"})

        target_pos = None
        for pos in positions:
            if pos.ticket == ticket:
                target_pos = pos
                break

        if not target_pos:
            return jsonify({"status": "error", "message": f"Position #{ticket} not found or already closed"}), 404

        symbol = target_pos.symbol
        pos_type = target_pos.type  # 0 for BUY, 1 for SELL
        volume = target_pos.volume

        close_price = mt5.symbol_info_tick(symbol).bid if pos_type == 0 else mt5.symbol_info_tick(symbol).ask
        order_type = mt5.ORDER_TYPE_SELL if pos_type == 0 else mt5.ORDER_TYPE_BUY

        mt5_req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "position": ticket,
            "price": close_price,
            "deviation": 20,
            "magic": getattr(config, 'MAGIC_NUMBER', 100988),
            "comment": f"Manual Close Single #{ticket}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(mt5_req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            trade_tracker.record_close_trade(ticket, close_price, f"✋ Manual Close: Single Trade #{ticket} closed from Dashboard", "MANUAL", profit=getattr(target_pos, 'profit', 0.0))
            add_log(f"[MANUAL CLOSE SINGLE] Position #{ticket} closed successfully")
            return jsonify({"status": "success", "message": f"Trade #{ticket} closed successfully!"})
        else:
            err_msg = res.comment if res else "Order send failed"
            return jsonify({"status": "error", "message": f"Failed to close Trade #{ticket}: {err_msg}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/order/close_buys', methods=['POST'])
def close_buys():
    try:
        req_data = request.get_json(silent=True) or {}
        max_close = req_data.get('count', None)
        if max_close is not None:
            max_close = int(max_close)

        mt5_client = MT5Interface()
        positions = mt5_client.get_open_positions()
        if not positions:
            return jsonify({"status": "info", "message": "No open positions to close"})
        
        closed_count = 0
        for pos in positions:
            if max_close is not None and closed_count >= max_close:
                break
            if pos.type == mt5.ORDER_TYPE_BUY:
                symbol = pos.symbol
                ticket = pos.ticket
                volume = pos.volume
                close_price = mt5.symbol_info_tick(symbol).bid
                mt5_req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": volume,
                    "type": mt5.ORDER_TYPE_SELL,
                    "position": ticket,
                    "price": close_price,
                    "deviation": 20,
                    "magic": getattr(config, 'MAGIC_NUMBER', 100988),
                    "comment": "Manual Close BUYs",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                res = mt5.order_send(mt5_req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    closed_count += 1
                    trade_tracker.record_close_trade(ticket, close_price, "✋ Manual Close: Closed BUY position manually from Dashboard", "MANUAL", profit=getattr(pos, 'profit', 0.0))
                
        add_log(f"[MANUAL CLOSE BUYS] Closed {closed_count} BUY position(s)")
        return jsonify({"status": "success", "message": f"Closed {closed_count} BUY position(s)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/order/close_sells', methods=['POST'])
def close_sells():
    try:
        req_data = request.get_json(silent=True) or {}
        max_close = req_data.get('count', None)
        if max_close is not None:
            max_close = int(max_close)

        mt5_client = MT5Interface()
        positions = mt5_client.get_open_positions()
        if not positions:
            return jsonify({"status": "info", "message": "No open positions to close"})
        
        closed_count = 0
        for pos in positions:
            if max_close is not None and closed_count >= max_close:
                break
            if pos.type == mt5.ORDER_TYPE_SELL:
                symbol = pos.symbol
                ticket = pos.ticket
                volume = pos.volume
                close_price = mt5.symbol_info_tick(symbol).ask
                mt5_req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": volume,
                    "type": mt5.ORDER_TYPE_BUY,
                    "position": ticket,
                    "price": close_price,
                    "deviation": 20,
                    "magic": getattr(config, 'MAGIC_NUMBER', 100988),
                    "comment": "Manual Close SELLs",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                res = mt5.order_send(mt5_req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    closed_count += 1
                    trade_tracker.record_close_trade(ticket, close_price, "✋ Manual Close: Closed SELL position manually from Dashboard", "MANUAL", profit=getattr(pos, 'profit', 0.0))
                
        add_log(f"[MANUAL CLOSE SELLS] Closed {closed_count} SELL position(s)")
        return jsonify({"status": "success", "message": f"Closed {closed_count} SELL position(s)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/toggle_bot', methods=['POST'])
def toggle_bot():
    bot_state["is_running"] = not bot_state.get("is_running", True)
    status_str = "STARTED" if bot_state["is_running"] else "STOPPED / PAUSED"
    add_log(f"[BOT CONTROL] Auto Trading Bot {status_str}")
    return jsonify({"success": True, "is_running": bot_state["is_running"], "status": status_str})

@app.route('/api/trigger_recovery', methods=['POST'])
def trigger_recovery():
    bot_state["is_recovery_mode"] = True
    current_rec = float(bot_state.get("pending_recovery_amount", 0.0))
    bot_state["pending_recovery_amount"] = max(10.0, current_rec if current_rec > 0 else 10.0)
    add_log("⚡ MANUAL RECOVERY SCALPER TRIGGERED: Instant Dual BUY+SELL 0.01 Scalper Engine engaged!")
    return jsonify({"status": "success", "message": "Dual Recovery Scalper Engine Activated!"})

@app.route('/api/set_pair', methods=['POST'])
def set_pair():
    from flask import request
    data = request.get_json()
    if data and "symbol" in data:
        config.SYMBOL = data["symbol"]
        config.save_user_settings()
        bot_state["symbol"] = data["symbol"]
        add_log(f"Pair switched to: {data['symbol']}")
        return jsonify({"success": True, "symbol": data["symbol"]})
    return jsonify({"success": False}), 400

def kill_process_on_port(port=8020):
    try:
        import os
        import subprocess
        cmd = f'netstat -ano | findstr :{port}'
        output = subprocess.check_output(cmd, shell=True).decode('utf-8', errors='ignore')
        my_pid = os.getpid()
        pids = set()
        for line in output.strip().splitlines():
            if 'LISTENING' in line:
                parts = line.strip().split()
                pid = parts[-1]
                if pid and pid.isdigit() and int(pid) != my_pid and int(pid) > 0:
                    pids.add(pid)
        for pid in pids:
            add_log(f"🧹 Killing previous process (PID {pid}) occupying port {port}...")
            subprocess.run(f'taskkill /F /PID {pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)
    except Exception:
        pass

def run_web_server():
    kill_process_on_port(8000)
    time.sleep(0.5)
    try:
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        add_log("🌐 Web Dashboard running on http://127.0.0.1:8020")
        app.run(host='0.0.0.0', port=8020, debug=False, use_reloader=False)
    except Exception as e:
        add_log(f"Port 8020 error: {e}")




def _collect_tp3_runners(positions):
    """TP3 magics ONLY. Never treat manual trades (magic == 0) or regular trades as TP3 runners."""
    runners = []
    for p in positions:
        if p.magic in TP3_MAGICS:
            runners.append(p)
    return runners

def ensure_tp3_reverse_stop(mt5_client, pos, digits):
    """Keep exactly one reverse stop glued to TP3 SL (SL + reverse always together)."""
    if not pos.sl or float(pos.sl) <= 0:
        return False

    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        return False

    is_buy = pos.type == mt5.POSITION_TYPE_BUY
    rev_price = round(float(pos.sl), digits)
    rev_sl = round(float(pos.price_open), digits)
    rev_vol = float(pos.volume)

    if is_buy:
        order_type = 'SELL_STOP'
        magic = MAGIC_REV_SELL
        if rev_price >= float(tick.bid):
            return False
    else:
        order_type = 'BUY_STOP'
        magic = MAGIC_REV_BUY
        if rev_price <= float(tick.ask):
            return False

    orders = [o for o in (mt5.orders_get(symbol=pos.symbol) or []) if int(getattr(o, 'magic', 0) or 0) == magic]
    keep = None
    for o in orders:
        price_ok = abs(float(o.price_open) - rev_price) <= 0.50
        sl_ok = abs(float(o.sl) - rev_sl) <= 0.50 if float(o.sl or 0) > 0 else False
        o_vol = float(getattr(o, 'volume_initial', getattr(o, 'volume_current', getattr(o, 'volume', 0.0))))
        vol_ok = abs(o_vol - rev_vol) < 0.001
        if keep is None and price_ok and vol_ok:
            if not sl_ok or abs(float(o.price_open) - rev_price) > 0.01:
                if mt5_client.modify_pending_order(o.ticket, price=rev_price, sl=rev_sl, tp=0.0):
                    add_log(f"🔗 TP3 reverse synced #{o.ticket}: entry={rev_price:.2f} SL={rev_sl:.2f} (with pos SL)")
            keep = o
        else:
            if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})

    if keep is not None:
        return True

    res = mt5_client.place_pending_order(
        pos.symbol, order_type, rev_vol, rev_price, rev_sl, 0.0, magic=magic
    )
    if res:
        add_log(
            f"🔗 TP3 reverse {order_type} @ ${rev_price:.2f} SL ${rev_sl:.2f} "
            f"(paired with #{pos.ticket} SL)"
        )
        return True
    return False

def manage_tp3_runners(mt5_client, h1_rates=None, m5_rates=None):
    """
    TP3 rules:
    - Always live-update virtual far target (never broker-TP close without reversal)
    - Close ONLY on opposite checklist reversal (>=15) or SL
    - SL and reverse stop always stay together
    """
    positions = mt5_client.get_open_positions() or []
    runners = _collect_tp3_runners(positions)
    virtual = dict(bot_state.get("tp3_virtual_targets") or {})
    checklist = bot_state.get("checklist") or {}
    buy_matched = int(checklist.get("buy_matched_count", 0) or 0)
    sell_matched = int(checklist.get("sell_matched_count", 0) or 0)

    if h1_rates is None:
        h1_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=100)
    if m5_rates is None:
        m5_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=100)

    active_runner_tickets = set()
    has_buy_tp3 = False
    has_sell_tp3 = False

    for pos in runners:
        active_runner_tickets.add(pos.ticket)
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        if is_buy:
            has_buy_tp3 = True
        else:
            has_sell_tp3 = True

        symbol_info = mt5.symbol_info(pos.symbol)
        digits = symbol_info.digits if symbol_info else 5
        curr_tp = float(pos.tp or 0)

        h1_df_tp3 = Strategy.calculate_indicators(h1_rates) if (h1_rates is not None and len(h1_rates) > 0) else None
        last_h1 = h1_df_tp3.iloc[-1] if (h1_df_tp3 is not None and len(h1_df_tp3) > 0) else {}

        # STRICT H1 EARLY EXIT & TP MODIFY PROTECTION:
        # TP2 & TP3 targets are only modified (expanded or pulled in for early profit exit) IF & ONLY IF verified against a confirmed H1 SMC Structure Zone (H1 Swing Low/High or H1 Order Block).
        has_last_h1 = (last_h1 is not None and hasattr(last_h1, 'get') and len(last_h1) > 0)
        h1_c = float(last_h1.get('close', 0)) if (has_last_h1 and pd.notna(last_h1.get('close'))) else 0.0
        h1_lh = float(last_h1.get('last_high', 0)) if (has_last_h1 and pd.notna(last_h1.get('last_high'))) else 0.0
        h1_ll = float(last_h1.get('last_low', 0)) if (has_last_h1 and pd.notna(last_h1.get('last_low'))) else 0.0
        h1_choch_bear = bool(last_h1.get('h1_choch_bear', False)) or (h1_c > 0 and h1_ll > 0 and h1_c < h1_ll) if has_last_h1 else False
        h1_choch_bull = bool(last_h1.get('h1_choch_bull', False)) or (h1_c > 0 and h1_lh > 0 and h1_c > h1_lh) if has_last_h1 else False
        
        # Verify valid H1 reversal zone before modifying TP for early exit:
        if not is_buy and h1_ll > 0 and h1_ll > float(pos.price_current) and h1_choch_bull:
            h1_rev_tp = round(h1_ll, digits)
            if abs(curr_tp - h1_rev_tp) > 1.5 and h1_rev_tp < float(pos.price_open):
                if mt5_client.modify_tp(pos.ticket, h1_rev_tp):
                    add_log(f"🛡️ VERIFIED H1 REVERSAL TP PROTECTION: Adjusted SELL #{pos.ticket} TP to Verified H1 Support Zone ${h1_rev_tp:.2f}")

        elif is_buy and h1_lh > 0 and h1_lh < float(pos.price_current) and h1_choch_bear:
            h1_rev_tp = round(h1_lh, digits)
            if abs(curr_tp - h1_rev_tp) > 1.5 and h1_rev_tp > float(pos.price_open):
                if mt5_client.modify_tp(pos.ticket, h1_rev_tp):
                    add_log(f"🛡️ VERIFIED H1 REVERSAL TP PROTECTION: Adjusted BUY #{pos.ticket} TP to Verified H1 Resistance Zone ${h1_rev_tp:.2f}")

        # Reverse stops managed strictly via 3-leg Pending Engine in main loop
        # if float(pos.sl or 0) > 0:
        #     ensure_tp3_reverse_stop(mt5_client, pos, digits)

        # Close ONLY on opposite reversal signal
        rev_hit = (
            (is_buy and sell_matched >= 15 and sell_matched > buy_matched) or
            ((not is_buy) and buy_matched >= 15 and buy_matched > sell_matched)
        )
        if rev_hit:
            side_name = "SELL" if is_buy else "BUY"
            score = sell_matched if is_buy else buy_matched
            if mt5_client.close_position(pos.ticket):
                add_log(f"✅ TP3 #{pos.ticket} closed on {side_name} reversal signal ({score}/21)")
                trade_tracker.record_close_trade(
                    pos.ticket, float(pos.price_current),
                    f"TP3 Reversal Close {side_name} {score}/21",
                    "TP3_REVERSAL",
                    profit=float(pos.profit) + float(getattr(pos, 'swap', 0) or 0),
                )
                virtual.pop(str(pos.ticket), None)
                rev_magic = MAGIC_REV_SELL if is_buy else MAGIC_REV_BUY
                for o in (mt5.orders_get(symbol=pos.symbol) or []):
                    if int(getattr(o, 'magic', 0) or 0) == rev_magic:
                        if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                        add_log(f"🧹 Removed TP3 reverse #{o.ticket} after reversal close")

    # Orphan reverse stops if no matching TP3 runner
    for o in (mt5.orders_get() or []):
        mag = int(getattr(o, 'magic', 0) or 0)
        if mag == MAGIC_REV_SELL and not has_buy_tp3:
            if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            add_log(f"🧹 Orphan TP3 reverse SELL_STOP #{o.ticket} removed")
        elif mag == MAGIC_REV_BUY and not has_sell_tp3:
            if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            add_log(f"🧹 Orphan TP3 reverse BUY_STOP #{o.ticket} removed")

    # Drop virtual targets for closed tickets
    for key in list(virtual.keys()):
        try:
            if int(key) not in active_runner_tickets:
                # keep briefly if still open as non-runner? drop if ticket gone
                if not any(p.ticket == int(key) for p in positions):
                    virtual.pop(key, None)
        except Exception:
            virtual.pop(key, None)

    bot_state["tp3_virtual_targets"] = virtual

def manage_open_positions(mt5_client, h1_rates=None, m15_rates=None, m5_rates=None):
    positions = mt5_client.get_open_positions() # Fetch ALL open positions on account
    pos_list = []

    if positions:
        if h1_rates is None:
            h1_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=100)
        if m15_rates is None:
            m15_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=100)
        if m5_rates is None:
            m5_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=100)

        # Check Total Floating PnL across running open trades against Account Balance
        acc_info = mt5_client.get_account_info()
        acc_balance = acc_info['balance'] if acc_info else getattr(config, 'INITIAL_BALANCE', 1000.0)
        # Floating PnL for bot caps — exclude isolated manual positions
        bot_positions = [p for p in positions if int(getattr(p, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS]
        total_floating = sum([float(p.profit) + float(getattr(p, 'swap', 0)) for p in bot_positions])
        floating_pct = (total_floating / acc_balance) * 100.0 if acc_balance > 0 else 0.0

        # Check Account Drawdown / Recent Loss Status
        hist_deals_recent = mt5.history_deals_get(time.time() - 86400, time.time() + 5) or []
        accumulated_loss = sum([abs(float(d.profit) + float(d.swap) + float(d.commission)) for d in hist_deals_recent if d.entry == mt5.DEAL_ENTRY_OUT and (float(d.profit) + float(d.swap) + float(d.commission)) < 0])
        accumulated_profit = sum([float(d.profit) + float(d.swap) + float(d.commission) for d in hist_deals_recent if d.entry == mt5.DEAL_ENTRY_OUT and (float(d.profit) + float(d.swap) + float(d.commission)) > 0])
        net_history_loss = max(0.0, accumulated_loss - accumulated_profit)
        is_in_drawdown = (net_history_loss > 0) or (acc_info and acc_info['equity'] < acc_info['balance'])

        # 1. Floating Profit Circuit Breaker (Closes ALL Trades & Purges ALL Pending Orders)
        enable_profit = getattr(config, 'ENABLE_FLOATING_PROFIT_CAP', False)
        target_profit_cap = getattr(config, 'FLOATING_MAX_PROFIT_PERCENT', 10.0)
        if enable_profit and floating_pct >= target_profit_cap and not is_in_drawdown:
            add_log(f"🎯 FLOATING PROFIT CAP HIT (+{floating_pct:.2f}% >= +{target_profit_cap:.1f}%)! Closing BOT trades & Pending Stops (manual kept)!")
            for p in bot_positions:
                close_price = mt5.symbol_info_tick(p.symbol).bid if p.type == mt5.POSITION_TYPE_BUY else mt5.symbol_info_tick(p.symbol).ask
                order_close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": p.symbol,
                    "volume": p.volume,
                    "type": order_close_type,
                    "position": p.ticket,
                    "price": close_price,
                    "magic": p.magic,
                    "comment": f"Floating Profit Target +{target_profit_cap}% Hit",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                mt5.order_send(req)
                trade_tracker.record_close_trade(p.ticket, close_price, f"🎯 Floating Profit Target Reached (+{floating_pct:.2f}%)", "FLOATING_PROFIT_CAP", profit=p.profit)
            
            # Cancel all active pending orders on account
            p_orders = mt5.orders_get() or []
            for po in p_orders:
                if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                    mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": po.ticket})
                add_log(f"🧹 PURGED PENDING ORDER #{po.ticket} on Profit Cap Hit!")
            return

        # 2. Floating Loss Circuit Breaker — only if UI set a % (no static default)
        enable_loss = getattr(config, 'ENABLE_FLOATING_LOSS_CAP', False)
        target_loss_cap = getattr(config, 'FLOATING_MAX_LOSS_PERCENT', None)
        try:
            target_loss_cap = float(target_loss_cap) if target_loss_cap is not None else 0.0
        except (TypeError, ValueError):
            target_loss_cap = 0.0
        if enable_loss and target_loss_cap > 0 and floating_pct <= -target_loss_cap:
            add_log(f"🛑 FLOATING LOSS CIRCUIT BREAKER HIT ({floating_pct:.2f}% <= -{target_loss_cap:.1f}%)! Closing BOT trades & Pending Stops (manual kept)!")
            for p in bot_positions:
                close_price = mt5.symbol_info_tick(p.symbol).bid if p.type == mt5.POSITION_TYPE_BUY else mt5.symbol_info_tick(p.symbol).ask
                order_close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": p.symbol,
                    "volume": p.volume,
                    "type": order_close_type,
                    "position": p.ticket,
                    "price": close_price,
                    "magic": p.magic,
                    "comment": f"Floating Loss Safety Exit -{target_loss_cap}%",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                mt5.order_send(req)
                trade_tracker.record_close_trade(p.ticket, close_price, f"🛑 Floating Loss Safety Exit ({floating_pct:.2f}%)", "FLOATING_LOSS_CAP", profit=p.profit)
            
            # Cancel all active pending orders on account
            p_orders = mt5.orders_get() or []
            for po in p_orders:
                if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                    mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": po.ticket})
                add_log(f"🧹 PURGED PENDING ORDER #{po.ticket} on Loss Cap Shield Hit!")
            return

        # Pre-identify TP3 runners so auto-repair / dynamic TP never attach broker TP
        tp3_tickets = {p.ticket for p in _collect_tp3_runners(positions)}

        for pos in positions:
            # Fetch Symbol-Specific Parameters for EACH open position (BTCUSD, XAUUSD, etc.)
            pos_symbol = pos.symbol
            symbol_info = mt5.symbol_info(pos_symbol)
            digits = symbol_info.digits if symbol_info else 5
            pip_size = (10 ** -digits) * 10
            lock_profit_offset = pip_size * 2

            pos_rates = mt5_client.fetch_rates(pos_symbol, mt5.TIMEFRAME_M1, num_bars=250)
            pos_df = Strategy.calculate_indicators(pos_rates) if pos_rates is not None else None
            atr_val = pos_df.iloc[-1]['atr'] if pos_df is not None and len(pos_df) > 0 else 0.001
            latest_atr = float(atr_val) if pd.notna(atr_val) and atr_val is not None else 0.001
            swap_val = float(pos.swap) if hasattr(pos, 'swap') and pos.swap is not None else 0.0
            profit_val = float(pos.profit) if hasattr(pos, 'profit') and pos.profit is not None else 0.0
            
            p_sl = float(pos.sl) if pos.sl > 0 else 0.0
            p_tp = float(pos.tp) if pos.tp > 0 else 0.0
            p_open = float(pos.price_open)
            p_vol = float(pos.volume)
            is_b = (pos.type == mt5.POSITION_TYPE_BUY)
            
            sl_amt = (p_open - p_sl) * p_vol if (is_b and p_sl > 0) else ((p_sl - p_open) * p_vol if p_sl > 0 else 0.0)
            tp_amt = (p_tp - p_open) * p_vol if (is_b and p_tp > 0) else ((p_open - p_tp) * p_vol if p_tp > 0 else 0.0)

            pos_list.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "type": "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL",
                "volume": pos.volume,
                "price_open": round(p_open, 2),
                "sl": round(p_sl, 2),
                "sl_amount": round(sl_amt, 2),
                "tp": round(p_tp, 2),
                "tp_amount": round(tp_amt, 2),
                "profit": round(profit_val + swap_val, 2)
            })

            ticket = pos.ticket
            entry_price = pos.price_open
            current_price = pos.price_current
            sl = pos.sl
            tp = pos.tp
            pos_type = pos.type
            is_tp3 = (ticket in tp3_tickets) or (int(getattr(pos, 'magic', 0) or 0) in TP3_MAGICS)
            is_tp2 = int(getattr(pos, 'magic', 0) or 0) in TP2_MAGICS
            pos_magic = int(getattr(pos, 'magic', 0) or 0)
            # SMC recovery engine owns SL/TP on its magics — do not auto-repair different zone TPs
            if (
                pos_magic in smc_recovery.ALL_ENGINE_MAGICS
                
            ):
                continue

            # AUTO-REPAIR MISSING SL OR TP ON ANY OPEN POSITION (USING FULL SMC STRUCTURE ZONES)
            # TP3 runners: NEVER attach broker TP (close only on SL / reversal). Repair SL only.
            side_str = 'BUY' if pos_type == mt5.POSITION_TYPE_BUY else 'SELL'
            h1_rates_pos = mt5_client.fetch_rates(pos_symbol, mt5.TIMEFRAME_M1, num_bars=250)
            h1_df_pos = Strategy.calculate_indicators(h1_rates_pos) if h1_rates_pos is not None else pos_df
            smc_sl_rep, smc_tp_rep, _, _ = Strategy.calculate_valid_zone_sl_tp(h1_df_pos, pos_df, side_str, entry_price)

            if is_tp3:
                # Calculate Weekly/Macro TP3 target based on SMC Gann 360 / MTF High
                _, _, _, macro_tp3 = Strategy.calculate_pending_zone_sl_tp(h1_df_pos, pos_df, side_str, entry_price)
                macro_tp3 = round(macro_tp3, digits)
                if abs(tp - macro_tp3) > 5.0:  # Only modify if target changes significantly (> $5.0)
                    if mt5_client.modify_tp(ticket, macro_tp3):
                        add_log(f"🎯 TP3 RUNNER MACRO TP: Set Weekly TP3 on #{ticket} -> ${macro_tp3:.2f}")
                        tp = macro_tp3
                if sl == 0:
                    calc_sl = round(smc_sl_rep, digits)
                    req_sltp = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": ticket,
                        "symbol": pos.symbol,
                        "sl": calc_sl,
                        "tp": tp
                    }
                    res_mod = mt5.order_send(req_sltp)
                    if res_mod and res_mod.retcode == mt5.TRADE_RETCODE_DONE:
                        add_log(f"🛠️ TP3 SMC AUTO-REPAIR: Attached SMC Zone SL (${calc_sl}) to #{ticket}")
                        sl = calc_sl
            elif sl == 0 or tp == 0:
                calc_sl = round(smc_sl_rep, digits)
                calc_tp = round(smc_tp_rep, digits)
                
                target_sl = sl if sl > 0 else calc_sl
                target_tp = tp if tp > 0 else calc_tp
                
                req_sltp = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": ticket,
                    "symbol": pos.symbol,
                    "sl": target_sl,
                    "tp": target_tp
                }
                res_mod = mt5.order_send(req_sltp)
                if res_mod and res_mod.retcode == mt5.TRADE_RETCODE_DONE:
                    add_log(f"🛠️ SMC AUTO-REPAIR: Attached SMC Zone SL (${target_sl}) & TP (${target_tp}) to Position #{ticket}!")
                    sl = target_sl
                    tp = target_tp

            risk_dist = abs(entry_price - sl) if sl > 0 else abs(entry_price - smc_sl_rep)
            if risk_dist <= 0:
                continue

            # HYBRID SMC BREAK-EVEN & ZONE TRAILING LOGIC:
            # 1. When TP1 is hit (or TP1 order closed), SL IMMEDIATELY moves to Break-Even (Entry Price + lock offset).
            # 2. As new SMC Structural Zones form, SL trails further behind confirmed Higher Lows (BUY) or Lower Highs (SELL).
            pos_df_h1 = Strategy.calculate_indicators(h1_rates_pos) if (h1_rates_pos is not None and len(h1_rates_pos) > 20) else None
            
            if pos_type == mt5.POSITION_TYPE_BUY:
                current_profit_dist = current_price - entry_price
                has_tp1_pos = any(int(getattr(p, 'magic', 0) or 0) == MAGIC_MAIN_BUY for p in positions)
                tp1_hit = (not has_tp1_pos) or (current_profit_dist >= max(risk_dist * 0.8, 5.0))

                confirmed_hl_h1 = pos_df_h1['last_low'].iloc[-1] if (pos_df_h1 is not None and pd.notna(pos_df_h1['last_low'].iloc[-1])) else None

                # 1. Break-Even Trigger: Move SL to Entry immediately when TP1 is hit
                if config.ENABLE_BREAKEVEN and tp1_hit:
                    be_sl = round(entry_price + lock_profit_offset, digits)
                    if sl < be_sl:
                        add_log(f"🛡️ TP1 HIT BREAK-EVEN: Moving BUY #{ticket} SL to Break-Even ${be_sl:.2f}")
                        if mt5_client.modify_sl(ticket, be_sl):
                            trade_tracker.record_trade_update(ticket, sl_moved_to_be=True, sl=be_sl)
                            sl = be_sl

                # 2. H1 SMC Zone Trailing: Move SL higher if a new valid H1 SMC Zone forms above Break-Even
                if config.ENABLE_TRAILING_STOP and confirmed_hl_h1 and confirmed_hl_h1 > entry_price:
                    smc_zone_sl = round(confirmed_hl_h1 - (latest_atr * 0.3), digits)
                    if smc_zone_sl > sl and smc_zone_sl < current_price:
                        add_log(f"📈 NEW H1 SMC ZONE TRAILING: Moving BUY #{ticket} SL to New H1 Support Zone ${smc_zone_sl:.2f}")
                        if mt5_client.modify_sl(ticket, smc_zone_sl):
                            trade_tracker.record_trade_update(ticket, sl=smc_zone_sl)
                            sl = smc_zone_sl

            elif pos_type == mt5.POSITION_TYPE_SELL:
                current_profit_dist = entry_price - current_price
                tp1_open = any(int(getattr(p, 'magic', 0) or 0) == MAGIC_MAIN_SELL for p in positions)
                tp1_hit = (not tp1_open) or (current_profit_dist >= max(risk_dist * 0.8, 5.0))
                confirmed_lh_h1 = pos_df_h1['last_high'].iloc[-1] if (pos_df_h1 is not None and pd.notna(pos_df_h1['last_high'].iloc[-1])) else None

                # 1. Break-Even Trigger: Move SL to Entry immediately when TP1 is hit
                if config.ENABLE_BREAKEVEN and tp1_hit:
                    be_sl = round(entry_price - lock_profit_offset, digits)
                    if sl == 0 or sl > be_sl:
                        add_log(f"🛡️ TP1 HIT BREAK-EVEN: Moving SELL #{ticket} SL to Break-Even ${be_sl:.2f}")
                        if mt5_client.modify_sl(ticket, be_sl):
                            trade_tracker.record_trade_update(ticket, sl_moved_to_be=True, sl=be_sl)
                            sl = be_sl

                # 2. H1 SMC Zone Trailing: Move SL lower if a new valid H1 SMC Zone forms below Break-Even
                if config.ENABLE_TRAILING_STOP and confirmed_lh_h1 and confirmed_lh_h1 < entry_price:
                    smc_zone_sl = round(confirmed_lh_h1 + (latest_atr * 0.3), digits)
                    if (sl == 0 or smc_zone_sl < sl) and smc_zone_sl > current_price:
                        add_log(f"📉 NEW H1 SMC ZONE TRAILING: Moving SELL #{ticket} SL to New H1 Resistance Zone ${smc_zone_sl:.2f}")
                        if mt5_client.modify_sl(ticket, smc_zone_sl):
                            trade_tracker.record_trade_update(ticket, sl=smc_zone_sl)
                            sl = smc_zone_sl

                # Unify SL across open SELL trades: Move higher SLs down to match active lower SMC SLs
                other_sell_sls = [float(p.sl) for p in positions if p.type == mt5.POSITION_TYPE_SELL and float(p.sl or 0) > 0 and float(p.sl or 0) < sl and float(p.sl or 0) > current_price]
                if other_sell_sls:
                    unified_sl = min(other_sell_sls)
                    if sl == 0 or sl > unified_sl:
                        add_log(f"🔗 UNIFIED SELL SL SYNC: Moving SELL #{ticket} SL to match active zone ${unified_sl:.2f}")
                        if mt5_client.modify_sl(ticket, unified_sl):
                            trade_tracker.record_trade_update(ticket, sl=unified_sl)
                            sl = unified_sl

            # REASONABLE DYNAMIC TP ADJUSTMENT (skip TP2/TP3 and manual trades — user/pending engine manages those)
            if is_tp3 or is_tp2 or int(getattr(pos, 'magic', 0) or 0) not in (MAGIC_MAIN_BUY, MAGIC_MAIN_SELL):
                continue

            sym_h1 = mt5_client.fetch_rates(pos_symbol, mt5.TIMEFRAME_M1, num_bars=100)
            sym_m15 = mt5_client.fetch_rates(pos_symbol, mt5.TIMEFRAME_M1, num_bars=100)
            sym_m5 = pos_rates if pos_rates is not None else mt5_client.fetch_rates(pos_symbol, mt5.TIMEFRAME_M1, num_bars=100)

            if sym_h1 is not None and sym_m15 is not None and sym_m5 is not None:
                h1_df = Strategy.calculate_indicators(sym_h1)
                m15_df = Strategy.calculate_indicators(sym_m15)
                m5_df = Strategy.calculate_indicators(sym_m5)
                
                sig_str = "BUY" if pos_type == mt5.ORDER_TYPE_BUY else "SELL"
                dyn_tp_target_rrr = Strategy.calculate_dynamic_rrr(h1_df, m15_df, m5_df, sig_str)
                
                # Dynamic TP Distance Calculation
                target_tp_dist = risk_dist * dyn_tp_target_rrr
                new_computed_tp = round(entry_price + target_tp_dist if pos_type == mt5.ORDER_TYPE_BUY else entry_price - target_tp_dist, digits)
                
                current_pos_tp = pos.tp
                # Modifies TP ONLY when dynamic target shifts significantly (>= 0.5 ATR)
                if current_pos_tp > 0 and abs(new_computed_tp - current_pos_tp) >= (latest_atr * 0.5):
                    stoplevel = symbol_info.trade_stops_level * (10 ** -digits) if symbol_info and hasattr(symbol_info, 'trade_stops_level') else 0.0001
                    dist_to_price = abs(new_computed_tp - current_price)
                    if dist_to_price > stoplevel:
                        add_log(f"🎯 DYNAMIC TP ADJUSTMENT: Modifying Position #{ticket} TP from {current_pos_tp:.2f} to {new_computed_tp:.2f} (Target RRR: 1:{dyn_tp_target_rrr:.2f})")
                        if mt5_client.modify_tp(ticket, new_computed_tp):
                            trade_tracker.record_trade_update(ticket, tp=new_computed_tp)

    # After SL trails, keep TP3 reverse stops glued + virtual targets updated
    manage_tp3_runners(mt5_client, h1_rates=h1_rates, m5_rates=m5_rates)

    bot_state["positions"] = pos_list
    bot_state["open_trades"] = pos_list

def calculate_periodic_pnl():
    """Calculates Daily, Weekly, and Monthly Profit/Loss Percentage & Trade Statistics from MT5 Deal History"""
    try:
        now = time.time()
        # Daily: since midnight today
        today_start = pd.Timestamp.now().floor('D').timestamp()
        # Weekly: since Monday this week
        week_start = (pd.Timestamp.now() - pd.Timedelta(days=pd.Timestamp.now().weekday())).floor('D').timestamp()
        # Monthly: since 1st of current month
        month_start = pd.Timestamp.now().replace(day=1).floor('D').timestamp()

        # Fetch deals history (from beginning of account or month_start)
        deals = mt5.history_deals_get(0, int(now))
        ref_balance = getattr(config, 'INITIAL_BALANCE', 1000.0)
        
        daily_pnl = 0.0
        weekly_pnl = 0.0
        monthly_pnl = 0.0
        
        total_closed_trades = 0
        total_profit_trades = 0
        total_loss_trades = 0

        if deals:
            for deal in deals:
                if deal.entry == mt5.DEAL_ENTRY_OUT and deal.symbol:  # Closed trades only
                    profit = deal.profit + deal.swap + deal.commission
                    d_time = deal.time
                    
                    total_closed_trades += 1
                    if profit > 0:
                        total_profit_trades += 1
                    elif profit < 0:
                        total_loss_trades += 1

                    if d_time >= today_start:
                        daily_pnl += profit
                    if d_time >= week_start:
                        weekly_pnl += profit
                    if d_time >= month_start:
                        monthly_pnl += profit

        acc_info = mt5.account_info()
        live_balance = acc_info.balance if acc_info else getattr(config, 'INITIAL_BALANCE', 5000.0)
        ref_balance = max(100.0, live_balance)
        
        daily_pct = (daily_pnl / ref_balance) * 100.0 if ref_balance > 0 else 0.0
        weekly_pct = (weekly_pnl / ref_balance) * 100.0 if ref_balance > 0 else 0.0
        monthly_pct = (monthly_pnl / ref_balance) * 100.0 if ref_balance > 0 else 0.0
        win_rate = (total_profit_trades / total_closed_trades * 100.0) if total_closed_trades > 0 else 0.0

        return {
            "daily_pnl": round(daily_pnl, 2),
            "daily_pct": round(daily_pct, 2),
            "weekly_pnl": round(weekly_pnl, 2),
            "weekly_pct": round(weekly_pct, 2),
            "monthly_pnl": round(monthly_pnl, 2),
            "monthly_pct": round(monthly_pct, 2),
            "total_closed": total_closed_trades,
            "total_profit_trades": total_profit_trades,
            "total_loss_trades": total_loss_trades,
            "win_rate": round(win_rate, 1)
        }
    except Exception as e:
        logging.error(f"Error calculating periodic PnL: {e}")
        return {
            "daily_pnl": 0.0, "daily_pct": 0.0, "weekly_pnl": 0.0, "weekly_pct": 0.0, "monthly_pnl": 0.0, "monthly_pct": 0.0,
            "total_closed": 0, "total_profit_trades": 0, "total_loss_trades": 0, "win_rate": 0.0
        }

def bot_loop():
    add_log("Starting MoneyMoney Automated Trading Bot with Live Web Dashboard...")
    mt5_client = MT5Interface()

    while True:
        try:
            if not mt5_client.connected:
                if not mt5_client.initialize():
                    add_log("Waiting for MetaTrader 5 Terminal to be OPEN... Retrying connection in 10s")
                    time.sleep(10)
                    continue

            # 1. Update Account Info, Calculate Periodic PnL & Check Circuit Breakers
            acc = mt5_client.get_account_info()
            if acc:
                current_equity = acc['equity']
                current_balance = acc['balance']
                ref_balance = max(100.0, current_balance)
                
                # Calculate Total Profit / Loss Percentage
                pnl_amount = current_equity - ref_balance
                pnl_pct = (pnl_amount / ref_balance) * 100.0 if ref_balance > 0 else 0.0
                
                acc["pnl_amount"] = round(pnl_amount, 2)
                acc["pnl_pct"] = round(pnl_pct, 2)

                # Fetch Periodic PnL (Daily, Weekly, Monthly)
                pnl_periods = calculate_periodic_pnl()
                acc["periodic_pnl"] = pnl_periods
                bot_state["account"] = acc

                bot_state["pending_recovery_amount"] = 0.0
                bot_state["is_recovery_mode"] = False

                # Check Daily Loss Limit (Bypassed when Loss Recovery is active to ensure account is fully recovered)
                daily_pct = pnl_periods["daily_pct"]
                if daily_pct <= -getattr(config, 'MAX_DAILY_LOSS_PERCENT', 10.0) and getattr(config, 'STOP_BOT_ON_DAILY_LOSS', False):
                    daily_limit = float(getattr(config, 'MAX_DAILY_LOSS_PERCENT', 50.0))
                    daily_msg = f"🛑 DAILY SAFETY PAUSE: Daily loss reached {abs(daily_pct):.2f}% (Limit: {daily_limit:.0f}%). Bot paused today for safety, will resume tomorrow to continue multi-day drawdown recovery!"
                    add_log(daily_msg)
                    bot_state["forecast"] = {
                        "has_forecast": False,
                        "signal": None,
                        "symbol": config.SYMBOL,
                        "waiting_reason": f"🛑 BOT PAUSED TODAY: 10% Daily Limit Hit. Will resume tomorrow at midnight to recover total drawdown."
                    }
                    time.sleep(10)
                    continue

            # 2. Manage Active Trades & Track Closed SL-Hit Orders for Instant Reverse Recovery
            manage_open_positions(mt5_client)

            # Recovery mechanism handled cleanly via Pending Order Engine (BUY_STOP / SELL_STOP)
            pass

            positions = mt5_client.get_open_positions()
            pos_list = []
            if positions:
                for p in positions:
                    swap_val = float(p.swap) if hasattr(p, 'swap') and p.swap is not None else 0.0
                    profit_val = float(p.profit) if hasattr(p, 'profit') and p.profit is not None else 0.0
                    
                    p_sl = float(p.sl) if p.sl > 0 else 0.0
                    p_tp = float(p.tp) if p.tp > 0 else 0.0
                    p_open = float(p.price_open)
                    p_vol = float(p.volume)
                    is_b = (p.type == mt5.POSITION_TYPE_BUY)
                    
                    sl_amt = (p_open - p_sl) * p_vol if (is_b and p_sl > 0) else ((p_sl - p_open) * p_vol if p_sl > 0 else 0.0)
                    tp_amt = (p_tp - p_open) * p_vol if (is_b and p_tp > 0) else ((p_open - p_tp) * p_vol if p_tp > 0 else 0.0)

                    pos_list.append({
                        "ticket": p.ticket,
                        "symbol": p.symbol,
                        "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                        "volume": p.volume,
                        "open_price": round(p_open, 2),
                        "price_open": round(p_open, 2),
                        "sl": round(p_sl, 2),
                        "sl_amount": round(sl_amt, 2),
                        "tp": round(p_tp, 2),
                        "tp_amount": round(tp_amt, 2),
                        "profit": round(profit_val + swap_val, 2)
                    })
            else:
                # Signal Memory Lock Management:
                # If a trade hit TP, keep last_executed_signal active until:
                # A) Opposite side reaches >= 6 rules (Reversal Setup Met), OR
                # B) Trade was Cut-Off / SL hit (allowing clean fresh entry)
                last_sig = bot_state.get("last_executed_signal")
                if last_sig is not None and bot_state.get("checklist"):
                    opp_score = bot_state["checklist"].get("sell_matched_count", 0) if last_sig == "BUY" else bot_state["checklist"].get("buy_matched_count", 0)
                    if opp_score >= 6:
                        add_log(f"🔀 REVERSAL CRITERIA MET: Opposite setup matched {opp_score}/13 rules (Min 6 Required). Signal lock released!")
                        bot_state["last_executed_signal"] = None
                        bot_state["reversal_unlocked"] = True
                    elif bot_state.get("was_cut_off", False):
                        add_log("✂️ CUT-OFF RESET: Trade was cut-off early. Signal lock released for fresh setup!")
                        bot_state["last_executed_signal"] = None
                        bot_state["was_cut_off"] = False

            bot_state["positions"] = pos_list
            bot_state["open_trades"] = pos_list

            # Check Market Open / Closed Standby Status
            is_mkt_open, mkt_msg = mt5_client.is_market_open(config.SYMBOL)
            bot_state["market_open_status"] = {"is_open": is_mkt_open, "msg": mkt_msg}

            if not is_mkt_open:
                if bot_state.get("prev_market_open", True) != False:
                    add_log(f"🔴 MARKET IS CURRENTLY CLOSED ({mkt_msg}): Bot is in Standby Mode. As soon as market opens, auto trading will resume automatically!")
                    bot_state["prev_market_open"] = False
                # Still refresh BUY/SELL checklist from last available bars so UI is not stuck on "Scanning..."
                try:
                    h1_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=250)
                    m15_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=250)
                    m5_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=250)
                    if h1_rates is not None and m15_rates is not None and m5_rates is not None:
                        checklist_info = Strategy.get_checklist_status(h1_rates, m15_rates, m5_rates)
                        checklist_info["active_signal"] = bot_state.get("last_executed_signal")
                        checklist_info["market_closed"] = True
                        bot_state["checklist"] = checklist_info
                        bot_state["market_regime"] = checklist_info.get("market_regime") or {}
                        h1_df = Strategy.calculate_indicators(h1_rates)
                        m5_df = Strategy.calculate_indicators(m5_rates)
                        bot_state["candlestick_patterns"] = scan_h1_and_m5(h1_df, m5_df, config.SYMBOL)
                except Exception as e_chk:
                    logging.error(f"Checklist refresh while market closed failed: {e_chk}")
                time.sleep(5)
                continue
            else:
                if bot_state.get("prev_market_open", False) == False:
                    add_log(f"🟢 MARKET IS NOW OPEN: Standby Mode deactivated. Resuming Auto Trading Engine!")
                    bot_state["prev_market_open"] = True

            # 3. Fetch Triple Timeframe Data (H1, M15, M5)
            h1_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=250)

            m15_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=250)
            m5_rates = mt5_client.fetch_rates(config.SYMBOL, mt5.TIMEFRAME_M1, num_bars=250)

            if h1_rates is not None and m15_rates is not None and m5_rates is not None:
                h1_df = Strategy.calculate_indicators(h1_rates)
                m15_df = Strategy.calculate_indicators(m15_rates)
                m5_df = Strategy.calculate_indicators(m5_rates)

                last_h1 = h1_df.iloc[-1]
                last_m15 = m15_df.iloc[-1]
                last_m5 = m5_df.iloc[-1]

                h1_lh = last_h1['last_high'] if pd.notna(last_h1['last_high']) and last_h1['last_high'] is not None else last_h1['close']
                m15_lh = last_m15['last_high'] if pd.notna(last_m15['last_high']) and last_m15['last_high'] is not None else last_m15['close']

                h1_structure = "BULLISH (BOS)" if last_h1['close'] >= h1_lh else "BEARISH (CHoCH)"
                m15_structure = "BULLISH (BOS)" if last_m15['close'] >= m15_lh else "BEARISH (CHoCH)"
                m5_structure = "BULLISH (FVG)" if last_m5['fvg_bullish'] else ("BEARISH (FVG)" if last_m5['fvg_bearish'] else "ORDER BLOCK")

                # Compute Real-Time Dynamic Structure RRR
                dyn_rrr_buy = Strategy.calculate_dynamic_rrr(h1_df, m15_df, m5_df, 'BUY')
                dyn_rrr_sell = Strategy.calculate_dynamic_rrr(h1_df, m15_df, m5_df, 'SELL')

                bot_state["market"] = {
                    "h1_trend": h1_structure,
                    "m15_trend": m15_structure,
                    "m5_trend": m5_structure,
                    "rsi_m5": round(float(last_m5['rsi']), 2),
                    "atr": round(float(last_m5['atr']), 5),
                    "vol_spike": bool(last_m5['vol_spike']),
                    "support": round(float(last_m5['support']), 2) if pd.notna(last_m5['support']) else 0.0,
                    "resistance": round(float(last_m5['resistance']), 2) if pd.notna(last_m5['resistance']) else 0.0,
                    "pivot": round(float(last_m5['pivot']), 2) if pd.notna(last_m5['pivot']) else 0.0,
                    "pivot_r1": round(float(last_m5['pivot_r1']), 2) if pd.notna(last_m5['pivot_r1']) else 0.0,
                    "pivot_s1": round(float(last_m5['pivot_s1']), 2) if pd.notna(last_m5['pivot_s1']) else 0.0,
                    "dynamic_rrr_buy": dyn_rrr_buy,
                    "dynamic_rrr_sell": dyn_rrr_sell
                }
                bot_state["m5_trend_status"] = Strategy.m5_hit_confirm_status(h1_df, m5_df)
                bot_state["candlestick_patterns"] = scan_h1_and_m5(h1_df, m5_df, config.SYMBOL)

                # Update Forecast, Waiting Reason & Strategy Checklist Progress
                forecast = Strategy.check_upcoming_forecast(h1_rates, m15_rates, m5_rates)
                waiting_reason = Strategy.get_waiting_reason(h1_rates, m15_rates, m5_rates)
                checklist_info = Strategy.get_checklist_status(h1_rates, m15_rates, m5_rates)
                
                checklist_info["active_signal"] = bot_state.get("last_executed_signal")
                checklist_info["market_closed"] = False
                forecast["waiting_reason"] = waiting_reason
                bot_state["forecast"] = forecast
                bot_state["checklist"] = checklist_info
                bot_state["market_regime"] = checklist_info.get("market_regime") or {}

                # Pending Engine focused scanning log stream
                pend_lvls_preview = Strategy.calculate_pending_triggers(h1_df, m5_df, float(last_m5['close']), float(last_m5['close']), m15_df=m15_df)
                p_buy_trig = float(pend_lvls_preview.get("buy_trig", 0.0))
                p_sell_trig = float(pend_lvls_preview.get("sell_trig", 0.0))
                p_mode = pend_lvls_preview.get("mode", getattr(config, 'PENDING_MODE', 'SMC_PMAX_RECOVERY'))

                regime = bot_state["market_regime"]
                regime_txt = f"{regime.get('regime', '?')} (ADX {regime.get('adx', 0)})"

                add_log(f"[PENDING SCAN] {config.SYMBOL} | Mode:{p_mode} | {regime_txt} | BuyStop:${p_buy_trig:.2f} | SellStop:${p_sell_trig:.2f} | H1:{h1_structure} | M5:{m5_structure}")

                open_positions = mt5_client.get_open_positions() or []
                tick_now = mt5.symbol_info_tick(config.SYMBOL)
                curr_ask = tick_now.ask if tick_now else float(last_m5['close'])
                curr_bid = tick_now.bid if tick_now else float(last_m5['close'])
                symbol_info = mt5.symbol_info(config.SYMBOL)
                digits = symbol_info.digits if symbol_info else (2 if "XAU" in str(config.SYMBOL).upper() else 5)

                # LIVE H1 SMC STRUCTURAL SYNC FOR SL ONLY (TP1 & TP2 are FIXED once opened)
                # Skip when SMC recovery engine owns the tickets (hedge sells must stay SL=0 / shared TP)
                h1_swing_low_pos = float(last_h1['last_low']) if (last_h1 is not None and pd.notna(last_h1.get('last_low'))) else curr_bid - 15.0
                h1_swing_high_pos = float(last_h1['last_high']) if (last_h1 is not None and pd.notna(last_h1.get('last_high'))) else curr_ask + 15.0
                atr_pos_raw = float(last_m5.get('atr', 2.0)) if (last_m5 is not None and pd.notna(last_m5.get('atr'))) else 2.0

                tp3_tickets = {p.ticket for p in _collect_tp3_runners(open_positions)}
                smc_mode = getattr(config, 'PENDING_MODE', '').upper() in ('PMAX_RECOVERY', 'SMC_PMAX_RECOVERY')
                for p in open_positions:
                    p_mag = int(getattr(p, 'magic', 0) or 0)
                    if (smc_mode and p_mag in smc_recovery.ALL_ENGINE_MAGICS) :
                        continue
                    is_tp3_pos = (p.ticket in tp3_tickets) or (int(getattr(p, 'magic', 0) or 0) in TP3_MAGICS)
                    p_sl = float(p.sl or 0)
                    side = 'BUY' if p.type == mt5.POSITION_TYPE_BUY else 'SELL'
                    entry_p = float(p.price_open)

                    if side == 'SELL':
                        h1_smc_sl = round(h1_swing_high_pos + (atr_pos_raw * 0.3), digits)
                    else:
                        h1_smc_sl = round(h1_swing_low_pos - (atr_pos_raw * 0.3), digits)

                    # Target SL Sync: Sync SL to H1 SMC level only if SL is missing or initial fallback
                    target_sl = p_sl
                    if p_sl <= 0 or abs(p_sl - h1_smc_sl) > 2.0:
                        if side == 'SELL' and (p_sl <= 0 or p_sl > entry_p):
                            target_sl = h1_smc_sl
                        elif side == 'BUY' and (p_sl <= 0 or p_sl < entry_p):
                            target_sl = h1_smc_sl

                    # STRICT H1 VERIFIED EARLY EXIT PROTECTION (MUST REQUIRE VALID H1 REVERSAL CHOCH & RE-PLACE PENDING ORDERS):
                    has_tp1_pos = any(int(getattr(op, 'magic', 0) or 0) in (MAGIC_MAIN_BUY, MAGIC_MAIN_SELL) for op in open_positions)
                    tp1_already_hit = not has_tp1_pos

                    if tp1_already_hit:
                        is_tp2_or_tp3 = (int(getattr(p, 'magic', 0) or 0) in TP2_MAGICS or int(getattr(p, 'magic', 0) or 0) in TP3_MAGICS or is_tp3_pos)
                        if is_tp2_or_tp3:
                            p_tp = float(p.tp or 0)
                            # Verify valid H1 CHoCH Breakout before early exit
                            h1_c_val = float(last_h1.get('close', 0)) if (last_h1 is not None and pd.notna(last_h1.get('close'))) else 0.0
                            h1_lh_val = float(last_h1.get('last_high', 0)) if (last_h1 is not None and pd.notna(last_h1.get('last_high'))) else 0.0
                            h1_ll_val = float(last_h1.get('last_low', 0)) if (last_h1 is not None and pd.notna(last_h1.get('last_low'))) else 0.0
                            h1_choch_bull = (h1_c_val > 0 and h1_lh_val > 0 and h1_c_val > h1_lh_val)
                            h1_choch_bear = (h1_c_val > 0 and h1_ll_val > 0 and h1_c_val < h1_ll_val)

                            # Early Exit for SELL position: Must have H1 CHoCH Bullish verification
                            if side == 'SELL' and h1_swing_low_pos > float(p.price_current) and h1_swing_low_pos < entry_p and h1_choch_bull:
                                h1_early_tp = round(h1_swing_low_pos, digits)
                                if abs(p_tp - h1_early_tp) > 1.5:
                                    if mt5_client.modify_tp(p.ticket, h1_early_tp):
                                        add_log(f"🛡️ H1 VERIFIED EARLY EXIT (POST-TP1): Adjusted SELL #{p.ticket} TP to H1 Support Zone ${h1_early_tp:.2f}")

                            # Early Exit for BUY position: Must have H1 CHoCH Bearish verification
                            elif side == 'BUY' and h1_swing_high_pos < float(p.price_current) and h1_swing_high_pos > entry_p and h1_choch_bear:
                                h1_early_tp = round(h1_swing_high_pos, digits)
                                if abs(p_tp - h1_early_tp) > 1.5:
                                    if mt5_client.modify_tp(p.ticket, h1_early_tp):
                                        add_log(f"🛡️ H1 VERIFIED EARLY EXIT (POST-TP1): Adjusted BUY #{p.ticket} TP to H1 Resistance Zone ${h1_early_tp:.2f}")

                    # Pending-engine legs keep SL glued to the opposite stop — do not drift H1 SL
                    pend_magic = int(getattr(p, 'magic', 0) or 0)
                    if pend_magic not in ENTRY_BUY_MAGICS and pend_magic not in ENTRY_SELL_MAGICS:
                        if (target_sl > 0 and abs(p_sl - target_sl) > 0.20):
                            if mt5_client.modify_sl(p.ticket, target_sl):
                                add_log(f"🛡️ H1 ZONE SL SYNC: Updated #{p.ticket} ({side}) SL to ${target_sl:.2f}")

                # Pending engine: 3 BUY_STOP + 3 SELL_STOP (TP1 / TP2 / TP3) while that side is flat
                if bot_state.get("is_running", True) and getattr(config, 'ENABLE_PENDING_ENGINE', True):
                    try:
                        algo_on, algo_msg = mt5_client.get_algo_trading_status()
                        if not algo_on and not bot_state.get("algo_off_warned"):
                            add_log(f"⛔ {algo_msg} — MT5-এ AutoTrading বাটন ON করো, নাহলে pending কাটা/বসানো যাবে না")
                            bot_state["algo_off_warned"] = True
                        elif algo_on:
                            bot_state["algo_off_warned"] = False
                        pend_lvls = Strategy.calculate_pending_triggers(h1_df, m5_df, curr_ask, curr_bid, m15_df=m15_df)
                        buy_trig = float(pend_lvls["buy_trig"])
                        sell_trig = float(pend_lvls["sell_trig"])
                        min_from_price = float(pend_lvls["min_from_price"])
                        mode_tag = pend_lvls["mode"]

                        # Fetch active pending orders & positions account-wide
                        orders = mt5.orders_get() or []
                        open_positions = mt5_client.get_open_positions() or []
                        
                        # Purge legacy reverse-stop orders if any remain (magic 100990 / 100991)
                        rev_orders = [o for o in orders if int(getattr(o, 'magic', 0) or 0) in REV_MAGICS]
                        for r_ord in rev_orders:
                            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": r_ord.ticket})

                        def _is_xau_pending(o, buy, order_type_check=None):
                            mag = int(getattr(o, 'magic', 0) or 0)
                            if mag in REV_MAGICS:
                                return False
                            if order_type_check is not None:
                                if o.type != order_type_check:
                                    return False
                            else:
                                valid_types = (mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_BUY_LIMIT) if buy else (mt5.ORDER_TYPE_SELL_STOP, mt5.ORDER_TYPE_SELL_LIMIT)
                                if o.type not in valid_types:
                                    return False
                            return 'XAU' in str(getattr(o, 'symbol', '')).upper()

                        existing_buy_stops = [o for o in orders if _is_xau_pending(o, True, mt5.ORDER_TYPE_BUY_STOP)]
                        existing_sell_stops = [o for o in orders if _is_xau_pending(o, False, mt5.ORDER_TYPE_SELL_STOP)]

                        # Active open positions check
                        has_active_buy_trade = any(p.type == mt5.POSITION_TYPE_BUY for p in open_positions)
                        has_active_sell_trade = any(p.type == mt5.POSITION_TYPE_SELL for p in open_positions)

                        tick_now = mt5.symbol_info_tick(config.SYMBOL)
                        curr_ask = tick_now.ask if tick_now else last_m5['close']
                        curr_bid = tick_now.bid if tick_now else last_m5['close']

                        has_active_buy = any(
                            p.type == mt5.POSITION_TYPE_BUY and int(getattr(p, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS
                            for p in open_positions
                        )
                        has_active_sell = any(
                            p.type == mt5.POSITION_TYPE_SELL and int(getattr(p, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS
                            for p in open_positions
                        )

                        pending_buy_stop = existing_buy_stops[0] if existing_buy_stops else None
                        pending_sell_stop = existing_sell_stops[0] if existing_sell_stops else None

                        active_buy_pos = next(
                            (p for p in open_positions
                             if p.type == mt5.POSITION_TYPE_BUY and int(getattr(p, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS),
                            None,
                        )
                        active_sell_pos = next(
                            (p for p in open_positions
                             if p.type == mt5.POSITION_TYPE_SELL and int(getattr(p, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS),
                            None,
                        )

                        # MANUAL SL/TP AUTO-FILL REMOVED STRICTLY
                        # Daily drawdown — bot positions only; never force-close isolated manual
                        acc_info = mt5.account_info()
                        curr_balance = acc_info.balance if acc_info else 1000.0
                        bot_open = [p for p in open_positions if int(getattr(p, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS]
                        daily_drawdown_hit = trade_tracker.is_daily_drawdown_limit_reached(curr_balance, open_positions=bot_open)

                        if daily_drawdown_hit:
                            bot_state["is_running"] = False
                            bot_state["bot_status_msg"] = "🚨 DISABLED: Daily Drawdown Limit Reached!"
                            for p_close in bot_open:
                                mt5_client.close_position(p_close.ticket)
                                add_log(f"🚨 EMERGENCY EXIT: Closed bot position #{p_close.ticket} due to Daily Drawdown Limit.")
                            for o in orders:
                                if o.type in [mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_SELL_STOP, mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT]:
                                    if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                                        mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                            add_log("🚨 EMERGENCY ACCOUNT SAFETY CIRCUIT BREAKER ACTIVATED: BOT TRADES CLOSED & BOT DISABLED FOR THE DAY (manual kept)!")

                        # Check 9-Loss Pending Circuit Breaker Status
                        circuit_breaker_active = trade_tracker.is_circuit_breaker_active()
                        is_ranging = bool(last_m5.get('is_consolidation', False))

                        if circuit_breaker_active:
                            for o in orders:
                                if o.type in [mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_SELL_STOP, mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT]:
                                    if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                                        mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                            add_log(f"⚠️ DAILY CIRCUIT BREAKER ACTIVE: 9 consecutive losses reached today! Pending Orders suspended.")

                        else:
                            if is_ranging:
                                if bot_state.get("prev_ranging", False) is not True:
                                    detail = (bot_state.get("market_regime") or {}).get("detail", "consolidating")
                                    add_log(f"🛡️ RANGING MODE: {detail}. Pending = RANGE BREAKOUT. Regular market entries BLOCKED.")
                                    bot_state["prev_ranging"] = True
                            else:
                                if bot_state.get("prev_ranging", False) is True:
                                    add_log(f"🟢 MARKET SMOOTH / TRENDING: Pending = {mode_tag}. Regular engine unblocked.")
                                    bot_state["prev_ranging"] = False

                        atr_raw = float(last_m5.get('atr', 2.0)) if pd.notna(last_m5.get('atr', 2.0)) else 2.0

                        buy_sl, buy_tp1, buy_tp2, buy_tp3 = Strategy.calculate_pending_zone_sl_tp(
                            h1_df, m5_df, 'BUY', buy_trig, opposite_entry=None
                        )
                        sell_sl, sell_tp1, sell_tp2, sell_tp3 = Strategy.calculate_pending_zone_sl_tp(
                            h1_df, m5_df, 'SELL', sell_trig, opposite_entry=None
                        )

                        target_lot = trade_tracker.get_next_pending_lot()
                        now_ts = time.time()
                        last_why = float(bot_state.get("pending_why_ts", 0) or 0)

                        def _why(msg):
                            if now_ts - last_why >= 12:
                                add_log(msg)
                                bot_state["pending_why_ts"] = now_ts

                        acc = mt5.account_info()
                        if acc is not None and not bool(getattr(acc, 'trade_expert', True)):
                            _why("⛔ MT5 Expert Advisors OFF. Tools→Options→Expert Advisors→Allow algorithmic trading + AutoTrading ON")

                        buy_legs = (
                            (MAGIC_MAIN_BUY, buy_tp1),
                            (MAGIC_TP2_BUY, buy_tp2),
                            (MAGIC_TP3_BUY, buy_tp3),
                        )
                        sell_legs = (
                            (MAGIC_MAIN_SELL, sell_tp1),
                            (MAGIC_TP2_SELL, sell_tp2),
                            (MAGIC_TP3_SELL, sell_tp3),
                        )

                        # State-based Magic Cleanup Function
                        def _clean_unwanted_pendings(keep_magics):
                            for o in orders:
                                mag = int(getattr(o, 'magic', 0) or 0)
                                if 'XAU' in str(getattr(o, 'symbol', '')).upper() and mag not in keep_magics:
                                    if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                                        mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})

                        LIMIT_BUY_MAGICS = (MAGIC_MAIN_BUY, MAGIC_TP2_BUY, MAGIC_TP3_BUY)
                        LIMIT_SELL_MAGICS = (MAGIC_MAIN_SELL, MAGIC_TP2_SELL, MAGIC_TP3_SELL)
                        STOP_BUY_MAGICS = (MAGIC_BREAKOUT_BUY, 100998, 100999)
                        STOP_SELL_MAGICS = (MAGIC_BREAKOUT_SELL, 100900, 100901)

                        def _trim_dupes(orders, keep_magics):
                            keep = []
                            seen = set()
                            for o in sorted(orders, key=lambda x: int(x.ticket)):
                                mag = int(getattr(o, 'magic', 0) or 0)
                                if mag in keep_magics and mag not in seen:
                                    keep.append(o)
                                    seen.add(mag)
                            return keep

                        existing_buy_limits = _trim_dupes([o for o in orders if _is_xau_pending(o, True, mt5.ORDER_TYPE_BUY_LIMIT) and int(getattr(o, 'magic', 0) or 0) in LIMIT_BUY_MAGICS], LIMIT_BUY_MAGICS)
                        existing_sell_limits = _trim_dupes([o for o in orders if _is_xau_pending(o, False, mt5.ORDER_TYPE_SELL_LIMIT) and int(getattr(o, 'magic', 0) or 0) in LIMIT_SELL_MAGICS], LIMIT_SELL_MAGICS)
                        existing_buy_stops = _trim_dupes([o for o in orders if _is_xau_pending(o, True, mt5.ORDER_TYPE_BUY_STOP) and int(getattr(o, 'magic', 0) or 0) in STOP_BUY_MAGICS], STOP_BUY_MAGICS)
                        existing_sell_stops = _trim_dupes([o for o in orders if _is_xau_pending(o, False, mt5.ORDER_TYPE_SELL_STOP) and int(getattr(o, 'magic', 0) or 0) in STOP_SELL_MAGICS], STOP_SELL_MAGICS)

                        def _fill_side(orders, trig, sl_px, legs, order_type):
                            max_target = len(legs)
                            if len(orders) >= max_target:
                                return 0
                            if orders:
                                trig = float(orders[0].price_open)
                                sl_px = float(orders[0].sl) if float(getattr(orders[0], 'sl', 0) or 0) > 0 else sl_px
                            have = {int(getattr(o, 'magic', 0) or 0) for o in orders}
                            placed = 0
                            for mag, tp_px in legs:
                                if mag in have:
                                    continue
                                if len(orders) + placed >= max_target:
                                    break
                                if mt5_client.place_pending_order(
                                    config.SYMBOL, order_type, target_lot, trig, sl_px, tp_px, magic=mag
                                ):
                                    placed += 1
                                else:
                                    _why(f"❌ {order_type} place FAILED @ ${trig} mag={mag}")
                                    break
                            return placed

                        n_buy = 0
                        n_sell = 0

                        pend_mode_cfg = getattr(config, 'PENDING_MODE', 'HYBRID_LIMIT_BREAKOUT').upper()

                        if pend_mode_cfg in ('PMAX_RECOVERY', 'SMC_PMAX_RECOVERY'):
                            # 3-step no-loss: H1 valid stops outside zone + M5 PMAX/HalfTrend gates
                            smc_recovery.manage_smc_pmax_recovery(
                                mt5_client, bot_state, m1_df, m5_df,
                                float(curr_ask), float(curr_bid), add_log,
                                target_lot=target_lot,
                            )
                        elif pend_mode_cfg == 'HYBRID_LIMIT_BREAKOUT':
                            # Exact User Sequential State Flow:
                            # State A (No Active Trade): ONLY place BUY_STOP and SELL_STOP. Cancel any old limit orders.
                            # State B (Buy Trade Active / BUY_STOP hit): Place BUY_LIMIT pullback (sharing same SL) + SELL_STOP at SL. Cancel old buy stops.
                            # State C (Sell Trade Active / SELL_STOP hit): Place SELL_LIMIT pullback (sharing same SL) + BUY_STOP at SL. Cancel old sell stops.

                            # 1. State A: No trade in market -> Place BOTH BUY_STOP and SELL_STOP outside zones.
                            if not has_active_buy and not has_active_sell:
                                # Remove any lingering limit orders
                                for o in orders:
                                    if o.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT):
                                        if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                                            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})

                                v_lvls = Strategy.get_validated_breakout_levels(h1_df, float(curr_ask), float(curr_bid))
                                breakout_buy_trig = float(v_lvls["buy_stop"])
                                breakout_sell_trig = float(v_lvls["sell_stop"])
                                breakout_buy_trig, breakout_sell_trig = smc_recovery._ensure_stops_outside_zones(
                                    breakout_buy_trig, breakout_sell_trig, v_lvls,
                                    float(curr_ask), float(curr_bid), digits,
                                )

                                b_stop_sl, b_stop_tp1, b_stop_tp2, b_stop_tp3 = Strategy.calculate_pending_zone_sl_tp(
                                    h1_df, m5_df, 'BUY', breakout_buy_trig, opposite_entry=None
                                )
                                s_stop_sl, s_stop_tp1, s_stop_tp2, s_stop_tp3 = Strategy.calculate_pending_zone_sl_tp(
                                    h1_df, m5_df, 'SELL', breakout_sell_trig, opposite_entry=None
                                )

                                stop_buy_legs = (
                                    (MAGIC_BREAKOUT_BUY, b_stop_tp1),
                                    (100998, b_stop_tp2),
                                    (100999, b_stop_tp3),
                                )
                                stop_sell_legs = (
                                    (MAGIC_BREAKOUT_SELL, s_stop_tp1),
                                    (100900, s_stop_tp2),
                                    (100901, s_stop_tp3),
                                )

                                n_buy += _fill_side(existing_buy_stops, breakout_buy_trig, b_stop_sl, stop_buy_legs, 'BUY_STOP')
                                n_sell += _fill_side(existing_sell_stops, breakout_sell_trig, s_stop_sl, stop_sell_legs, 'SELL_STOP')

                            # 2. State B: BUY_STOP hit (has active buy trade) -> Place BUY_LIMIT pullback (Upper Zone) + SELL_STOP (Lower SL Zone)
                            elif has_active_buy:
                                for o in orders:
                                    if o.type in (mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_SELL_LIMIT):
                                        if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                                            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})

                                h1_swing_low = float(last_h1['last_low']) if (last_h1 is not None and pd.notna(last_h1.get('last_low'))) else sell_trig

                                buy_positions = [p for p in (mt5_client.get_open_positions() or []) if p.type == mt5.POSITION_TYPE_BUY]
                                raw_sl = float(buy_positions[0].sl) if buy_positions and float(buy_positions[0].sl or 0) > 0 else h1_swing_low

                                # SELL_STOP entry is strictly in the LOWER zone (Buy position SL level)
                                breakout_sell_trig = raw_sl
                                if breakout_sell_trig >= float(curr_bid):
                                    breakout_sell_trig = round(float(curr_bid) - 3.0, digits)

                                # BUY_LIMIT entry is in the UPPER pullback zone (clearly separated above lower SELL_STOP zone)
                                limit_buy_trig = float(pend_lvls.get("limit_buy_trig", buy_trig))
                                min_limit_buy = round(breakout_sell_trig + 4.0, digits)
                                if limit_buy_trig <= min_limit_buy:
                                    limit_buy_trig = min_limit_buy
                                if limit_buy_trig >= float(curr_bid):
                                    limit_buy_trig = round(float(curr_bid) - 1.0, digits)

                                # BUY_LIMIT SL is set to the lower SELL_STOP entry level
                                buy_limit_sl = breakout_sell_trig

                                _, l_buy_tp1, l_buy_tp2, l_buy_tp3 = Strategy.calculate_pending_zone_sl_tp(
                                    h1_df, m5_df, 'BUY', limit_buy_trig, opposite_entry=None
                                )
                                l_buy_legs = (
                                    (MAGIC_MAIN_BUY, l_buy_tp1),
                                    (MAGIC_TP2_BUY, l_buy_tp2),
                                    (MAGIC_TP3_BUY, l_buy_tp3),
                                )
                                if len(existing_buy_limits) < 3:
                                    n_buy += _fill_side(existing_buy_limits, limit_buy_trig, buy_limit_sl, l_buy_legs, 'BUY_LIMIT')

                                # Place SELL_STOP at lower SL zone
                                s_stop_sl, s_stop_tp1, s_stop_tp2, s_stop_tp3 = Strategy.calculate_pending_zone_sl_tp(
                                    h1_df, m5_df, 'SELL', breakout_sell_trig, opposite_entry=None
                                )
                                stop_sell_legs = (
                                    (MAGIC_BREAKOUT_SELL, s_stop_tp1),
                                    (100900, s_stop_tp2),
                                    (100901, s_stop_tp3),
                                )
                                if len(existing_sell_stops) < 3:
                                    n_sell += _fill_side(existing_sell_stops, breakout_sell_trig, s_stop_sl, stop_sell_legs, 'SELL_STOP')

                            # 3. State C: SELL_STOP hit (has active sell trade) -> Place SELL_LIMIT pullback (Lower Zone) + BUY_STOP (Upper SL Zone)
                            elif has_active_sell:
                                for o in orders:
                                    if o.type in (mt5.ORDER_TYPE_SELL_STOP, mt5.ORDER_TYPE_BUY_LIMIT):
                                        if int(getattr(po if "po" in locals() else o, "magic", 0) or 0) in smc_recovery.ALL_ENGINE_MAGICS:
                                            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})

                                h1_swing_high = float(last_h1['last_high']) if (last_h1 is not None and pd.notna(last_h1.get('last_high'))) else buy_trig

                                sell_positions = [p for p in (mt5_client.get_open_positions() or []) if p.type == mt5.POSITION_TYPE_SELL]
                                raw_sl = float(sell_positions[0].sl) if sell_positions and float(sell_positions[0].sl or 0) > 0 else h1_swing_high

                                # BUY_STOP entry is strictly in the UPPER zone (Sell position SL level)
                                breakout_buy_trig = raw_sl
                                if breakout_buy_trig <= float(curr_ask):
                                    breakout_buy_trig = round(float(curr_ask) + 3.0, digits)

                                # SELL_LIMIT entry is in the LOWER pullback zone (clearly separated below upper BUY_STOP zone)
                                limit_sell_trig = float(pend_lvls.get("limit_sell_trig", sell_trig))
                                max_limit_sell = round(breakout_buy_trig - 4.0, digits)
                                if limit_sell_trig >= max_limit_sell:
                                    limit_sell_trig = max_limit_sell
                                if limit_sell_trig <= float(curr_ask):
                                    limit_sell_trig = round(float(curr_ask) + 1.0, digits)

                                # SELL_LIMIT SL is set to the upper BUY_STOP entry level
                                sell_limit_sl = breakout_buy_trig

                                _, l_sell_tp1, l_sell_tp2, l_sell_tp3 = Strategy.calculate_pending_zone_sl_tp(
                                    h1_df, m5_df, 'SELL', limit_sell_trig, opposite_entry=None
                                )
                                l_sell_legs = (
                                    (MAGIC_MAIN_SELL, l_sell_tp1),
                                    (MAGIC_TP2_SELL, l_sell_tp2),
                                    (MAGIC_TP3_SELL, l_sell_tp3),
                                )
                                if len(existing_sell_limits) < 3:
                                    n_sell += _fill_side(existing_sell_limits, limit_sell_trig, sell_limit_sl, l_sell_legs, 'SELL_LIMIT')

                                # Place BUY_STOP at upper SL zone
                                b_stop_sl, b_stop_tp1, b_stop_tp2, b_stop_tp3 = Strategy.calculate_pending_zone_sl_tp(
                                    h1_df, m5_df, 'BUY', breakout_buy_trig, opposite_entry=None
                                )
                                stop_buy_legs = (
                                    (MAGIC_BREAKOUT_BUY, b_stop_tp1),
                                    (100998, b_stop_tp2),
                                    (100999, b_stop_tp3),
                                )
                                if len(existing_buy_stops) < 3:
                                    n_buy += _fill_side(existing_buy_stops, breakout_buy_trig, b_stop_sl, stop_buy_legs, 'BUY_STOP')

                        if n_buy or n_sell:
                            add_log(
                                f"🎯 HYBRID PENDING PLACED: 3 Limit Pullback @{buy_trig:.2f} + 1 Breakout Stop | {target_lot} lot"
                            )

                        # Live-modify open TP2 legs (mid target); TP3 runners handled below
                        open_positions = mt5_client.get_open_positions() or []
                        for p in open_positions:
                            mag = int(getattr(p, 'magic', 0) or 0)
                            if mag not in TP2_MAGICS or float(p.tp or 0) <= 0:
                                continue
                            side = 'BUY' if p.type == mt5.POSITION_TYPE_BUY else 'SELL'
                            opp = float(p.sl) if float(p.sl or 0) > 0 else None
                            try:
                                _, _, live_tp2, _ = Strategy.calculate_pending_zone_sl_tp(
                                    h1_df, m5_df, side, float(p.price_open), opposite_entry=opp
                                )
                                live_tp2 = float(live_tp2)
                                if abs(float(p.tp) - live_tp2) > 1.0:
                                    ok_dir = (side == 'BUY' and live_tp2 > float(p.price_open)) or (
                                        side == 'SELL' and live_tp2 < float(p.price_open)
                                    )
                                    if ok_dir and mt5_client.modify_tp(p.ticket, live_tp2):
                                        add_log(f"🔄 LIVE TP2 {side} pos #{p.ticket}: {float(p.tp):.2f} -> {live_tp2:.2f}")
                            except Exception as e_tp2:
                                add_log(f"TP2 live note #{p.ticket}: {e_tp2}")

                        # Open TP3 runners: virtual target + reverse stop (no broker TP close)
                        manage_tp3_runners(mt5_client, h1_rates=h1_rates, m5_rates=m5_rates)


                    except Exception as e_pend:
                        add_log(f"Pending Engine Note: {str(e_pend)}")



                    
            time.sleep(3)  # Real-time scan every 3 seconds

        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            add_log(f"Bot Loop Exception: {str(e)}\n{tb_str}")
            mt5_client.connected = False
            time.sleep(5)


if __name__ == "__main__":
    import webbrowser
    # Start Flask Web Server in background thread
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    # Automatically open browser dashboard after server starts
    def _open_dashboard_browser():
        time.sleep(2.5)
        try:
            webbrowser.open("http://127.0.0.1:8020")
        except Exception:
            pass

    threading.Thread(target=_open_dashboard_browser, daemon=True).start()



    # Start Bot Loop
    bot_loop()


