import torch
import pandas as pd
from neuralforecast import NeuralForecast
from neuralforecast.models.nhits import NHITS

# == Фиктивный скейлер, просто пропускает данные ==
class _IdentityScaler:
    def transform(self, x):
        return x
    def inverse_transform(self, x):
        return x

class NHitsModel:
    def __init__(self, path_up, path_down):
        print("[NHITS] ⚙️ Инициализация моделей по параметрам из чекпоинтов...")

        state_up = torch.load(path_up, map_location="cpu")
        state_down = torch.load(path_down, map_location="cpu")

        hparams_up = state_up.get("hyper_parameters", {})
        hparams_down = state_down.get("hyper_parameters", {})

        print(f"[NHITS-UP] Найдены гиперпараметры: {list(hparams_up.keys())[:5]} ...")
        print(f"[NHITS-DOWN] Найдены гиперпараметры: {list(hparams_down.keys())[:5]} ...")

        self.model_up = NHITS(**{k: v for k, v in hparams_up.items() if k in NHITS.__init__.__code__.co_varnames})
        self.model_down = NHITS(**{k: v for k, v in hparams_down.items() if k in NHITS.__init__.__code__.co_varnames})

        if "state_dict" in state_up:
            state_up = state_up["state_dict"]
        if "state_dict" in state_down:
            state_down = state_down["state_dict"]

        self.model_up.load_state_dict(state_up, strict=False)
        self.model_down.load_state_dict(state_down, strict=False)
        print("[NHITS] ✅ Веса загружены.")

        self.nf_up = NeuralForecast(models=[self.model_up], freq="15min")
        self.nf_down = NeuralForecast(models=[self.model_down], freq="15min")

        # Принудительно размечаем модели как обученные
        for nf in [self.nf_up, self.nf_down]:
            nf.models[0]._is_fitted = True
            nf._fitted = True
            nf.fitted = True
            nf.freq = "15min"
            nf.time_col = "ds"
            nf.id_col = "unique_id"
            nf.target_col = "y"
            nf.scalers_ = {"y": _IdentityScaler()}

        print("[NHITS] ⚡ Модели окончательно размечены как обученные и готовы к инференсу.")

    def predict(self, df):
        print("[NHITS] 🔮 Предсказание...")

        df = df.copy()

        # Нормализуем колонки
        if "time" in df.columns:
            df.rename(columns={"time": "ds"}, inplace=True)
        if "unique_id" not in df.columns:
            df["unique_id"] = df.get("symbol", "unknown")

        df["up_move"] = df.get("up_move", 0.0)
        df["down_move"] = df.get("down_move", 0.0)
        df["ds"] = pd.to_datetime(df["ds"], errors="coerce")
        df = df.dropna(subset=["ds"])

        base_cols = ["unique_id", "ds", "up_move", "down_move"]
        df = df[[c for c in base_cols if c in df.columns]]
        df["up_move"] = df["up_move"].astype(float)
        df["down_move"] = df["down_move"].astype(float)

        df_up = df.rename(columns={"up_move": "y"})
        df_down = df.rename(columns={"down_move": "y"})

        pred_up = self.nf_up.predict(df=df_up)
        pred_down = self.nf_down.predict(df=df_down)

        pred_up.rename(columns={"NHITS": "up_pred"}, inplace=True)
        pred_down.rename(columns={"NHITS": "down_pred"}, inplace=True)
        merged = pred_up.merge(pred_down, on=["unique_id", "ds"], how="inner")
        merged["direction"] = merged["up_pred"] - merged["down_pred"]

        print(f"[NHITS] ✅ Готово! {len(merged)} строк предсказаний.")
        return merged
