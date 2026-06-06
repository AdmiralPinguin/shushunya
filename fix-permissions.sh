#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find "$ROOT" -type d -exec chmod 755 {} +
find "$ROOT" -type f -exec chmod 644 {} +

find "$ROOT" -type f \( \
  -name '*.sh' -o \
  -name '*.py' -o \
  -path '*/llama.cpp/*' \
\) -exec chmod 755 {} +

echo "Permissions restored under $ROOT"
