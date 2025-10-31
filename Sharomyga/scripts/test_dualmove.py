import pandas as pd
from neuralforecast import NeuralForecast

DATA_PATH = 'data/processed/all_pairs_merged.csv'
MODEL_UP = 'models/nhits/up'
MODEL_DOWN = 'models/nhits/down'

print("Loading dataset...")
df = pd.read_csv(DATA_PATH)
df['time'] = pd.to_datetime(df['time'])
df['ds'] = df['time']
df = df.sort_values(['symbol', 'time'])

H = 5
up_moves = df.groupby('symbol')['m15_close'].apply(lambda x: (x.shift(-H) - x).clip(lower=0)).reset_index(level=0, drop=True)
down_moves = df.groupby('symbol')['m15_close'].apply(lambda x: (x - x.shift(-H)).clip(lower=0)).reset_index(level=0, drop=True)
df['up_move'] = up_moves
df['down_move'] = down_moves
df = df.dropna(subset=['up_move', 'down_move'])

df_tail = df.groupby('symbol').tail(500).copy()
df_tail['unique_id'] = df_tail['symbol']

df_up = df_tail[['unique_id', 'ds', 'up_move']]
df_down = df_tail[['unique_id', 'ds', 'down_move']]

symbols = df_tail['symbol'].unique()
print("Symbols:", symbols)

print("Loading models...")
nf_up = NeuralForecast.load(MODEL_UP)
nf_down = NeuralForecast.load(MODEL_DOWN)

print("Predicting...")
pred_up = nf_up.predict(df=df_up)
pred_down = nf_down.predict(df=df_down)

pred_up.columns = ['symbol','ds','up_pred']
pred_down.columns = ['symbol','ds','down_pred']

merged = pd.merge(pred_up, pred_down, on=['symbol','ds'], how='inner')
merged['direction'] = (merged['up_pred'] - merged['down_pred']).round(4)

print("\n=== Sample predictions ===")
print(merged.tail(10))
