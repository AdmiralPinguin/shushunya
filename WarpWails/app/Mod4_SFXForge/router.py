from fastapi import APIRouter, UploadFile, File, Form, Response
import tempfile, subprocess, os, json

router = APIRouter(prefix="/mod4_sfx", tags=["mod4"])

# playlist JSON формат:
# {"sr":24000,"ch":1,"items":[{"path":"/abs/sfx/laugh.wav","start_ms":600,"gain_db":-6},{"path":"/abs/sfx/creak.wav","start_ms":1200,"gain_db":-9}]}

@router.post("")
async def sfx(file: UploadFile = File(...), playlist: str = Form(None)):
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td,"in.wav"); open(src,"wb").write(await file.read())
        if not playlist:
            return Response(content=open(src,"rb").read(), media_type="audio/wav")
        pl = json.loads(playlist)
        items = [it for it in pl.get("items",[]) if os.path.exists(it.get("path",""))]
        if not items:
            return Response(content=open(src,"rb").read(), media_type="audio/wav")
        inputs = ["-i", src]
        for it in items: inputs += ["-i", it["path"]]
        fc = ["[0:a]anull[a0]"]
        mix_labels = []
        for i,it in enumerate(items, start=1):
            d = int(max(0, it.get("start_ms",0)))
            vol = 10**((it.get("gain_db",-6))/20.0)
            fc.append(f"[{i}:a]adelay={d}|{d},volume={vol:.4f}[s{i}]")
            mix_labels.append(f"[s{i}]")
        # sum sfx -> sfxsum
        graph = ";".join(fc) + ";" + "".join(mix_labels) + f"amix=inputs={len(items)}:normalize=0[sfxsum];"
        # duck voice by sfxsum, then mix back
        graph += "[a0][sfxsum]sidechaincompress=threshold=-20dB:ratio=4:attack=5:release=120:makeup=6[duck];"
        graph += "[duck][sfxsum]amix=inputs=2:normalize=0,alimiter=limit=0.97[out]"
        dst = os.path.join(td,"out.wav")
        cmd = ["ffmpeg","-y","-hide_banner","-loglevel","error"] + inputs + ["-filter_complex", graph, "-map","[out]","-c:a","pcm_s16le","-ar",str(pl.get("sr",24000)),"-ac",str(pl.get("ch",1)), dst]
        run = subprocess.run(cmd, capture_output=True, text=True)
        if run.returncode != 0: return Response(content=f"ffmpeg failed\n{run.stderr}\ncmd: {' '.join(cmd)}", status_code=500, media_type="text/plain")
        return Response(content=open(dst,"rb").read(), media_type="audio/wav")
