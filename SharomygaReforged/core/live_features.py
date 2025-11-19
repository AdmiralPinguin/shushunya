import pandas as pd
import numpy as np
from binance.client import Client
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")

client = Client(api_key, api_secret)

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    up = np.where(delta > 0, delta, 0)
    down = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(up).ewm(alpha=1/period).mean()
    avg_loss = pd.Series(down).ewm(alpha=1/period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period).mean()

def fetch(symbol, interval, limit):
    kl = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(kl, columns=["time","open","high","low","close","volume","_","_","_","_","_","_"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df

def build_live_features(symbols):
    frames = []
    for sym in symbols:
        m15 = fetch(sym, Client.KLINE_INTERVAL_15MINUTE, 500)
        h1 = fetch(sym, Client.KLINE_INTERVAL_1HOUR, 500)
        h4 = fetch(sym, Client.KLINE_INTERVAL_4HOUR, 500)

        def calc(df):
            df = df.copy()
            df["returns"] = df["close"].pct_change()
            df["spread"] = (df["high"] - df["low"]) / df["close"]
            df["atr"] = atr(df)
            df["rsi"] = rsi(df["close"])
            df["ema_fast"] = ema(df["close"], 12)
            df["ema_slow"] = ema(df["close"], 26)
            df["ema_diff"] = df["ema_fast"] - df["ema_slow"]
            df["volume_norm"] = df["volume"] / df["volume"].rolling(20).mean()
            return df
      
        m15 = calc(m15)
        h1 = calc(h1)
        h4 = calc(h4)

        # align by timestamp
        base = m15.copy()
        base["h1_open"] = h1["open"].reindex(base.index, method='ffill')
        base["h1_high"] = h1["high"].reindex(base.index, method='ffill')
        base["h1_low"] = h1["low"].reindex(base.index, method='ffill')
        base["h1_close"] = h1["close"].reindex(base.index, method='ffill')
        base["h1_volume"] = h1["volume"].reindex(base.index, method='ffill')
        base["h1_returns"] = h1["returns"].reindex(base.index, method='ffill')
        base["h1_spread"] = h1["spread"].reindex(base.index, method='ffill')
        base["h1_atr"] = h1["atr"].reindex(base.index, method='ffill')
        base["h1_rsi"] = h1["rsi"].reindex(base.index, method='ffill')
        base["h1_ema_fast"] = h1["ema_fast"].reindex(base.index, method='ffill')
        base["h1_ema_slow"] = h1["ema_slow"].reindex(base.index, method='ffill')
        base["h1_ema_diff"] = h1["ema_diff"].reindex(base.index, method='ffill')
        base["h1_volume_norm"] = h1["volume_norm"].reindex(base.index, method='ffill')

        base["h4_open"] = h4["open"].reindex(base.index, method='ffill')
        base["h4_high"] = h4["high"].reindex(base.index, method='ffill')
        base["h4_low"] = h4["low"].reindex(base.index, method='ffill')
        base["h4_close"] = h4["close"].reindex(base.index, method='ffill')
        base["h4_volume"] = h4["volume"].reindex(base.index, method='ffill')
        base["h4_returns"] = h4["returns"].reindex(base.index, method='ffill')
        base["h4_spread"] = h4["spread"].reindex(base.index, method='ffill')
        base["h4_atr"] = h4["atr"].reindex(base.index, method='ffill')
        base["h4_rsi"] = h4["rsi"].reindex(base.index, method='ffill')
        base["h4_ema_fast"] = h4["ema_fast"].reindex(base.index, method='ffill')
        base["h4_ema_slow"] = h4["ema_slow"].reindex(base.index, method='ffill')
        base["h4_ema_diff"] = h4["ema_diff"].reindex(base.index, method='ffill')
        base["h4_volume_norm"] = h4["volume_norm"].reindex(base.index, method='ffill')

        base["symbol"] = sym
        frames.append(base)

    df = pd.concat(frames)
    df = df.sort_values(["symbol","time"])
    return df
