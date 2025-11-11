from pathlib import Path
from typing import Dict

from neuralforecast import NeuralForecast

BASE_DIR = Path("/media/acab/LMS/Shushunya/Sharomyga")
MODEL_BASE = BASE_DIR / "models" / "nhits"

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "MATICUSDT",
    "LINKUSDT",
    "LTCUSDT",
]


def load_models() -> tuple[Dict[str, NeuralForecast], Dict[str, NeuralForecast]]:
    """
    Грузим по каждой паре свою модель up/down из:
    models/nhits/<SYMBOL>/up
    models/nhits/<SYMBOL>/down
    Отсутствующие директории просто скипаем.
    """
    nf_up = {}
    nf_down = {}

    for sym in SYMBOLS:
        up_dir = MODEL_BASE / sym / "up"
        down_dir = MODEL_BASE / sym / "down"

        if up_dir.exists():
            nf_up[sym] = NeuralForecast.load(str(up_dir))

        if down_dir.exists():
            nf_down[sym] = NeuralForecast.load(str(down_dir))

    if not nf_up or not nf_down:
        raise RuntimeError("No NHITS models loaded. Check models/nhits/<SYMBOL>/{up,down}.")

    return nf_up, nf_down
