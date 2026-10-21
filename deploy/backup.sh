#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T app sh -lc 'cd /app/backend && python -m scripts.backup "$@"' _ "$@"
