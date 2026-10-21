#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [ -f .venv/bin/python ]; then
    .venv/bin/python -m scripts.preflight "$@"
else
    python3 -m scripts.preflight "$@"
fi
