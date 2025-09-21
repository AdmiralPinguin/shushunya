import os, numpy as np, torch, inspect
class SileroRU:
    def __init__(self, device="cpu"):
        self.device = device
        self.sr = int(os.getenv("SILERO_SR", "24000"))
        self.model, *_ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language="ru",
            speaker="v3_1_ru",
            trust_repo=True
        )
        self.model.to(self.device)
        self._sig = inspect.signature(self.model.apply_tts)
    def tts(self, text: str):
        try:
            audio = self.model.apply_tts(text=text, sample_rate=self.sr, put_accent=True, put_yo=True); sr=self.sr
        except TypeError:
            try:
                audio = self.model.apply_tts(texts=[text], sample_rate=self.sr, put_accent=True, put_yo=True); audio=audio[0]; sr=self.sr
            except TypeError:
                audio = self.model.apply_tts(text); sr=int(os.getenv("SILERO_FALLBACK_SR","48000"))
        return np.asarray(audio, dtype=np.float32), sr
