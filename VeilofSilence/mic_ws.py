import asyncio, json, sounddevice as sd, numpy as np, websockets, queue

WS_URL = "ws://127.0.0.1:8011/ws/stt"
SR = 16000
FRAME_MS = 20
SAMPLES = SR * FRAME_MS // 1000
q = queue.Queue()

def callback(indata, frames, time, status):
    q.put((indata[:,0]*32767).astype(np.int16).tobytes())

async def run():
    async with websockets.connect(WS_URL, max_size=2**23) as ws:
        await ws.send(json.dumps({"lang":"ru"}))
        async def sender():
            with sd.InputStream(channels=1, samplerate=SR, dtype='float32', callback=callback, blocksize=SAMPLES):
                while True:
                    await ws.send(q.get())
        async def receiver():
            async for msg in ws:
                print(msg, flush=True)
        await asyncio.gather(sender(), receiver())

if __name__ == "__main__":
    asyncio.run(run())
