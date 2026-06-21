#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/media/shushunya/SHUSHUNYA/shushunya"

cd "$PROJECT_ROOT/Mechanicum/ShushunyaAgent"
./scripts/stop-agent-api.sh

cd "$PROJECT_ROOT/ArchiveOfHeresy"
./stop-main.sh

cd "$PROJECT_ROOT/CoreOfMadness"
./llm-host/scripts/stop-host.sh
