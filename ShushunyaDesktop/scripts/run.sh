#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Runtime is missing. Run ./scripts/install.sh first." >&2
  exit 2
fi

export QSG_RHI_BACKEND="${QSG_RHI_BACKEND:-opengl}"
export SHUSHUNYA_DIAGNOSTICS_DIR="${SHUSHUNYA_DIAGNOSTICS_DIR:-$ROOT/runtime/live}"

# A shell opened outside the compositor may lose the Wayland variables even
# though the graphical session is alive. Recover the current user's runtime
# directory and select the real COSMIC socket instead of assuming wayland-0.
if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
  runtime_candidate="/run/user/$(id -u)"
  if [[ -d "$runtime_candidate" && -x "$runtime_candidate" ]]; then
    export XDG_RUNTIME_DIR="$runtime_candidate"
  fi
fi

wayland_socket=""
if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "$XDG_RUNTIME_DIR" ]]; then
  if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
    if [[ "$WAYLAND_DISPLAY" == /* ]]; then
      socket_candidate="$WAYLAND_DISPLAY"
    else
      socket_candidate="$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY"
    fi
    if [[ -S "$socket_candidate" ]]; then
      wayland_socket="$socket_candidate"
    fi
  fi

  if [[ -z "$wayland_socket" ]]; then
    shopt -s nullglob
    for socket_candidate in "$XDG_RUNTIME_DIR"/wayland-*; do
      if [[ -S "$socket_candidate" ]]; then
        wayland_socket="$socket_candidate"
        export WAYLAND_DISPLAY="$(basename "$socket_candidate")"
        break
      fi
    done
    shopt -u nullglob
  fi
fi

if [[ -z "${QT_QPA_PLATFORM:-}" ]]; then
  if [[ -n "$wayland_socket" ]]; then
    export QT_QPA_PLATFORM=wayland
  elif [[ -n "${DISPLAY:-}" ]]; then
    export QT_QPA_PLATFORM=xcb
  else
    echo "No graphical display socket is available for user $(id -un)." >&2
    echo "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-<unset>}" >&2
    echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-<unset>}" >&2
    echo "DISPLAY=${DISPLAY:-<unset>}" >&2
    echo "Run this command from a terminal inside the COSMIC desktop session." >&2
    exit 3
  fi
fi

exec .venv/bin/python -m shushunya_desktop.main "$@"
