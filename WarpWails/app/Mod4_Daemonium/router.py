from fastapi import APIRouter, UploadFile, File, Response
router = APIRouter(prefix="/mod4_sfx", tags=["mod4"])
@router.post("")
async def sfx(file: UploadFile = File(...)):
    data = await file.read()  # passthrough
    return Response(content=data, media_type="audio/wav")
