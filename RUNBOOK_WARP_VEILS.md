# Warp Veils (WarpWails) — запуск
Скрипт: `WarpWails/scripts/warp_veils.sh`
Запуск: `./WarpWails/scripts/warp_veils.sh`
Фон: `nohup ./WarpWails/scripts/warp_veils.sh > /tmp/uvicorn.log 2>&1 &`
Стоп: `pkill -f "uvicorn .*WarpWails"; fuser -k 8009/tcp`
