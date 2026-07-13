#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$ROOT/packaging/shushunya-desktop.desktop.in"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
TARGET="$APPLICATIONS_DIR/shushunya-desktop.desktop"
TEMP="$(mktemp)"
trap 'rm -f "$TEMP"' EXIT

sed "s|@@ROOT@@|$ROOT|g" "$TEMPLATE" > "$TEMP"
install -D -m 0644 "$TEMP" "$TARGET"
echo "Launcher installed at $TARGET"

if [[ "${1:-}" == "--autostart" ]]; then
  AUTOSTART_TARGET="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/shushunya-desktop.desktop"
  install -D -m 0644 "$TEMP" "$AUTOSTART_TARGET"
  echo "Autostart installed at $AUTOSTART_TARGET"
fi
