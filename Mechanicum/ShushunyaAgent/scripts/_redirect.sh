#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/media/shushunya/SHUSHUNYA/shushunya"
SCRIPT_NAME="$(basename "$0")"
TARGET="$PROJECT_ROOT/EyeOfTerror/Warmaster/MobileGateway/ShushunyaAgent/scripts/$SCRIPT_NAME"

if [[ ! -x "$TARGET" ]]; then
  echo "Missing migrated ShushunyaAgent script: $TARGET" >&2
  exit 127
fi

exec "$TARGET" "$@"
