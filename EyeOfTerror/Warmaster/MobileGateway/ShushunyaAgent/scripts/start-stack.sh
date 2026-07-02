#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/media/shushunya/SHUSHUNYA/shushunya"

cd "$PROJECT_ROOT/CoreOfMadness"
./llm-host/scripts/start-host.sh

cd "$PROJECT_ROOT/ArchiveOfHeresy"
./start-main.sh

cd "$PROJECT_ROOT/EyeOfTerror/Services/Search/SearXNG"
./scripts/start-searxng.sh

cd "$PROJECT_ROOT/EyeOfTerror/Warmaster/MobileGateway/ShushunyaAgent"
./scripts/start-agent-api.sh
./scripts/check-agent.sh
