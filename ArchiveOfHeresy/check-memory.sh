#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${ARCHIVE_PORT:-8090}"
BASE_URL="${ARCHIVE_BASE_URL:-http://127.0.0.1:$PORT}"
NAMESPACE="${1:-agent}"
QUERY="${2:-memory}"
ENV_FILE="$ROOT/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

AUTH_ARGS=()
if [ -n "${ARCHIVE_API_KEY:-}" ]; then
  AUTH_ARGS=(-H "Authorization: Bearer $ARCHIVE_API_KEY")
fi

echo "Archive health:"
curl -fsS "$BASE_URL/health"
echo
echo

echo "Active focus namespace=$NAMESPACE:"
curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL/archive/focus/active?namespace=$NAMESPACE"
echo
echo

echo "Vector search namespace=$NAMESPACE query=$QUERY:"
curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL/archive/vector/search?namespace=$NAMESPACE&q=$QUERY"
echo
echo

echo "Graph search namespace=$NAMESPACE query=$QUERY:"
curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL/archive/graph/search?namespace=$NAMESPACE&q=$QUERY"
echo
echo

echo "Recent memory events namespace=$NAMESPACE:"
curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL/archive/memory/events?namespace=$NAMESPACE&limit=5"
echo
