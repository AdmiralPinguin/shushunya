#!/usr/bin/env sh
set -eu
export SERVICE_URL="${SERVICE_URL:-http://localhost:8080}"
python -m app.config_loader
