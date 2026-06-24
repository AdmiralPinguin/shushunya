#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

uv venv .venv
. .venv/bin/activate
uv pip install --upgrade pip
uv pip install aider-chat mini-swe-agent pytest

if [ ! -d agents/openhands/.git ]; then
  git clone --depth 1 https://github.com/OpenHands/openhands agents/openhands
else
  git -C agents/openhands pull --ff-only
fi

if [ ! -d agents/mini-swe-agent/.git ]; then
  git clone --depth 1 https://github.com/SWE-agent/mini-swe-agent agents/mini-swe-agent
else
  git -C agents/mini-swe-agent pull --ff-only
fi

echo "AgentArena candidates installed under $ROOT"

