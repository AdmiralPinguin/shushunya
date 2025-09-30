from __future__ import annotations
import uvicorn
from fastapi import FastAPI
from .engine import load_config
from .routes import router

_cfg = load_config()
app = FastAPI(title="CoreOfMadness", version="0.1.0")
app.include_router(router, prefix="")

def run():
    uvicorn.run(
        "app.main:app",
        host=_cfg["server"]["host"],
        port=_cfg["server"]["port"],
        reload=False,
        workers=1,
    )

if __name__ == "__main__":
    run()
