import pandas as pd
from core.model_nhits import NHitsModel
import yaml, os

print("[Sharomyga Reforged start.]")

# === Пути к весам ===
path_up = "/media/acab/LMS/Shushunya/Sharomyga/models/nhits/up/NHITS_0.ckpt"
path_down = "/media/acab/LMS/Shushunya/Sharomyga/models/nhits/down/NHITS_0.ckpt"

if not (os.path.isfile(path_up) and os.path.isfile(path_down)):
    raise FileNotFoundError("❌ Файлы весов NHITS не найдены! Проверь пути.")

print("[NHITS] ⚙️ Загрузка NHITS моделей...")
model = NHitsModel(path_up, path_down)

print("[NHITS] ✅ Модели готовы к работе.")

# === Заглушка данных для теста ===
df = pd.read_csv("/media/acab/LMS/Shushunya/Sharomyga/data/processed/all_pairs_merged.csv")
df = df.rename(columns={"time": "ds"})
df["unique_id"] = df["symbol"]
df["up_move"] = 0.0
df["down_move"] = 0.0

print("[Cycle start...]")
preds = model.predict(df.tail(500))
print(preds.head())
