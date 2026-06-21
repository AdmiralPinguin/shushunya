#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env"
QUERY="${1:-memory}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

PORT="${ARCHIVE_PORT:-8090}"
BASE_URL="${ARCHIVE_BASE_URL:-http://127.0.0.1:$PORT}"

AUTH_ARGS=()
if [ -n "${ARCHIVE_API_KEY:-}" ]; then
  AUTH_ARGS=(-H "Authorization: Bearer $ARCHIVE_API_KEY")
fi

echo "Archive health:"
curl -fsS "$BASE_URL/health"
echo

echo "Models through archive:"
curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL/v1/models"
echo
echo

echo "Memory diagnostics query=$QUERY:"
python3 - "$BASE_URL" "$QUERY" "${ARCHIVE_API_KEY:-}" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

base_url, query, api_key = sys.argv[1], sys.argv[2], sys.argv[3]


def request(path, params=None):
    url = base_url + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def catalog(namespace):
    data = request("/archive/memory/catalog", {"namespace": namespace, "requester": "check-main"})
    focus = data.get("focus", {})
    wiki = data.get("wiki", {})
    vector = data.get("vector", {})
    graph = data.get("graph", {})
    print(
        f"catalog[{namespace}]: "
        f"focus={len(focus.get('books', []) or [])}, "
        f"wiki={len(wiki.get('pages', []) or [])}, "
        f"vector_chunks={vector.get('chunks', 0)}, "
        f"vector_turns={vector.get('turns', 0)}, "
        f"graph_nodes={graph.get('nodes', 0)}, "
        f"graph_edges={graph.get('edges', 0)}"
    )


for namespace in ("default", "agent"):
    catalog(namespace)

health = request("/health")
embedding = health.get("vector_embedding") or {}
print(
    "vector_embedding: "
    f"backend={embedding.get('backend')}, "
    f"resolved={embedding.get('resolved_version')}, "
    f"last={embedding.get('last_backend')}"
)

for namespace in ("default", "agent"):
    search = request(
        "/archive/memory/search",
        {
            "namespace": namespace,
            "requester": "check-main",
            "q": query,
            "limit": 5,
            "layers": "focus,wiki,vector,graph",
            "include_content": 0,
        },
    )
    counts = search.get("counts", {})
    print(
        f"memory_search[{namespace}]: "
        f"focus={counts.get('focus', 0)}, "
        f"wiki={counts.get('wiki', 0)}, "
        f"vector={counts.get('vector', 0)}, "
        f"graph_nodes={counts.get('graph_nodes', 0)}, "
        f"graph_edges={counts.get('graph_edges', 0)}"
    )

    vector = request("/archive/vector/search", {"namespace": namespace, "q": query})
    graph = request("/archive/graph/search", {"namespace": namespace, "q": query})
    graph_matches = graph.get("matches", {})
    print(f"vector_search[{namespace}]: matches={len(vector.get('matches', []) or [])}")
    print(
        f"graph_search[{namespace}]: "
        f"nodes={len(graph_matches.get('nodes', []) or [])}, "
        f"edges={len(graph_matches.get('edges', []) or [])}"
    )
PY
