import os
import json
import logging
from daily_logger import setup_daily_logger

# Log configuration - Console Output + Daily Entry/Exit File Logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
setup_daily_logger()


# Trading Parameters
SYMBOL = "XAUUSD"
MAGIC_NUMBER = 100999             # Unique Expert Advisor Magic ID for MT5 Order Tracking

# Dynamic Capital Preservation & Trade Parameters
MAX_OPEN_TRADES = 1              # Only 1 trade at a time
ENABLE_FLOATING_PROFIT_CAP = False  # OFF by default — enable from UI if needed (Clear Close Rules keeps OFF)
ENABLE_FLOATING_LOSS_CAP = True     # ON by default from UI input configuration
FLOATING_MAX_PROFIT_PERCENT = 10.0 # Used only when profit cap is enabled from UI
FLOATING_MAX_LOSS_PERCENT = 80.0   # Default 80% loss cap — cuts all trades if account floating loss hits 80%
MAX_DAILY_LOSS_PERCENT = 50.0    # Daily Safety Circuit Breaker: Stop trading for the day if 50% daily loss is hit (No daily profit cap)
MAX_TOTAL_LOSS_PERCENT = 50.0    # Emergency Circuit Breaker: Pause & Stop Bot if Total Balance Loss hits 50%

# Dual Engine Enable Flags (UI shows ON/OFF from these)
ENABLE_PENDING_ENGINE = True       # Pending Order Engine
ENABLE_REGULAR_ENGINE = True       # Regular Order Engine
PENDING_MODE = "PMAX_RECOVERY"     # 3-step no-loss: H1 valid stops outside zone + M5 C1/C2 gates

# Lot sizes
PENDING_BASE_LOT = 0.05            # Pending engine base lot (default 0.02)
REGULAR_BASE_LOT = 0.05            # Regular engine base lot (default 0.02)
PENDING_MAX_LOT = 0.80             # Cap pending martingale

# PMAX_RECOVERY — unlimited 0-loss steps; Step2 keeps 1 runner; Step3+ flatten all
MAX_RECOVERY_STEPS = 99            # Soft cap only; keep recovering until flat
ZONE_EDGE_ATR_FRAC = 0.15
APPROACH_ATR_FRAC = 0.5
REQUIRE_DUAL_TREND = True
MAX_RECOVERY_LOT_MULT = 5.0
BASKET_EQUAL_TOLERANCE = 1.0
NEUTRALIZE_PROFIT_TOLERANCE = 2.0
RECOVERY_TARGET_MOVE = 5.0         # ~50 pips on gold — recovery horizon (required)
RECOVERY_PROFIT_MULTIPLIER = 1.1    # Default 1.1x target: 100% loss recovery + 10% profit target (configurable from UI)
RECOVERY_TARGET_ATR_FRAC = 0.35
RECOVERY_MOVE_FLOOR = 5.0
ZERO_LOSS_BUFFER_USD = 8.0         # Slight profit pad (not bare 0 — covers spread + small profit)
COVER_LEGS = 1                     # Step2: 1 cover recovers full loss; 1 runner kept
PENDING_STOP_SL = 0.0              # Initial BuyStop/SellStop: NO SL

# Backward-compatibility aliases for legacy internal references
SMC_MAX_RECOVERY_STEPS = MAX_RECOVERY_STEPS
SMC_ZONE_EDGE_ATR_FRAC = ZONE_EDGE_ATR_FRAC
SMC_APPROACH_ATR_FRAC = APPROACH_ATR_FRAC
SMC_REQUIRE_DUAL_TREND = REQUIRE_DUAL_TREND
SMC_MAX_RECOVERY_LOT_MULT = MAX_RECOVERY_LOT_MULT
SMC_BASKET_EQUAL_TOLERANCE = BASKET_EQUAL_TOLERANCE
SMC_NEUTRALIZE_PROFIT_TOLERANCE = NEUTRALIZE_PROFIT_TOLERANCE
SMC_RECOVERY_TARGET_MOVE = RECOVERY_TARGET_MOVE
SMC_RECOVERY_PROFIT_MULTIPLIER = RECOVERY_PROFIT_MULTIPLIER
SMC_RECOVERY_TARGET_ATR_FRAC = RECOVERY_TARGET_ATR_FRAC
SMC_RECOVERY_MOVE_FLOOR = RECOVERY_MOVE_FLOOR
SMC_ZERO_LOSS_BUFFER_USD = ZERO_LOSS_BUFFER_USD
SMC_COVER_LEGS = COVER_LEGS
SMC_PENDING_STOP_SL = PENDING_STOP_SL

# Candle confirmation modes
# H1 is fixed LIVE (no close wait).
H1_CONFIRM_CLOSED = False
# M5 closed-candle confirmation ON by default.
M5_CONFIRM_CLOSED = True

# Progressive Drawdown Step Lot Scaling Rule & Recovery Mechanism
INITIAL_BALANCE = 1000.0          # Initial Account Capital Reference
ENABLE_DRAWDOWN_STEP_LOT = True   # Dynamic Step Scaling based on account drawdown
DRAWDOWN_LOT_STEPS = [
    (0.0, 0.01),                  # Initial / Normal state -> 0.01 Lot
    (1.0, 0.02),                  # 1% Total Drawdown -> 0.02 Lot (Step 1 Recovery)
    (3.0, 0.03),                  # 3% Total Drawdown -> 0.03 Lot (Step 2 Recovery)
    (6.0, 0.06),                  # 6% Total Drawdown -> 0.06 Lot (Step 3 Recovery)
]

# High Win-Rate Indicator Thresholds
EMA_FAST = 50
EMA_SLOW = 200
RSI_PERIOD = 14
RSI_OVERSOLD = 30                # Deep oversold area for higher precision BUY
RSI_OVERBOUGHT = 70              # Deep overbought area for higher precision SELL
ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 2.5          # Safe & Wide Stop Loss (Prevents premature SL hits)

# Ultra-Fast Capital Protection (No-Loss Guarantee Rules)
REWARD_TO_RISK_RATIO = 1.1       # Target 1:1.1 RRR profit
REWARD_RISK_RATIO = 1.1          # Alias for Auto SL/TP calculation
# SMC Valid Zone & Reversal Stop Order Parameters
ENABLE_BREAKEVEN = False             # SL does NOT move to breakeven; placed strictly at valid S&R/Order Block zone boundary
ENABLE_RANGING_FILTER = True         # Filter out ranging/consolidation market entries (No stops inside ranges)
POSITION_SPLIT_COUNT = 3             # 3-split orders: Trade 1 (TP1 Must Hit), Trade 2 (TP2 Must Hit), Trade 3 (Runner + Reversal Trigger)
ENABLE_REVERSAL_STOP_ORDERS = True   # Automatically place Buy Stop / Sell Stop outside S&R/Swing zones when runner closes
CONSECUTIVE_LOSS_LIMIT = 9           # 9 consecutive losses (3 batches of 3) triggers daily circuit breaker
FALLBACK_LOT_SIZE = 0.01             # Fallback regular trade lot size after circuit breaker triggers

# Legacy Trail/Protection Flags
ENABLE_PARTIAL_CLOSE = True          # Secure partial profit early
ENABLE_TRAILING_STOP = False         # Keep SL at valid SMC S&R zone boundary
TRAILING_STOP_ATR_MULT = 1.0         # Dynamic Zone Buffer
ENABLE_EARLY_TREND_EXIT = True       # Close trade if trend strictly reverses before SL hit

# Persistent User UI Settings Storage
import sys
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_FILE = os.path.join(BASE_DIR, "user_settings.json")


def save_user_settings():
    """Saves dynamic user UI settings to user_settings.json so changes persist across app restarts."""
    global PENDING_BASE_LOT, REGULAR_BASE_LOT, SMC_RECOVERY_PROFIT_MULTIPLIER
    global REWARD_TO_RISK_RATIO, REWARD_RISK_RATIO, ENABLE_FLOATING_LOSS_CAP
    global FLOATING_MAX_LOSS_PERCENT, ENABLE_FLOATING_PROFIT_CAP
    global FLOATING_MAX_PROFIT_PERCENT, M5_CONFIRM_CLOSED
    global ENABLE_PENDING_ENGINE, ENABLE_REGULAR_ENGINE, SYMBOL

    settings = {
        "PENDING_BASE_LOT": PENDING_BASE_LOT,
        "REGULAR_BASE_LOT": REGULAR_BASE_LOT,
        "SMC_RECOVERY_PROFIT_MULTIPLIER": SMC_RECOVERY_PROFIT_MULTIPLIER,
        "REWARD_TO_RISK_RATIO": REWARD_TO_RISK_RATIO,
        "REWARD_RISK_RATIO": REWARD_RISK_RATIO,
        "ENABLE_FLOATING_LOSS_CAP": ENABLE_FLOATING_LOSS_CAP,
        "FLOATING_MAX_LOSS_PERCENT": FLOATING_MAX_LOSS_PERCENT,
        "ENABLE_FLOATING_PROFIT_CAP": ENABLE_FLOATING_PROFIT_CAP,
        "FLOATING_MAX_PROFIT_PERCENT": FLOATING_MAX_PROFIT_PERCENT,
        "M5_CONFIRM_CLOSED": M5_CONFIRM_CLOSED,
        "ENABLE_PENDING_ENGINE": ENABLE_PENDING_ENGINE,
        "ENABLE_REGULAR_ENGINE": ENABLE_REGULAR_ENGINE,
        "SYMBOL": SYMBOL
    }
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
        logging.info(f"💾 User settings saved to {SETTINGS_FILE}: {settings}")
    except Exception as e:
        logging.error(f"Failed to save user settings to {SETTINGS_FILE}: {e}")


def load_user_settings():
    """Loads user UI settings from user_settings.json if present on startup."""
    global PENDING_BASE_LOT, REGULAR_BASE_LOT, SMC_RECOVERY_PROFIT_MULTIPLIER
    global REWARD_TO_RISK_RATIO, REWARD_RISK_RATIO, ENABLE_FLOATING_LOSS_CAP
    global FLOATING_MAX_LOSS_PERCENT, ENABLE_FLOATING_PROFIT_CAP
    global FLOATING_MAX_PROFIT_PERCENT, M5_CONFIRM_CLOSED
    global ENABLE_PENDING_ENGINE, ENABLE_REGULAR_ENGINE, SYMBOL

    if not os.path.exists(SETTINGS_FILE):
        return

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)

        if "PENDING_BASE_LOT" in settings:
            PENDING_BASE_LOT = float(settings["PENDING_BASE_LOT"])
        if "REGULAR_BASE_LOT" in settings:
            REGULAR_BASE_LOT = float(settings["REGULAR_BASE_LOT"])
        if "SMC_RECOVERY_PROFIT_MULTIPLIER" in settings:
            SMC_RECOVERY_PROFIT_MULTIPLIER = float(settings["SMC_RECOVERY_PROFIT_MULTIPLIER"])
            REWARD_TO_RISK_RATIO = SMC_RECOVERY_PROFIT_MULTIPLIER
            REWARD_RISK_RATIO = SMC_RECOVERY_PROFIT_MULTIPLIER
        if "ENABLE_FLOATING_LOSS_CAP" in settings:
            ENABLE_FLOATING_LOSS_CAP = bool(settings["ENABLE_FLOATING_LOSS_CAP"])
        if "FLOATING_MAX_LOSS_PERCENT" in settings:
            val = settings["FLOATING_MAX_LOSS_PERCENT"]
            FLOATING_MAX_LOSS_PERCENT = float(val) if val is not None else None
        if "ENABLE_FLOATING_PROFIT_CAP" in settings:
            ENABLE_FLOATING_PROFIT_CAP = bool(settings["ENABLE_FLOATING_PROFIT_CAP"])
        if "FLOATING_MAX_PROFIT_PERCENT" in settings:
            val = settings["FLOATING_MAX_PROFIT_PERCENT"]
            FLOATING_MAX_PROFIT_PERCENT = float(val) if val is not None else 10.0
        if "M5_CONFIRM_CLOSED" in settings:
            M5_CONFIRM_CLOSED = bool(settings["M5_CONFIRM_CLOSED"])
        if "ENABLE_PENDING_ENGINE" in settings:
            ENABLE_PENDING_ENGINE = bool(settings["ENABLE_PENDING_ENGINE"])
        if "ENABLE_REGULAR_ENGINE" in settings:
            ENABLE_REGULAR_ENGINE = bool(settings["ENABLE_REGULAR_ENGINE"])
        if "SYMBOL" in settings:
            SYMBOL = str(settings["SYMBOL"])

        logging.info(f"⚙️ Loaded persistent user settings from {SETTINGS_FILE}")
    except Exception as e:
        logging.error(f"Failed to load user settings from {SETTINGS_FILE}: {e}")


# Load any saved user settings immediately when module is imported
load_user_settings()


