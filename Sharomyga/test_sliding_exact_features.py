import os, pickle
from pathlib import Path
import pandas as pd
import numpy as np
import torch
from neuralforecast import NeuralForecast

BASE = Path("/media/acab/LMS/Shushunya/Sharomyga")
DATA = BASE/"data/processed/BTCUSDT.csv"
MODEL_UP   = BASE/"models/nhits_finetuned/BTCUSDT/up"
MODEL_DOWN = BASE/"models/nhits_finetuned/BTCUSDT/down"

torch.set_float32_matmul_precision("medium")

def load_nf(path: Path):
    nf = NeuralForecast.load(str(path))
    print(f"[LOAD] {path}")
    return nf

def load_required_cols(path: Path):
    ds_pkl = path/"dataset.pkl"
    with open(ds_pkl, "rb") as f:
        ds = pickle.load(f)
    temporal_cols = list(ds.temporal_cols)
    skip_cols = {"y", "available_mask", "mask", "sample_weight", "time", "ds", "symbol"}
    feat_cols = [c for c in temporal_cols if c not in skip_cols]
    print(f"[COLS] total={len(temporal_cols)} -> feats={len(feat_cols)}")
    return feat_cols

def prepare_df(src_csv: Path, feat_cols):
    df = pd.read_csv(src_csv)
    df["ds"] = pd.to_datetime(df["time"], utc=True)
    df["unique_id"] = "BTCUSDT"
    keep = set(["unique_id","ds","up_move","down_move"]).union(feat_cols)
    df = df[[c for c in df.columns if c in keep]].sort_values("ds").reset_index(drop=True)
    for c in feat_cols + ["up_move", "down_move"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df[feat_cols] = df[feat_cols].ffill().bfill().fillna(0.0).astype("float32")
    return df

def predict_window(nf, df_slice, target="up_move"):
    df_slice["y"] = df_slice[target].astype("float32") if target in df_slice.columns else 0.0
    pred = nf.predict(df=df_slice)
    val = float(pred["NHITS"].abs().max())
    ts = df_slice["ds"].iloc[-1]
    return ts, val

def sliding_run(df, nf_up, nf_down, steps=10, window=500):
    out = []
    for i in range(steps):
        sl = df.iloc[-window - i : -i if i>0 else None].copy()
        if len(sl) < 100:
            break
        ts_u, upv = predict_window(nf_up, sl.copy(), "up_move")
        ts_d, dnv = predict_window(nf_down, sl.copy(), "down_move")
        out.append({"ds": ts_u, "up_max": upv, "down_max": dnv})
        print(f"[{ts_u}] up_max={upv:.6f}  down_max={dnv:.6f}")
    return pd.DataFrame(out)

def main():
    nf_up   = load_nf(MODEL_UP)
    nf_down = load_nf(MODEL_DOWN)
    feats_up   = load_required_cols(MODEL_UP)
    feats_down = load_required_cols(MODEL_DOWN)
    if feats_up != feats_down:
        print("[WARN] up/down feature lists differ in order or content.")
    feat_cols = feats_up
    df = prepare_df(DATA, feat_cols)
    # фильтр: только данные после 6 ноября 22:00
    start_ts = pd.Timestamp("2025-11-06 22:00:00", tz="UTC")
    df = df[df["ds"] >= start_ts].reset_index(drop=True)
    print(f"[FILTER] rows after {start_ts}: {len(df)}")
    if len(df) < 100:
        print("[WARN] слишком мало строк после фильтра.")
    res = sliding_run(df, nf_up, nf_down, steps=10, window=min(500, len(df)))
    print("\n--- SUMMARY ---")
    if not res.empty:
        print(res.sort_values("ds"))
    else:
        print("[EMPTY] нет предсказаний — возможно, данных меньше 100 строк.")

if __name__ == "__main__":
    main()
