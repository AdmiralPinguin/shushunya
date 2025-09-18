from fastapi import APIRouter, Body, HTTPException, Response
from app.Core.config import CFG
import numpy as np, torch, struct, threading

router = APIRouter()
_MODEL=None; _LOCK=threading.Lock()
SR=int(CFG.tts.sr); CH=int(CFG.tts.channels); BPS=int(CFG.tts.bps)
SPEAKERS={"aidar","baya","kseniya","xenia","eugene","random"}

def _load_model():
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            obj = torch.hub.load('snakers4/silero-models','silero_tts',
                                 language='ru', speaker='v4_ru')
            _MODEL = obj[0] if isinstance(obj,(tuple,list)) else obj
            dev = CFG.tts.device
            if dev=='auto':
                dev = 'cuda' if (hasattr(torch,'cuda') and torch.cuda.is_available()) else 'cpu'
            try: _MODEL.to(dev)
            except Exception: pass
    return _MODEL

def _norm_speaker(s:str)->str:
    s=s.strip().lower()
    if s=='aidar_v2': s='aidar'
    if s not in SPEAKERS:
        raise HTTPException(status_code=400, detail=f"speaker must be: {', '.join(sorted(SPEAKERS))}")
    return s

def _pcm_s16le(x:np.ndarray)->bytes:
    x=np.clip(x,-1.0,1.0); return (x*32767.0).astype('<i2').tobytes()

def _wav_header(ns:int,sr:int=SR,ch:int=CH,bps:int=BPS)->bytes:
    br=sr*ch*(bps//8); ba=ch*(bps//8); ds=ns*ba; rs=36+ds
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF',rs,b'WAVE',b'fmt ',16,1,ch,sr,br,ba,bps,b'data',ds)

def _tts_bytes(text:str, speaker:str)->bytes:
    m=_load_model()
    y=m.apply_tts(text=text, speaker=speaker, sample_rate=SR)
    if isinstance(y,torch.Tensor): y=y.detach().cpu().numpy()
    pcm=_pcm_s16le(y.astype(np.float32))
    return _wav_header(len(pcm)//2)+pcm

@router.post("/speak")
def speak(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    spk  = _norm_speaker(str(payload.get("speaker") or CFG.tts.speaker_default))
    if not text: raise HTTPException(status_code=400, detail="text is required")
    return Response(content=_tts_bytes(text, spk), media_type="audio/wav")
