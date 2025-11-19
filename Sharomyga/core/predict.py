from __future__ import annotations

from typing import Dict
import pandas as pd
from neuralforecast import NeuralForecast


def _extract_model_col(df: pd.DataFrame) -> str:
    # В predict-результате столбцы: [unique_id, ds, <model_name>]
    for c in df.columns:
        if c not in ("unique_id", "ds"):
            return c
    raise ValueError("No forecast column found in NeuralForecast output")


def get_predictions(
    nf_up_map: Dict[str, NeuralForecast],
    nf_down_map: Dict[str, NeuralForecast],
    data_by_symbol: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    На вход:
      - nf_up_map / nf_down_map: dict[symbol] -> NeuralForecast (up/down)
      - data_by_symbol: dict[symbol] -> DataFrame как в build_merged:
            time, symbol,
            m15_*, h1_*, h4_*,
            up_move, down_move

    На выход:
      DataFrame[symbol, up_pred, down_pred].
    """
    rows = []

    for sym, df in data_by_symbol.items():
        if sym not in nf_up_map or sym not in nf_down_map:
            continue

        if "time" not in df.columns:
            continue

        local = df.copy()
        local["ds"] = pd.to_datetime(local["time"], utc=True)
        local["unique_id"] = sym

        drop_cols = {"time", "ds", "unique_id", "symbol", "up_move", "down_move"}
        feat_cols = [c for c in local.columns if c not in drop_cols]

        if "up_move" not in local.columns or "down_move" not in local.columns:
            # Без таргетов история для NHITS будет некорректной.
            continue

        # === UP ===
        df_up = local[["unique_id", "ds", "up_move"] + feat_cols].rename(
            columns={"up_move": "y"}
        )
        pred_up = nf_up_map[sym].predict(df=df_up)
        col_up = _extract_model_col(pred_up)
        up_val = float(pred_up[col_up].tail(1).iloc[0])

        # === DOWN ===
        df_down = local[["unique_id", "ds", "down_move"] + feat_cols].rename(
            columns={"down_move": "y"}
        )
        pred_down = nf_down_map[sym].predict(df=df_down)
        col_down = _extract_model_col(pred_down)
        down_val = float(pred_down[col_down].tail(1).iloc[0])

        rows.append(
            {
                "symbol": sym,
                "up_pred": up_val,
                "down_pred": down_val,
            }
        )

    return pd.DataFrame(rows)
