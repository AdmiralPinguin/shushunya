from fastapi import APIRouter, UploadFile, File, Response
router = APIRouter(prefix="/mod5_masterfx", tags=["mod5"])
@router.post("")
async def masterfx(file: UploadFile = File(...)):
    data = await file.read()  # passthrough
    return Response(content=data, media_type="audio/wav")
