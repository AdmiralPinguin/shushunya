import numpy as np
import pandas as pd

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = np.where(delta > 0, delta, 0.0)
    down = np.where(delta < 0, -delta, 0.0)
    roll_up = pd.Series(up, index=series.index).ewm(alpha=1/period, adjust=False).mean()
    roll_down = pd.Series(down, index=series.index).ewm(alpha=1/period, adjust=False).mean()
    rs = roll_up / (roll_down + 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))

def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bbands(series: pd.Series, period=20, nstd=2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    upper = mid + nstd*std
    lower = mid - nstd*std
    return lower, mid, upper

def stoch(df: pd.DataFrame, k_period=14, d_period=3):
    low_min = df['low'].rolling(k_period).min()
    high_max = df['high'].rolling(k_period).max()
    k = 100 * (df['close'] - low_min) / (high_max - low_min + 1e-12)
    d = k.rolling(d_period).mean()
    return k, d

def res(le=False, lx=False, se=False, sx=False):
    return {"long_entry": bool(le), "long_exit": bool(lx), "short_entry": bool(se), "short_exit": bool(sx)}

# === STRATEGIES ===

def strat_ema_trend(df: pd.DataFrame, cfg) -> dict:
    c = df['close']
    e12 = ema(c, 12); e26 = ema(c, 26); e50 = ema(c, 50)
    le = (c.iloc[-1] > e50.iloc[-1]) and (e12.iloc[-1] > e26.iloc[-1] >= e50.iloc[-1])
    se = (c.iloc[-1] < e50.iloc[-1]) and (e12.iloc[-1] < e26.iloc[-1] <= e50.iloc[-1])
    lx = (c.iloc[-1] < e50.iloc[-1]) or (e12.iloc[-1] < e26.iloc[-1])
    sx = (c.iloc[-1] > e50.iloc[-1]) or (e12.iloc[-1] > e26.iloc[-1])
    return res(le=le, lx=lx, se=se, sx=sx)

def strat_macd_momentum(df: pd.DataFrame, cfg) -> dict:
    c = df['close']
    m, s, h = macd(c)
    cross_up = (m.iloc[-2] <= s.iloc[-2]) and (m.iloc[-1] > s.iloc[-1])
    cross_dn = (m.iloc[-2] >= s.iloc[-2]) and (m.iloc[-1] < s.iloc[-1])
    strong_up = cross_up and (h.iloc[-1] > 0 and h.iloc[-1] > h.iloc[-2] > 0)
    strong_dn = cross_dn and (h.iloc[-1] < 0 and h.iloc[-1] < h.iloc[-2] < 0)
    le = cross_up or strong_up
    se = cross_dn or strong_dn
    lx = (h.iloc[-1] < 0 and m.iloc[-1] < s.iloc[-1])  # momentum down
    sx = (h.iloc[-1] > 0 and m.iloc[-1] > s.iloc[-1])  # momentum up
    return res(le=le, lx=lx, se=se, sx=sx)

def strat_rsi_trend(df: pd.DataFrame, cfg) -> dict:
    r = rsi(df['close'])
    rising2 = (r.iloc[-1] > r.iloc[-2] > r.iloc[-3])
    falling2 = (r.iloc[-1] < r.iloc[-2] < r.iloc[-3])
    le = (r.iloc[-1] < 30 and rising2) or (30 <= r.iloc[-1] <= 60 and rising2)
    se = (r.iloc[-1] > 70 and falling2) or (40 <= r.iloc[-1] <= 70 and falling2)
    lx = (r.iloc[-1] < 45 and falling2)
    sx = (r.iloc[-1] > 55 and rising2)
    return res(le=le, lx=lx, se=se, sx=sx)

def strat_bbands_breakout(df: pd.DataFrame, cfg) -> dict:
    c = df['close']
    lower, mid, upper = bbands(c, 20, 2.0)
    # Проверка объёма: для пробоя необходимо подтверждение повышенным объёмом.
    vol_ok = True
    try:
        if getattr(cfg, "VOLUME_FILTER_ENABLED", False) and ("volume" in df.columns):
            lookback = int(getattr(cfg, "VOLUME_LOOKBACK", 20))
            mult = float(getattr(cfg, "VOLUME_MULTIPLIER", 1.5))
            vol_avg = df["volume"].rolling(lookback).mean()
            if not np.isnan(vol_avg.iloc[-1]):
                vol_ok = df["volume"].iloc[-1] > vol_avg.iloc[-1] * mult
    except Exception:
        vol_ok = True

    # breakout conditions
    le_raw = (c.iloc[-1] > upper.iloc[-1] and c.iloc[-2] <= upper.iloc[-2])
    se_raw = (c.iloc[-1] < lower.iloc[-1] and c.iloc[-2] >= lower.iloc[-2])
    le = le_raw and vol_ok
    se = se_raw and vol_ok
    lx = (c.iloc[-1] < mid.iloc[-1])
    sx = (c.iloc[-1] > mid.iloc[-1])
    return res(le=le, lx=lx, se=se, sx=sx)

def strat_atr_channel(df: pd.DataFrame, cfg) -> dict:
    c = df['close']; a = atr(df, cfg.ATR_PERIOD if hasattr(cfg,'ATR_PERIOD') else 14)
    up = c.iloc[-2] - a.iloc[-2]; dn = c.iloc[-2] + a.iloc[-2]
    le = c.iloc[-1] > dn  # expanded up
    se = c.iloc[-1] < up  # expanded down
    lx = c.iloc[-1] < (c.iloc[-2])  # loss of expansion
    sx = c.iloc[-1] > (c.iloc[-2])
    return res(le=le, lx=lx, se=se, sx=sx)

def strat_stoch_reversal(df: pd.DataFrame, cfg) -> dict:
    k, d = stoch(df)
    cross_up = (k.iloc[-2] <= d.iloc[-2]) and (k.iloc[-1] > d.iloc[-1])
    cross_dn = (k.iloc[-2] >= d.iloc[-2]) and (k.iloc[-1] < d.iloc[-1])
    le = cross_up and (k.iloc[-1] < 30)
    se = cross_dn and (k.iloc[-1] > 70)
    lx = cross_dn
    sx = cross_up
    return res(le=le, lx=lx, se=se, sx=sx)

def strat_pullback_ema20(df: pd.DataFrame, cfg) -> dict:
    c = df['close']; e20 = ema(c, 20)
    le = (c.iloc[-2] < e20.iloc[-2]) and (c.iloc[-1] > e20.iloc[-1])  # reclaim 20ema
    se = (c.iloc[-2] > e20.iloc[-2]) and (c.iloc[-1] < e20.iloc[-1])
    lx = (c.iloc[-1] < e20.iloc[-1])
    sx = (c.iloc[-1] > e20.iloc[-1])
    return res(le=le, lx=lx, se=se, sx=sx)

def strat_breakout_hl(df: pd.DataFrame, cfg) -> dict:
    hh = df['high'].rolling(20).max()
    ll = df['low'].rolling(20).min()
    le_raw = df['close'].iloc[-1] > hh.iloc[-2]
    se_raw = df['close'].iloc[-1] < ll.iloc[-2]
    # Объёмный фильтр: подтверждаем пробой повышенным объёмом.
    vol_ok = True
    try:
        if getattr(cfg, "VOLUME_FILTER_ENABLED", False) and ("volume" in df.columns):
            lookback = int(getattr(cfg, "VOLUME_LOOKBACK", 20))
            mult = float(getattr(cfg, "VOLUME_MULTIPLIER", 1.5))
            vol_avg = df["volume"].rolling(lookback).mean()
            if not np.isnan(vol_avg.iloc[-1]):
                vol_ok = df["volume"].iloc[-1] > vol_avg.iloc[-1] * mult
    except Exception:
        vol_ok = True
    le = le_raw and vol_ok
    se = se_raw and vol_ok
    lx = df['close'].iloc[-1] < df['close'].iloc[-2]
    sx = df['close'].iloc[-1] > df['close'].iloc[-2]
    return res(le=le, lx=lx, se=se, sx=sx)

ALL_STRATEGIES = {
    "ema_trend":        strat_ema_trend,
    "macd_momentum":    strat_macd_momentum,
    "rsi_trend":        strat_rsi_trend,
    "bbands_breakout":  strat_bbands_breakout,
    "atr_channel":      strat_atr_channel,
    "stoch_reversal":   strat_stoch_reversal,
    "pullback_ema20":   strat_pullback_ema20,
    "breakout_hl":      strat_breakout_hl,
}
