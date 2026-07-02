#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/media/shushunya/SHUSHUNYA/shushunya"

cd "$PROJECT_ROOT/EyeOfTerror/Warmaster/MobileGateway/ShushunyaAgent"
./scripts/stop-agent-tunnel.sh
./scripts/stop-agent-api.sh

cd "$PROJECT_ROOT/EyeOfTerror/Services/Search/SearXNG"
./scripts/stop-searxng.sh

cd "$PROJECT_ROOT/ArchiveOfHeresy"
./stop-main.sh

cd "$PROJECT_ROOT/CoreOfMadness"
./llm-host/scripts/stop-host.sh
