#!/bin/bash
cd /media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness/vm-sandbox || exit 1
# The data disk remounts the key world-readable (644); SSH refuses it. Force 600.
chmod 600 skitarii_key 2>/dev/null || { mkdir -p ~/.ssh; cp skitarii_key ~/.ssh/skitarii_key; chmod 600 ~/.ssh/skitarii_key; }
for PID in $(pgrep -f "qemu-system.*skitarii"); do kill "$PID" 2>/dev/null; done
sleep 2
setsid nohup qemu-system-x86_64 \
  -name skitarii-vm \
  -machine q35,accel=kvm -cpu host -smp 8 -m 8192 \
  -drive file=disk.qcow2,if=virtio,format=qcow2 \
  -drive file=seed.iso,if=virtio,format=raw \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:2222-:22 \
  -device virtio-net-pci,netdev=n0 \
  -display none -serial file:console.log \
  > qemu.log 2>&1 &
echo "qemu pid $!"
