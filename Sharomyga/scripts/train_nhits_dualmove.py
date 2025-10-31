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

time_col = [c for c in df.columns if 'time' in c][0]
df[time_col] = pd.to_datetime(df[time_col], errors='coerce')
price_col = [c for c in df.columns if 'close' in c and 'm15' in c][0]
df = df.rename(columns={time_col:'ds', price_col:'close'})
df = df[['symbol','ds','close']+[c for c in df.columns if c not in ['symbol','ds','close']]]

H=5
df['future_high']=df.groupby('symbol')['close'].transform(lambda x:x.shift(-1).rolling(H).max())
df['future_low']=df.groupby('symbol')['close'].transform(lambda x:x.shift(-1).rolling(H).min())
df['up_move']=(df['future_high']-df['close'])/df['close']
df['down_move']=(df['close']-df['future_low'])/df['close']
df['efficiency']=df['up_move']/(df['up_move']+df['down_move']+1e-8)
df=df.dropna().reset_index(drop=True)

feature_cols=[c for c in df.columns if c not in [
 'symbol','ds','close','future_high','future_low','up_move','down_move','efficiency']]
scaler=RobustScaler()
df[feature_cols]=scaler.fit_transform(df[feature_cols])
df=df.rename(columns={'symbol':'unique_id'})
print("Dataset:",df.shape)

model_up=NHITS(h=H,input_size=128,loss=MSE(),max_steps=320000,batch_size=16,
    precision='16-mixed',
               random_seed=42,early_stop_patience_steps=4000)
model_down=NHITS(h=H,input_size=128,loss=MSE(),max_steps=320000,batch_size=16,
    precision='16-mixed',
                 random_seed=43,early_stop_patience_steps=4000)

nf_up=NeuralForecast(models=[model_up],freq='15min')
nf_down=NeuralForecast(models=[model_down],freq='15min')

print("Training UP model...")
nf_up.fit(df=df,id_col='unique_id',time_col='ds',target_col='up_move',val_size=96)
nf_up.save(os.path.join(MODEL_DIR,'up'))

print("Training DOWN model...")
nf_down.fit(df=df,id_col='unique_id',time_col='ds',target_col='down_move',val_size=96)
nf_down.save(os.path.join(MODEL_DIR,'down'))

print("Training complete. Models saved in:",MODEL_DIR)
