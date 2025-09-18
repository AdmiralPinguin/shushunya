from fastapi import APIRouter, Body, Request
from fastapi.responses import StreamingResponse
import io

router = APIRouter()

@router.post("/mod4_sfx")
async def mod4_sfx(request: Request):
    data = await request.body()
    return StreamingResponse(io.BytesIO(data), media_type="audio/wav")
