import os
import pandas as pd
from pathlib import Path
import torch
from neuralforecast import NeuralForecast

BASE_DIR = Path("/media/acab/LMS/Shushunya/Sharomyga")
DATA_PATH = BASE_DIR / "data" / "processed" / "BTCUSDT.csv"
MODEL_DIR = BASE_DIR / "models" / "nhits_finetuned" / "BTCUSDT"

torch.set_float32_matmul_precision("medium")

def load_model(tag: str):
    path = MODEL_DIR / tag
    if not path.exists():
        raise FileNotFoundError(f"Model path not found: {path}")
    nf = NeuralForecast.load(str(path))
    print(f"[BTCUSDT][{tag}] model loaded -> {path}")
    return nf

def prepare_df(tag: str):
    df = pd.read_csv(DATA_PATH)
    df["ds"] = pd.to_datetime(df["time"], utc=True)
    df["unique_id"] = "BTCUSDT"
    df = df.sort_values("ds")

    # выбрасываем нечисловые колонки
    drop_cols = {"time", "symbol"}
    df = df.drop(columns=[c for c in df.columns if c in drop_cols], errors="ignore")

    # создаем y (таргет)
    if tag == "up" and "up_move" in df.columns:
        df["y"] = df["up_move"]
    elif tag == "down" and "down_move" in df.columns:
        df["y"] = df["down_move"]
    else:
        df["y"] = 0.0

    # все числовые значения -> float32
    for c in df.columns:
        if c not in ["unique_id", "ds"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("float32")

    print(f"Data ready for {tag}: {len(df)} rows, {df.shape[1]} cols")
    return df

def predict_one(nf, df):
    pred = nf.predict(df=df.tail(500))
    print(pred.tail(3))
    return pred

def main():
    nf_up = load_model("up")
    nf_down = load_model("down")

    df_up = prepare_df("up")
    df_down = prepare_df("down")

    print("\n=== BTCUSDT OFFLINE TEST ===")
    pred_up = predict_one(nf_up, df_up)
    pred_down = predict_one(nf_down, df_down)

    up_last = pred_up["NHITS"].iloc[-1] if "NHITS" in pred_up else None
    down_last = pred_down["NHITS"].iloc[-1] if "NHITS" in pred_down else None
    print(f"\nFinal up={up_last} down={down_last}")

if __name__ == "__main__":
    main()
