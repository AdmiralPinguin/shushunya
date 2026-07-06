#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for name in ashur-kai heartbeat; do
  pid_file="$ROOT/runtime/$name.pid"
  if [ -f "$pid_file" ]; then
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "Stopped $name PID $pid"
    fi
    rm -f "$pid_file"
  fi
done
