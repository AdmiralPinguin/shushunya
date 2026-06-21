#!/usr/bin/env bash
set -euo pipefail

SANDBOX_ROOT="${SANDBOX_ROOT:-/media/shushunya/ARCHIVE/shushunya-agent-sandbox}"
MOUNT_POINT="${MOUNT_POINT:-/media/shushunya/ARCHIVE}"
PROJECT_ID="${PROJECT_ID:-4242}"
PROJECT_NAME="${PROJECT_NAME:-shushunya-agent-sandbox}"
LIMIT="${LIMIT:-500G}"

validate_project_files() {
  local project_line name_line
  project_line="$(grep -E "^${PROJECT_ID}:" /etc/projects 2>/dev/null || true)"
  name_line="$(grep -E "^${PROJECT_NAME}:" /etc/projid 2>/dev/null || true)"
  if [[ -n "$project_line" && "$project_line" != "$PROJECT_ID:$SANDBOX_ROOT" ]]; then
    echo "Project id $PROJECT_ID is already assigned differently in /etc/projects: $project_line" >&2
    exit 1
  fi
  if [[ -n "$name_line" && "$name_line" != "$PROJECT_NAME:$PROJECT_ID" ]]; then
    echo "Project name $PROJECT_NAME is already assigned differently in /etc/projid: $name_line" >&2
    exit 1
  fi
}

cat <<EOF
This prepares ext4 project quota for:
  sandbox: $SANDBOX_ROOT
  mount:   $MOUNT_POINT
  project: $PROJECT_NAME ($PROJECT_ID)
  limit:   $LIMIT

Requirements:
  - ext4 mounted with prjquota
  - quota tools installed
  - root privileges

This script may edit /etc/projects and /etc/projid and remount $MOUNT_POINT.
Run with CONFIRM=1 to apply.
EOF

if [[ ! -d "$SANDBOX_ROOT" ]]; then
  echo "Sandbox root does not exist: $SANDBOX_ROOT" >&2
  exit 1
fi

if [[ ! -d "$MOUNT_POINT" ]]; then
  echo "Mount point does not exist: $MOUNT_POINT" >&2
  exit 1
fi

if ! findmnt -T "$SANDBOX_ROOT" >/dev/null; then
  echo "Could not resolve filesystem mount for sandbox root: $SANDBOX_ROOT" >&2
  exit 1
fi

validate_project_files

if [[ "${CONFIRM:-0}" != "1" ]]; then
  current_mount="$(findmnt -no TARGET,FSTYPE,OPTIONS -T "$SANDBOX_ROOT" || true)"
  cat <<EOF

Current sandbox filesystem:
  $current_mount

Dry run. Commands that would be used:
  sudo mount -o remount,prjquota "$MOUNT_POINT"
  echo "$PROJECT_ID:$SANDBOX_ROOT" | sudo tee -a /etc/projects
  echo "$PROJECT_NAME:$PROJECT_ID" | sudo tee -a /etc/projid
  sudo chattr -p "$PROJECT_ID" "$SANDBOX_ROOT"
  sudo xfs_quota -x -c 'project -s $PROJECT_NAME' "$MOUNT_POINT"
  sudo xfs_quota -x -c 'limit -p bhard=$LIMIT bsoft=$LIMIT $PROJECT_NAME' "$MOUNT_POINT"

Note: despite the name, xfs_quota is also the usual userspace tool for ext4
project quotas on modern Linux.
EOF
  exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo CONFIRM=1 $0" >&2
  exit 1
fi

command -v xfs_quota >/dev/null || {
  echo "Missing xfs_quota. Install quota/xfsprogs package first." >&2
  exit 1
}

mount -o remount,prjquota "$MOUNT_POINT"

grep -q "^$PROJECT_ID:" /etc/projects 2>/dev/null || echo "$PROJECT_ID:$SANDBOX_ROOT" >> /etc/projects
grep -q "^$PROJECT_NAME:" /etc/projid 2>/dev/null || echo "$PROJECT_NAME:$PROJECT_ID" >> /etc/projid

chattr -p "$PROJECT_ID" "$SANDBOX_ROOT"
xfs_quota -x -c "project -s $PROJECT_NAME" "$MOUNT_POINT"
xfs_quota -x -c "limit -p bhard=$LIMIT bsoft=$LIMIT $PROJECT_NAME" "$MOUNT_POINT"
xfs_quota -x -c "report -p" "$MOUNT_POINT"
