#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/media/shushunya/SHUSHUNYA/shushunya"
AGENT_ROOT="$PROJECT_ROOT/Mechanicum/ShushunyaAgent"

echo "LLM host:"
curl -fsS "http://127.0.0.1:8080/health"
echo

echo "ArchiveOfHeresy:"
curl -fsS "http://127.0.0.1:8090/health"
echo

echo "ShushunyaAgent API:"
curl -fsS "http://127.0.0.1:8095/health"
echo

echo "Sandbox self-test:"
cd "$AGENT_ROOT"
./scripts/self-test.sh
