#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_NAME="${PROFILE_NAME:-usr.local.bin.shushunya-agent-shell}"
SOURCE_PROFILE="${SOURCE_PROFILE:-$ROOT/profiles/apparmor/$PROFILE_NAME}"
TARGET_PROFILE="${TARGET_PROFILE:-/etc/apparmor.d/$PROFILE_NAME}"
MODE="${MODE:-enforce}"

cat <<EOF
This prepares an AppArmor profile for ShushunyaAgent sandbox launcher:
  source: $SOURCE_PROFILE
  target: $TARGET_PROFILE
  mode:   $MODE

Requirements:
  - AppArmor enabled on the host
  - apparmor_parser installed
  - root privileges to install/reload

Run with CONFIRM=1 to install. Default is dry-run.
EOF

if [[ ! -f "$SOURCE_PROFILE" ]]; then
  echo "Missing source profile: $SOURCE_PROFILE" >&2
  exit 1
fi

if [[ "${CONFIRM:-0}" != "1" ]]; then
  if command -v apparmor_parser >/dev/null; then
    if apparmor_parser --skip-kernel-load "$SOURCE_PROFILE" >/dev/null 2>&1; then
      syntax_status="Profile syntax check passed."
    else
      syntax_status="Profile syntax check could not run without elevated AppArmor cache access."
    fi
  else
    syntax_status="Profile syntax check skipped: apparmor_parser is not installed."
  fi
  cat <<EOF

Dry run. Commands that would be used:
  sudo install -m 0644 "$SOURCE_PROFILE" "$TARGET_PROFILE"
  sudo apparmor_parser -r "$TARGET_PROFILE"
  sudo aa-$MODE "$TARGET_PROFILE"

$syntax_status
EOF
  exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo CONFIRM=1 $0" >&2
  exit 1
fi

if ! command -v apparmor_parser >/dev/null; then
  echo "Missing apparmor_parser. Install apparmor/apparmor-utils first." >&2
  exit 1
fi

apparmor_parser --skip-kernel-load "$SOURCE_PROFILE" >/dev/null

install -m 0644 "$SOURCE_PROFILE" "$TARGET_PROFILE"
apparmor_parser -r "$TARGET_PROFILE"
if command -v "aa-$MODE" >/dev/null; then
  "aa-$MODE" "$TARGET_PROFILE"
else
  echo "aa-$MODE is not available; profile was loaded but mode was not changed" >&2
fi
