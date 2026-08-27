import os
import time
import logging
from datetime import datetime, timezone, timedelta

_BD_TZ = timezone(timedelta(hours=6))

# Ensure standard logging formatters (asctime) across Python use Bangladesh Time (UTC+6)
def _bd_time_converter(*args):
    return datetime.now(_BD_TZ).timetuple()

logging.Formatter.converter = _bd_time_converter

import sys

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Entry & Exit Keywords filter
ENTRY_KEYWORDS = [
    "EXECUTED", "ORDER PLACED", "BUY_STOP", "SELL_STOP", "PENDING PLACED",
    "RECOVERY OPEN", "BASE STOPS", "MANUAL BUY", "MANUAL SELL", "EXTRA OPEN",
    "INSTANT REGULAR TRADE TRIGGER", "BUY @", "SELL @"
]

EXIT_KEYWORDS = [
    "POSITION CLOSED", "TP HIT", "SL HIT", "EMERGENCY CLOSE",
    "PROFIT CAP HIT", "LOSS SHIELD HIT", "DRAWDOWN LIMIT HIT", "FLATTEN",
    "ZERO-LOSS", "CANCELLED", "CANCELLED PENDING"
]

def is_entry_or_exit_msg(msg):
    msg_upper = str(msg).upper()

    # Reject non-trade routine logs (e.g., market closed/standby notes)
    if "MARKET IS CURRENTLY CLOSED" in msg_upper or "MARKET: CLOSED" in msg_upper or "STANDBY MODE" in msg_upper or "LIVE NOTE" in msg_upper or "WAITING" in msg_upper or "NOT INSIDE A ZONE" in msg_upper or "CONSOLIDATION BLOCK" in msg_upper:
        if not ("EXECUTED" in msg_upper or "POSITION CLOSED" in msg_upper or "PLACED" in msg_upper):
            return False, None

    for kw in ENTRY_KEYWORDS:
        if kw in msg_upper:
            return True, "ENTRY"

    for kw in EXIT_KEYWORDS:
        if kw in msg_upper:
            return True, "EXIT"

    return False, None

class DailyEntryExitHandler(logging.Handler):
    def emit(self, record):
        try:
            raw_msg = record.getMessage()
            is_valid, log_type = is_entry_or_exit_msg(raw_msg)
            if not is_valid:
                return  # Filter out routine non-trade logs

            now_bd = datetime.now(_BD_TZ)
            today_str = now_bd.strftime("%Y-%m-%d")
            log_file_path = os.path.join(LOG_DIR, f"{today_str}.log")
            timestamp = now_bd.strftime("%Y-%m-%d %H:%M:%S")

            clean_msg = str(raw_msg).encode('ascii', errors='ignore').decode('ascii')
            entry_exit_line = f"[{timestamp}] [{log_type}] {clean_msg}\n"

            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(entry_exit_line)
        except Exception:
            pass

def log_trade_event(event_type, msg):
    """Direct helper to log entry or exit explicitly in Bangladesh Time (UTC+6)."""
    now_bd = datetime.now(_BD_TZ)
    today_str = now_bd.strftime("%Y-%m-%d")
    log_file_path = os.path.join(LOG_DIR, f"{today_str}.log")
    timestamp = now_bd.strftime("%Y-%m-%d %H:%M:%S")
    clean_msg = str(msg).encode('ascii', errors='ignore').decode('ascii')
    line = f"[{timestamp}] [{event_type.upper()}] {clean_msg}\n"
    try:
        with open(log_file_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def setup_daily_logger():
    os.makedirs(LOG_DIR, exist_ok=True)

    # Immediately ensure today's log file exists inside logs/ folder
    now_bd = datetime.now(_BD_TZ)
    today_str = now_bd.strftime("%Y-%m-%d")
    log_file_path = os.path.join(LOG_DIR, f"{today_str}.log")
    if not os.path.exists(log_file_path):
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(f"[{now_bd.strftime('%Y-%m-%d %H:%M:%S')}] [SYSTEM] Daily Log File Initialized (BD Time UTC+6)\n")
        except Exception:
            pass

    root_logger = logging.getLogger()

    # Prevent duplicate DailyEntryExitHandler registration
    for h in root_logger.handlers:
        if isinstance(h, DailyEntryExitHandler):
            return

    handler = DailyEntryExitHandler()
    handler.setLevel(logging.INFO)

    # Remove old FileHandlers pointing to bot.log if any
    for h in list(root_logger.handlers):
        if isinstance(h, logging.FileHandler):
            root_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
            
    root_logger.addHandler(handler)
