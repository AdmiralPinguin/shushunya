from dataclasses import dataclass
import numpy as np
import pandas as pd
from . import config as cfg
from .strategies import ALL_STRATEGIES  # каждая стратегия возвращает dict с флагами long_entry/long_exit/short_entry/short_exit

@dataclass
class Votes:
    long_entry: float
    long_exit: float
    short_entry: float
    short_exit: float

def _ema(s, span):
    return s.ewm(span=span, adjust=False).mean()

def _atr(df: pd.DataFrame, period: int):
    high, low, close = df['high'], df['low'], df['close']
    prev_close = close.shift(1)
    tr = np.maximum.reduce([
        (high - low).values,
        (high - prev_close).abs().values,
        (low - prev_close).abs().values
    ])
    return pd.Series(tr, index=df.index).rolling(period).mean()

def _rsi(close: pd.Series, period: int = 14):
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1/period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = roll_up / (roll_down.replace(0, np.nan))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def _superentry_boosts(df: pd.DataFrame):
    """Возвращает dict name->mult (мягкий буст)."""
    boosts = {name: 1.0 for name in ALL_STRATEGIES.keys()}
    if not getattr(cfg, "SUPERENTRY_ENABLED", True):
        return boosts

    close = df["close"]
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    ema50 = _ema(close, 50)

    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal

    atr_now = _atr(df, getattr(cfg, "ATR_PERIOD", 14)).iloc[-1]
    atr_smooth = _atr(df, getattr(cfg, "MICRO_TP_ATR_SMOOTH_BARS", 96)).iloc[-1]
    hi_vol = bool(atr_now > atr_smooth if pd.notna(atr_smooth) else False)

    # Bollinger
    mid = close.rolling(20).mean()
    std = close.rolling(20).std(ddof=0)
    upper = mid + 2*std
    lower = mid - 2*std
    last_close = close.iloc[-1]
    last_upper = upper.iloc[-1]
    last_lower = lower.iloc[-1]
    rsi_last = _rsi(close, 14).iloc[-1]

    factor = float(getattr(cfg, "BOOST_FACTOR", 1.5))
    factor = min(factor, float(getattr(cfg, "BOOST_MAX_MULT", factor)))

    # Трендовый буст
    bull = (ema12.iloc[-1] > ema26.iloc[-1] > ema50.iloc[-1]) and (macd_line.iloc[-1] > 0) and hi_vol
    bear = (ema12.iloc[-1] < ema26.iloc[-1] < ema50.iloc[-1]) and (macd_line.iloc[-1] < 0) and hi_vol
    if bull or bear:
        for name in ("ema_trend", "atr_channel"):
            if name in boosts:
                boosts[name] = factor

    # Экстремумы Боллинджера (только с подтверждением RSI)
    if pd.notna(last_upper) and pd.notna(last_lower):
        if (last_close > last_upper and rsi_last >= 65) or (last_close < last_lower and rsi_last <= 35):
            if "bbands_breakout" in boosts:
                boosts["bbands_breakout"] = factor

    return boosts

def compute_votes(df: pd.DataFrame, cfg_mod=cfg) -> Votes:
    weights = getattr(cfg_mod, "STRATEGY_WEIGHTS", {}) or {}
    boosts  = _superentry_boosts(df)
    boost_on_exits = bool(getattr(cfg_mod, "BOOST_ON_EXITS", False))

    le = lx = se = sx = 0.0
    for name, strat in ALL_STRATEGIES.items():
        try:
            res = strat(df, cfg_mod)  # dict с флагами
        except Exception:
            continue

        base_w = float(weights.get(name, 1.0))
        mult   = float(boosts.get(name, 1.0))

        w_in   = base_w * mult                # входы всегда с бустом
        w_out  = (base_w * mult) if boost_on_exits else base_w  # выходы НЕ бустим

        if res.get("long_entry"):   le += w_in
        if res.get("short_entry"):  se += w_in
        if res.get("long_exit"):    lx += w_out
        if res.get("short_exit"):   sx += w_out

    return Votes(le, lx, se, sx)

# Детализированный вариант для ресёрча (если включишь RESEARCH_MODE)
def compute_votes_with_details(df: pd.DataFrame, cfg_mod=cfg):
    weights = getattr(cfg_mod, "STRATEGY_WEIGHTS", {}) or {}
    boosts  = _superentry_boosts(df)
    boost_on_exits = bool(getattr(cfg_mod, "BOOST_ON_EXITS", False))

    sums = {'LE': 0.0, 'LX': 0.0, 'SE': 0.0, 'SX': 0.0}
    details = []

    for name, strat in ALL_STRATEGIES.items():
        try:
            res = strat(df, cfg_mod)
        except Exception:
            continue

        base_w = float(weights.get(name, 1.0))
        mult   = float(boosts.get(name, 1.0))

        w_in  = base_w * mult
        w_out = (base_w * mult) if boost_on_exits else base_w

        row = {'name': name, 'base': base_w, 'boost': mult, 'le': 0.0, 'lx': 0.0, 'se': 0.0, 'sx': 0.0}

        if res.get("long_entry"):   sums['LE'] += w_in;  row['le'] = w_in
        if res.get("short_entry"):  sums['SE'] += w_in;  row['se'] = w_in
        if res.get("long_exit"):    sums['LX'] += w_out; row['lx'] = w_out
        if res.get("short_exit"):   sums['SX'] += w_out; row['sx'] = w_out

        if row['le'] or row['lx'] or row['se'] or row['sx']:
            details.append(row)

    v = Votes(sums['LE'], sums['LX'], sums['SE'], sums['SX'])
    return v, details, boosts

def _fmt_votes(v: Votes) -> str:
    # компакт без галочек — чтобы не путаться: "LE.. LX.. | SE.. SX.."
    return f"LE{v.long_entry:.2f} LX{v.long_exit:.2f} | SE{v.short_entry:.2f} SX{v.short_exit:.2f}"
