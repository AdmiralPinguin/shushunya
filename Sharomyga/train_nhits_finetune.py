import os
from pathlib import Path
import torch
import pandas as pd
from neuralforecast import NeuralForecast
from neuralforecast.losses.pytorch import MSE

BASE_DIR = Path("/media/acab/LMS/Shushunya/Sharomyga")
DATA_DIR = BASE_DIR / "data" / "processed"
MODEL_DIR = BASE_DIR / "models" / "nhits" / "BTCUSDT"
OUT_DIR = BASE_DIR / "models" / "nhits_finetuned" / "BTCUSDT"
OUT_DIR.mkdir(parents=True, exist_ok=True)

H = 3
INPUT_SIZE = 192
LR_FINE = 1e-5
MAX_STEPS_FINE = 100000


def clean_df(df: pd.DataFrame):
    df = df.copy()
    df["ds"] = pd.to_datetime(df["time"], utc=True)
    df["unique_id"] = "BTCUSDT"
    df = df.dropna(subset=["up_move", "down_move"])
    drop_cols = {"time", "ds", "unique_id", "symbol", "up_move", "down_move"}
    feat_cols = [c for c in df.columns if c not in drop_cols]
    df[feat_cols] = df[feat_cols].ffill().bfill().fillna(0)
    df = df.sort_values("ds").reset_index(drop=True)
    return df, feat_cols


def finetune_target(tag, target_col):
    print(f"\n[BTCUSDT][{tag}] fine-tune start")

    path = MODEL_DIR / tag
    if not path.exists():
        print(f"[BTCUSDT][{tag}] no model found at {path}")
        return

    # загрузка уже обученной модели
    nf = NeuralForecast.load(str(path))
    model = nf.models[0]

    # корректировка параметров под fine-tune
    model.learning_rate = LR_FINE
    model.max_steps = MAX_STEPS_FINE
    model.loss = MSE()
    model.valid_loss = MSE()

    # подготовка данных
    df = pd.read_csv(DATA_DIR / "BTCUSDT.csv", engine="python")
    df, feat_cols = clean_df(df)
    base_col = "m15_close" if "m15_close" in df.columns else "close"
    df["up_move"] = df["up_move"] / (df[base_col] + 1e-6)
    df["down_move"] = df["down_move"] / (df[base_col] + 1e-6)

    train_df = df[["unique_id", "ds", target_col] + feat_cols].rename(columns={target_col: "y"})

    print(f"[BTCUSDT][{tag}] fitting with LR={LR_FINE}, steps={MAX_STEPS_FINE}")
    nf.fit(df=train_df, val_size=96, verbose=True)
    print(f"[BTCUSDT][{tag}] finished fine-tune")

    # сохранение результата
    out_path = OUT_DIR / tag
    out_path.mkdir(parents=True, exist_ok=True)
    nf.save(str(out_path))
    print(f"[BTCUSDT][{tag}] saved finetuned model -> {out_path}")

    # очистка
    del nf, model, train_df
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def main():
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:64"
    torch.set_float32_matmul_precision("medium")

    finetune_target("up", "up_move")
    finetune_target("down", "down_move")


if __name__ == "__main__":
    main()
