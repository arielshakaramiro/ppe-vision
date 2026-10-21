<div align="center">
  <img src="assets/apd-vision-title.svg" alt="APD Vision — pemantauan keselamatan kerja" width="960">

  <p><strong>Pemantauan kepatuhan APD dari kamera dan rekaman, dengan bukti yang dapat ditinjau.</strong></p>
</div>

## Video demo

> **Video demo akan ditambahkan di sini.** Setelah video diunggah ke GitHub, tempel URL lampirannya di bagian ini. README tidak menyertakan video contoh atau tautan sementara yang tidak dapat dibuka.

APD Vision memantau penggunaan **helm, rompi, sarung tangan, dan sepatu** dari video. Sistem menghubungkan hasil deteksi ke pekerja yang terlacak, menerapkan aturan APD per zona, lalu menyimpan event, foto evidence, dan klip pendek. Dashboard menyediakan Live, Analitik, Evidence, analisis rekaman, dan laporan; Agent K3 menjawab pertanyaan berbahasa Indonesia menggunakan data yang tersedia.

> **Batas model:** Helm dan rompi menjadi syarat pelanggaran bawaan. Deteksi sarung tangan dan sepatu masih lemah pada objek kecil, jauh, atau tertutup; keduanya tidak dijadikan syarat pelanggaran keras secara bawaan. Kelas `shoes` berarti sepatu tertutup, **bukan** sertifikasi sepatu safety. Hasil sistem perlu ditinjau petugas, bukan diperlakukan sebagai keputusan keselamatan otomatis.

## Kemampuan utama

| Area | Kemampuan |
|---|---|
| Pemantauan | Stream RTSP/HTTP, video simulasi, beberapa kamera dengan scheduler inferensi bersama, sambung ulang saat sumber terputus. |
| Rekaman | Upload bertahap, video di server, analisis dengan waktu mulai rekaman, job yang dapat dijeda/dilanjutkan/dibatalkan. Data simulasi dibedakan dari data live dan rekaman. |
| Deteksi | YOLO pose + detektor APD, asosiasi APD ke bagian tubuh, tracking ByteTrack, aturan per zona, serta status **patuh / pelanggaran / tidak pasti** dengan hysteresis. |
| Bukti | Foto beranotasi, crop pekerja, klip singkat, blur wajah, hash SHA-256, review supervisor, dan jejak audit. |
| Analitik & tindakan | Kepatuhan per APD/kamera/zona, menit paparan, intervensi sebelum–sesudah, kualitas kamera, dan laporan K3 siap cetak. |
| Agent K3 | Tanya jawab bahasa Indonesia; angka diambil melalui tool baca-saja. Ollama lokal dan router offline tersedia, penyedia cloud bersifat opsional. |
| Akses & alert | Peran `viewer`, `supervisor`, `admin`; sesi pengguna, audit aktivitas, dan notifikasi Telegram opsional. |

### Alur deteksi

```text
Kamera / video → deteksi orang + keypoint → deteksi APD → asosiasi ke pekerja
               → aturan APD per zona → status stabil → event + evidence → dashboard / alert
```

APD hanya dikaitkan dengan pekerja bila posisinya sesuai wilayah tubuh. Bukti yang belum cukup menghasilkan status **tidak pasti**, bukan otomatis pelanggaran. Review *false positive* memengaruhi statistik kepatuhan. Identitas pekerja adalah ID tracking per sesi kamera, bukan pengenalan wajah.

## Arsitektur singkat

- **Frontend:** React 18, TypeScript, Vite; dashboard dan API menggunakan origin yang sama saat produksi.
- **Backend:** FastAPI, SQLModel, SQLite; melayani API, stream MJPEG, autentikasi, evidence, dan frontend hasil build.
- **Pipeline:** YOLO11n-pose, model APD fine-tune, OpenCV, ByteTrack. Worker produksi memisahkan inferensi kamera, job rekaman, notifikasi, backup, dan retensi dari proses API.
- **Penyimpanan:** database dan evidence di `backend/data/`, bobot model di `backend/weights/`; data runtime dan secret tidak disimpan dalam Git.
- **Produksi:** Docker Compose dengan Caddy untuk HTTPS; pipeline berbagi status dan frame live dengan API melalui volume.

Rincian modul, alur data, tabel, keamanan, serta konvensi kode ada di **[dokumentasi teknis](TECHNICAL_OVERVIEW.md)**.

```text
backend/     API, pipeline computer vision, worker, skrip, dan pengujian
frontend/    dashboard React dan pengujian Playwright
training/    notebook dan utilitas persiapan/evaluasi model
deploy/      Compose produksi, Caddy, backup, dan pemeriksaan deploy
assets/      aset README
```

## Menjalankan secara lokal

### Prasyarat

- Python 3.11, Node.js 20+, dan npm.
- Bobot APD `backend/weights/best.pt` serta bobot pose `backend/weights/yolo11n-pose.pt` untuk pipeline kamera. Bobot tidak ada di Git; bobot pose dapat diunduh oleh Ultralytics saat pertama digunakan.
- Ollama opsional untuk Agent K3; tanpa Ollama, pertanyaan yang didukung router offline tetap dapat dijawab.

### Backend

Dari root repo:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
# Salin konfigurasi contoh hanya jika belum ada .env; jangan timpa secret lokal.
if [ ! -f ../.env ]; then cp ../.env.example ../.env; fi
# Sesuaikan ../.env sebelum membuat akun.
python -m scripts.users add admin-lokal --role admin
uvicorn app.main:app --reload --port 8000
```

Sunting `.env` sesuai lingkungan sebelum menjalankan server. Untuk memakai model lokal Agent K3, jalankan Ollama dan unduh model yang dikonfigurasi (misalnya `ollama pull qwen2.5:7b`). Di macOS Apple Silicon, backend native dapat memakai `mps`; Docker di Mac memakai CPU.

> **Jangan menjalankan `scripts.seed_demo --force` pada database berisi data nyata.** Untuk data contoh, gunakan database terpisah lalu jalankan `python -m scripts.seed_demo` tanpa `--force`.

### Frontend

Di terminal lain, dari root repo:

```bash
cd frontend
npm ci
npm run dev
```

Buka **http://localhost:5173**. Vite meneruskan `/api` dan `/evidence` ke backend di port 8000. Untuk build produksi: `npm run build`; FastAPI menyajikan hasil `frontend/dist` dari http://localhost:8000.

### Mengaktifkan pipeline kamera

1. Letakkan model APD aktif di `backend/weights/best.pt` dan periksa `models.ppe` serta `class_map` di `backend/config.yaml`.
2. Daftarkan kamera/video lewat dashboard, atau arahkan sumber di konfigurasi untuk penggunaan lokal.
3. Jalankan backend dengan `RUN_PIPELINE=1` pada lingkungan development. Alternatif untuk memproses satu rekaman tanpa server: dari folder `backend/`, jalankan `python -m scripts.process_video --source data/videos/rekaman.mp4 --start 2026-10-20T08:00` (sesuaikan path dan waktu mulai rekaman).

### Docker demo dan produksi

```bash
# Demo lokal (CPU); siapkan .env dari .env.example bila belum ada,
# lalu atur akun awal dan konfigurasi yang diperlukan.
docker compose up --build
```

Untuk produksi, isi `DOMAIN`, kredensial awal, dan secret di `.env`, lalu jalankan:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d --build
```

Demo tersedia di **http://localhost:8000**. Konfigurasi produksi menggunakan Caddy/HTTPS; sebelum membuka akses publik, atur domain, secret, backup, dan kebijakan retensi sesuai kebutuhan. Akun awal yang dibuat melalui `DASHBOARD_PASSWORD` tidak memerlukan password tersebut tetap berada di `.env` setelah bootstrap.

## Pengujian

```bash
cd backend && python -m pytest -q
cd ../frontend && npm ci && npm run build
npx playwright install chromium  # sekali saja pada mesin baru
npx playwright test --project=chromium
```

Playwright menjalankan server pengujian dan database sementara; pengujian tidak memakai database operasional. Cakupannya meliputi login/peran, Live, Evidence/review, analitik, rekaman/upload/job, Agent, pengaturan, halaman admin, dan tampilan seluler.

## Training dan evaluasi

Notebook dan script tersedia di `training/`. Model hasil training adalah **candidate** sampai lulus evaluasi pada gold test set yang dipisahkan dari data latih. `training/evaluate_model.py` melaporkan mAP per kelas; `training/promote_model.py` memeriksa gate sebelum mengganti `backend/weights/best.pt`. Jangan melatih model menggunakan gold test set.

- [SH17](https://github.com/ahmadmughees/SH17dataset) — CC BY-NC-SA (non-komersial).
- [PPE Dataset for Workplace](https://universe.roboflow.com/siabar/ppe-dataset-for-workplace) — CC BY 4.0.
- Data lokal hasil anotasi sendiri.

**Keterbatasan evaluasi:** Pada gold set reviewed 340 frame, model aktif memperoleh mAP50-95 rata-rata **0,277** (helmet 0,604; vest 0,432; gloves 0,045; shoes 0,026). Gate promosi empat kelas belum tercapai. Angka ini tidak boleh dipakai sebagai klaim kesiapan deteksi empat APD penuh di lapangan; performa harus diuji ulang pada kamera dan kondisi lokasi target. Benchmark lokal MacBook M1 (`mps`, video 1080p) menghasilkan sekitar 11,9 pemeriksaan/dtk per kamera untuk 1 kamera dan 5,7 untuk 2 kamera; 3–4 kamera perlu perangkat lebih kuat atau penyesuaian konfigurasi.

## Lisensi dan penggunaan

APD Vision adalah portofolio pribadi, bukan proyek open source. Kode, dokumentasi, dan materi asli proyek dilindungi; penyalinan, modifikasi, penggunaan ulang, atau distribusi memerlukan izin tertulis. Lihat [LICENSE](LICENSE). Lisensi dependency, model, dataset, font, dan aset pihak ketiga berlaku terpisah.
