#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

require_command qemu-img
assert_vm_stopped

[[ -r "$OVMF_CODE" ]] || die "OVMF code is not readable: $OVMF_CODE"
[[ -r "$OVMF_VARS_TEMPLATE" ]] || die "OVMF vars template is not readable: $OVMF_VARS_TEMPLATE"

umask 0007
mkdir -p "$VM_DIR" "$LOG_DIR" "$RUN_DIR" "$PROJECT_ROOT/runtime/snapshots"

create_disk_if_missing() {
  local path="$1"
  local size="$2"
  local label="$3"

  if [[ -e "$path" ]]; then
    [[ -f "$path" ]] || die "$label path exists but is not a regular file: $path"
    chmod 0660 "$path"
    note "$label already exists: $path"
    return
  fi

  qemu-img create -f qcow2 -o lazy_refcounts=on "$path" "$size"
  chmod 0660 "$path"
  note "$label created: $path ($size virtual)"
}

create_disk_if_missing "$SYSTEM_DISK" "$SYSTEM_DISK_SIZE" "system disk"
create_disk_if_missing "$DATA_DISK" "$DATA_DISK_SIZE" "archive disk"
validate_qcow2_image "$SYSTEM_DISK" "$SYSTEM_DISK_BYTES" "system disk"
validate_qcow2_image "$DATA_DISK" "$DATA_DISK_BYTES" "archive disk"

if [[ -e "$OVMF_VARS" ]]; then
  [[ -f "$OVMF_VARS" ]] || die "NVRAM path exists but is not a regular file: $OVMF_VARS"
  note "UEFI NVRAM already exists: $OVMF_VARS"
else
  cp --reflink=auto -- "$OVMF_VARS_TEMPLATE" "$OVMF_VARS"
  note "private UEFI NVRAM created: $OVMF_VARS"
fi
chmod 0660 "$OVMF_VARS"

note "Prepared only. Nothing was formatted or started."
