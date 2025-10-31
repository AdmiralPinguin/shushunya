import os, pandas as pd, numpy as np
from neuralforecast import NeuralForecast
from neuralforecast.models import NHITS
from neuralforecast.losses.pytorch import MSE
from sklearn.preprocessing import RobustScaler

DATA_PATH = "/media/acab/LMS/Shushunya/Sharomyga/data/processed/all_pairs_merged.csv"
MODEL_DIR = "/media/acab/LMS/Shushunya/Sharomyga/models/nhits"
os.makedirs(MODEL_DIR, exist_ok=True)

print("Loading dataset...")
df = pd.read_csv(DATA_PATH)

# определяем реальное имя колонки времени и цены
time_col = [c for c in df.columns if 'time' in c][0]
price_col = [c for c in df.columns if 'close' in c and 'm15' in c][0]

df = df.rename(columns={time_col: 'ds', price_col: 'close'})
df = df[['symbol','ds','close'] + [c for c in df.columns if c not in ['symbol','ds','close']]]

H = 5
df['future_close'] = df.groupby('symbol')['close'].shift(-H)
df['y'] = (df['future_close'] - df['close']) / df['close']
df['move_magnitude'] = df['y'].abs()
df['move_sign'] = np.sign(df['y']).fillna(0)

def stability(x):
    return (np.sign(x) == np.sign(x.iloc[0])).mean()

df['stability'] = df.groupby('symbol')['y'].apply(lambda x: x.rolling(H).apply(stability, raw=False)).fillna(0)
df = df.dropna().reset_index(drop=True)

feature_cols = [c for c in df.columns if c not in ['symbol','ds','close','future_close','y','move_magnitude','move_sign','stability']]
scaler = RobustScaler()
df[feature_cols] = scaler.fit_transform(df[feature_cols])

df = df.rename(columns={'symbol':'unique_id'})
print("Dataset:", df.shape)

model = NHITS(h=H, input_size=256, loss=MSE(), max_epochs=10, batch_size=64, random_seed=42)
nf = NeuralForecast(models=[model], freq='15min')

print("Training started (target = relative change)...")
nf.fit(df=df, id_col='unique_id', time_col='ds', target_col='y')
nf.save(MODEL_DIR)
print("Model saved to:", MODEL_DIR)
