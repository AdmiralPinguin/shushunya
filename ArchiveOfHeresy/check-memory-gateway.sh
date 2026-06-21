#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${ARCHIVE_PORT:-8090}"
BASE_URL="${ARCHIVE_BASE_URL:-http://127.0.0.1:$PORT}"
NAMESPACE="${1:-agent}"
QUERY="${2:-memory gateway}"
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

echo "Memory Gateway catalog namespace=$NAMESPACE:"
curl -fsS -G "${AUTH_ARGS[@]}" --data-urlencode "namespace=$NAMESPACE" "$BASE_URL/archive/memory/catalog"
echo
echo

echo "Memory Gateway search namespace=$NAMESPACE query=$QUERY:"
curl -fsS -G "${AUTH_ARGS[@]}" --data-urlencode "namespace=$NAMESPACE" --data-urlencode "q=$QUERY" "$BASE_URL/archive/memory/search"
echo
echo

echo "Memory Gateway active focus namespace=$NAMESPACE:"
curl -fsS -G "${AUTH_ARGS[@]}" --data-urlencode "namespace=$NAMESPACE" --data-urlencode "id=active" "$BASE_URL/archive/memory/focus"
echo
echo

echo "Memory Gateway wiki missing-page probe namespace=$NAMESPACE:"
probe_file="$(mktemp)"
status="$(
  curl -sS -o "$probe_file" -w "%{http_code}" -G "${AUTH_ARGS[@]}" \
    --data-urlencode "namespace=$NAMESPACE" \
    --data-urlencode "title=__archive_gateway_probe_missing__" \
    "$BASE_URL/archive/memory/wiki"
)"
cat "$probe_file"
rm -f "$probe_file"
echo
if [ "$status" != "404" ]; then
  echo "Expected missing wiki probe to return 404, got $status" >&2
  exit 1
fi
echo

echo "Memory Gateway events namespace=$NAMESPACE:"
curl -fsS -G "${AUTH_ARGS[@]}" --data-urlencode "namespace=$NAMESPACE" --data-urlencode "limit=5" "$BASE_URL/archive/memory/events"
echo

if [ "${ARCHIVE_GATEWAY_PROPOSE:-0}" = "1" ]; then
  echo
  echo "Memory Gateway proposal smoke namespace=$NAMESPACE:"
  curl -fsS -X POST "${AUTH_ARGS[@]}" \
    -H "Content-Type: application/json" \
    -d "{\"namespace\":\"$NAMESPACE\",\"requester\":\"gateway-smoke-script\",\"target\":\"focus\",\"importance\":2,\"proposal\":\"Memory Gateway smoke script can submit proposals through ArchiveOfHeresy.\",\"evidence\":\"ARCHIVE_GATEWAY_PROPOSE=1 was set.\"}" \
    "$BASE_URL/archive/memory/propose-change"
  echo
fi
