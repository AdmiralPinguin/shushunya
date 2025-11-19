import os, pandas as pd
from neuralforecast import NeuralForecast
from neuralforecast.models import NHITS
from neuralforecast.losses.pytorch import MSE
from neuralforecast.utils import AirPassengersDF
from sklearn.preprocessing import RobustScaler
from torch import device

DATA_PATH = "/media/acab/LMS/Shushunya/Sharomyga/data/processed/all_pairs_merged.csv"
MODEL_DIR = "/media/acab/LMS/Shushunya/Sharomyga/models/nhits"
os.makedirs(MODEL_DIR, exist_ok=True)

print("Loading dataset...")
df = pd.read_csv(DATA_PATH)

# базовые колонки
df = df.rename(columns={'m15_time':'ds','m15_close':'y'})
df = df[['symbol','ds','y'] + [c for c in df.columns if c not in ['symbol','ds','y']]]

# нормализация
scaler = RobustScaler()
feature_cols = [c for c in df.columns if c not in ['symbol','ds','y']]
df[feature_cols] = scaler.fit_transform(df[feature_cols])

# NeuralForecast ожидает unique_id
df = df.rename(columns={'symbol':'unique_id'})

print("Dataset:", df.shape)

model = NHITS(h=5, input_size=256, loss=MSE(), max_epochs=10, batch_size=64, random_seed=42)
nf = NeuralForecast(models=[model], freq='15min', local_scaler_type=None)

print("Training started...")
nf.fit(df=df, id_col='unique_id', time_col='ds', target_col='y', static_features=None)
nf.save(MODEL_DIR)
print("Model saved to:", MODEL_DIR)
