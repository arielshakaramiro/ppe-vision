#!/usr/bin/env bash
# Menjalankan backend APD Vision untuk uji e2e Playwright.
# Database sementara + data contoh + tiga akun peran, menyajikan frontend hasil build.
# Full lokal: agent mode offline, pipeline mati. Dipanggil dari root repo (cwd: ..).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/backend"

# shellcheck disable=SC1091
source .venv/bin/activate

E2E_DIR="${TMPDIR:-/tmp}/apd-vision-e2e"
rm -rf "$E2E_DIR"
mkdir -p "$E2E_DIR"

export DATABASE_URL="sqlite:///$E2E_DIR/e2e.db"
export LLM_PROVIDER="offline"
export RUN_PIPELINE="0"
export ANALYSIS_JOB_RUNNER="0"
export TELEGRAM_POLLING="0"
export DASHBOARD_USER=""
export DASHBOARD_PASSWORD=""
export API_DOCS="0"

# Data contoh + akun uji per peran (password memenuhi password_problem).
python - <<'PY'
from app.db import init_db, engine, User
from app import auth
from sqlmodel import Session, select
from scripts import seed_demo

init_db()
seed_demo.main(days=7, per_day=12, seed=1, force=True)
with Session(engine) as db:
    for name, role in [("viewer_e2e", "viewer"), ("supervisor_e2e", "supervisor"), ("admin_e2e", "admin")]:
        exists = db.exec(select(User).where(User.username == name)).first()
        if not exists:
            auth.create_user(db, name, "rahasia-e2e-2026", role, actor="e2e")
    db.commit()
print("e2e: database siap")
PY

exec uvicorn app.main:app --host 127.0.0.1 --port 8099
