#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

require_command ss
assert_vm_stopped

validate_qcow2_image "$SYSTEM_DISK" "$SYSTEM_DISK_BYTES" "system disk"
validate_qcow2_image "$DATA_DISK" "$DATA_DISK_BYTES" "archive disk"

[[ -s "$OVMF_VARS" ]] || die "private UEFI NVRAM is missing or empty: $OVMF_VARS"
[[ -r "$OVMF_CODE" ]] || die "OVMF code is not readable: $OVMF_CODE"

if ss -H -ltn "sport = :$VM_SSH_PORT" | grep -q .; then
  die "reserved TCP port $VM_SSH_PORT is already listening"
fi

note "OK: UEFI code and private NVRAM are present"
note "OK: $VM_NAME is stopped and TCP port $VM_SSH_PORT is free"
note "OK: both images have independent metadata and no backing/data file"
