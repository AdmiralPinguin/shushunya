from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Eye Of Terror", version="0.1.0")

class Ingest(BaseModel):
    module: str
    text: str

@app.get("/healthz")
def healthz():
    return {"ok": True, "name": "EyeOfTerror"}

@app.post("/ingest")
def ingest(data: Ingest):
    print(f"[{data.module}] {data.text}")
    return {"ok": True}
