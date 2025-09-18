from fastapi import FastAPI
from app.Core.pipeline_router import router as pipeline_router
from app.Mod5_MasterFX.router import router as mod5_router
from app.Mod4_SFX.router import router as mod4_router
from app.Mod1_Emotion.router import router as mod1_router
from app.Mod2_TTS.stream_router import router as tts_stream_router
from app.Mod3_VoxTormenta.router import router as vox3_router
from app.Mod2_TTS.router import router as Mod2Router
try:
    from app.Mod3_VoxTormenta.router import router as Mod3Router
except Exception:
    Mod3Router = None

app = FastAPI(title="WarpWails Core", version="0.2.0")





app.include_router(pipeline_router)
app.include_router(mod5_router)
app.include_router(mod4_router)
app.include_router(mod1_router)
app.include_router(tts_stream_router)
app.include_router(Mod2Router)
if Mod3Router:
    app.include_router(Mod3Router)

@app.get("/healthz")
def healthz(): return {"ok": True}
