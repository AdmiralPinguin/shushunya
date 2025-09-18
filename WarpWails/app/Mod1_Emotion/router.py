from fastapi import APIRouter
from pydantic import BaseModel
import re

router = APIRouter(prefix="/mod1_emotion", tags=["mod1"])

class Inp(BaseModel):
    text: str
    emotion: str = "neutral"   # neutral, anger, fear, sad, whisper, solemn
    intensity: float = 0.5     # 0..1
    tempo: float = 0.0         # -1..+1

class Out(BaseModel):
    text: str
    m3_preset: str
    m5_preset: str

def _augment(text:str, emo:str, I:float, tempo:float)->tuple[str,str,str]:
    emo = emo.lower()
    if emo=="anger":
        t = re.sub(r'([,.])', r'!', text).replace('..','.')
        return t, "neutral", "radio_void"
    if emo=="fear":
        t = re.sub(r'([,.])', r'...', text)
        return t, "imp_light", "ghost"
    if emo=="sad":
        t = re.sub(r'([,])', r' — ', text)
        return t, "neutral", "preverb"
    if emo=="whisper":
        t = text.replace("!", ".")
        return t, "imp_light", "ghost"
    if emo=="solemn":
        t = re.sub(r',', r';', text)
        return t, "neutral", "abyss"
    return text, "neutral", "abyss"

@router.post("", response_model=Out)
def map_emotion(inp: Inp):
    t, m3, m5 = _augment(inp.text, inp.emotion, max(0,min(1,inp.intensity)), max(-1,min(1,inp.tempo)))
    return Out(text=t, m3_preset=m3, m5_preset=m5)
