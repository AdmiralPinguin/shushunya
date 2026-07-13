#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
. "$ROOT/deploy/shushunya-core.conf"
if [[ -r "$ROOT/.secrets/shushunya-core.env" ]]; then
  . "$ROOT/.secrets/shushunya-core.env"
fi
set +a
RUNTIME="${SHUSHUNYA_CORE_RUNTIME_DIR:-$ROOT/runtime/shushunya-core}"
install -d -m 0700 "$RUNTIME"

# The full stack is started by a system unit under the codexbox account. Give
# that non-login process an explicit route to the lingering user manager.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

install -d -m 0700 "$HOME/.config/systemd/user"
install -m 0644 "$ROOT/deploy/shushunya-core.service" "$HOME/.config/systemd/user/shushunya-core.service"
systemctl --user daemon-reload
systemctl --user enable shushunya-core.service >/dev/null
# A healthy old process is still old code. Deployment always restarts the
# supervised unit after installing the current contract.
systemctl --user restart shushunya-core.service

for _ in {1..60}; do
  if curl -fsS --max-time 2 http://127.0.0.1:7600/health/ready >/dev/null 2>&1; then
    echo "ShushunyaCore ready on 127.0.0.1:7600"
    exit 0
  fi
  sleep 0.5
done

echo "ShushunyaCore did not become ready" >&2
systemctl --user --no-pager --full status shushunya-core.service >&2 || true
exit 1
