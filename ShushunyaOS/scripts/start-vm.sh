#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

require_command qemu-system-x86_64
require_command qemu-img
require_command ss

attach_data=0
if [[ "${1:-}" == "--with-data" && $# -eq 1 ]]; then
  attach_data=1
elif [[ $# -ne 0 ]]; then
  die "usage: $0 [--with-data]"
fi

[[ "$VM_SSH_BIND" == "127.0.0.1" ]] || die "VM_SSH_BIND must remain 127.0.0.1"
[[ "$VM_SSH_PORT" =~ ^[0-9]+$ ]] || die "VM_SSH_PORT must be numeric"
((VM_SSH_PORT >= 1 && VM_SSH_PORT <= 65535)) || die "VM_SSH_PORT is out of range"

assert_vm_stopped

[[ -f "$SYSTEM_DISK" ]] || die "system disk is missing; run scripts/prepare-vm.sh"
[[ -f "$OVMF_VARS" ]] || die "UEFI NVRAM is missing; run scripts/prepare-vm.sh"
[[ -r "$OVMF_CODE" ]] || die "OVMF code is not readable: $OVMF_CODE"
[[ -r /dev/kvm && -w /dev/kvm ]] || die "/dev/kvm is unavailable to this user"
[[ -f "$OS_INSTALLED_MARKER" ]] || die "boot is locked: no OS is installed; marker missing: $OS_INSTALLED_MARKER"

validate_qcow2_image "$SYSTEM_DISK" "$SYSTEM_DISK_BYTES" "system disk"
if [[ $attach_data -eq 1 ]]; then
  validate_qcow2_image "$DATA_DISK" "$DATA_DISK_BYTES" "archive disk"
fi

if ss -H -ltn "sport = :$VM_SSH_PORT" | grep -q .; then
  die "TCP port $VM_SSH_PORT is already listening"
fi

umask 0007
mkdir -p "$RUN_DIR" "$LOG_DIR"
rm -f -- "$QMP_SOCKET"

qemu_args=(
  -name "$VM_NAME"
  -machine q35,accel=kvm
  -sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny
  -cpu host
  -smp "$VM_CPUS"
  -m "$VM_RAM_MIB"
  -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
  -drive "if=pflash,format=raw,file=$OVMF_VARS"
  -drive "file=$SYSTEM_DISK,if=none,format=qcow2,id=osdisk,cache=none,discard=unmap"
  -device "virtio-blk-pci,drive=osdisk,serial=SHUSHUNYA-OS"
  -netdev "user,id=net0,restrict=on,hostfwd=tcp:$VM_SSH_BIND:$VM_SSH_PORT-:22"
  -device "virtio-net-pci,netdev=net0,mac=$VM_MAC"
  -device virtio-rng-pci
  -rtc base=utc,clock=host
  -display none
  -serial "file:$SERIAL_LOG"
  -qmp "unix:$QMP_SOCKET,server=on,wait=off"
  -daemonize
  -pidfile "$PID_FILE"
)

if [[ $attach_data -eq 1 ]]; then
  qemu_args+=(
    -drive "file=$DATA_DISK,if=none,format=qcow2,id=datadisk,cache=none,discard=unmap"
    -device "virtio-blk-pci,drive=datadisk,serial=SHUSHUNYA-DATA"
  )
fi

qemu-system-x86_64 "${qemu_args[@]}"

pid="$(require_running_vm_pid)"
note "$VM_NAME started as PID $pid; SSH forward: $VM_SSH_BIND:$VM_SSH_PORT; archive attached: $attach_data"
