import os, pandas as pd, numpy as np, time
from binance.client import Client
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange
from tqdm import tqdm

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
client = Client(API_KEY, API_SECRET)

PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
TFS = {"15m": "15m", "1h": "1h", "4h": "4h"}
DATA_ROOT = "/media/acab/LMS/Shushunya/Sharomyga/data"
os.makedirs(f"{DATA_ROOT}/raw", exist_ok=True)
os.makedirs(f"{DATA_ROOT}/processed", exist_ok=True)

def fetch_klines(symbol, interval, days=365):
    klines = []
    start_time = int((time.time() - days*86400) * 1000)
    while True:
        data = client.get_klines(symbol=symbol, interval=interval, startTime=start_time, limit=1000)
        if not data:
            break
        klines.extend(data)
        start_time = data[-1][0] + 1
        if len(data) < 1000:
            break
        time.sleep(0.1)
    df = pd.DataFrame(klines, columns=[
        'open_time','open','high','low','close','volume','close_time',
        'qav','trades','tbbav','tbqav','ignore'
    ])
    df = df.astype(float)
    df['time'] = pd.to_datetime(df['open_time'], unit='ms')
    return df[['time','open','high','low','close','volume']]

def add_features(df, tf_tag):
    df['returns'] = df['close'].pct_change().fillna(0)
    df['spread'] = (df['high'] - df['low']) / df['close']
    df['atr'] = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
    df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
    df['ema_fast'] = EMAIndicator(df['close'], window=20).ema_indicator()
    df['ema_slow'] = EMAIndicator(df['close'], window=100).ema_indicator()
    df['ema_diff'] = (df['ema_fast'] - df['ema_slow']) / df['close']
    df['volume_norm'] = df['volume'] / df['volume'].rolling(50).mean()
    df = df.add_prefix(f"{tf_tag}_")
    return df

def merge_timeframes(symbol):


    base = add_features(fetch_klines(symbol, '15m'), 'm15')
    h1 = add_features(fetch_klines(symbol, '1h'), 'h1')
    h4 = add_features(fetch_klines(symbol, '4h'), 'h4')

    base = base.rename(columns={'m15_time': 'time'})
    h1 = h1.rename(columns={'h1_time': 'time'})
    h4 = h4.rename(columns={'h4_time': 'time'})

    h1 = h1.set_index('time').reindex(base['time'], method='ffill').reset_index()
    h4 = h4.set_index('time').reindex(base['time'], method='ffill').reset_index()

    merged = pd.concat([base, h1.drop(columns=['time']), h4.drop(columns=['time'])], axis=1)
    merged['symbol'] = symbol
    return merged.dropna().reset_index(drop=True)

all_data = []
for s in tqdm(PAIRS, desc="Processing pairs"):


    try:
        df = merge_timeframes(s)
        all_data.append(df)
        df.to_csv(f"{DATA_ROOT}/raw/{s}_merged.csv", index=False)
    except Exception as e:
        print(f"{s} error:", e)

final = pd.concat(all_data)
final.to_csv(f"{DATA_ROOT}/processed/all_pairs_merged.csv", index=False)
print("Dataset saved to:", f"{DATA_ROOT}/processed/all_pairs_merged.csv")
