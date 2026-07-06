#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="/media/shushunya/SHUSHUNYA/shushunya"
SEARCH_VENV="$ROOT/.venv"
SRC="$ROOT/searxng-src"
BOOTSTRAP="$ROOT/bootstrap/get-pip.py"
SETTINGS="$ROOT/config/settings.yml"
LIMITER="$ROOT/config/limiter.toml"

mkdir -p "$ROOT/bootstrap" "$ROOT/config"

if [[ ! -d "$SRC/.git" ]]; then
  git clone --depth 1 https://github.com/searxng/searxng.git "$SRC"
fi

if [[ ! -f "$BOOTSTRAP" ]]; then
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$BOOTSTRAP"
fi

if [[ ! -x "$SEARCH_VENV/bin/python" ]]; then
  python3 -m venv --without-pip "$SEARCH_VENV"
  "$SEARCH_VENV/bin/python" "$BOOTSTRAP"
fi

"$SEARCH_VENV/bin/python" -m pip install -U pip setuptools wheel
"$SEARCH_VENV/bin/python" -m pip install -r "$SRC/requirements.txt"
"$SEARCH_VENV/bin/python" -m pip install --no-build-isolation -e "$SRC"

if [[ ! -f "$LIMITER" ]]; then
  cp "$SRC/searx/limiter.toml" "$LIMITER"
  chmod 600 "$LIMITER"
fi

if [[ ! -f "$SETTINGS" ]]; then
  secret="$("$SEARCH_VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat > "$SETTINGS" <<EOF
use_default_settings:
  engines:
    remove:
      - ahmia
      - torch

general:
  instance_name: "Shushunya SearXNG"
  enable_metrics: false

search:
  safe_search: 0
  autocomplete: ""
  default_lang: "auto"
  formats:
    - html
    - json

server:
  port: 8888
  bind_address: "127.0.0.1"
  base_url: false
  limiter: false
  public_instance: false
  secret_key: "$secret"
  method: "GET"
  image_proxy: false

valkey:
  url: false

outgoing:
  request_timeout: 5.0
  max_request_timeout: 10.0
  enable_http2: true

engines:
  - name: brave
    disabled: true
  - name: brave.images
    disabled: true
  - name: brave.videos
    disabled: true
  - name: brave.news
    disabled: true
  - name: startpage
    disabled: true
  - name: startpage news
    disabled: true
  - name: startpage images
    disabled: true
EOF
  chmod 600 "$SETTINGS"
fi

echo "SearXNG environment ready:"
echo "  Python: $SEARCH_VENV/bin/python"
echo "  Settings: $SETTINGS"
