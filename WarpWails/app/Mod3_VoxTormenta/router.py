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


# --- BEGIN autogen stubs (safe minimal) ---
# These stubs ensure imports succeed. Replace with real processing later.
import shutil
def pitch_shift_resample(input_wav: str, output_wav: str, semitones: float = 0.0):
    shutil.copyfile(input_wav, output_wav)
    return output_wav

def shelf_filter(input_wav: str, output_wav: str, gain_db: float = 0.0):
    shutil.copyfile(input_wav, output_wav)
    return output_wav

def comp_soft(input_wav: str, output_wav: str, threshold_db: float = -18.0, ratio: float = 3.0):
    shutil.copyfile(input_wav, output_wav)
    return output_wav

def saturate(input_wav: str, output_wav: str, drive: float = 1.0):
    shutil.copyfile(input_wav, output_wav)
    return output_wav

PRESETS = {
    "imp_light": {"semitones": 3, "hpf": 80, "lpf": 6200}
}
# --- END autogen stubs ---
