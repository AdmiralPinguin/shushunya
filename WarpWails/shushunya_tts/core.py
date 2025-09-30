import numpy as np
import torch

# RU multi-speaker Silero
_MODEL = None
_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_SR_DEFAULT = 48000  # исторический дефолт

def _get_model():
    global _MODEL
    if _MODEL is None:
        _MODEL, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language="ru",
            speaker="v3_1_ru",   # ru_v3 семейство
            trust_repo=True,
        )
        _MODEL.to(_DEVICE).eval()
    return _MODEL

def synthesize(
    text: str,
    speaker: str = "baya",
    sample_rate: int = _SR_DEFAULT,
    put_accent: bool = True,
    put_yo: bool = True,
) -> np.ndarray:
    """
    Возврат: np.ndarray float32 mono, sample_rate Гц
    Сигнатура совместима с адаптером Mod2 (PY backend).
    """
    if not text or not text.strip():
        raise ValueError("empty text")
    model = _get_model()
    with torch.inference_mode():
        audio = model.apply_tts(
            text=text,
            speaker=speaker,
            sample_rate=sample_rate,
            put_accent=put_accent,
            put_yo=put_yo,
        )
    x = np.asarray(audio, dtype=np.float32)
    # Silero отдаёт моно; гарантируем тип и форму
    if x.ndim > 1:
        x = x.mean(axis=1).astype(np.float32)
    return x
