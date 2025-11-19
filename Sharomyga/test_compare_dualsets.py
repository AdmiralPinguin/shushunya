import os, pickle
from pathlib import Path
import pandas as pd
import numpy as np
import torch
from neuralforecast import NeuralForecast

BASE = Path("/media/acab/LMS/Shushunya/Sharomyga")
DATA = BASE/"data/processed/BTCUSDT.csv"
SETS = {
    "base": BASE/"models/nhits/BTCUSDT",
    "finetuned": BASE/"models/nhits_finetuned/BTCUSDT"
}

torch.set_float32_matmul_precision("medium")

def load_nf(path):
    nf = NeuralForecast.load(str(path))
    print(f"[LOAD] {path}")
    return nf

def load_features(dir_path):
    ds_pkl = dir_path/"up"/"dataset.pkl"
    with open(ds_pkl, "rb") as f:
        ds = pickle.load(f)
    skip = {"y","available_mask","mask","sample_weight","time","ds","symbol"}
    feats = [c for c in list(ds.temporal_cols) if c not in skip]
    return feats

def prepare_df(csv_path, feats):
    df = pd.read_csv(csv_path)
    df["ds"] = pd.to_datetime(df["time"], utc=True)
    df["unique_id"] = "BTCUSDT"
    keep = set(["unique_id","ds","up_move","down_move"]).union(feats)
    df = df[[c for c in df.columns if c in keep]].sort_values("ds").reset_index(drop=True)
    for c in feats + ["up_move","down_move"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df[feats] = df[feats].ffill().bfill().fillna(0.0).astype("float32")
    return df

def predict_window(nf, df_slice, target):
    df_slice["y"] = df_slice[target].astype("float32") if target in df_slice.columns else 0.0
    pred = nf.predict(df=df_slice)
    val = float(pred["NHITS"].abs().max())
    ts = df_slice["ds"].iloc[-1]
    return ts, val

def run_test(name, dir_path, df, feats):
    nf_up   = load_nf(dir_path/"up")
    nf_down = load_nf(dir_path/"down")
    results = []
    for i in range(20):
        slice_df = df.iloc[-500 - i : -i if i>0 else None].copy()
        if len(slice_df) < 40:
            break
        ts, upv  = predict_window(nf_up, slice_df.copy(), "up_move")
        _,  dnv  = predict_window(nf_down, slice_df.copy(), "down_move")
        results.append({"set": name, "time": ts, "up_max": upv, "down_max": dnv})
        print(f"[{name}] {ts} | up={upv:.5f}  down={dnv:.5f}")
    return pd.DataFrame(results)

def main():
    dfs = {}
    for name, path in SETS.items():
        if not path.exists():
            print(f"[SKIP] {name}: {path} not found.")
            continue
        feats = load_features(path)
        dfs[name] = (path, feats)
    df = prepare_df(DATA, list(dfs.values())[0][1])
    allres = []
    for name,(p,feats) in dfs.items():
        allres.append(run_test(name, p, df, feats))
    out = pd.concat(allres).sort_values(["set","time"]).reset_index(drop=True)
    print("\n=== COMPARISON (last 20 predictions) ===")
    print(out.tail(40))

if __name__ == "__main__":
    main()
