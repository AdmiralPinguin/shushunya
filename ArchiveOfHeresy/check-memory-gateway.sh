#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${ARCHIVE_PORT:-8090}"
BASE_URL="${ARCHIVE_BASE_URL:-http://127.0.0.1:$PORT}"
NAMESPACE="${1:-agent}"
QUERY="${2:-memory gateway}"
REQUESTER="${ARCHIVE_GATEWAY_REQUESTER:-check-memory-gateway}"
MANIFEST_ONLY=0
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  cat <<'USAGE'
Usage:
  ./check-memory-gateway.sh [namespace] [query]
  ./check-memory-gateway.sh --manifest-only

Environment:
  ARCHIVE_BASE_URL           Override archive URL, default http://127.0.0.1:8090
  ARCHIVE_API_KEY            Bearer token when archive auth is enabled
  ARCHIVE_GATEWAY_REQUESTER  Audit requester label, default check-memory-gateway
  ARCHIVE_GATEWAY_PROPOSE=1  Also submit a write proposal smoke test
USAGE
  exit 0
fi
if [ "$NAMESPACE" = "--manifest-only" ]; then
  MANIFEST_ONLY=1
  NAMESPACE="agent"
fi
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

if [ "$MANIFEST_ONLY" = "1" ]; then
  curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL/archive/memory/gateway"
  echo
  exit 0
fi

echo "Archive health:"
curl -fsS "$BASE_URL/health"
echo
echo

echo "Memory Gateway manifest:"
curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL/archive/memory/gateway"
echo
echo

echo "Memory Gateway catalog namespace=$NAMESPACE:"
curl -fsS -G "${AUTH_ARGS[@]}" --data-urlencode "namespace=$NAMESPACE" --data-urlencode "requester=$REQUESTER" "$BASE_URL/archive/memory/catalog"
echo
echo

echo "Memory Gateway search namespace=$NAMESPACE query=$QUERY:"
curl -fsS -G "${AUTH_ARGS[@]}" --data-urlencode "namespace=$NAMESPACE" --data-urlencode "requester=$REQUESTER" --data-urlencode "q=$QUERY" "$BASE_URL/archive/memory/search"
echo
echo

echo "Memory Gateway active focus namespace=$NAMESPACE:"
curl -fsS -G "${AUTH_ARGS[@]}" --data-urlencode "namespace=$NAMESPACE" --data-urlencode "requester=$REQUESTER" --data-urlencode "id=active" "$BASE_URL/archive/memory/focus"
echo
echo

echo "Memory Gateway wiki missing-page probe namespace=$NAMESPACE:"
probe_file="$(mktemp)"
status="$(
  curl -sS -o "$probe_file" -w "%{http_code}" -G "${AUTH_ARGS[@]}" \
    --data-urlencode "namespace=$NAMESPACE" \
    --data-urlencode "requester=$REQUESTER" \
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

echo "Memory Gateway filtered search events namespace=$NAMESPACE:"
curl -fsS -G "${AUTH_ARGS[@]}" \
  --data-urlencode "namespace=$NAMESPACE" \
  --data-urlencode "component=memory_gateway" \
  --data-urlencode "event_action=search" \
  --data-urlencode "requester=$REQUESTER" \
  --data-urlencode "limit=3" \
  "$BASE_URL/archive/memory/events"
echo
echo

echo "Memory Gateway unknown namespace guard:"
guard_file="$(mktemp)"
guard_status="$(
  curl -sS -o "$guard_file" -w "%{http_code}" -G "${AUTH_ARGS[@]}" \
    --data-urlencode "namespace=__gateway_unknown_probe__" \
    "$BASE_URL/archive/memory/catalog"
)"
cat "$guard_file"
rm -f "$guard_file"
echo
if [ "$guard_status" != "404" ]; then
  echo "Expected unknown namespace guard to return 404, got $guard_status" >&2
  exit 1
fi

if [ "${ARCHIVE_GATEWAY_PROPOSE:-0}" = "1" ]; then
  echo
  echo "Memory Gateway proposal smoke namespace=$NAMESPACE:"
  curl -fsS -X POST "${AUTH_ARGS[@]}" \
    -H "Content-Type: application/json" \
    -d "{\"namespace\":\"$NAMESPACE\",\"requester\":\"gateway-smoke-script\",\"target\":\"focus\",\"importance\":2,\"proposal\":\"Memory Gateway smoke script can submit proposals through ArchiveOfHeresy.\",\"evidence\":\"ARCHIVE_GATEWAY_PROPOSE=1 was set.\"}" \
    "$BASE_URL/archive/memory/propose-change"
  echo
fi
