#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

force=0
if [[ "${1:-}" == "--force" ]]; then
  force=1
elif [[ $# -ne 0 ]]; then
  die "usage: $0 [--force]"
fi

pid="$(require_running_vm_pid)"

if [[ $force -eq 0 ]]; then
  die "safe guest shutdown is not wired yet; shut it down inside the guest or rerun with --force"
fi

pid_belongs_to_vm "$pid" || die "PID identity changed; refusing to signal it"
kill -TERM "$pid"

for _ in {1..50}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f -- "$PID_FILE" "$QMP_SOCKET"
    note "$VM_NAME stopped"
    exit 0
  fi
  sleep 0.2
done

die "$VM_NAME did not stop after SIGTERM; no stronger signal was sent"
