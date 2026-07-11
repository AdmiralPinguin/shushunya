#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find "$ROOT" -type d -exec chmod 755 {} +
find "$ROOT" -type f -exec chmod 644 {} +

find "$ROOT" -type f \( \
  -name '*.sh' -o \
  -name '*.py' -o \
  -name 'cloudflared' -o \
  -path '*/android-tools/gradle/bin/*' -o \
  -path '*/android-tools/jdk/bin/*' -o \
  -path '*/android-tools/jdk/lib/jspawnhelper' -o \
  -path '*/android-tools/android-sdk/cmdline-tools/latest/bin/*' -o \
  -path '*/android-tools/android-sdk/platform-tools/*' -o \
  -path '*/android-tools/android-sdk/build-tools/*/*' -o \
  -path '*/android-tools/whisper.cpp/build/bin/*' -o \
  -path '*/.gradle-home/caches/*/transformed/aapt2-*-linux/aapt2' -o \
  -path '*/llama.cpp/*' -o \
  -path '*/WarpWails/tools/ffmpeg-*-static/*' \
\) -exec chmod 755 {} +

if [ -d "$ROOT/.secrets" ]; then
  chmod 700 "$ROOT/.secrets"
  find "$ROOT/.secrets" -type f -exec chmod 600 {} +
fi

echo "Permissions restored under $ROOT"
