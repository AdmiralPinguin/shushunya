#!/usr/bin/env bash
set -euo pipefail

pattern='[s]hushunya_desktop.main'
mapfile -t pids < <(pgrep -u "$(id -u)" -f "$pattern" || true)

if (( ${#pids[@]} == 0 )); then
  echo "Shushunya Desktop is not running for user $(id -un)."
  exit 0
fi

kill "${pids[@]}"
echo "Stopped Shushunya Desktop (${pids[*]})."
