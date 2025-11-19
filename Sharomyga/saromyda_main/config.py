# === Symbols / Market ===
SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","XRPUSDT",
    "LTCUSDT","LINKUSDT","DOTUSDT","BCHUSDT","UNIUSDT","ATOMUSDT",
    "AVAXUSDT","SANDUSDT","MANAUSDT"
]
ALLOW_SHORTS = True

# === Timeframe / Data ===
TIMEFRAME = "15m"
LIMIT = 200

# === Risk / Leverage / Position caps ===
LEVERAGE = 2
MAX_OPEN_POS = 6
POSITION_MARGIN_MAX_FRAC = 0.15

# === Комиссии / Статистика ===
FEE_TAKER = 0.0004
STATS_ENABLED = True
STATS_DIR = "stats"

# === ATR ===
ATR_PERIOD = 14
TP_WORKING_TYPE = "CONTRACT_PRICE"
SL_WORKING_TYPE = "MARK_PRICE"

# === Sleep / Fast mode ===
CYCLE_SLEEP_SEC = 300
FAST_MODE_ENABLED = True
FAST1_DURATION_SEC = 90
FAST2_DURATION_SEC = 600
FAST1_SEC = 5
FAST2_SEC = 20
TIME_SYNC_EACH_CYCLE = True

# === Gates ===
LONG_ENTRY_SOFT_BUY   = 8
LONG_ENTRY_MIN_BUY    = 9
SHORT_ENTRY_SOFT_SELL = 8
SHORT_ENTRY_MIN_SELL  = 9

LONG_EXIT_MIN_SELL = 4
LONG_EXIT_MAX_LE   = 2.5
SHORT_EXIT_MIN_BUY = 4
SHORT_EXIT_MAX_SE  = 2.5

# === Flip / Soft / Boost ===
FLIP_ON_OPPOSITE = True
SOFT_HALF_ENABLED = True
BOOST_THRESHOLD = 8.0
SUPERENTRY_ENABLED = True
BOOST_FACTOR    = 1.5
BOOST_MAX_MULT  = 1.5
BOOST_ON_EXITS  = False

# === Weights ===
STRATEGY_WEIGHTS = {
    "ema_trend":        2.26,
    "macd_momentum":    2.10,
    "rsi_trend":        0.89,
    "bbands_breakout":  3.23,
    "atr_channel":      2.18,
    "stoch_reversal":   1.21,
    "pullback_ema20":   2.02,
    "breakout_hl":      3.50,
}

# === ATR exits ===
ATR_MULT_SL = 1.8
ATR_MULT_TP = 2.5
ATR_SL_MULT = ATR_MULT_SL
ATR_TP_MULT = ATR_MULT_TP

RETRACE_MIN_MFE_PCT = 0.009
RETRACE_DROP_PCT    = 0.0035

SL_PCT = 0.006
TP_PCT = 0.005

USE_ATR_EXITS = True
FAST_MODE_ENABLED = True

# === Логирование ===
EXPLAIN_DECISION = False
