import os
import pandas as pd
from pathlib import Path
import torch
from neuralforecast import NeuralForecast

BASE_DIR = Path("/media/acab/LMS/Shushunya/Sharomyga")
DATA_PATH = BASE_DIR / "data" / "processed" / "BTCUSDT.csv"
MODEL_DIR = BASE_DIR / "models" / "nhits_finetuned" / "BTCUSDT"

torch.set_float32_matmul_precision("medium")

DAY_FILTER = "2025-11-06"     # день, который проверяем
TIME_FILTER = "22:00:00"      # начиная с этого времени

def load_model(tag: str):
    path = MODEL_DIR / tag
    nf = NeuralForecast.load(str(path))
    print(f"[BTCUSDT][{tag}] model loaded -> {path}")
    return nf

def prepare_df():
    df = pd.read_csv(DATA_PATH)
    df["ds"] = pd.to_datetime(df["time"], utc=True)
    df["unique_id"] = "BTCUSDT"
    df = df.sort_values("ds")
    df = df.drop(columns=["time", "symbol"], errors="ignore")
    for c in df.columns:
        if c not in ["unique_id", "ds"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("float32")
    # фильтр по дате и времени
    start_ts = pd.Timestamp(f"{DAY_FILTER} {TIME_FILTER}", tz="UTC")
    df = df[df["ds"] >= start_ts].reset_index(drop=True)
    print(f"Filtered to {len(df)} rows starting {start_ts}.")
    return df

def predict_sequence(nf_up, nf_down, df, steps=10, window=500):
    results = []
    for i in range(steps):
        df_slice = df.iloc[-window - i : -i if i > 0 else None].copy()
        if len(df_slice) < 100:
            break

        df_slice["y"] = df_slice.get("up_move", 0.0)
        pred_up = nf_up.predict(df=df_slice)

        df_slice["y"] = df_slice.get("down_move", 0.0)
        pred_down = nf_down.predict(df=df_slice)

        up_val = float(pred_up["NHITS"].max())
        down_val = float(pred_down["NHITS"].max())
        ts = df_slice["ds"].iloc[-1]
        results.append({"time": ts, "up_pred": up_val, "down_pred": down_val})
        print(f"[{ts}] up={up_val:.3f}  down={down_val:.3f}")
    return pd.DataFrame(results)

def main():
    df = prepare_df()
    if len(df) < 600:
        print("Not enough data after filter.")
        return
    nf_up = load_model("up")
    nf_down = load_model("down")

    print(f"\n=== BTCUSDT SLIDING TEST AFTER {DAY_FILTER} {TIME_FILTER} ===")
    preds = predict_sequence(nf_up, nf_down, df, steps=10)
    print("\n--- SUMMARY ---")
    print(preds.sort_values("time"))

if __name__ == "__main__":
    main()
