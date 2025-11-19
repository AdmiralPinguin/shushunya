#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, time
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT"]
TF_BASE = "15m"
TF_HIGHER = ["1h","4h"]
ALL_TF = [TF_BASE] + TF_HIGHER
BARS = 128

API_KEY = os.getenv("BINANCE_API_KEY","")
API_SEC = os.getenv("BINANCE_API_SECRET","")

UP_DIR   = "/media/acab/LMS/Shushunya/SharomygaReforged/models/nhits/up"
DOWN_DIR = "/media/acab/LMS/Shushunya/SharomygaReforged/models/nhits/down"

client = Client(API_KEY, API_SEC)

# ================= INDICATORS ================
def ema(x, s): return x.ewm(span=s, adjust=False).mean()
def sma(x, w): return x.rolling(w, min_periods=w).mean()
def rsi(close, p=14):
    d = close.diff(); up = d.clip(lower=0.0); dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1/p, adjust=False).mean(); rd = dn.ewm(alpha=1/p, adjust=False).mean()
    rs = ru/(rd+1e-12); return 100 - (100/(1+rs))
def macd(close,f=12,s=26,sg=9):
    ef, es = ema(close,f), ema(close,s); m = ef-es; si = ema(m,sg); return m, si, m-si
def stoch(h,l,c,kp=14,dp=3,sp=3):
    ll = l.rolling(kp,min_periods=kp).min(); hh = h.rolling(kp,min_periods=kp).max()
    k = 100*(c-ll)/(hh-ll+1e-12); ks = k.rolling(sp,min_periods=sp).mean(); d = ks.rolling(dp,min_periods=dp).mean(); return ks, d
def TR(h,l,c):
    return pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
def atr(h,l,c,p=14): return TR(h,l,c).ewm(alpha=1/p, adjust=False).mean()
def adx(h,l,c,p=14):
    um=h.diff(); dm=-l.diff()
    plus=np.where((um>dm)&(um>0),um,0.0); minus=np.where((dm>um)&(dm>0),dm,0.0)
    tr=TR(h,l,c); atr_=tr.ewm(alpha=1/p, adjust=False).mean()
    pdi=100*pd.Series(plus,index=h.index).ewm(alpha=1/p,adjust=False).mean()/(atr_+1e-12)
    mdi=100*pd.Series(minus,index=h.index).ewm(alpha=1/p,adjust=False).mean()/(atr_+1e-12)
    dx=100*(pdi-mdi).abs()/(pdi+mdi+1e-12); return dx.ewm(alpha=1/p,adjust=False).mean(),pdi,mdi
def bb(close,w=20,s=2.0):
    mid=close.rolling(w,min_periods=w).mean(); sd=close.rolling(w,min_periods=w).std(ddof=0)
    up=mid+s*sd; lo=mid-s*sd; width=(up-lo)/(mid+1e-12); return up,mid,lo,width
def obv(c,v): return (np.sign(c.diff().fillna(0.0))*v).cumsum()
def vol_ema(v,s): return v.ewm(span=s, adjust=False).mean()

def add_indicators(df):
    o = df.copy()
    o["rsi_14"]=rsi(o["close"],14)
    o["ema_12"]=ema(o["close"],12); o["ema_26"]=ema(o["close"],26); o["sma_50"]=sma(o["close"],50)
    m,si,hi=macd(o["close"]); o["macd"]=m; o["macd_signal"]=si; o["macd_hist"]=hi
    k,d=stoch(o["high"],o["low"],o["close"]); o["stoch_k"]=k; o["stoch_d"]=d
    o["atr_14"]=atr(o["high"],o["low"],o["close"])
    ax,pdi,mdi=adx(o["high"],o["low"],o["close"]); o["adx_14"]=ax; o["plus_di"]=pdi; o["minus_di"]=mdi
    bu,bm,bl,bw=bb(o["close"]); o["bb_u"]=bu; o["bb_m"]=bm; o["bb_l"]=bl; o["bb_w"]=bw
    o["obv"]=obv(o["close"],o["volume"]); o["vol_ema_20"]=vol_ema(o["volume"],20); o["vol_ema_50"]=vol_ema(o["volume"],50)
    return o.fillna(method="ffill").fillna(0.0)

def add_moves(df):
    df=df.copy()
    df["price_diff"]=df["close"].diff()
    df["up_move"]=df["price_diff"].clip(lower=0.0)
    df["down_move"]=(-df["price_diff"]).clip(lower=0.0)
    return df

# ================ DATA ==================
def fetch_klines(sym, tf, limit=BARS):
    r = client.futures_klines(symbol=sym, interval=tf, limit=limit)
    df = pd.DataFrame(r, columns=["t","o","h","l","c","v","x1","x2","x3","x4","x5","x6"])
    df["t"]=pd.to_datetime(df["t"],unit="ms",utc=True)
    for x in ["o","h","l","c","v"]: df[x]=df[x].astype(float)
    df=df.rename(columns={"t":"ds","o":"open","h":"high","l":"low","c":"close","v":"volume"})
    return df[["ds","open","high","low","close","volume"]]

def build_live(sym):
    frames={}
    for tf in ALL_TF:
        raw = fetch_klines(sym, tf)
        df  = add_indicators(raw)
        df  = add_moves(df)
        pref={"15m":"m15_","1h":"h1_","4h":"h4_"}[tf]
        df=df.rename(columns={c:pref+c for c in df.columns if c!="ds"})
        frames[tf]=df; time.sleep(0.1)
    base=frames["15m"].set_index("ds")
    for tf in ["1h","4h"]:
        hi=frames[tf].set_index("ds").reindex(base.index,method="ffill")
        base=base.join(hi,how="left")
        # expose raw names for NF expect
        base["up_move"] = base["m15_up_move"]
        base["down_move"] = base["m15_down_move"]

    base=base.tail(BARS).reset_index()
    base.insert(0,"symbol",sym)
    return base

def load_nf(path):
    from neuralforecast import NeuralForecast
    nf = NeuralForecast.load(path)
    ex=[]
    for m in nf.models:
        fl=getattr(m,"futr_exog_list",None)
        if fl:
            for v in fl:
                if isinstance(v,(list,tuple)): ex+=list(v)
                else: ex.append(v)
    return nf,sorted(set([e for e in ex if isinstance(e,str)]))

nf_up,exog_up = load_nf(UP_DIR)
nf_dn,exog_dn = load_nf(DOWN_DIR)

def align(df,need):
    d=df.rename(columns={"symbol":"unique_id"}).copy()
    for c in need:
        if c not in d: d[c]=0.0
    cols=["unique_id","ds"]+need
    d=d[[x for x in cols if x in d]]
    for c in need: d[c]=pd.to_numeric(d[c],errors="coerce").fillna(0.0)
    return d

# ================ TIME & BALANCE ================
def offset_sync(): return client.futures_time()["serverTime"]/1000.0 - time.time()

def log_balance():
    try:
        b=client.futures_account_balance()
        u=next(x for x in b if x["asset"]=="USDT")["balance"]
        print(f"[BALANCE] USDT={u}")
    except Exception:
        pass

offset=offset_sync()

# ================ MAIN LOOP =====================
def main_loop():
    global offset
    print("[BOOT] SharomygaReforged online, 15m close sync")
    log_balance()

    while True:
        t=time.time()+offset
        sec=900-(t%900)
        if sec>2:
            rem=int(sec-2)
            print(f"[WAIT] жду закрытия свечи через ~{rem} сек")
            log_balance()
            time.sleep(rem)

        offset=offset_sync()
        ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n=== CYCLE @ {ts} ===")
        log_balance()

        for sym in SYMBOLS:
            try:
                raw=build_live(sym)
                price=float(raw["m15_close"].iloc[-1])
                d_up=align(raw,exog_up)
                d_dn=align(raw,exog_dn)
                up=float(nf_up.predict(d_up).iloc[-1]["yhat"])
                dn=float(nf_dn.predict(d_dn).iloc[-1]["yhat"])
                side="FLAT"
                if up>=3*dn: side="LONG"
                elif dn>=3*up: side="SHORT"
                print(f"[PRED] {sym} price={price:.6f} up={up:.6f} dn={dn:.6f} side={side}")
            except Exception as e:
                print(f"[ERR] {sym}: {e}")

        time.sleep(5)

if __name__=="__main__":
    main_loop()
