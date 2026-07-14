#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

matches="$(find_matching_vm_pids)"
if [[ ! -f "$PID_FILE" ]]; then
  [[ -z "$matches" ]] || die "$VM_NAME is running without the expected PID file (PID: $matches)"
  note "state: stopped"
else
  pid="$(read_pid_file)"
  if kill -0 "$pid" 2>/dev/null; then
    pid_belongs_to_vm "$pid" || die "PID $pid is alive but is not $VM_NAME; refusing to trust it"
    [[ "$matches" == "$pid" ]] || die "unexpected duplicate or mismatched $VM_NAME process set: $matches"
    note "state: running"
    note "pid: $pid"
  else
    [[ -z "$matches" ]] || die "$VM_NAME is running with a stale PID file (live PID: $matches)"
    note "state: stopped (stale PID file: $pid)"
  fi
fi

note "name: $VM_NAME"
note "ssh: $VM_SSH_BIND:$VM_SSH_PORT"
note "system disk: $SYSTEM_DISK"
note "archive disk: $DATA_DISK"
note "OS boot unlocked: $([[ -f "$OS_INSTALLED_MARKER" ]] && printf yes || printf no)"
note "archive attachment default: no (requires --with-data for each start)"
