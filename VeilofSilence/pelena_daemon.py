import os, queue, sys, time, httpx, webrtcvad, numpy as np, sounddevice as sd

SERVER_URL = os.getenv("VEIL_URL", "http://127.0.0.1:8011/stt")
LANG = os.getenv("VEIL_LANG", "ru")
PULSE_SOURCE = os.getenv("PULSE_SOURCE")   # имя источника Pulse
SD_INPUT_INDEX = os.getenv("SD_INPUT_INDEX")  # индекс устройства sounddevice

samplerate = 16000
block_size = 30  # мс
vad = webrtcvad.Vad(2)
q = queue.Queue()

def callback(indata, frames, time_info, status):
    if status:
        print("Status:", status, file=sys.stderr)
    q.put(bytes(indata))

def main():
    device = None
    if SD_INPUT_INDEX:
        device = int(SD_INPUT_INDEX)
    elif PULSE_SOURCE:
        device = PULSE_SOURCE

    print(f"[Pelena] старт записи, устройство={device}")
    with sd.RawInputStream(samplerate=samplerate, blocksize=int(samplerate*block_size/1000),
                           dtype='int16', channels=1, callback=callback, device=device):
        buf = b''
        while True:
            buf += q.get()
            while len(buf) >= samplerate * 2:  # 1 сек в байтах (16bit)
                chunk, buf = buf[:samplerate*2], buf[samplerate*2:]
                is_speech = vad.is_speech(chunk, samplerate)
                if is_speech:
                    try:
                        resp = httpx.post(SERVER_URL, files={"file": ("chunk.wav", chunk, "audio/wav")},
                                          data={"lang": LANG}, timeout=30)
                        print("[Pelena->Veil]", resp.status_code, resp.text[:100])
                    except Exception as e:
                        print("Ошибка отправки:", e)

if __name__ == "__main__":
    main()
