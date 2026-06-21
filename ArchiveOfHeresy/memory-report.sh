#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env"
NAMESPACE="${1:-default}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

PORT="${ARCHIVE_PORT:-8090}"
BASE_URL="${ARCHIVE_BASE_URL:-http://127.0.0.1:$PORT}"

python3 - "$BASE_URL" "$NAMESPACE" "${ARCHIVE_API_KEY:-}" "$ROOT/runtime/archive-main.log" <<'PY'
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

base_url, namespace, api_key, log_path = sys.argv[1:5]


def request(path, params=None):
    url = base_url + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def compact(value, limit=180):
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


print(f"ArchiveOfHeresy memory report namespace={namespace}")

health = request("/health")
print(f"service={health.get('service')} status={health.get('status')}")
print(f"magos_context_layers={health.get('magos_context_layers')}")

catalog = request("/archive/memory/catalog", {"namespace": namespace, "requester": "memory-report"})
focus = catalog.get("focus", {})
wiki = catalog.get("wiki", {})
vector = catalog.get("vector", {})
graph = catalog.get("graph", {})
books = focus.get("books", []) or []
active = next((book for book in books if book.get("active")), None)

print("")
print("Counts")
print(f"active_focus={active.get('title') if active else '(none)'}")
print(f"focus_files={len(books)}")
print(f"wiki_pages={len(wiki.get('pages', []) or [])}")
print(f"vector_chunks={vector.get('chunks', 0)}")
print(f"vector_turns={vector.get('turns', 0)}")
print(f"graph_nodes={graph.get('nodes', 0)}")
print(f"graph_edges={graph.get('edges', 0)}")

events = request(
    "/archive/memory/events",
    {"namespace": namespace, "limit": 10},
).get("events", [])
print("")
print("Recent memory events")
for event in events:
    body = event.get("event") if isinstance(event.get("event"), dict) else {}
    component = body.get("component") or "unknown"
    action = body.get("action") or body.get("status") or body.get("result", {}).get("status") or "event"
    created = event.get("created_at") or event.get("turn_created_at") or ""
    detail = body.get("error") or body.get("query") or body.get("result") or ""
    print(f"- {created} {component}:{action} {compact(detail)}")

print("")
print("Recent librarian/magos errors")
errors = []
path = Path(log_path)
if path.exists():
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
        lowered = line.lower()
        if "librarian" in lowered or "magos" in lowered:
            if "error" in lowered or "fail-soft" in lowered or "exception" in lowered:
                errors.append(line.strip())
for line in errors[-10:]:
    print(f"- {compact(line, 240)}")
if not errors:
    print("- none in recent runtime log")
PY
