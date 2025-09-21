from fastapi import FastAPI
app = FastAPI(title="Eye Of Terror", version="0.1.0")
@app.get("/healthz")
def healthz(): return {"ok": True}
