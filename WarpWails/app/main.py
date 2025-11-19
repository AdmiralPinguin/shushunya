from fastapi import FastAPI
from app.Mod2_TTS_Core import router as tts_router
from app.Mod3_VoxTormenta import router as voxtormenta_router
from app.Mod4_WarpWails import router as warpwails_router
from app.Mod5_DaemonEngine import router as daemon_router

app = FastAPI(title="Shushunya Core API", version="0.1.1")

app.include_router(tts_router.router)
app.include_router(voxtormenta_router.router)
app.include_router(warpwails_router.router)
app.include_router(daemon_router.router)

@app.get("/health")
def health():
    return {"ok": True}
