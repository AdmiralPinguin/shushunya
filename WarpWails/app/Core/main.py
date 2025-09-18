from fastapi import FastAPI
from app.Mod2_TTS.stream_router import router as tts_stream_router
from app.Mod3_VoxTormenta.router import router as vox3_router
from app.Mod2_TTS.router import router as Mod2Router
try:
    from app.Mod3_VoxTormenta.router import router as Mod3Router
except Exception:
    Mod3Router = None

app = FastAPI(title="WarpWails Core", version="0.2.0")

app.include_router(tts_stream_router)
app.include_router(Mod2Router)
if Mod3Router:
    app.include_router(Mod3Router)

@app.get("/healthz")
def healthz(): return {"ok": True}
