#!/bin/bash
# Master boot for the whole Shushunya stack, in dependency order.
# Idempotent: every component skips if already healthy. Safe to re-run.
# Requires /media/shushunya/SHUSHUNYA mounted first (see fstab).
ROOT=/media/shushunya/SHUSHUNYA/shushunya
cd "$ROOT" || { echo "disk not mounted at $ROOT"; exit 1; }

echo "[boot] gemma vLLM (3090, 8080)…"; bash CoreOfMadness/llm-host/start-gemma-vllm.sh
echo "[boot] qwen (CPU 8081)…";         bash CoreOfMadness/llm-host/start-qwen.sh
echo "[boot] dispatcher+archive+Vox…";  bash ArchiveOfHeresy/start-main.sh || true
echo "[boot] governors+gateway…";       bash EyeOfTerror/Warmaster/start-governors.sh
echo "[boot] moriana (7103)…"
if ! curl -fsS --max-time 3 http://127.0.0.1:7103/health >/dev/null 2>&1; then
  setsid nohup DemonsForge/DemonsForge/bin/python -m EyeOfTerror.Pictorium.Moriana.moriana_governor \
    --host 127.0.0.1 --port 7103 >> EyeOfTerror/Warmaster/runtime/boot-moriana.log 2>&1 &
fi
echo "[boot] telegram bot…";            ( cd CoreOfMadness/telegram-bot && bash start-bot.sh ) || true

echo "[boot] Skitarii sandbox VM…"
if ! pgrep -f "qemu-system.*skitarii" >/dev/null 2>&1; then
  bash CoreOfMadness/vm-sandbox/start-vm.sh
fi
echo "[boot] Skitarii warband service (7200)…"
# Prefer the user-systemd unit (survives sessions + auto-restarts). Fall back to a
# bare nohup only if systemd --user isn't available for some reason.
if systemctl --user list-unit-files skitarii-warband.service >/dev/null 2>&1; then
  systemctl --user start skitarii-warband.service || true
elif ! curl -fsS --max-time 3 http://127.0.0.1:7200/health >/dev/null 2>&1; then
  setsid nohup python3 EyeOfTerror/Mechanicum/Skitarii/service.py \
    >> EyeOfTerror/Mechanicum/Skitarii/service.log 2>&1 &
fi

echo "[boot] Administratum / AshurKai (7300)…"
if ! curl -fsS --max-time 3 http://127.0.0.1:7300/health >/dev/null 2>&1; then
  bash EyeOfTerror/Administratum/start-administratum.sh || true
fi

echo "[boot] WarpWails voice (7500)…"
# Voice daemon has its own user-systemd unit (survives sessions + auto-restart).
if systemctl --user list-unit-files warpwails.service >/dev/null 2>&1; then
  systemctl --user start warpwails.service || true
elif ! curl -fsS --max-time 3 http://127.0.0.1:7500/health >/dev/null 2>&1; then
  ( cd WarpWails && setsid nohup WarpWails-F5/bin/python warpwails_service.py \
      >> runtime/warpwails.log 2>&1 & ) || true
fi

echo "[boot] done. Status:"
for pp in "8080 gemma" "8081 qwen" "8079 dispatcher" "8090 archive" "7000 gateway" "7101 iskandar" "7104 ceraxia" "7103 moriana" "7200 skitarii-warband" "7300 administratum" "7500 warpwails"; do
  set -- $pp
  if curl -fsS -m2 "http://127.0.0.1:$1/health" >/dev/null 2>&1 || curl -fsS -m2 "http://127.0.0.1:$1/v1/models" >/dev/null 2>&1; then
    echo "  UP   $2 ($1)"; else echo "  DOWN $2 ($1)"; fi
done
