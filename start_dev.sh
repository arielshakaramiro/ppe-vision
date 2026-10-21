#!/usr/bin/env bash
# APD Vision - Script untuk menjalankan Backend & Frontend sekaligus
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

echo "================================================="
echo "   Memulai APD Vision (Backend + Frontend)       "
echo "================================================="

# Trap Ctrl+C (SIGINT) dan SIGTERM untuk mematikan kedua proses
cleanup() {
    echo ""
    echo "Menghentikan semua server..."
    kill $(jobs -p) 2>/dev/null || true
    wait $(jobs -p) 2>/dev/null || true
    echo "Semua server telah dihentikan."
}
trap cleanup SIGINT SIGTERM EXIT

# 1. Jalankan Backend
echo "[1/2] Menjalankan Backend FastAPI di port 8000..."
cd "$BACKEND_DIR"
if [ ! -d ".venv" ]; then
    echo "Virtualenv backend/.venv tidak ditemukan. Silakan buat dulu dengan: python3 -m venv backend/.venv"
    exit 1
fi
"$BACKEND_DIR/.venv/bin/uvicorn" app.main:app --reload --port 8000 &
BACKEND_PID=$!

# Tunggu backend siap sebentar
sleep 1

# 2. Jalankan Frontend
echo "[2/2] Menjalankan Frontend Vite di port 5173..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "================================================="
echo "  Sistem Aktif!"
echo "  - Frontend: http://localhost:5173"
echo "  - Backend:  http://localhost:8000"
echo "  - Swagger:  http://localhost:8000/docs"
echo "================================================="
echo "Tekan Ctrl+C untuk menghentikan kedua server."
echo ""

# Tunggu kedua background jobs
wait
