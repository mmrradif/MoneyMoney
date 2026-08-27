import os
import json
import time
import logging
import csv
import sys
from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5
import config

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

TRACKER_FILE = os.path.join(BASE_DIR, "trade_tracker.json")
TRADE_AUDIT_CSV = os.path.join(LOG_DIR, "trade_audit_bd.csv")
TRADE_AUDIT_LOG = os.path.join(LOG_DIR, "trade_audit_bd.log")
_BD_TZ = timezone(timedelta(hours=6))


def _bd_now_str():
    return datetime.now(_BD_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _append_trade_audit_row(row):
    """
    Persistent trade audit trail in 2 formats:
    - CSV (Excel-friendly)
    - Plain log (Notepad-friendly)
    """
    headers = [
        "bd_time",
        "event",
        "ticket",
        "account_login",
        "symbol",
        "side",
        "volume",
        "open_price",
        "close_price",
        "sl",
        "tp",
        "profit",
        "reason",
        "badge",
    ]
    safe_row = {k: row.get(k, "") for k in headers}

    # CSV for Excel
    try:
        write_header = (not os.path.exists(TRADE_AUDIT_CSV)) or os.path.getsize(TRADE_AUDIT_CSV) == 0
        with open(TRADE_AUDIT_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            if write_header:
                w.writeheader()
            w.writerow(safe_row)
    except Exception as e:
        logging.error(f"Trade audit CSV write failed: {e}")

    # Plain text for Notepad
    try:
        line = (
            f"[{safe_row['bd_time']}] {safe_row['event']} "
            f"ticket={safe_row['ticket']} acc={safe_row['account_login']} "
            f"sym={safe_row['symbol']} side={safe_row['side']} vol={safe_row['volume']} "
            f"open={safe_row['open_price']} close={safe_row['close_price']} "
            f"sl={safe_row['sl']} tp={safe_row['tp']} pnl={safe_row['profit']} "
            f"badge={safe_row['badge']} reason={safe_row['reason']}"
        )
        with open(TRADE_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logging.error(f"Trade audit text log write failed: {e}")

def load_tracker():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading trade_tracker.json: {e}")
            return {}
    return {}

def save_tracker(data):
    try:
        with open(TRACKER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving trade_tracker.json: {e}")

def record_open_trade(ticket, symbol, pos_type, volume, open_price, sl, tp, open_reason, checklist_summary=None):
    tracker = load_tracker()
    t_str = str(ticket)
    acc_info = mt5.account_info()
    curr_acc = acc_info.login if acc_info else None
    
    tracker[t_str] = {
        "ticket": ticket,
        "account_login": curr_acc,
        "symbol": symbol,
        "type": pos_type,  # "BUY" or "SELL"
        "volume": volume,
        "open_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "open_price": float(open_price),
        "initial_sl": float(sl),
        "initial_tp": float(tp),
        "sl": float(sl),
        "tp": float(tp),
        "sl_moved_to_be": False,
        "open_reason": open_reason,
        "checklist_summary": checklist_summary or [],
        "close_time": None,
        "close_price": None,
        "profit": None,
        "close_reason": None,
        "close_type_badge": None,
        "status": "OPEN"
    }
    save_tracker(tracker)
    _append_trade_audit_row({
        "bd_time": _bd_now_str(),
        "event": "OPEN",
        "ticket": ticket,
        "account_login": curr_acc if curr_acc is not None else "",
        "symbol": symbol,
        "side": pos_type,
        "volume": volume,
        "open_price": float(open_price),
        "close_price": "",
        "sl": float(sl),
        "tp": float(tp),
        "profit": "",
        "reason": open_reason,
        "badge": "OPEN",
    })

def record_trade_update(ticket, sl_moved_to_be=None, sl=None, tp=None):
    tracker = load_tracker()
    t_str = str(ticket)
    if t_str in tracker:
        if sl_moved_to_be is not None:
            tracker[t_str]["sl_moved_to_be"] = sl_moved_to_be
        if sl is not None:
            tracker[t_str]["sl"] = float(sl)
        if tp is not None:
            tracker[t_str]["tp"] = float(tp)
        save_tracker(tracker)

def record_close_trade(ticket, close_price, close_reason, close_type_badge, profit=0.0):
    tracker = load_tracker()
    t_str = str(ticket)
    tracked = tracker.get(t_str, {})
    acc_info = mt5.account_info()
    curr_acc = acc_info.login if acc_info else tracked.get("account_login")
    if t_str in tracker:
        tracker[t_str]["close_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tracker[t_str]["close_price"] = float(close_price)
        tracker[t_str]["profit"] = float(profit)
        tracker[t_str]["close_reason"] = close_reason
        tracker[t_str]["close_type_badge"] = close_type_badge
        tracker[t_str]["status"] = "CLOSED"
        save_tracker(tracker)
    _append_trade_audit_row({
        "bd_time": _bd_now_str(),
        "event": "CLOSE",
        "ticket": ticket,
        "account_login": curr_acc if curr_acc is not None else "",
        "symbol": tracked.get("symbol", ""),
        "side": tracked.get("type", ""),
        "volume": tracked.get("volume", ""),
        "open_price": tracked.get("open_price", ""),
        "close_price": float(close_price),
        "sl": tracked.get("sl", tracked.get("initial_sl", "")),
        "tp": tracked.get("tp", tracked.get("initial_tp", "")),
        "profit": float(profit),
        "reason": close_reason,
        "badge": close_type_badge,
    })

def get_enriched_trade_history():
    """Fetches MT5 deals history and enriches with tracked Open/Close reasons filtered strictly by active connected MT5 account"""
    tracker = load_tracker()
    history = []
    
    acc_info = mt5.account_info()
    curr_acc = acc_info.login if acc_info else None
    
    # 1. Fetch deals from MT5 history for active logged in account
    now = time.time()
    deals = mt5.history_deals_get(0, int(now))
    
    # Map deals by position_id
    closed_deals = {}
    entry_deals = {}
    valid_mt5_tickets = set()
    
    if deals:
        for d in deals:
            if d.symbol and d.position_id > 0:
                pos_id = str(d.position_id)
                valid_mt5_tickets.add(pos_id)
                if d.entry == mt5.DEAL_ENTRY_IN:
                    entry_deals[pos_id] = d
                elif d.entry == mt5.DEAL_ENTRY_OUT:
                    closed_deals[pos_id] = d

    # Combine tracked items and MT5 deal history strictly for current account
    all_tickets = set()
    for t_str, tracked in tracker.items():
        rec_acc = tracked.get("account_login")
        # Include if ticket exists in current MT5 account deals history OR rec_acc matches active MT5 account
        if t_str in valid_mt5_tickets or (curr_acc is not None and rec_acc == curr_acc):
            all_tickets.add(t_str)

    # Include any remaining deals from MT5 history
    all_tickets = all_tickets.union(valid_mt5_tickets)
    
    for t_str in sorted(all_tickets, key=lambda x: int(x), reverse=True):
        tracked = tracker.get(t_str, {})
        deal_out = closed_deals.get(t_str)
        deal_in = entry_deals.get(t_str)
        
        ticket = int(t_str)
        symbol = tracked.get("symbol") or (deal_out.symbol if deal_out else (deal_in.symbol if deal_in else "BTCUSD"))
        pos_type = tracked.get("type") or ("BUY" if (deal_in and deal_in.type == mt5.ORDER_TYPE_BUY) or (deal_out and deal_out.type == mt5.ORDER_TYPE_SELL) else "SELL")
        volume = tracked.get("volume") or (deal_out.volume if deal_out else (deal_in.volume if deal_in else 0.01))
        
        open_time = tracked.get("open_time") or (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(deal_in.time)) if deal_in else "-")
        open_price = tracked.get("open_price") or (deal_in.price if deal_in else 0.0)
        
        sl = tracked.get("initial_sl") or tracked.get("sl") or 0.0
        tp = tracked.get("initial_tp") or tracked.get("tp") or 0.0
        sl_moved_to_be = tracked.get("sl_moved_to_be", False)
        
        # Open Reason
        if tracked.get("open_reason"):
            open_reason = tracked["open_reason"]
        else:
            open_reason = f"{pos_type} Signal Entry | Zone Strategy Alignment | Dynamic Risk Management"
        
        checklist_summary = tracked.get("checklist_summary", [])
        
        # Close Details & Close Reason
        status = tracked.get("status")
        if deal_out:
            status = "CLOSED"
            close_time = tracked.get("close_time") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(deal_out.time))
            close_price = tracked.get("close_price") or deal_out.price
            profit = round(deal_out.profit + deal_out.swap + deal_out.commission, 2)
            
            # Determine Close Reason if not explicitly set
            if tracked.get("close_reason"):
                close_reason = tracked["close_reason"]
                close_type_badge = tracked.get("close_type_badge", "CLOSED")
            else:
                # Deduce close reason from deal comment / price action
                comment = deal_out.comment.lower() if deal_out.comment else ""
                
                if "tp" in comment or (tp > 0 and ((pos_type == "BUY" and close_price >= tp - 2) or (pos_type == "SELL" and close_price <= tp + 2))):
                    close_reason = f"🎯 Take Profit Hit: Target TP reached @ ${close_price:.2f}"
                    close_type_badge = "TAKE_PROFIT"
                elif sl_moved_to_be or (profit >= 0 and abs(close_price - open_price) <= 10.0):
                    close_reason = f"🛡️ Zero-Loss Shield: Breakeven SL hit @ ${close_price:.2f} (Capital Protected with +${profit:.2f} profit)"
                    close_type_badge = "BREAKEVEN"
                elif "sl" in comment or profit < 0:
                    close_reason = f"🛑 Stop Loss Hit: Price reached initial SL level @ ${close_price:.2f}"
                    close_type_badge = "STOP_LOSS"
                else:
                    close_reason = f"Closed by MT5 Market Order @ ${close_price:.2f}"
                    close_type_badge = "CLOSED"
        else:
            if status == "CLOSED":
                close_time = tracked.get("close_time", "-")
                close_price = tracked.get("close_price", 0.0)
                profit = tracked.get("profit", 0.0)
                close_reason = tracked.get("close_reason", "Closed")
                close_type_badge = tracked.get("close_type_badge", "CLOSED")
            else:
                status = "OPEN"
                close_time = "-"
                close_price = None
                profit = None
                close_reason = "Trade active on MT5 - Monitoring Rule-Drop Cut-off (<=3) & TP"
                close_type_badge = "ACTIVE"
        
        history.append({
            "ticket": ticket,
            "symbol": symbol,
            "type": pos_type,
            "volume": volume,
            "open_time": open_time,
            "open_price": round(float(open_price), 2),
            "sl": round(float(sl), 2),
            "tp": round(float(tp), 2),
            "sl_moved_to_be": sl_moved_to_be,
            "open_reason": open_reason,
            "checklist_summary": checklist_summary,
            "status": status,
            "close_time": close_time,
            "close_price": round(float(close_price), 2) if close_price is not None else None,
            "profit": profit,
            "close_reason": close_reason,
            "close_type_badge": close_type_badge
        })
        
    return history

def get_consecutive_loss_count(comment_filter=""):
    """Counts consecutive loss trades for current day to enforce 9-loss circuit breaker"""
    history = get_enriched_trade_history()
    today_str = time.strftime("%Y-%m-%d")
    
    consecutive_losses = 0
    for trade in history:
        if trade.get("status") == "CLOSED":
            close_time = trade.get("close_time", "")
            if close_time.startswith(today_str):
                comment = str(trade.get("open_reason", ""))
                # Isolated dashboard/manual trades never affect bot loss streaks
                if "Manual" in comment or "ISOLATED" in comment:
                    continue
                if comment_filter and comment_filter not in comment:
                    continue
                profit = trade.get("profit")
                if profit is not None:
                    if profit < 0:
                        consecutive_losses += 1
                    else:
                        break # Break consecutive streak on profitable trade
    return consecutive_losses

def get_consecutive_pending_loss_count():
    """Counts consecutive losses specifically for Pending Stop Orders"""
    return get_consecutive_loss_count(comment_filter="Pending")

def is_circuit_breaker_active():
    """Returns True if 9 consecutive pending stop trades hit loss in a single day"""
    limit = getattr(config, 'CONSECUTIVE_LOSS_LIMIT', 9)
    return get_consecutive_pending_loss_count() >= limit

def get_engine_loss_streak(engine_filter="Pending"):
    """Consecutive closed losses for Pending engine, newest first."""
    history = get_enriched_trade_history()
    loss_streak = 0
    for trade in history:
        if trade.get("status") != "CLOSED":
            continue
        reason = str(trade.get("open_reason", "")) + " " + str(trade.get("close_reason", ""))
        if "Manual" in reason or "ISOLATED" in reason:
            continue
        if "Pending" not in reason and "PENDING" not in reason.upper() and "pending" not in reason.lower():
            continue
        profit = trade.get("profit")
        if profit is None:
            continue
        if profit < 0:
            loss_streak += 1
        else:
            break
    return loss_streak

def get_next_pending_lot():
    """Next Pending lot: base 0.02, doubles on consecutive Pending losses (0.02 → 0.04 → 0.08 → 0.16)."""
    base_lot = float(getattr(config, 'PENDING_BASE_LOT', 0.02))
    max_lot = float(getattr(config, 'PENDING_MAX_LOT', 0.80))
    loss_streak = get_engine_loss_streak("Pending")
    computed_lot = base_lot * (2 ** loss_streak)
    return round(min(max_lot, computed_lot), 2)

def get_lot_status():
    """UI payload: base + current/next lot (+ pending streak only)."""
    pend_base = float(getattr(config, 'PENDING_BASE_LOT', 0.02))
    pend_streak = get_engine_loss_streak("Pending")
    return {
        "pending_base_lot": pend_base,
        "pending_current_lot": get_next_pending_lot(),
        "pending_loss_streak": pend_streak,
        "pending_doubling": True,
    }

def get_today_net_pnl():
    """Calculates total net profit/loss for closed trades today (excludes isolated Manual)."""
    history = get_enriched_trade_history()
    today_str = time.strftime("%Y-%m-%d")
    
    net_pnl = 0.0
    for trade in history:
        if trade.get("status") == "CLOSED":
            close_time = trade.get("close_time", "")
            if close_time.startswith(today_str):
                reason = str(trade.get("open_reason", "")) + " " + str(trade.get("close_reason", ""))
                if "Manual" in reason or "ISOLATED" in reason:
                    continue
                profit = trade.get("profit")
                if profit is not None:
                    net_pnl += float(profit)
    return net_pnl

def is_daily_drawdown_limit_reached(account_balance=1000.0, open_positions=None):
    """Returns True if daily net loss (closed PnL + open floating PnL) >= 10% of account balance"""
    max_daily_loss_pct = getattr(config, 'MAX_DAILY_LOSS_PERCENT', 10.0)
    net_pnl = get_today_net_pnl()

    # Include floating PnL of open positions
    if open_positions:
        floating_pnl = sum(float(getattr(p, 'profit', 0) or 0) + float(getattr(p, 'swap', 0) or 0) for p in open_positions)
        net_pnl += floating_pnl
    
    # Net loss is negative, check if total net_pnl <= -(10% of balance)
    max_allowed_loss = -1.0 * (account_balance * (max_daily_loss_pct / 100.0))
    return net_pnl <= max_allowed_loss



