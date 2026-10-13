import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

ZPI_API_KEY = os.getenv("ZPI_API_KEY", "").strip()
ZPI_BASE_URL = os.getenv("ZPI_BASE_URL", "").strip().rstrip("/")
ZPI_QUOTE_PATH = os.getenv("ZPI_QUOTE_PATH", "quote").strip()
ZPI_SUMMARY_PATH = os.getenv("ZPI_SUMMARY_PATH", "summary").strip()
ZPI_BROKER_PATH = os.getenv("ZPI_BROKER_PATH", "").strip()
ZPI_IDX_BASE_URL = os.getenv("ZPI_IDX_BASE_URL", "https://api.zapi.ink/v1/finance:idx").strip().rstrip("/")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_GROUP_CHAT_ID = os.getenv("TELEGRAM_GROUP_CHAT_ID", "").strip()

# Independent private-only PTSN-pattern momentum. Shares the existing scanner/cache.
PTSN_STRUCTURE_ENABLED = os.getenv("PTSN_STRUCTURE_ENABLED", "true").lower() in {"1", "true", "yes", "y"}
PTSN_STRUCTURE_COOLDOWN_MINUTES = int(os.getenv("PTSN_STRUCTURE_COOLDOWN_MINUTES", "90"))
PTSN_STRUCTURE_MAX_ALERTS_PER_CYCLE = int(os.getenv("PTSN_STRUCTURE_MAX_ALERTS_PER_CYCLE", "3"))

USE_MULTI_QUOTE = os.getenv("USE_MULTI_QUOTE", "true").lower() in {"1", "true", "yes", "y"}
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", "50"))
FORCE_RUN = os.getenv("FORCE_RUN", "false").lower() in {"1", "true", "yes", "y"}
MICRO_MAX_CANDIDATES = int(os.getenv("MICRO_MAX_CANDIDATES", "6"))
FLOW_SCREENER_ENABLED = os.getenv("FLOW_SCREENER_ENABLED", "true").lower() in {"1", "true", "yes", "y"}
FLOW_RESERVED_SLOTS = int(os.getenv("FLOW_RESERVED_SLOTS", "2"))
FLOW_PRE_MIN_SCORE = float(os.getenv("FLOW_PRE_MIN_SCORE", "50"))
FLOW_ALERT_THRESHOLD = float(os.getenv("FLOW_ALERT_THRESHOLD", "75"))
FLOW_ALERT_COOLDOWN_MINUTES = int(os.getenv("FLOW_ALERT_COOLDOWN_MINUTES", "60"))

# Filter 2 is a relaxed clone of the primary momentum engine. Existing FLOW_*
# environment variables remain accepted as fallbacks so production does not
# require an immediate Secrets/Variables migration.
FILTER2_ENABLED = os.getenv(
    "FILTER2_ENABLED", os.getenv("FLOW_SCREENER_ENABLED", "true")
).lower() in {"1", "true", "yes", "y"}
FILTER2_RESERVED_SLOTS = int(
    os.getenv("FILTER2_RESERVED_SLOTS", os.getenv("FLOW_RESERVED_SLOTS", "2"))
)
FILTER2_MIN_PRE_SCORE = float(os.getenv("FILTER2_MIN_PRE_SCORE", "25"))
FILTER2_DAILY_VOLUME_MA_RATIO_MIN = float(
    os.getenv("FILTER2_DAILY_VOLUME_MA_RATIO_MIN", "1.20")
)
FILTER2_LEAKAGE_THRESHOLD = float(os.getenv("FILTER2_LEAKAGE_THRESHOLD", "60"))
FILTER2_MOMENTUM_THRESHOLD = float(os.getenv("FILTER2_MOMENTUM_THRESHOLD", "70"))
FILTER2_PRE_MOMENTUM_THRESHOLD = float(
    os.getenv("FILTER2_PRE_MOMENTUM_THRESHOLD", "55")
)
FILTER2_MICRO_MIN_CONFIRM_SCORE = float(
    os.getenv("FILTER2_MICRO_MIN_CONFIRM_SCORE", "40")
)
FILTER2_PRE_MOMENTUM_MIN_MICRO_SCORE = float(
    os.getenv("FILTER2_PRE_MOMENTUM_MIN_MICRO_SCORE", "45")
)
FILTER2_PRE_MOMENTUM_MIN_AGGRESSIVE_BUY = float(
    os.getenv("FILTER2_PRE_MOMENTUM_MIN_AGGRESSIVE_BUY", "0.55")
)
FILTER2_PRE_MOMENTUM_MIN_INTERVAL_RETURN_PCT = float(
    os.getenv("FILTER2_PRE_MOMENTUM_MIN_INTERVAL_RETURN_PCT", "0.00")
)
FILTER2_PRE_MOMENTUM_MIN_BREAKOUT_PCT = float(
    os.getenv("FILTER2_PRE_MOMENTUM_MIN_BREAKOUT_PCT", "-0.50")
)
FILTER2_MIN_BREAKOUT_PCT = float(os.getenv("FILTER2_MIN_BREAKOUT_PCT", "0.00"))
FILTER2_MOMENTUM_MIN_INTERVAL_RETURN_PCT = float(
    os.getenv("FILTER2_MOMENTUM_MIN_INTERVAL_RETURN_PCT", "0.15")
)
FILTER2_PRE_MOMENTUM_MIN_POSITION_IN_RANGE = float(
    os.getenv("FILTER2_PRE_MOMENTUM_MIN_POSITION_IN_RANGE", "0.60")
)
FILTER2_IGNITION_THRESHOLD = float(os.getenv("FILTER2_IGNITION_THRESHOLD", "55"))
FILTER2_IGNITION_MIN_VOLUME_SPIKE = float(
    os.getenv("FILTER2_IGNITION_MIN_VOLUME_SPIKE", "2.0")
)
FILTER2_IGNITION_MIN_MICRO_SCORE = float(
    os.getenv("FILTER2_IGNITION_MIN_MICRO_SCORE", "45")
)
FILTER2_IGNITION_MIN_AGGRESSIVE_BUY = float(
    os.getenv("FILTER2_IGNITION_MIN_AGGRESSIVE_BUY", "0.55")
)
FILTER2_ALERT_COOLDOWN_MINUTES = int(
    os.getenv(
        "FILTER2_ALERT_COOLDOWN_MINUTES",
        os.getenv("FLOW_ALERT_COOLDOWN_MINUTES", "60"),
    )
)
FILTER2_MIN_POSITION_IN_RANGE = float(
    os.getenv("FILTER2_MIN_POSITION_IN_RANGE", "0.30")
)
MICRO_MIN_PRE_SCORE = float(os.getenv("MICRO_MIN_PRE_SCORE", "35"))
MICRO_MIN_CONFIRM_SCORE = float(os.getenv("MICRO_MIN_CONFIRM_SCORE", "45"))
IGNITION_RESERVED_SLOTS = int(os.getenv("IGNITION_RESERVED_SLOTS", "2"))
IGNITION_THRESHOLD = float(os.getenv("IGNITION_THRESHOLD", "65"))
IGNITION_MIN_VOLUME_SPIKE = float(os.getenv("IGNITION_MIN_VOLUME_SPIKE", "3.0"))
IGNITION_MIN_MICRO_SCORE = float(os.getenv("IGNITION_MIN_MICRO_SCORE", "55"))
IGNITION_MIN_AGGRESSIVE_BUY = float(os.getenv("IGNITION_MIN_AGGRESSIVE_BUY", "0.65"))
IGNITION_MIN_INTERVAL_RETURN_PCT = float(os.getenv("IGNITION_MIN_INTERVAL_RETURN_PCT", "-0.30"))
IGNITION_MAX_INTERVAL_RETURN_PCT = float(os.getenv("IGNITION_MAX_INTERVAL_RETURN_PCT", "1.50"))
IGNITION_MAX_DAILY_CHANGE_PCT = float(os.getenv("IGNITION_MAX_DAILY_CHANGE_PCT", "4.0"))
RUNNING_TRADE_MIN_LOT = int(os.getenv("RUNNING_TRADE_MIN_LOT", "100"))
RUNNING_TRADE_MAX_AGE_MINUTES = int(os.getenv("RUNNING_TRADE_MAX_AGE_MINUTES", "15"))
BIG_PRINT_MIN_LOT = int(os.getenv("BIG_PRINT_MIN_LOT", "500"))

# Internal Tradeflow Confirmation Layer. This is observational only for now:
# it does not change Filter 1/2 eligibility and is not rendered to Telegram.
TRADEFLOW_INTERNAL_ENABLED = os.getenv(
    "TRADEFLOW_INTERNAL_ENABLED", "true"
).lower() in {"1", "true", "yes", "y"}
TRADEFLOW_LOT_SIZE = int(os.getenv("TRADEFLOW_LOT_SIZE", "100"))
TRADEFLOW_TREND_SNAPSHOTS = int(os.getenv("TRADEFLOW_TREND_SNAPSHOTS", "4"))
TRADEFLOW_TREND_MIN_SNAPSHOTS = int(
    os.getenv("TRADEFLOW_TREND_MIN_SNAPSHOTS", "3")
)
TRADEFLOW_TREND_FLAT_TOLERANCE_PCT = float(
    os.getenv("TRADEFLOW_TREND_FLAT_TOLERANCE_PCT", "8.0")
)

RECENT_SNAPSHOTS = int(os.getenv("RECENT_SNAPSHOTS", "12"))
MIN_BASELINE_INTERVALS = int(os.getenv("MIN_BASELINE_INTERVALS", "4"))

LEAKAGE_THRESHOLD = float(os.getenv("LEAKAGE_THRESHOLD", "70"))
MOMENTUM_THRESHOLD = float(os.getenv("MOMENTUM_THRESHOLD", "80"))
PRE_MOMENTUM_THRESHOLD = float(os.getenv("PRE_MOMENTUM_THRESHOLD", "65"))
PRE_MOMENTUM_MIN_MICRO_SCORE = float(os.getenv("PRE_MOMENTUM_MIN_MICRO_SCORE", "55"))
PRE_MOMENTUM_MIN_AGGRESSIVE_BUY = float(os.getenv("PRE_MOMENTUM_MIN_AGGRESSIVE_BUY", "0.60"))
PRE_MOMENTUM_MIN_INTERVAL_RETURN_PCT = float(os.getenv("PRE_MOMENTUM_MIN_INTERVAL_RETURN_PCT", "0.10"))
PRE_MOMENTUM_MIN_BREAKOUT_PCT = float(os.getenv("PRE_MOMENTUM_MIN_BREAKOUT_PCT", "-0.30"))
PRE_MOMENTUM_MIN_POSITION_IN_RANGE = float(os.getenv("PRE_MOMENTUM_MIN_POSITION_IN_RANGE", "0.70"))
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "30"))
SIGNAL_REARM_MINUTES = int(os.getenv("SIGNAL_REARM_MINUTES", "30"))
MARKET_CAP_FILTER_ENABLED = os.getenv("MARKET_CAP_FILTER_ENABLED", "true").lower() in {"1", "true", "yes", "y"}
MIN_MARKET_CAP_IDR = float(os.getenv("MIN_MARKET_CAP_IDR", "100000000000"))
MAX_MARKET_CAP_IDR = float(os.getenv("MAX_MARKET_CAP_IDR", "10000000000000"))
MARKET_CAP_CACHE_HOURS = int(os.getenv("MARKET_CAP_CACHE_HOURS", "24"))
MAX_ALERT_PRICE = float(os.getenv("MAX_ALERT_PRICE", "500"))
DAILY_FILTER_ENABLED = os.getenv("DAILY_FILTER_ENABLED", "true").lower() in {"1", "true", "yes", "y"}
VOLUME_MA_SHORT = int(os.getenv("VOLUME_MA_SHORT", "10"))
VOLUME_MA_LONG = int(os.getenv("VOLUME_MA_LONG", "100"))
VOLUME_MA_RATIO_MIN = float(os.getenv("VOLUME_MA_RATIO_MIN", "2.0"))
VALUE_MA_WINDOW = int(os.getenv("VALUE_MA_WINDOW", "5"))
MIN_VALUE_MA_IDR = float(os.getenv("MIN_VALUE_MA_IDR", "500000000"))

# Bot 4 V2 Filter 1 liquidity quality gate. This uses the existing stock-history
# payload, so it adds no extra ZAPI endpoint call. Filter 2 and the PTSN
# structural lane intentionally do not hard-require this threshold.
FILTER1_VALUE_MA50_ENABLED = os.getenv(
    "FILTER1_VALUE_MA50_ENABLED", "true"
).lower() in {"1", "true", "yes", "y"}
FILTER1_VALUE_MA50_WINDOW = int(os.getenv("FILTER1_VALUE_MA50_WINDOW", "50"))
FILTER1_MIN_VALUE_MA50_IDR = float(
    os.getenv("FILTER1_MIN_VALUE_MA50_IDR", "5000000000")
)

# Final quality/ranking layer. Only new Filter 1 alerts/upgrades compete for
# these slots; suppressed candidates remain observable and may rank later.
FILTER1_TOP_N_ALERTS_PER_CYCLE = int(
    os.getenv("FILTER1_TOP_N_ALERTS_PER_CYCLE", "3")
)
QUALITY_SCORE_ENABLED = os.getenv(
    "QUALITY_SCORE_ENABLED", "true"
).lower() in {"1", "true", "yes", "y"}

# Post-alert entry guidance. This changes Telegram messaging only; it does not
# alter Filter 1/2 eligibility, ranking, or execute orders.
ENTRY_SETUP_ENABLED = os.getenv(
    "ENTRY_SETUP_ENABLED", "true"
).lower() in {"1", "true", "yes", "y"}
ENTRY_PULLBACK_TICKS = int(os.getenv("ENTRY_PULLBACK_TICKS", "2"))
ENTRY_MIN_AGGRESSIVE_BUY_RATIO = float(
    os.getenv("ENTRY_MIN_AGGRESSIVE_BUY_RATIO", "0.55")
)
ENTRY_MIN_BID_PERCENT = float(
    os.getenv("ENTRY_MIN_BID_PERCENT", "50")
)

# Live post-alert reversal engine. It only watches a small set of recently
# alerted tickers and activates matched-trade analysis after pullback/retest.
REVERSAL_LIVE_ENABLED = os.getenv(
    "REVERSAL_LIVE_ENABLED", "true"
).lower() in {"1", "true", "yes", "y"}
EOD_ENTRY_ENGINE_ENABLED = os.getenv(
    "EOD_ENTRY_ENGINE_ENABLED", "true"
).lower() in {"1", "true", "yes", "y"}
EOD_ENTRY_LIVE_PLAN_LIMIT = int(os.getenv("EOD_ENTRY_LIVE_PLAN_LIMIT", "20"))
REVERSAL_WATCH_MINUTES = int(os.getenv("REVERSAL_WATCH_MINUTES", "480"))
REVERSAL_MAX_WATCHES = int(os.getenv("REVERSAL_MAX_WATCHES", "3"))
REVERSAL_CONFIRM_SCORE = float(os.getenv("REVERSAL_CONFIRM_SCORE", "80"))
REVERSAL_BUILDING_SCORE = float(os.getenv("REVERSAL_BUILDING_SCORE", "70"))
REVERSAL_MIN_NEW_TRADES = int(os.getenv("REVERSAL_MIN_NEW_TRADES", "3"))
REVERSAL_MIN_AGGRESSIVE_BUY_RATIO = float(
    os.getenv("REVERSAL_MIN_AGGRESSIVE_BUY_RATIO", "0.60")
)
REVERSAL_MIN_NET_AGGRESSION_IDR = float(
    os.getenv("REVERSAL_MIN_NET_AGGRESSION_IDR", "50000000")
)
REVERSAL_MIN_BID_PERCENT = float(
    os.getenv("REVERSAL_MIN_BID_PERCENT", "50")
)
REVERSAL_MIN_ABSORBED_SELL_IDR = float(
    os.getenv("REVERSAL_MIN_ABSORBED_SELL_IDR", "50000000")
)
REVERSAL_FAILED_ALERT_ENABLED = os.getenv(
    "REVERSAL_FAILED_ALERT_ENABLED", "true"
).lower() in {"1", "true", "yes", "y"}

# Selling absorption is a soft smart-money signal, not a hard entry gate.
SELLING_ABSORPTION_MIN_SELL_VALUE_IDR = float(
    os.getenv("SELLING_ABSORPTION_MIN_SELL_VALUE_IDR", "250000000")
)
SELLING_ABSORPTION_MIN_BID_PERCENT = float(
    os.getenv("SELLING_ABSORPTION_MIN_BID_PERCENT", "55")
)
SELLING_ABSORPTION_MIN_POSITION_IN_RANGE = float(
    os.getenv("SELLING_ABSORPTION_MIN_POSITION_IN_RANGE", "0.60")
)
SELLING_ABSORPTION_MIN_INTERVAL_RETURN_PCT = float(
    os.getenv("SELLING_ABSORPTION_MIN_INTERVAL_RETURN_PCT", "-0.30")
)

DAILY_FILTER_CACHE_HOURS = int(os.getenv("DAILY_FILTER_CACHE_HOURS", "18"))
STOCK_HISTORY_LENGTH = int(os.getenv("STOCK_HISTORY_LENGTH", "120"))
SHAREHOLDER_FILTER_ENABLED = os.getenv("SHAREHOLDER_FILTER_ENABLED", "true").lower() in {"1", "true", "yes", "y"}
MAX_SHAREHOLDERS = int(os.getenv("MAX_SHAREHOLDERS", "5000"))
SHAREHOLDER_CACHE_HOURS = int(os.getenv("SHAREHOLDER_CACHE_HOURS", "168"))
SHAREHOLDER_FAILURE_CACHE_HOURS = int(os.getenv("SHAREHOLDER_FAILURE_CACHE_HOURS", "6"))
SHAREHOLDER_ANNOUNCEMENT_LENGTH = int(os.getenv("SHAREHOLDER_ANNOUNCEMENT_LENGTH", "50"))

MIN_DAILY_CHANGE_FOR_LEAKAGE = float(os.getenv("MIN_DAILY_CHANGE_FOR_LEAKAGE", "-2.0"))
MAX_DAILY_CHANGE_FOR_LEAKAGE = float(os.getenv("MAX_DAILY_CHANGE_FOR_LEAKAGE", "4.0"))
MAX_DAILY_CHANGE_FOR_MOMENTUM = float(os.getenv("MAX_DAILY_CHANGE_FOR_MOMENTUM", "10.0"))

MIN_ACTIVITY_SPIKE = float(os.getenv("MIN_ACTIVITY_SPIKE", "1.5"))
MIN_BREAKOUT_PCT = float(os.getenv("MIN_BREAKOUT_PCT", "0.30"))
MIN_MOMENTUM_INTERVAL_RETURN_PCT = float(
    os.getenv("MIN_MOMENTUM_INTERVAL_RETURN_PCT", "0.35")
)

_db_raw = os.getenv("DB_PATH", "data/cuankus_momentum.db")
DB_PATH = Path(_db_raw)
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH


def validate_zpi_config() -> None:
    if not ZPI_API_KEY:
        raise RuntimeError("ZPI_API_KEY belum diisi")
    if not ZPI_BASE_URL:
        raise RuntimeError("ZPI_BASE_URL belum diisi")


def validate_telegram_config() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN belum diisi")
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID belum diisi")
