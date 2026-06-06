#!/usr/bin/env bash
set -euo pipefail

PORT="${ARCHIVE_PORT:-8090}"
BASE_URL="${ARCHIVE_BASE_URL:-http://127.0.0.1:$PORT}"

echo "Archive health:"
curl -fsS "$BASE_URL/health"
echo

echo "Models through archive:"
curl -fsS "$BASE_URL/v1/models"
echo
