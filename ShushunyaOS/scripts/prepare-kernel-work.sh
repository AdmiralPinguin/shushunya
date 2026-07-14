#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
LOCK_FILE="$PROJECT_ROOT/kernel/source.lock.json"
SERIES_FILE="$PROJECT_ROOT/kernel/patches/series"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

for command_name in python3 patch tar flock mkdir chmod mv cp find; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command not found: $command_name"
done

KERNEL_VERSION="$(python3 - "$LOCK_FILE" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["version"])
PY
)"
[[ "$KERNEL_VERSION" =~ ^[1-9][0-9]*\.[0-9]+\.[0-9]+$ ]] || die "invalid kernel version in source lock"

WORK_PARENT="$PROJECT_ROOT/kernel/work"
WORK_DIR="$WORK_PARENT/linux-$KERNEL_VERSION"
PATCH_DIR="$PROJECT_ROOT/kernel/patches"
TARBALL="$PROJECT_ROOT/kernel/cache/linux-$KERNEL_VERSION.tar.xz"
RUN_DIR="$PROJECT_ROOT/runtime/run"

umask 0027
mkdir -p "$WORK_PARENT" "$RUN_DIR"
exec 8>"$RUN_DIR/kernel-work-$KERNEL_VERSION.lock"
flock -n 8 || die "another kernel work-tree preparation is already running"

[[ ! -e "$WORK_DIR" ]] || die "work tree already exists; it will not be overwritten: $WORK_DIR"
"$SCRIPT_DIR/fetch-kernel.sh"
[[ -f "$TARBALL" ]] || die "verified tarball is missing: $TARBALL"

STAGING_DIR="$WORK_PARENT/linux-$KERNEL_VERSION.partial.$$"
[[ ! -e "$STAGING_DIR" ]] || die "staging path already exists: $STAGING_DIR"
mkdir -m 0700 "$STAGING_DIR"
tar --extract --xz --file "$TARBALL" --directory "$STAGING_DIR" \
  --strip-components=1 --no-same-owner --no-same-permissions
cp -- "$LOCK_FILE" "$STAGING_DIR/.shushunya-source-lock.json"

while IFS= read -r patch_name || [[ -n "$patch_name" ]]; do
  patch_name="${patch_name%%#*}"
  patch_name="${patch_name#"${patch_name%%[![:space:]]*}"}"
  patch_name="${patch_name%"${patch_name##*[![:space:]]}"}"
  [[ -n "$patch_name" ]] || continue
  [[ "$patch_name" != /* && "$patch_name" != *".."* ]] || die "unsafe patch path in series: $patch_name"
  [[ -f "$PATCH_DIR/$patch_name" ]] || die "patch listed in series is missing: $patch_name"
  patch --batch --fuzz=0 --directory "$STAGING_DIR" --strip=1 --forward < "$PATCH_DIR/$patch_name"
done < "$SERIES_FILE"

find "$STAGING_DIR" -type d -exec chmod 2770 {} +
find "$STAGING_DIR" -type f -perm /111 -exec chmod 0770 {} +
find "$STAGING_DIR" -type f ! -perm /111 -exec chmod 0660 {} +
mv -T -- "$STAGING_DIR" "$WORK_DIR"
printf 'Prepared writable patch work tree: %s\n' "$WORK_DIR"
