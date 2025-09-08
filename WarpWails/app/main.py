from fastapi import FastAPI
from app.Core.main import app as core_app
from app.Mod1_VoxMortis.router import router as mod1
from app.Mod3_VoxTormenta.router import router as mod3
from app.Mod4_Daemonium.router import router as mod4
from app.Mod5_AbyssEcho.router import router as mod5

app = FastAPI(title="WarpWails", version="1.0.0")
app.mount("", core_app)  # /speak, /speak_pcm
app.include_router(mod1)
app.include_router(mod3)
app.include_router(mod4)
app.include_router(mod5)
