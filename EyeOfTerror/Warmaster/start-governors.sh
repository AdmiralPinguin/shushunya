#!/bin/bash
# Governors (Iskandar 7101, Ceraxia 7104) + Warmaster gateway (7000).
cd /media/shushunya/SHUSHUNYA/shushunya/EyeOfTerror/Warmaster || exit 1
PY=../../DemonsForge/DemonsForge/bin/python
RESEARCH_SECRET=/media/shushunya/SHUSHUNYA/shushunya/.secrets/research-warband-shadow.env
ARCHIVE_CLIENT_SECRET=/media/shushunya/SHUSHUNYA/shushunya/.secrets/archive-clients.env
set -a
if [ -r "$RESEARCH_SECRET" ]; then
  # Operator-owned mode-0600 file supplies the exact 7201 token to Iskandar
  # and Abaddon. Never echo it or copy it into repository config.
  . "$RESEARCH_SECRET"
fi
if [ -r "$ARCHIVE_CLIENT_SECRET" ]; then
  # The same local-only task-page key is consumed by Abaddon and Ceraxia.
  . "$ARCHIVE_CLIENT_SECRET"
fi
set +a
mkdir -p runtime
start() {
  local name="$1" port="$2"; shift 2
  if curl -fsS --max-time 3 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then echo "$name already up"; return; fi
  setsid nohup "$PY" "$@" >> "runtime/boot-$name.log" 2>&1 &
  echo "$name pid $! (port $port)"
}
start iskandar 7101 -m eye_of_terror.inner_circle.iskandar_service
start ceraxia  7104 -m eye_of_terror.inner_circle.ceraxia_service \
      --default-run-root runtime/warmaster-runs
sleep 4
export SKITARII_AUTOAPPLY="${SKITARII_AUTOAPPLY:-1}"
export SKITARII_AUTOPUBLISH="${SKITARII_AUTOPUBLISH:-1}"
start gateway  7000 -m eye_of_terror.warmaster_gateway --host 127.0.0.1 --port 7000 \
      --run-root runtime/warmaster-runs --governor-transport http --governor-host 127.0.0.1
