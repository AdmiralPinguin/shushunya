#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$ROOT/ShushunyaPalatineConsole.desktop"

DESKTOP_DIR="${XDG_DESKTOP_DIR:-}"
if [ -z "$DESKTOP_DIR" ] && [ -f "$HOME/.config/user-dirs.dirs" ]; then
  # shellcheck disable=SC1090
  . "$HOME/.config/user-dirs.dirs"
  DESKTOP_DIR="${XDG_DESKTOP_DIR:-}"
fi
if [ -z "$DESKTOP_DIR" ]; then
  DESKTOP_DIR="$HOME/Desktop"
fi
DESKTOP_DIR="${DESKTOP_DIR/#\$HOME/$HOME}"

mkdir -p "$DESKTOP_DIR"
TARGET="$DESKTOP_DIR/Shushunya Palatine Console.desktop"
cp "$SOURCE" "$TARGET"
chmod +x "$TARGET"

if command -v gio >/dev/null 2>&1; then
  gio set "$TARGET" metadata::trusted true >/dev/null 2>&1 || true
fi

echo "$TARGET"
