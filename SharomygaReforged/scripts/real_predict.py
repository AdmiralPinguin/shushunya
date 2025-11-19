# --- real_predict.py (patched tail) ---
import time
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import os
from binance.client import Client

from core.models import load_models
from core.predict import get_predictions
from core.executor import process_signal

load_dotenv()
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")
client = Client(api_key, api_secret)

symbols = ["ETHUSDT","SOLUSDT","XRPUSDT"]
limit = 500
H = 5

def ema(x, span): return x.ewm(span=span, adjust=False).mean()

def rsi(x, p=14):
    d = x.diff()
    up = d.clip(lower=0)
    dn = (-d).clip(lower=0)
    rs = up.ewm(alpha=1/p).mean() / (dn.ewm(alpha=1/p).mean() + 1e-9)
    return 100 - 100/(1+rs)

def atr(df, p=14):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1/p).mean()

def get(symbol, interval):
    kl = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(kl, columns=["time","open","high","low","close","volume","_","_","_","_","_","_"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
    return df[["time","open","high","low","close","volume"]]

def make(sym):
    m15 = get(sym, Client.KLINE_INTERVAL_15MINUTE)
    h1 = get(sym, Client.KLINE_INTERVAL_1HOUR)
    h4 = get(sym, Client.KLINE_INTERVAL_4HOUR)

    def calc(df):
        df = df.copy()
        df["returns"] = df["close"].pct_change()
        df["spread"] = (df["high"]-df["low"])/df["close"]
        df["atr"] = atr(df)
        df["rsi"] = rsi(df["close"])
        df["ema_fast"] = ema(df["close"],12)
        df["ema_slow"] = ema(df["close"],26)
        df["ema_diff"] = df["ema_fast"]-df["ema_slow"]
        df["volume_norm"] = df["volume"]/df["volume"].rolling(20).mean()
        return df

    m15 = calc(m15); h1 = calc(h1); h4 = calc(h4)

    base = m15.copy()
    for tf,src in zip(["h1","h4"], [h1,h4]):
        for col in ["open","high","low","close","volume","returns","spread","atr","rsi","ema_fast","ema_slow","ema_diff","volume_norm"]:
            base[f"{tf}_{col}"] = src[col].reindex(base.index,method="ffill")

    base["symbol"]=sym
    return base

def get_usdt_balance():
    bal = client.futures_account_balance()
    x = next((i for i in bal if i["asset"]=="USDT"),None)
    return x["balance"] if x else None

nf_up, nf_down = load_models()

while True:
    bal = get_usdt_balance()
    print(f"Balance USDT: {bal}")

    server_time = client.get_server_time()["serverTime"]/1000
    now = int(server_time)
    next_close = ((now // 900) + 1) * 900
    sleep_for = max(0, next_close - now - 3)

    print(f"\nWaiting {sleep_for:.1f}s to next candle close...")
    time.sleep(sleep_for)

    bal = get_usdt_balance()
    print(f"Balance USDT: {bal}")

    frames = [make(s) for s in symbols]
    df = pd.concat(frames).sort_values(["symbol","time"])

    df["up_move"] = (df.groupby("symbol")["close"].shift(-H) - df["close"]).clip(lower=0)
    df["down_move"] = (df["close"] - df.groupby("symbol")["close"].shift(-H)).clip(lower=0)
    df = df.dropna(subset=["up_move","down_move"])

    df["unique_id"]=df["symbol"]
    df_up = df[["unique_id","time","up_move"]].rename(columns={"time":"ds"})
    df_down = df[["unique_id","time","down_move"]].rename(columns={"time":"ds"})

    preds = get_predictions(nf_up,nf_down,df_up,df_down)

    print("=== CLOSED CANDLE SIGNALS ===")
    for _,r in preds.groupby("symbol").tail(1).iterrows():
        print(f"{r['symbol']:7} up={r['up_pred']:.6f} down={r['down_pred']:.6f}")

    print("\n=== EXECUTION ===")
    for _, r in preds.groupby("symbol").tail(1).iterrows():
        sym = r["symbol"]
        up = r["up_pred"]
        down = r["down_pred"]
        price = float(client.futures_mark_price(symbol=sym)["markPrice"])

        if up >= 3 * max(down,0):
            sig = "LONG"
        elif down >= 3 * max(up,0):
            sig = "SHORT"
        else:
            sig = "HOLD"

        print(f"{sym:7} [sig={sig}] up={up:.4f} down={down:.4f} price={price:.2f}")
        process_signal(sym, price, up, down)
