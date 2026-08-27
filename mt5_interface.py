import os
import subprocess
import time
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import logging
import config

COMMON_MT5_PATHS = [
    r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe",
    r"C:\Program Files\MetaTrader 5 EXNESS\terminal.exe",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\Exness MetaTrader 5 Terminal\terminal64.exe",
    r"C:\Program Files\MetaTrader 5 - Exness\terminal64.exe",
    r"C:\Program Files (x86)\Exness MetaTrader 5 Terminal\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5 EXNESS\terminal64.exe"
]

def find_mt5_path_from_registry():
    """Queries Windows Registry for installed MetaTrader 5 paths"""
    if os.name != 'nt':
        return None
    try:
        import winreg
        reg_keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hkey, subkey in reg_keys:
            try:
                key = winreg.OpenKey(hkey, subkey)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        sub_key = winreg.OpenKey(key, sub_name)
                        display_name, _ = winreg.QueryValueEx(sub_key, "DisplayName")
                        if "MetaTrader 5" in str(display_name) or "MT5" in str(display_name):
                            install_loc, _ = winreg.QueryValueEx(sub_key, "InstallLocation")
                            if install_loc:
                                t64 = os.path.join(str(install_loc), "terminal64.exe")
                                texe = os.path.join(str(install_loc), "terminal.exe")
                                if os.path.exists(t64):
                                    return t64
                                elif os.path.exists(texe):
                                    return texe
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass
    return None

def auto_start_mt5_terminal():
    reg_path = find_mt5_path_from_registry()
    paths_to_try = [reg_path] + COMMON_MT5_PATHS if reg_path else COMMON_MT5_PATHS

    for path in paths_to_try:
        if path and os.path.exists(path):
            try:
                # 1. Try direct initialization if terminal is already open
                if mt5.initialize(path=path) or mt5.initialize():
                    logging.info(f"Connected to MT5 at: {path}")
                    return True
                
                # 2. Launch MT5 Terminal GUI on Windows
                logging.info(f"Launching MT5 Terminal: {path}")
                if os.name == 'nt' and hasattr(os, 'startfile'):
                    os.startfile(path)
                else:
                    subprocess.Popen([path])
                
                time.sleep(5)
                
                # 3. Retry initialization after terminal launches
                if mt5.initialize(path=path) or mt5.initialize():
                    logging.info(f"MT5 terminal launched and initialized successfully!")
                    return True
            except Exception as e:
                logging.warning(f"Error launching MT5 at {path}: {e}")
    return False

class MT5Interface:
    def __init__(self):
        self.connected = False

    def initialize(self):
        """Initializes MT5 connection and auto-starts MT5 terminal if closed"""
        if not mt5.initialize():
            if not auto_start_mt5_terminal():
                logging.error(f"MT5 Initialization failed. Error code: {mt5.last_error()}")
                return False
        
        self.connected = True
        account_info = mt5.account_info()
        if account_info:
            logging.info(f"Connected to MT5 Account: {account_info.login} | Server: {account_info.server} | Balance: ${account_info.balance:.2f} | Equity: ${account_info.equity:.2f}")
        return True


    def get_algo_trading_status(self):
        """Reads MT5 terminal AutoTrading (Algo Trading) button state — not the in-app bot pause flag."""
        try:
            if not self.connected:
                self.initialize()
            info = mt5.terminal_info()
            if info is None:
                return False, "MT5 NOT CONNECTED"
            allowed = bool(getattr(info, "trade_allowed", False))
            if allowed:
                return True, "ALGO TRADING: ON (MT5 AutoTrading enabled)"
            return False, "ALGO TRADING: OFF (MT5 AutoTrading disabled)"
        except Exception as e:
            return False, f"ALGO TRADING: OFF ({e})"

    def _is_in_trade_session(self, symbol, now_utc):
        """
        Check broker trade sessions for the symbol.
        MT5 day_of_week: 0=Sunday … 6=Saturday.
        Session times follow the trade server clock; UTC wall clock is used as approximation.
        """
        try:
            mt5_day = (now_utc.weekday() + 1) % 7  # Python Mon=0 → MT5 Sun=0
            now_sec = now_utc.hour * 3600 + now_utc.minute * 60 + now_utc.second

            found_any = False
            for session_index in range(8):
                sess = mt5.symbol_info_session_trade(symbol, mt5_day, session_index)
                if sess is None:
                    break
                found_any = True
                # Python MT5 returns namedtuple with fields 'from' and 'to' (datetime.time)
                t_from = getattr(sess, "from", None)
                t_to = getattr(sess, "to", None)
                if t_from is None or t_to is None:
                    try:
                        t_from, t_to = sess[0], sess[1]
                    except Exception:
                        continue
                from_sec = t_from.hour * 3600 + t_from.minute * 60 + t_from.second
                to_sec = t_to.hour * 3600 + t_to.minute * 60 + t_to.second
                if from_sec == to_sec:
                    continue
                if from_sec < to_sec:
                    if from_sec <= now_sec < to_sec:
                        return True
                else:
                    # Overnight wrap (e.g. 22:00 → 06:00)
                    if now_sec >= from_sec or now_sec < to_sec:
                        return True
            if not found_any:
                return None  # No session table — caller falls back to other checks
            return False
        except Exception:
            return None

    def is_market_open(self, symbol):
        """Checks if the market for the given symbol (XAUUSD / BTCUSD) is currently open for trading"""
        try:
            import time
            from datetime import datetime, timezone
            
            sym_upper = str(symbol).upper()
            is_crypto = ("BTC" in sym_upper) or ("ETH" in sym_upper) or ("CRYPTO" in sym_upper)
            now_utc = datetime.now(timezone.utc)
            
            if not is_crypto:
                # Check calendar UTC day & hour for Forex/Gold weekend closure
                weekday = now_utc.weekday() # 0 = Mon, 4 = Fri, 5 = Sat, 6 = Sun
                
                if weekday == 5:
                    return False, "MARKET: CLOSED 🔴 (Weekend - Saturday)"
                if weekday == 6 and now_utc.hour < 22:
                    return False, "MARKET: CLOSED 🔴 (Weekend - Sunday)"
                if weekday == 4 and now_utc.hour >= 22:
                    return False, "MARKET: CLOSED 🔴 (Weekend - Friday Night Close)"

            if not self.connected:
                self.initialize()
                
            # Attempt to select symbol variants (BTCUSD, BTCUSDm, XAUUSD, XAUUSDm)
            matched_sym = symbol
            for s_variant in [symbol, symbol + 'm', symbol + 'c', symbol + 'k']:
                if mt5.symbol_select(s_variant, True):
                    matched_sym = s_variant
                    break
                    
            tick = mt5.symbol_info_tick(matched_sym)
            sym_info = mt5.symbol_info(matched_sym)
            if sym_info is None or tick is None:
                return False, f"MARKET: CLOSED 🔴 ({symbol} No Price Ticks)"
            
            # CLOSEONLY / DISABLED / no new trades = treat as closed for engine entries
            if sym_info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
                return False, f"MARKET: CLOSED 🔴 ({matched_sym} Trade Mode Not Full)"

            # Broker session calendar (most accurate when available)
            if not is_crypto:
                in_session = self._is_in_trade_session(matched_sym, now_utc)
                if in_session is False:
                    return False, f"MARKET: CLOSED 🔴 ({matched_sym} Outside Trade Session)"
                
            curr_time = time.time()
            tick_age = curr_time - tick.time
            # Gold/Forex: no fresh tick for >90s almost always means closed / no quotes
            if tick_age > 90 and not is_crypto:
                return False, f"MARKET: CLOSED 🔴 ({matched_sym} Stale Price / No Live Quotes)"
            # Crypto: allow longer quiet periods
            if tick_age > 600 and is_crypto:
                return False, f"MARKET: CLOSED 🔴 ({matched_sym} Stale Crypto Quotes)"
                
            return True, f"MARKET: OPEN 🟢 ({matched_sym})"
        except Exception as e:
            return False, f"MARKET: CLOSED 🔴 ({str(e)})"




    def shutdown(self):

        """Shutdown MT5 connection"""
        mt5.shutdown()
        self.connected = False
        logging.info("MT5 Connection Closed.")

    def get_account_info(self):
        """Get current account balance, equity, login ID, server"""
        info = mt5.account_info()
        if info:
            return {
                "login": info.login,
                "server": info.server,
                "name": info.name,
                "balance": info.balance,
                "equity": info.equity,
                "currency": info.currency
            }
        return None

    def fetch_rates(self, symbol, timeframe, num_bars=500):
        """Fetch historical candle rates from MT5"""
        for sym in [symbol, symbol + 'm', symbol + 'c', symbol + 'k', symbol + '_i', 'XAUUSDm', 'XAUUSD']:
            mt5.symbol_select(sym, True)
            rates = mt5.copy_rates_from_pos(sym, timeframe, 0, num_bars)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')
                return df
        
        logging.error(f"Failed to fetch rates for {symbol}")
        return None

    def calculate_lot_size(self, symbol, sl_distance):
        """
        Calculates Lot Size dynamically based on account Balance and Risk Percentage (1% of Balance per trade):
        Formula: Lot = (Balance * Risk_Percent) / (SL_Pips * Pip_Value)
        """
        account = self.get_account_info()
        if not account:
            return 0.01

        current_balance = account['balance']
        current_equity = account['equity']
        
        # Base risk percentage per trade (1.0% of Balance)
        risk_percent = 0.01 
        risk_amount = current_balance * risk_percent

        # Default fallback lot calculation based on balance size
        # $1000 balance -> ~0.02 - 0.05 Lot depending on SL distance
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            return 0.01

        # Calculate lot from tick value / point value
        point_val = symbol_info.trade_tick_value if symbol_info.trade_tick_value > 0 else 1.0
        tick_size = symbol_info.trade_tick_size if symbol_info.trade_tick_size > 0 else 0.01
        
        if sl_distance > 0 and point_val > 0 and tick_size > 0:
            sl_points = sl_distance / tick_size
            risk_per_lot = sl_points * point_val
            if risk_per_lot > 0:
                raw_lot = risk_amount / risk_per_lot
            else:
                raw_lot = (current_balance / 1000.0) * 0.01
        else:
            raw_lot = (current_balance / 1000.0) * 0.01

        # Check drawdown step scaling if drawdown occurs
        ref_balance = getattr(config, 'INITIAL_BALANCE', 1000.0)
        if ref_balance > 0 and current_equity < ref_balance:
            drawdown_percent = ((ref_balance - current_equity) / ref_balance) * 100.0
        else:
            drawdown_percent = 0.0

        if hasattr(config, 'DRAWDOWN_LOT_STEPS') and config.ENABLE_DRAWDOWN_STEP_LOT and drawdown_percent > 1.0:
            sorted_steps = sorted(config.DRAWDOWN_LOT_STEPS, key=lambda x: x[0], reverse=True)
            for dd_thresh, lot_val in sorted_steps:
                if drawdown_percent >= dd_thresh:
                    raw_lot = max(raw_lot, lot_val)
                    break

        vol_min = symbol_info.volume_min if symbol_info.volume_min > 0 else 0.01
        vol_max = symbol_info.volume_max if symbol_info.volume_max > 0 else 100.0
        vol_step = symbol_info.volume_step if symbol_info.volume_step > 0 else 0.01

        # Round to valid volume step
        steps = round(raw_lot / vol_step)
        calculated_lot = steps * vol_step
        calculated_lot = max(vol_min, min(vol_max, calculated_lot))

        logging.info(f"BALANCE LOT SCALER: Balance=${current_balance:.2f} | Equity=${current_equity:.2f} | Risk Amount=${risk_amount:.2f} | Selected Lot={calculated_lot:.2f}")
        return round(calculated_lot, 2)

    def money_per_lot_for_move(self, symbol, price_move):
        """Approximate USD (account currency) PnL for 1.0 lot over abs(price_move)."""
        matched = None
        for sym in [symbol + 'm', 'XAUUSDm', symbol + 'c', 'XAUUSDc', symbol, symbol + 'k', symbol + '_i']:
            info = mt5.symbol_info(sym)
            if info:
                matched = sym
                break
        if not matched:
            return max(abs(float(price_move)) * 100.0, 1.0)  # gold-ish fallback
        info = mt5.symbol_info(matched)
        move = abs(float(price_move))
        tick_size = info.trade_tick_size if info.trade_tick_size > 0 else info.point
        tick_value = info.trade_tick_value if info.trade_tick_value > 0 else 1.0
        if tick_size <= 0:
            return max(move * 100.0, 1.0)
        return max((move / tick_size) * tick_value, 1e-6)

    def get_volume_constraints(self, symbol):
        matched = symbol
        for sym in [symbol + 'm', 'XAUUSDm', symbol + 'c', 'XAUUSDc', symbol]:
            info = mt5.symbol_info(sym)
            if info:
                matched = sym
                break
        info = mt5.symbol_info(matched)
        if not info:
            return 0.01, 100.0, 0.01
        return (
            info.volume_min if info.volume_min > 0 else 0.01,
            info.volume_max if info.volume_max > 0 else 100.0,
            info.volume_step if info.volume_step > 0 else 0.01,
        )

    def get_open_positions(self, symbol=None):
        """Get list of current open positions"""
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()
        return positions if positions else []

    def open_order(self, symbol, order_type, lot_size, sl_price, tp_price, magic=100988):
        """Executes a market order with SL and TP (supports suffix symbols & Market Execution 2-step SL/TP)"""
        matched_symbol = None
        for sym in [symbol, symbol + 'm', symbol + 'c', symbol + 'k', symbol + '_i', 'XAUUSDm']:
            info = mt5.symbol_info(sym)
            if info:
                matched_symbol = sym
                break
        
        if not matched_symbol:
            all_symbols = mt5.symbols_get()
            if all_symbols:
                for s in all_symbols:
                    if symbol in s.name:
                        matched_symbol = s.name
                        break

        if not matched_symbol:
            logging.error(f"Symbol {symbol} not found on broker server.")
            return False

        mt5.symbol_select(matched_symbol, True)
        symbol_info = mt5.symbol_info(matched_symbol)
        
        tick = mt5.symbol_info_tick(matched_symbol)
        if not tick:
            logging.error(f"Failed to get tick for {matched_symbol}")
            return False

        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        # Dynamic filling mode matching broker requirement
        filling_type = mt5.ORDER_FILLING_IOC
        if symbol_info:
            filling_mode = symbol_info.filling_mode
            if filling_mode & mt5.ORDER_FILLING_IOC:
                filling_type = mt5.ORDER_FILLING_IOC
            elif filling_mode & mt5.ORDER_FILLING_FOK:
                filling_type = mt5.ORDER_FILLING_FOK
            elif filling_mode & mt5.ORDER_FILLING_RETURN:
                filling_type = mt5.ORDER_FILLING_RETURN

        digits = symbol_info.digits if symbol_info else 5
        req_sl = round(float(sl_price), digits) if sl_price and float(sl_price) > 0 else 0.0
        req_tp = round(float(tp_price), digits) if tp_price and float(tp_price) > 0 else 0.0

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": matched_symbol,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "sl": req_sl,
            "tp": req_tp,
            "deviation": 20,
            "magic": magic,
            "comment": "Antigravity Smart EA" if magic == 100988 else "Manual Dashboard Order",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }

        result = mt5.order_send(request)

        # Fallback for ECN / Market Execution brokers that reject direct SL/TP inside TRADE_ACTION_DEAL
        if result and result.retcode != mt5.TRADE_RETCODE_DONE:
            logging.warning(f"Initial order send failed ({result.retcode}: {result.comment}). Retrying without inline SL/TP for Market Execution compatibility...")
            request["sl"] = 0.0
            request["tp"] = 0.0
            result = mt5.order_send(request)

        if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
            logging.error(f"Order send failed! Retcode: {result.retcode if result else 'None'}, Description: {result.comment if result else 'No response'}")
            return False

        # Guaranteed 2-step SL/TP attach: ensure SL & TP are active on opened position ticket
        if req_sl > 0 or req_tp > 0:
            time.sleep(0.15)
            # 1. Try exact position ticket lookup
            pos_by_ticket = mt5.positions_get(ticket=result.order)
            target_pos = pos_by_ticket[0] if pos_by_ticket else None
            
            # 2. Fallback to latest position sorted by ticket ID
            if not target_pos:
                pos_list = mt5.positions_get(symbol=matched_symbol)
                if pos_list:
                    for p in pos_list:
                        if p.ticket == result.order or getattr(p, 'identifier', 0) == result.order:
                            target_pos = p
                            break
                    if not target_pos:
                        target_pos = sorted(pos_list, key=lambda x: x.ticket, reverse=True)[0]

            if target_pos:
                cur_sl = float(getattr(target_pos, 'sl', 0) or 0)
                cur_tp = float(getattr(target_pos, 'tp', 0) or 0)
                if (req_sl > 0 and abs(cur_sl - req_sl) > 0.01) or (req_tp > 0 and abs(cur_tp - req_tp) > 0.01):
                    sltp_req = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": target_pos.ticket,
                        "symbol": matched_symbol,
                        "sl": req_sl if req_sl > 0 else cur_sl,
                        "tp": req_tp if req_tp > 0 else cur_tp
                    }
                    sltp_res = mt5.order_send(sltp_req)
                    if sltp_res and sltp_res.retcode == mt5.TRADE_RETCODE_DONE:
                        logging.info(f"Attached SL/TP via TRADE_ACTION_SLTP on Ticket #{target_pos.ticket} -> SL: {req_sl}, TP: {req_tp}")
                    else:
                        logging.error(f"Failed to attach SL/TP on Ticket #{target_pos.ticket}: {sltp_res.comment if sltp_res else 'Failed'}")

        logging.info(f"ORDER PLACED SUCCESSFULLY! Ticket: #{result.order}, Type: {'BUY' if order_type == mt5.ORDER_TYPE_BUY else 'SELL'}, Volume: {lot_size}, Price: {price}, SL: {req_sl}, TP: {req_tp}")
        return result

    def place_pending_order(self, symbol, order_type_str, lot_size, trigger_price, sl_price, tp_price, magic=100988):
        """Places a pending BUY_STOP or SELL_STOP order with SL and TP"""
        matched_symbol = None
        for sym in [symbol + 'm', 'XAUUSDm', symbol, symbol + 'c', symbol + 'k', symbol + '_i']:
            info = mt5.symbol_info(sym)
            if info:
                matched_symbol = sym
                break
        
        if not matched_symbol:
            logging.error(f"Symbol {symbol} not found on broker server.")
            return False

        mt5.symbol_select(matched_symbol, True)
        
        order_type_map = {
            'BUY_STOP': mt5.ORDER_TYPE_BUY_STOP,
            'SELL_STOP': mt5.ORDER_TYPE_SELL_STOP,
            'BUY_LIMIT': mt5.ORDER_TYPE_BUY_LIMIT,
            'SELL_LIMIT': mt5.ORDER_TYPE_SELL_LIMIT,
        }
        order_type = order_type_map.get(order_type_str.upper(), mt5.ORDER_TYPE_BUY_STOP)

        comment_map = {
            100994: "TP2 Mid",
            100995: "TP2 Mid",
            100990: "TP3 Reverse Stop",
            100991: "TP3 Reverse Stop",
            100992: "TP3 Runner",
            100993: "TP3 Runner",
        }
        symbol_info = mt5.symbol_info(matched_symbol)

        # Pending orders must use RETURN — IOC/FOK is for market deals and gets rejected
        filling_type = mt5.ORDER_FILLING_RETURN
        if symbol_info:
            filling_mode = symbol_info.filling_mode
            if filling_mode & mt5.ORDER_FILLING_RETURN:
                filling_type = mt5.ORDER_FILLING_RETURN
            elif filling_mode & mt5.ORDER_FILLING_IOC:
                filling_type = mt5.ORDER_FILLING_IOC
            elif filling_mode & mt5.ORDER_FILLING_FOK:
                filling_type = mt5.ORDER_FILLING_FOK

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": matched_symbol,
            "volume": lot_size,
            "type": order_type,
            "price": trigger_price,
            "sl": sl_price,
            "tp": tp_price if tp_price and float(tp_price) > 0 else 0.0,
            "deviation": 20,
            "magic": magic,
            "comment": comment_map.get(magic, "Pending Entry"),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"PENDING ORDER PLACED! Ticket: #{result.order}, Type: {order_type_str}, Price: {trigger_price}, SL: {sl_price}, TP: {tp_price}")
            return result
        else:
            err_msg = result.comment if result else "Unknown error"
            acc = mt5.account_info()
            expert = getattr(acc, 'trade_expert', None) if acc else None
            logging.error(f"Pending Order failed! Description: {err_msg} | expert={expert} | symbol={matched_symbol}")
            return False

    def modify_sl(self, ticket, new_sl):
        """Modifies Stop Loss of an existing open position"""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False

        pos = position[0]
        sym_info = mt5.symbol_info(pos.symbol)
        digits = sym_info.digits if sym_info else 5

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": round(new_sl, digits),
            "tp": round(float(pos.tp or 0), digits)
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"Position #{ticket} SL updated to {new_sl}")
            return True
        else:
            logging.error(f"Failed to update SL for #{ticket}: {result.comment}")
            return False

    def modify_tp(self, ticket, new_tp):
        """Modifies Take Profit (TP) of an existing open position (0 clears TP)."""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False

        pos = position[0]
        sym_info = mt5.symbol_info(pos.symbol)
        digits = sym_info.digits if sym_info else 5
        tp_val = round(float(new_tp), digits) if new_tp is not None and float(new_tp) > 0 else 0.0

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": pos.sl,
            "tp": tp_val
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"Position #{ticket} Dynamic TP updated to {tp_val}")
            return True
        else:
            logging.error(f"Failed to update TP for #{ticket}: {result.comment}")
            return False

    def modify_pending_tp(self, ticket, new_tp):
        """Live-modify Take Profit on an existing pending order (TP3 runner)."""
        orders = mt5.orders_get(ticket=ticket)
        if not orders:
            return False
        order = orders[0]
        sym_info = mt5.symbol_info(order.symbol)
        digits = sym_info.digits if sym_info else 5
        new_tp = round(float(new_tp), digits) if float(new_tp) > 0 else 0.0
        if abs(float(order.tp) - new_tp) < (10 ** (-digits)):
            return True

        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "price": order.price_open,
            "sl": order.sl,
            "tp": new_tp,
            "type_time": order.type_time,
            "type_filling": order.type_filling,
        }
        if order.type_time == mt5.ORDER_TIME_SPECIFIED:
            request["expiration"] = order.time_expiration

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"Pending #{ticket} live TP3 updated to {new_tp}")
            return True
        err = result.comment if result else "Unknown"
        logging.error(f"Failed to modify pending TP #{ticket}: {err}")
        return False

    def modify_pending_order(self, ticket, price=None, sl=None, tp=None, reason=None):
        """Live-modify pending entry / SL / TP (used to keep reverse stop glued to TP3 SL)."""
        orders = mt5.orders_get(ticket=ticket)
        if not orders:
            return False
        order = orders[0]
        sym_info = mt5.symbol_info(order.symbol)
        digits = sym_info.digits if sym_info else 5
        new_price = round(float(price), digits) if price is not None else order.price_open
        new_sl = round(float(sl), digits) if sl is not None else order.sl
        if tp is None:
            new_tp = order.tp
        else:
            new_tp = round(float(tp), digits) if float(tp) > 0 else 0.0

        # Broker-side pre-validation: keep pending stop price outside min stop/freeze distance.
        tick = mt5.symbol_info_tick(order.symbol)
        if tick and price is not None and sym_info:
            point = float(getattr(sym_info, "point", 0.0) or 0.0)
            stop_dist = float(getattr(sym_info, "trade_stops_level", 0) or 0) * point
            freeze_dist = float(getattr(sym_info, "trade_freeze_level", 0) or 0) * point
            min_dist = max(stop_dist, freeze_dist, point)
            ask = float(getattr(tick, "ask", 0.0) or 0.0)
            bid = float(getattr(tick, "bid", 0.0) or 0.0)
            if int(order.type) == int(mt5.ORDER_TYPE_BUY_STOP) and ask > 0:
                min_buy_price = ask + min_dist
                if new_price < min_buy_price:
                    logging.warning(
                        f"Skip pending modify #{ticket}: BUY_STOP too close "
                        f"(new={new_price}, min={round(min_buy_price, digits)}, reason={reason or 'n/a'})"
                    )
                    return False
            elif int(order.type) == int(mt5.ORDER_TYPE_SELL_STOP) and bid > 0:
                max_sell_price = bid - min_dist
                if new_price > max_sell_price:
                    logging.warning(
                        f"Skip pending modify #{ticket}: SELL_STOP too close "
                        f"(new={new_price}, max={round(max_sell_price, digits)}, reason={reason or 'n/a'})"
                    )
                    return False

        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "price": new_price,
            "sl": new_sl,
            "tp": new_tp,
            "type_time": order.type_time,
            "type_filling": order.type_filling,
        }
        if order.type_time == mt5.ORDER_TIME_SPECIFIED:
            request["expiration"] = order.time_expiration

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"Pending #{ticket} modified price={new_price} SL={new_sl} TP={new_tp} reason={reason or 'n/a'}")
            return True
        err = result.comment if result else "Unknown"
        logging.error(f"Failed to modify pending #{ticket}: {err} (reason={reason or 'n/a'})")
        return False

    def modify_sl_tp(self, ticket, new_sl=None, new_tp=None):
        """Modify SL and/or TP on an open position (tp=0 clears take-profit)."""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False
        pos = position[0]
        sym_info = mt5.symbol_info(pos.symbol)
        digits = sym_info.digits if sym_info else 5
        sl = round(float(new_sl), digits) if new_sl is not None else pos.sl
        if new_tp is None:
            tp = pos.tp
        else:
            tp = round(float(new_tp), digits) if float(new_tp) > 0 else 0.0
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": sl,
            "tp": tp,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"Position #{ticket} SL/TP updated SL={sl} TP={tp}")
            return True
        err = result.comment if result else "Unknown"
        logging.error(f"Failed SL/TP modify #{ticket}: {err}")
        return False

    def close_position(self, ticket):
        """Market-close an open position by ticket."""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False
        pos = position[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            return False
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": "TP3 Reversal Close",
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"Position #{ticket} closed (reversal)")
            return True
        err = result.comment if result else "Unknown"
        logging.error(f"Failed close #{ticket}: {err}")
        return False

    def get_pending_orders(self, symbol=None):
        """Get list of current pending orders"""
        if symbol:
            orders = mt5.orders_get(symbol=symbol)
        else:
            orders = mt5.orders_get()
        return list(orders) if orders else []

    def cancel_pending_order(self, ticket):
        """Cancels a pending order on MT5"""
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"Pending order #{ticket} cancelled successfully.")
            return True
        else:
            err = result.comment if result else "Unknown"
            logging.error(f"Failed to cancel pending order #{ticket}: {err}")
            return False

