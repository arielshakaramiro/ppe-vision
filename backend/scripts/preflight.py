"""Pemeriksaan kesiapan pra-deploy (preflight checklist) untuk operator APD Vision.

Menilai variabel lingkungan, model weights, database, konfigurasi zona, dan disk
tanpa pernah mencetak nilai rahasia (secret, password, atau key) ke terminal.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from app import config
from app.config import BASE_DIR, load_config


@dataclass
class CheckResult:
    item: str
    status: str  # "OK", "PERINGATAN", "GAGAL"
    detail: str
    is_blocker: bool = True


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    res = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            res[k] = v
        elif line and not line.startswith("#") and line.endswith("="):
            res[line[:-1].strip()] = ""
    return res


def run_checks(env_path: Path | None = None) -> list[CheckResult]:
    results: list[CheckResult] = []
    root_dir = BASE_DIR.parent
    if env_path is None:
        env_path = root_dir / ".env"

    env_map = _read_env_file(env_path) if env_path.is_file() else {}

    def get_var(k: str) -> str:
        # Utamakan yang ada di file .env jika dibaca, atau os.environ
        return env_map.get(k, os.environ.get(k, ""))

    # 1. File .env
    if env_path.is_file():
        results.append(CheckResult("File .env", "OK", f"Ditemukan di {env_path.name}", is_blocker=True))
    else:
        results.append(CheckResult("File .env", "GAGAL", "File .env tidak ditemukan di root proyek", is_blocker=True))

    # 2. DOMAIN
    domain = get_var("DOMAIN")
    if domain:
        results.append(CheckResult("DOMAIN", "OK", f"Terdefinisi ({domain})", is_blocker=True))
    else:
        results.append(CheckResult("DOMAIN", "GAGAL", "DOMAIN belum diisi di .env (wajib untuk Caddy HTTPS)", is_blocker=True))

    # 3. CAMERA_SECRET_KEY
    cam_key = get_var("CAMERA_SECRET_KEY")
    if len(cam_key) >= 32:
        results.append(CheckResult("CAMERA_SECRET_KEY", "OK", f"Terisi ({len(cam_key)} karakter, format Fernet)", is_blocker=True))
    elif cam_key:
        results.append(CheckResult("CAMERA_SECRET_KEY", "PERINGATAN", "Terisi tetapi panjang kurang dari standar 44 karakter Fernet", is_blocker=False))
    else:
        results.append(CheckResult("CAMERA_SECRET_KEY", "GAGAL", "Wajib diisi untuk enkripsi URL kamera sebelum deploy", is_blocker=True))

    # 4. AGENT_HISTORY_KEY
    hist_key = get_var("AGENT_HISTORY_KEY")
    if len(hist_key) >= 16:
        results.append(CheckResult("AGENT_HISTORY_KEY", "OK", f"Terisi ({len(hist_key)} karakter, memenuhi batas >= 16)", is_blocker=True))
    elif hist_key:
        results.append(CheckResult("AGENT_HISTORY_KEY", "GAGAL", f"Terlalu pendek ({len(hist_key)} karakter, wajib >= 16)", is_blocker=True))
    else:
        results.append(CheckResult("AGENT_HISTORY_KEY", "GAGAL", "Kosong. Wajib diisi agar tanda tangan riwayat chat persisten", is_blocker=True))

    # 5. COOKIE_SECURE
    cookie_sec = get_var("COOKIE_SECURE")
    if cookie_sec == "1":
        results.append(CheckResult("COOKIE_SECURE", "OK", "Aktif (1) untuk transmisi cookie via HTTPS", is_blocker=True))
    else:
        results.append(CheckResult("COOKIE_SECURE", "GAGAL", "Harus '1' di mode produksi HTTPS", is_blocker=True))

    # 6. API_DOCS
    api_docs = get_var("API_DOCS")
    if api_docs in ("0", ""):
        results.append(CheckResult("API_DOCS", "OK", "Nonaktif (skema API internal tertutup)", is_blocker=True))
    else:
        results.append(CheckResult("API_DOCS", "GAGAL", "Harus '0' di produksi agar /docs tidak terbuka", is_blocker=True))

    # 7. Model weights
    ppe_weights = BASE_DIR / "weights" / "best.pt"
    if ppe_weights.is_file() and ppe_weights.stat().st_size > 1_000_000:
        size_mb = ppe_weights.stat().st_size / (1024 * 1024)
        results.append(CheckResult("Model PPE (best.pt)", "OK", f"Tersedia ({size_mb:.1f} MB)", is_blocker=True))
    else:
        results.append(CheckResult("Model PPE (best.pt)", "GAGAL", "File backend/weights/best.pt tidak ditemukan atau rusak", is_blocker=True))

    pose_weights = BASE_DIR / "weights" / "yolo11n-pose.pt"
    if pose_weights.is_file() and pose_weights.stat().st_size > 1_000_000:
        size_mb = pose_weights.stat().st_size / (1024 * 1024)
        results.append(CheckResult("Model Pose (yolo11n-pose.pt)", "OK", f"Tersedia ({size_mb:.1f} MB)", is_blocker=True))
    else:
        results.append(CheckResult("Model Pose (yolo11n-pose.pt)", "GAGAL", "File backend/weights/yolo11n-pose.pt tidak ditemukan", is_blocker=True))

    # 8. Konfigurasi zona (config.yaml)
    try:
        cfg = load_config()
        exp_in_cfg = []
        for z in cfg.get("zones", []):
            exp = [item for item in z.get("required", []) if item in config.EXPERIMENTAL_ITEMS]
            if exp:
                exp_in_cfg.append(f"{z.get('name')}: {exp}")
        if exp_in_cfg:
            results.append(CheckResult("Config zona", "GAGAL", f"Memuat APD eksperimental: {', '.join(exp_in_cfg)}", is_blocker=True))
        else:
            results.append(CheckResult("Config zona", "OK", "Hanya menggunakan APD stabil (helmet, vest)", is_blocker=True))
    except Exception as e:
        results.append(CheckResult("Config zona", "GAGAL", f"Gagal membaca config.yaml: {e}", is_blocker=True))

    # 9. Database & Status Akun Admin
    db_file = BASE_DIR / "data" / "ppe.db"
    if db_file.is_file():
        try:
            with sqlite3.connect(db_file) as conn:
                integrity = [r[0] for r in conn.execute("PRAGMA integrity_check")]
                if integrity == ["ok"]:
                    results.append(CheckResult("Integritas SQLite", "OK", "PRAGMA integrity_check ok", is_blocker=True))
                else:
                    results.append(CheckResult("Integritas SQLite", "GAGAL", f"Integritas gagal: {integrity}", is_blocker=True))

                cur = conn.execute("SELECT count(*) FROM user WHERE role = 'admin' AND active = 1")
                admin_count = cur.fetchone()[0]
                if admin_count > 0:
                    results.append(CheckResult("Akun Admin", "OK", f"{admin_count} admin aktif terdaftar di database", is_blocker=True))
                    # Cek DASHBOARD_PASSWORD jika admin sudah ada
                    dash_pw = get_var("DASHBOARD_PASSWORD")
                    if dash_pw:
                        results.append(CheckResult(
                            "DASHBOARD_PASSWORD",
                            "PERINGATAN",
                            "Masih terisi di .env padahal akun admin sudah ada. Hapus dari .env sebelum produksi.",
                            is_blocker=False,
                        ))
                    else:
                        results.append(CheckResult("DASHBOARD_PASSWORD", "OK", "Sudah dikosongkan dari .env", is_blocker=False))
                else:
                    dash_u = get_var("DASHBOARD_USER")
                    dash_p = get_var("DASHBOARD_PASSWORD")
                    if dash_u and dash_p:
                        results.append(CheckResult("Akun Admin", "OK", f"Siap bootstrap untuk '{dash_u}' saat startup", is_blocker=True))
                    else:
                        results.append(CheckResult("Akun Admin", "GAGAL", "Belum ada akun di database dan kredensial bootstrap di .env belum diisi", is_blocker=True))
        except sqlite3.OperationalError:
            results.append(CheckResult("Database SQLite", "OK", "Tabel belum dibuat (akan diinisialisasi saat start)", is_blocker=False))
        except Exception as e:
            results.append(CheckResult("Database SQLite", "GAGAL", f"Gagal membaca database: {e}", is_blocker=True))
    else:
        results.append(CheckResult("Database SQLite", "OK", "Database baru (akan dibuat otomatis saat start)", is_blocker=False))

    # 10. Kapasitas ruang disk
    data_dir = BASE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(data_dir).free
    free_gb = free_bytes / (1024 ** 3)
    if free_gb >= 10.0:
        results.append(CheckResult("Ruang Disk", "OK", f"Tersedia {free_gb:.1f} GB (target >= 10 GB)", is_blocker=True))
    elif free_gb >= 5.0:
        results.append(CheckResult("Ruang Disk", "PERINGATAN", f"Tersedia {free_gb:.1f} GB (disarankan >= 10 GB untuk evidence)", is_blocker=False))
    else:
        results.append(CheckResult("Ruang Disk", "GAGAL", f"Sisa disk sangat kritis: {free_gb:.1f} GB", is_blocker=True))

    return results


def print_report(results: list[CheckResult]) -> bool:
    print("\n" + "=" * 70)
    print(" " * 18 + "APD VISION - PREFLIGHT CHECKLIST")
    print("=" * 70)
    print(f"{'Item':<26} | {'Status':<10} | Catatan")
    print("-" * 70)

    has_blocker_failure = False
    for r in results:
        status_label = f"[{r.status}]"
        print(f"{r.item:<26} | {status_label:<10} | {r.detail}")
        if r.status == "GAGAL" and r.is_blocker:
            has_blocker_failure = True

    print("=" * 70)
    if has_blocker_failure:
        print("KESIMPULAN: BELUM SIAP DEPLOY (terdapat persyaratan wajib yang gagal).")
        print("Perbaiki temuan berlabel [GAGAL] di atas sebelum melanjutkan.")
        return False
    else:
        print("KESIMPULAN: SIAP DEPLOY (seluruh persyaratan wajib terpenuhi).")
        return True


def main():
    parser = argparse.ArgumentParser(description="Pemeriksaan kesiapan deployment produksi APD Vision.")
    parser.add_argument("--env-file", type=Path, default=None, help="Path ke file .env yang akan diuji")
    args = parser.parse_args()

    results = run_checks(args.env_file)
    ready = print_report(results)
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
