#!/bin/bash
# Master boot for the whole Shushunya stack, in dependency order.
# Idempotent: every component skips if already healthy. Safe to re-run.
# Requires /media/shushunya/SHUSHUNYA mounted first (see fstab).
ROOT=/media/shushunya/SHUSHUNYA/shushunya
cd "$ROOT" || { echo "disk not mounted at $ROOT"; exit 1; }
# This script is launched by a system unit as codexbox, not by a login shell.
# Route all user units through the lingering codexbox manager explicitly.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
RESEARCH_SECRET="$ROOT/.secrets/research-warband-shadow.env"
if [ -r "$RESEARCH_SECRET" ]; then
  set -a
  . "$RESEARCH_SECRET"
  set +a
fi

echo "[boot] gemma-31B vLLM (3090, 8080)…"; bash CoreOfMadness/llm-host/start-gemma31-vllm.sh
echo "[boot] qwen (CPU 8081)…";         bash CoreOfMadness/llm-host/start-qwen.sh
echo "[boot] dispatcher+archive+Vox…";  bash ArchiveOfHeresy/start-main.sh || true
echo "[boot] ResearchWarband production backend (7201)…"
if systemctl --user list-unit-files research-warband-shadow.service >/dev/null 2>&1; then
  systemctl --user start research-warband-shadow.service || true
else
  echo "  research-warband-shadow.service is not installed"
fi
echo "[boot] governors+gateway…";       bash EyeOfTerror/Warmaster/start-governors.sh
echo "[boot] Shushunya personality core (7600)…"
bash ShushunyaCore/start-core.sh || echo "  ShushunyaCore FAILED; Archive will keep speech-only chat available"
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

echo "[boot] cloudflare tunnel (chat.shushunya.com — вход для приложения)…"
bash start-cloudflare-tunnel.sh >/dev/null 2>&1 || echo "  tunnel FAILED (см. runtime/cloudflare/)"

echo "[boot] done. Status:"
for pp in "8080 gemma" "8081 qwen" "8079 dispatcher" "8090 archive" "7600 shushunya-core" "7000 gateway" "7101 iskandar" "7104 ceraxia" "7103 moriana" "7200 skitarii-warband" "7201 research-warband" "7300 administratum" "7500 warpwails"; do
  set -- $pp
  auth=()
  if [ "$1" = 7201 ] && [ -n "${RESEARCH_WARBAND_BEARER_TOKEN:-}" ]; then
    auth=(-H "Authorization: Bearer $RESEARCH_WARBAND_BEARER_TOKEN")
  fi
  health_path="health"
  if [ "$1" = 7600 ]; then health_path="health/ready"; fi
  if curl -fsS -m2 "${auth[@]}" "http://127.0.0.1:$1/$health_path" >/dev/null 2>&1 || curl -fsS -m2 "http://127.0.0.1:$1/v1/models" >/dev/null 2>&1; then
    echo "  UP   $2 ($1)"; else echo "  DOWN $2 ($1)"; fi
done
