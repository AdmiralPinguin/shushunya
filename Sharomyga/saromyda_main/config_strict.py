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
MAX_OPEN_POS = 4  # сокращаем максимально допустимое количество одновременных позиций
POSITION_MARGIN_MAX_FRAC = 0.15  # максимум маржи на сделку (15% кошелька)

# === Fees / Stats ===
FEE_TAKER = 0.0004  # 0.04% taker
STATS_ENABLED = True
STATS_DIR = "stats"

# === ATR smoothing ===
ATR_PERIOD = 14


# TP триггерим по последней цене, SL по mark:
TP_WORKING_TYPE = "CONTRACT_PRICE"
SL_WORKING_TYPE = "MARK_PRICE"

# === Sleep / Fast mode ===
CYCLE_SLEEP_SEC = 300
FAST_MODE_ENABLED = False        # <<< ускоренный режим вырублен
FAST1_DURATION_SEC = 90
FAST1_SEC = 5
FAST2_DURATION_SEC = 600
FAST2_SEC = 20
TIME_SYNC_EACH_CYCLE = True

# === Sanitation toggles ===
RECREATE_MISSING_EXITS = True
CANCEL_ORDERS_IF_NO_POSITION = True
KEEP_ONLY_REDUCE_WHEN_POSITION = True

# === Votes thresholds (входы) ===
LONG_ENTRY_SOFT_BUY   = 8.0
LONG_ENTRY_MIN_BUY    = 9.0
SHORT_ENTRY_SOFT_SELL = 8.0
SHORT_ENTRY_MIN_SELL  = 9.0

# Усиленный вход (оверфулл) при очень сильном сигнале
LONG_ENTRY_BOOST_BUY   = 8.0
SHORT_ENTRY_BOOST_SELL = 8.0
BOOSTED_ENTRY_FRACTION = 1.5  # executor сам зажмёт по лимиту маржи (0.15)

# === ГЕЙТЫ ДЛЯ ВХОДА — ПРОТИВ СВОЕГО ВЫХОДА ===
LONG_ENTRY_MAX_LX  = 2.5   # не входить в LONG, если LX > 2.5
SHORT_ENTRY_MAX_SX = 2.5   # не входить в SHORT, если SX > 2.5

# === Пороги (выходы) — ужесточённые ===
LONG_EXIT_MIN_SELL  = 4.0
SHORT_EXIT_MIN_BUY  = 4.0

# === ГЕЙТЫ ДЛЯ ВЫХОДА — ПРОТИВ СВОЕГО ВХОДА ===
LONG_EXIT_MAX_LE  = 2.5    # не выходить из LONG, если LE > 2.5
SHORT_EXIT_MAX_SE = 2.5    # не выходить из SHORT, если SE > 2.5

# === ATR-мульты для SL/TP (SL по ATR; TP — микро при MICRO_TP_MODE = False) ===
# Для более сбалансированного отношения риск/прибыль ставим стоп примерно на 1.5 ATR и тейк на 1 ATR
ATR_MULT_SL = 1.5
ATR_MULT_TP = 1.0
ATR_SL_MULT = ATR_MULT_SL
ATR_TP_MULT = ATR_MULT_TP

# === Retrace exit (MFE-трейл в плюс) ===
RETRACE_ENABLED = True
# Выходим, если позиция дала хотя бы 0.3% прибыли и затем отступила на 0.2%, чтобы зафиксировать профит
RETRACE_MIN_MFE_PCT = 0.003   # 0.3%
RETRACE_DROP_PCT   = 0.002    # 0.2%

# === Research mode (по желанию) ===
RESEARCH_MODE = False
RESEARCH_LOG = "stats/research_votes.csv"

# === Weights (пересмотренные) ===
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

# === Boost (применяется только к входам) ===
SUPERENTRY_ENABLED = True
BOOST_FACTOR    = 1.5
BOOST_MAX_MULT  = 1.5
BOOST_ON_EXITS  = False

# === Динамический размер позиции на основе ATR ===
ATR_DYNAMIC_SIZE_ENABLED = True
ATR_BASE_RISK_PCT = 0.002  # риск 0.2% на сделку
ATR_MIN_FRACTION = 0.5
ATR_MAX_FRACTION = 1.5

# === Фильтр по объёму для пробойных стратегий ===
VOLUME_FILTER_ENABLED = True
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLIER = 1.5
