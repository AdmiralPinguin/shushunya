import os
from pathlib import Path
import torch
import pandas as pd
from neuralforecast import NeuralForecast
from neuralforecast.models import NHITS
from neuralforecast.losses.pytorch import MSE

BASE_DIR = Path("/media/acab/LMS/Shushunya/Sharomyga")
DATA_DIR = BASE_DIR / "data" / "processed"
OUT_DIR  = BASE_DIR / "models" / "nhits"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PAIRS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT",
    "ADAUSDT","DOGEUSDT","LINKUSDT","LTCUSDT",
    "MATICUSDT","XRPUSDT"
]

H = 3
INPUT_SIZE = 192

NHITS_CONFIG = dict(
    stack_types=['identity', 'identity'],
    n_blocks=[2, 2],
    mlp_units=[[256, 256], [256, 256]],
    n_pool_kernel_size=[2, 1],
    n_freq_downsample=[4, 2],
    pooling_mode='MaxPool1d',
    dropout_prob_theta=0.1,
    activation='ReLU',

    learning_rate=2e-4,
    batch_size=8,
    valid_batch_size=8,
    windows_batch_size=48,
    scaler_type='robust',
    random_seed=42,

    accelerator='gpu' if torch.cuda.is_available() else 'cpu',
    devices=1,
    precision='16-mixed',

    enable_checkpointing=True,
    logger=True,
)

def is_model_trained(sym: str, tag: str) -> bool:
    path = OUT_DIR / sym / tag
    if not path.exists():
        return False
    has_ckpt = any(f.suffix == ".ckpt" and f.stat().st_size > 0 for f in path.glob("*.ckpt"))
    has_pkls = all((path / f).exists() for f in ["alias_to_model.pkl", "configuration.pkl", "dataset.pkl"])
    return has_ckpt and has_pkls

def clean_df(df: pd.DataFrame, sym: str):
    df = df.copy()
    df["ds"] = pd.to_datetime(df["time"], utc=True)
    df["unique_id"] = sym
    df = df.dropna(subset=["up_move","down_move"])
    drop_cols = {"time","ds","unique_id","symbol","up_move","down_move"}
    feat_cols = [c for c in df.columns if c not in drop_cols]
    df[feat_cols] = df[feat_cols].ffill().bfill().fillna(0)
    df = df.sort_values("ds").reset_index(drop=True)
    return df, feat_cols

def fit_target(sym: str, df: pd.DataFrame, feat_cols, target_col: str, tag: str):
    print(f"\n[{sym}][{tag}] training start")
    base_col = "m15_close" if "m15_close" in df.columns else "close"
    df[target_col] = df[target_col] / (df[base_col] + 1e-6)
    train_df = df[["unique_id","ds",target_col] + feat_cols].rename(columns={target_col:"y"})

    model = NHITS(
        h=H,
        input_size=INPUT_SIZE,
        hist_exog_list=feat_cols,
        loss=MSE(),
        valid_loss=MSE(),
        max_steps=320000,
        **NHITS_CONFIG,
    )

    nf = NeuralForecast(models=[model], freq="15min")

    print(f"[{sym}][{tag}] fitting model...")
    nf.fit(df=train_df, val_size=96, verbose=True)
    print(f"[{sym}][{tag}] finished training")

    out_path = OUT_DIR / sym / tag
    out_path.mkdir(parents=True, exist_ok=True)

    try:
        nf.save(str(out_path))
        print(f"[{sym}][{tag}] saved successfully -> {out_path}")
    except Exception as e:
        print(f"[{sym}][{tag}] save failed: {e}")

    del nf, model, train_df
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()

def train_one(sym: str):
    path = DATA_DIR / f"{sym}.csv"
    if not path.exists():
        print(f"[{sym}] no dataset, skip")
        return
    df = pd.read_csv(path, engine="python")
    df, feat_cols = clean_df(df, sym)
    print(f"[{sym}] features: {len(feat_cols)}")

    if is_model_trained(sym, "up"):
        print(f"[{sym}][up] already trained, skip.")
    else:
        fit_target(sym, df, feat_cols, "up_move", "up")

    if is_model_trained(sym, "down"):
        print(f"[{sym}][down] already trained, skip.")
    else:
        fit_target(sym, df, feat_cols, "down_move", "down")

def main():
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:64"
    torch.set_float32_matmul_precision("medium")
    for sym in PAIRS:
        try:
            train_one(sym)
        except Exception as e:
            print(f"[{sym}] TRAIN ERROR: {e}")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()

if __name__ == "__main__":
    main()
