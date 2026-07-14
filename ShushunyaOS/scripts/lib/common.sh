#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_LIB_DIR/../.." && pwd -P)"
CONFIG_FILE="${SHUSHUNYA_VM_CONFIG:-$PROJECT_ROOT/config/vm.env}"

if [[ ! -r "$CONFIG_FILE" ]]; then
  printf 'ERROR: config is not readable: %s\n' "$CONFIG_FILE" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"

VM_DIR="$PROJECT_ROOT/runtime/vm"
LOG_DIR="$PROJECT_ROOT/runtime/logs"
RUN_DIR="$PROJECT_ROOT/runtime/run"

SYSTEM_DISK="$VM_DIR/system.qcow2"
DATA_DISK="$VM_DIR/archive.qcow2"
OVMF_VARS="$VM_DIR/OVMF_VARS.fd"
PID_FILE="$RUN_DIR/$VM_NAME.pid"
QMP_SOCKET="$RUN_DIR/$VM_NAME.qmp"
SERIAL_LOG="$LOG_DIR/$VM_NAME.serial.log"
OS_INSTALLED_MARKER="$VM_DIR/os-installed"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

read_pid_file() {
  [[ -f "$PID_FILE" ]] || return 1

  local pid
  IFS= read -r pid < "$PID_FILE" || true
  [[ "$pid" =~ ^[0-9]+$ ]] || die "invalid PID file: $PID_FILE"
  printf '%s\n' "$pid"
}

pid_belongs_to_vm() {
  local pid="$1"
  [[ -r "/proc/$pid/cmdline" ]] || return 1

  local executable
  executable="$(readlink -f -- "/proc/$pid/exe" 2>/dev/null)" || return 1
  [[ "${executable##*/}" == "qemu-system-x86_64" ]] || return 1

  local -a argv=()
  mapfile -d '' -t argv < "/proc/$pid/cmdline" || return 1

  local i
  local has_exact_name=0
  local has_exact_system_disk=0
  for ((i = 0; i + 1 < ${#argv[@]}; i++)); do
    if [[ "${argv[$i]}" == "-name" && "${argv[$((i + 1))]}" == "$VM_NAME" ]]; then
      has_exact_name=1
    fi
    if [[ "${argv[$i]}" == "-drive" && "${argv[$((i + 1))]}" == *"file=$SYSTEM_DISK,"* ]]; then
      has_exact_system_disk=1
    fi
  done

  [[ $has_exact_name -eq 1 && $has_exact_system_disk -eq 1 ]]
}

find_matching_vm_pids() {
  local proc pid
  for proc in /proc/[0-9]*; do
    pid="${proc##*/}"
    if pid_belongs_to_vm "$pid"; then
      printf '%s\n' "$pid"
    fi
  done
}

assert_vm_stopped() {
  local pid matches
  if [[ -f "$PID_FILE" ]]; then
    pid="$(read_pid_file)"
    if kill -0 "$pid" 2>/dev/null; then
      pid_belongs_to_vm "$pid" || die "PID $pid is alive but is not $VM_NAME; refusing to act"
      die "$VM_NAME is already running as PID $pid"
    fi
    rm -f -- "$PID_FILE" "$QMP_SOCKET"
  fi

  matches="$(find_matching_vm_pids)"
  [[ -z "$matches" ]] || die "$VM_NAME is running without the expected PID file (PID: $matches)"
}

require_running_vm_pid() {
  local pid
  [[ -f "$PID_FILE" ]] || die "$VM_NAME has no PID file: $PID_FILE"

  pid="$(read_pid_file)"
  kill -0 "$pid" 2>/dev/null || die "$VM_NAME PID $pid is not alive"
  pid_belongs_to_vm "$pid" || die "PID $pid is alive but is not $VM_NAME; refusing to act"
  printf '%s\n' "$pid"
}

validate_qcow2_image() {
  local path="$1"
  local expected_bytes="$2"
  local label="$3"

  require_command qemu-img
  require_command python3
  [[ -f "$path" ]] || die "$label is missing: $path"

  python3 - "$path" "$expected_bytes" "$label" <<'PY'
import json
import subprocess
import sys

path, expected_raw, label = sys.argv[1:]
expected = int(expected_raw)
proc = subprocess.run(
    ["qemu-img", "info", "--output=json", path],
    check=True,
    text=True,
    stdout=subprocess.PIPE,
)
info = json.loads(proc.stdout)

if info.get("format") != "qcow2":
    raise SystemExit(f"ERROR: {label} format is {info.get('format')!r}, expected 'qcow2'")
if info.get("virtual-size") != expected:
    raise SystemExit(
        f"ERROR: {label} virtual size is {info.get('virtual-size')}, expected {expected}"
    )

forbidden = {
    "backing-filename",
    "full-backing-filename",
    "data-file",
    "full-data-file",
}

def reject_external_files(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden and child:
                raise SystemExit(f"ERROR: {label} unexpectedly references external storage")
            reject_external_files(child)
    elif isinstance(value, list):
        for child in value:
            reject_external_files(child)

reject_external_files(info)
if info.get("format-specific", {}).get("data", {}).get("corrupt") is True:
    raise SystemExit(f"ERROR: {label} is marked corrupt")

print(f"OK: {label}: qcow2, {expected} bytes, independent")
PY
}
