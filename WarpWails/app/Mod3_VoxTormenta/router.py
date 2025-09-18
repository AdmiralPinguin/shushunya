from fastapi import APIRouter, UploadFile, File, Request, HTTPException, Response

router = APIRouter()

@router.post("/mod3_voicefx")
async def mod3_voicefx(request: Request, file: UploadFile | None = None, preset: str = "imp_light"):
    # получаем тело
    if file:
        data = await file.read()
    else:
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="provide WAV via multipart 'file' or raw body")

    # пока заглушка — просто вернуть то, что пришло
    return Response(content=data, media_type="audio/wav")
