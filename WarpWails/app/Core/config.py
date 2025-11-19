from omegaconf import OmegaConf
from pathlib import Path

_DEFAULT = {
    "tts": {"speaker_default": "aidar", "sr": 24000, "channels": 1, "bps": 16, "device": "auto"},
    "stream": {"chunk_bytes": 8192},
    "voicefx": {"preset_default": "imp_light", "use_rubberband": "auto"},
}

def load_config():
    cfg_path = Path(__file__).resolve().parents[2] / "conf" / "warp_veils.yaml"
    cfg = OmegaConf.create(_DEFAULT)
    if cfg_path.exists():
        cfg = OmegaConf.merge(cfg, OmegaConf.load(cfg_path))
    return cfg

CFG = load_config()
