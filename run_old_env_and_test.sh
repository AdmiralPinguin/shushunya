#!/usr/bin/env bash
set -Eeuo pipefail
trap 'code=$?; echo "Ошибка на строке $LINENO (exit $code)"; read -p "Enter..."' ERR

echo "[1] Стопну uvicorn"
pkill -f "uvicorn app.main:app" 2>/dev/null || true

echo "[2] Деактивирую текущее env"
deactivate 2>/dev/null || conda deactivate 2>/dev/null || true

echo "[3] Активирую ТВОЁ окружение"
if [[ -n "${SHU_ENV_PATH:-}" && -d "$SHU_ENV_PATH/bin" ]]; then
  source "$SHU_ENV_PATH/bin/activate"
elif [[ -d "./warpveils/bin" ]]; then
  source ./warpveils/bin/activate
else
  echo "Укажи путь: export SHU_ENV_PATH=/путь/к/твоему/env"; read -p "Enter..."; exit 1
fi

echo "[4] TTS точка входа"
export SHU_TTS_SPEC='shushunya_tts.core:synthesize'
test -f shushunya_tts/core.py || { echo "Нет shushunya_tts/core.py"; read -p "Enter..."; exit 1; }

echo "[5] Старт API"
uvicorn app.main:app --reload --port 8000 >/tmp/shu_api.log 2>&1 &

echo "[6] Жду health"
for _ in {1..50}; do curl -s http://127.0.0.1:8000/health | grep -q '"ok": true' && break; sleep 0.2; done

echo "[7] Backend:"
curl -s http://127.0.0.1:8000/mod2_tts/voices

echo "[8] Пайплайн-тест"
curl -s -X POST "http://127.0.0.1:8000/mod5_daemon/render_text?speaker=baya&vt_preset=imp_light&ww_preset=squeak_rush&ww_count=3&drive=0.35&ir_wet=0" \
  -F 'text=Проверка старого окружения. Варп слушает.' -o out_pipeline.wav

echo "OK -> out_pipeline.wav"
read -p "Enter..."
