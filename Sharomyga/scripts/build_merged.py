import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from binance.client import Client

BASE_DIR = "/media/acab/LMS/Shushunya/Sharomyga"
OUT_DIR = Path(BASE_DIR) / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PAIRS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","DOGEUSDT","MATICUSDT","LINKUSDT","LTCUSDT",
]

YEARS = 3
H = 3  # горизонт в 15m свечах

# ================== utils ==================

def _to_utc_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def fetch_klines(client, symbol: str, interval: str,
                 start: datetime, end: datetime,
                 limit: int = 1000) -> pd.DataFrame:
    start_ms = _to_utc_ms(start)
    end_ms = _to_utc_ms(end)
    out = []

    while True:
        data = client.get_klines(
            symbol=symbol,
            interval=interval,
            startTime=start_ms,
            endTime=end_ms,
            limit=limit
        )
        if not data:
            break

        out.extend(data)
        last_open = data[-1][0]
        start_ms = last_open + 1
        if start_ms >= end_ms or len(data) < limit:
            break

        time.sleep(0.15)

    if not out:
        return pd.DataFrame(columns=["time","open","high","low","close","volume"])

    df = pd.DataFrame(out, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","ntrades","tbb","tbq","ignore"
    ])
    df = df[["open_time","open","high","low","close","volume"]].copy()
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.astype({
        "open": float,
        "high": float,
        "low": float,
        "close": float,
        "volume": float,
    })
    df = df[["time","open","high","low","close","volume"]]
    df = df.drop_duplicates(subset=["time"]).sort_values("time")
    return df

# ================== indicators ==================

def ema(x, span):
    return x.ewm(span=span, adjust=False).mean()

def rsi(close, period=14):
    delta = close.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    avg_gain = up.ewm(alpha=1/period).mean()
    avg_loss = down.ewm(alpha=1/period).mean()
    rs = avg_gain / (avg_loss + 1e-12)
    return 100 - (100 / (1 + rs))

def true_range(df):
    prev_close = df["close"].shift(1)
    c1 = df["high"] - df["low"]
    c2 = (df["high"] - prev_close).abs()
    c3 = (df["low"] - prev_close).abs()
    return pd.concat([c1, c2, c3], axis=1).max(axis=1)

def atr(df, period=14):
    return true_range(df).ewm(alpha=1/period).mean()

def bbands(close, period=20, k=2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    up = mid + k * std
    lo = mid - k * std
    width_pct = (up - lo) / (mid + 1e-12)
    return mid, up, lo, width_pct

def macd(close, fast=12, slow=26, signal=9):
    ef = ema(close, fast)
    es = ema(close, slow)
    m = ef - es
    s = ema(m, signal)
    h = m - s
    return m, s, h

def stoch(df, k=14, d=3):
    low_min = df["low"].rolling(k).min()
    high_max = df["high"].rolling(k).max()
    k_fast = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-12)
    k_slow = k_fast.rolling(d).mean()
    return k_fast, k_slow

def williams_r(df, period=14):
    low_min = df["low"].rolling(period).min()
    high_max = df["high"].rolling(period).max()
    return -100 * (high_max - df["close"]) / (high_max - low_min + 1e-12)

def cci(df, period=20, c=0.015):
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    ma = tp.rolling(period).mean()
    md = (tp - ma).abs().rolling(period).mean()
    return (tp - ma) / (c * md + 1e-12)

def roc(close, period=12):
    return close.pct_change(periods=period)

def momentum(close, period=10):
    return close - close.shift(period)

def obv(df):
    direction = np.sign(df["close"].diff().fillna(0.0))
    return (direction * df["volume"]).cumsum()

def dm_pos(df):
    up = df["high"].diff()
    down = -df["low"].diff()
    return pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)

def dm_neg(df):
    up = df["high"].diff()
    down = -df["low"].diff()
    return pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

def adx(df, period=14):
    tr = true_range(df)
    dmp = dm_pos(df)
    dmn = dm_neg(df)
    tr_n = tr.ewm(alpha=1/period).mean()
    dmp_n = dmp.ewm(alpha=1/period).mean()
    dmn_n = dmn.ewm(alpha=1/period).mean()
    plus_di = 100 * (dmp_n / (tr_n + 1e-12))
    minus_di = 100 * (dmn_n / (tr_n + 1e-12))
    dx = 100 * (plus_di - minus_di).abs() / ((plus_di + minus_di) + 1e-12)
    adx_val = dx.ewm(alpha=1/period).mean()
    return plus_di, minus_di, adx_val

def add_feature_block(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    df = df.copy()

    df["ret_1"] = df["close"].pct_change()
    df["spread"] = (df["high"] - df["low"]) / (df["close"] + 1e-12)
    df["atr"] = atr(df, 14)
    df["rsi"] = rsi(df["close"], 14)

    df["ema_fast"] = ema(df["close"], 12)
    df["ema_slow"] = ema(df["close"], 26)
    df["ema_diff"] = df["ema_fast"] - df["ema_slow"]

    m, s, h = macd(df["close"])
    df["macd"] = m
    df["macd_signal"] = s
    df["macd_hist"] = h

    mid, up, lo, w = bbands(df["close"])
    df["bb_mid"] = mid
    df["bb_up"] = up
    df["bb_lo"] = lo
    df["bb_width_pct"] = w

    kf, kd = stoch(df)
    df["stoch_k"] = kf
    df["stoch_d"] = kd

    df["willr"] = williams_r(df)
    df["cci"] = cci(df)
    df["roc"] = roc(df["close"])
    df["mom"] = momentum(df["close"])
    df["obv"] = obv(df)

    plus_di, minus_di, adx_val = adx(df)
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    df["adx"] = adx_val

    keep = [
        "time","open","high","low","close","volume",
        "ret_1","spread","atr","rsi",
        "ema_fast","ema_slow","ema_diff",
        "macd","macd_signal","macd_hist",
        "bb_mid","bb_up","bb_lo","bb_width_pct",
        "stoch_k","stoch_d",
        "willr","cci","roc","mom",
        "obv","plus_di","minus_di","adx",
    ]
    df = df[keep]

    rename = {}
    for c in df.columns:
        if c == "time":
            rename[c] = "time"
        else:
            rename[c] = f"{prefix}_{c}"
    df = df.rename(columns=rename)
    return df

def merge_multi_tf(m15: pd.DataFrame, h1: pd.DataFrame, h4: pd.DataFrame, symbol: str) -> pd.DataFrame:
    base = m15.copy()
    base["symbol"] = symbol

    merged = pd.merge_asof(
        base.sort_values("time"),
        h1.sort_values("time"),
        on="time",
        direction="backward"
    )
    merged = pd.merge_asof(
        merged.sort_values("time"),
        h4.sort_values("time"),
        on="time",
        direction="backward"
    )
    return merged

# ================== main ==================

def build_symbol(sym: str, client: Client):
    print(f"[{sym}] fetch", flush=True)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * YEARS)

    m15 = fetch_klines(client, sym, Client.KLINE_INTERVAL_15MINUTE, start, end)
    h1 = fetch_klines(client, sym, Client.KLINE_INTERVAL_1HOUR, start, end)
    h4 = fetch_klines(client, sym, Client.KLINE_INTERVAL_4HOUR, start, end)

    if m15.empty or h1.empty or h4.empty:
        print(f"[{sym}] no data, skip")
        return

    print(f"[{sym}] indicators", flush=True)
    m15f = add_feature_block(m15, "m15")
    h1f = add_feature_block(h1, "h1")
    h4f = add_feature_block(h4, "h4")

    merged = merge_multi_tf(m15f, h1f, h4f, sym)

    # цели
    merged = merged.sort_values("time")
    high_fut = merged["m15_high"].shift(-1).rolling(H).max()
    low_fut = merged["m15_low"].shift(-1).rolling(H).min()

    merged["up_move"] = high_fut - merged["m15_close"]
    merged["down_move"] = merged["m15_close"] - low_fut

    merged = merged.dropna(subset=["up_move","down_move"])
    out_path = OUT_DIR / f"{sym}.csv"
    merged.to_csv(out_path, index=False)
    print(f"[{sym}] saved {len(merged)} rows -> {out_path}")

def main():
    client = Client(api_key=None, api_secret=None)
    for sym in PAIRS:
        try:
            build_symbol(sym, client)
        except Exception as e:
            print(f"[{sym}] ERROR: {e}")

if __name__ == "__main__":
    main()
