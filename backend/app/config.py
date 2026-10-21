"""Konfigurasi aplikasi: config.yaml + variabel lingkungan."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent  # folder backend/


def _load_dotenv() -> None:
    """Baca .env di root repo (tanpa dependensi tambahan). Variabel yang sudah di-set tidak ditimpa."""
    env = BASE_DIR.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]  # KEY="nilai" -> nilai
            if value:
                os.environ.setdefault(key.strip(), value)


_load_dotenv()


@lru_cache
def load_config() -> dict:
    path = Path(os.getenv("PPE_CONFIG", BASE_DIR / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.getenv("CAMERA_SOURCE"):
        cfg["camera"]["source"] = os.getenv("CAMERA_SOURCE")
    if os.getenv("MODEL_DEVICE"):
        cfg["models"]["device"] = os.getenv("MODEL_DEVICE")
    if os.getenv("STREAM_TARGET_FPS"):
        cfg.setdefault("stream", {})["target_fps"] = float(os.getenv("STREAM_TARGET_FPS", "24"))
    if os.getenv("STREAM_OVERLAY_MAX_AGE_FRAMES"):
        cfg.setdefault("stream", {})["overlay_max_age_frames"] = int(os.getenv("STREAM_OVERLAY_MAX_AGE_FRAMES", "3"))
    if os.getenv("STREAM_DISPLAY_RAW_WHEN_STALE"):
        cfg.setdefault("stream", {})["display_raw_when_stale"] = os.getenv("STREAM_DISPLAY_RAW_WHEN_STALE", "1") != "0"
    return cfg


DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'ppe.db'}")
EVIDENCE_DIR = BASE_DIR / load_config()["evidence"]["dir"]
TIMEZONE = load_config().get("timezone", "Asia/Jakarta")
RUN_PIPELINE = os.getenv("RUN_PIPELINE", "0") == "1"
# 1 = proses antrean AnalysisJob rekaman dari proses API tanpa menjalankan worker produksi.
# Aman untuk localhost/dev ketika hanya butuh upload video -> analisis, tanpa backup/retensi otomatis.
ANALYSIS_JOB_RUNNER = os.getenv("ANALYSIS_JOB_RUNNER", "0") == "1"

# Bawaan full lokal: Ollama di komputer yang sama. "anthropic" hanya jika sengaja memakai API cloud.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_CONNECT_TIMEOUT_S = float(os.getenv("OLLAMA_CONNECT_TIMEOUT_S", "3"))
OLLAMA_READ_TIMEOUT_S = float(os.getenv("OLLAMA_READ_TIMEOUT_S", "90"))
OLLAMA_MAX_STEPS = int(os.getenv("OLLAMA_MAX_STEPS", "3"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "250"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "3072"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ALERT_THREAD_ID = os.getenv("TELEGRAM_ALERT_THREAD_ID", "")  # ID topic untuk alert foto CCTV (opsional, untuk forum topics)
TELEGRAM_REVIEWER_IDS = os.getenv("TELEGRAM_REVIEWER_IDS", "")  # user ID yang boleh review, pisahkan koma
TELEGRAM_POLLING = os.getenv("TELEGRAM_POLLING", "1") == "1"      # tombol review di Telegram
ALERT_COOLDOWN_S = float(load_config().get("alerts", {}).get("cooldown_s", 120))

# Akun admin awal: dibuat otomatis saat start jika belum ada user dengan nama ini.
# Setelah akun dibuat, sebaiknya kosongkan DASHBOARD_PASSWORD dari .env.
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
SESSION_HOURS = float(os.getenv("SESSION_HOURS", "12"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"   # isi 1 jika diakses lewat HTTPS
# Origin tambahan yang boleh mengirim request yang mengubah data (selain origin server sendiri)
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
# 1 = tampilkan dokumentasi API interaktif (/docs). Mati secara bawaan supaya skema API tidak terbuka.
API_DOCS = os.getenv("API_DOCS", "0") == "1"
# Reverse proxy tepercaya (Caddy/Nginx), dipisah koma, boleh CIDR. Hanya dari alamat ini X-Forwarded-For dipercaya.
TRUSTED_PROXIES = os.getenv("TRUSTED_PROXIES", "")
AGENT_RATE_LIMIT = int(os.getenv("AGENT_RATE_LIMIT", "20"))  # permintaan per menit per IP; 0 = tanpa batas
# Kunci Fernet untuk mengenkripsi URL kamera sebelum disimpan ke database. Wajib jika memakai sumber RTSP.
CAMERA_SECRET_KEY = os.getenv("CAMERA_SECRET_KEY", "")

# Mode produksi: 1 jika berjalan di lingkungan produksi (diisi Compose produksi).
PRODUCTION = os.getenv("PRODUCTION", "0") == "1" or os.getenv("PPE_ENV", "").lower() == "production" or os.getenv("ENVIRONMENT", "").lower() == "production"
# Kunci tanda tangan riwayat chat agent (wajib diisi minimal 16 karakter di mode produksi).
AGENT_HISTORY_KEY = os.getenv("AGENT_HISTORY_KEY", "")

# APD stabil (siap produksi) vs eksperimental (belum memenuhi gerbang akurasi gold set).
STABLE_ITEMS = ("helmet", "vest")
EXPERIMENTAL_ITEMS = ("gloves", "shoes")


def validate_production_config(cfg: dict | None = None) -> None:
    """Validasi fail-fast untuk mode produksi.

    Memastikan:
    - AGENT_HISTORY_KEY diisi minimal 16 karakter agar riwayat chat persisten dan aman.
    - COOKIE_SECURE aktif (1) untuk proteksi sesi via HTTPS.
    - API_DOCS nonaktif (0) agar skema API internal tidak terekspos.
    - Default zona di config.yaml tidak memuat APD eksperimental (gloves, shoes).

    Melempar ValueError jika ada aturan produksi yang tidak terpenuhi.
    """
    if cfg is None:
        cfg = load_config()
    errors: list[str] = []

    if not AGENT_HISTORY_KEY or len(AGENT_HISTORY_KEY.strip()) < 16:
        errors.append("AGENT_HISTORY_KEY wajib diisi minimal 16 karakter di mode produksi agar tanda tangan riwayat chat persisten.")

    if not COOKIE_SECURE:
        errors.append("COOKIE_SECURE wajib diaktifkan (1) di mode produksi.")

    if API_DOCS:
        errors.append("API_DOCS wajib dinonaktifkan (0) di mode produksi.")

    zones = cfg.get("zones", [])
    for z in zones:
        req = z.get("required", [])
        invalid = [item for item in req if item in EXPERIMENTAL_ITEMS]
        if invalid:
            errors.append(
                f"Zona config '{z.get('name')}' memuat APD eksperimental {invalid}. "
                "Hanya 'helmet' dan 'vest' yang diizinkan untuk mode produksi."
            )

    if errors:
        raise ValueError("Kegagalan konfigurasi mode produksi:\n- " + "\n- ".join(errors))
