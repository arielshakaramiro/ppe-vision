# APD Vision — Dokumentasi Teknis

Dokumen tunggal yang merangkum produk, arsitektur, pipeline computer vision, model data,
keamanan, serta konvensi kode APD Vision.

---

## 1. Ringkasan produk

| | |
|---|---|
| Nama | APD Vision |
| Satu kalimat | Sistem CCTV yang mendeteksi kepatuhan APD (helm, rompi, sarung tangan, sepatu), mencatat pelanggaran beserta foto evidence dan waktunya, lalu bisa ditanya dalam bahasa Indonesia. |
| Visi | Supervisor K3 tidak perlu menonton CCTV sepanjang hari. Sistem memberi tahu siapa yang melanggar, kapan, di mana, dan menunjukkan buktinya. |

### Masalah yang diselesaikan

- Pengawasan APD manual tidak mungkin berjalan 24 jam dan bergantung pada perhatian satu orang.
- Rekaman CCTV menumpuk tanpa bisa ditanya; pertanyaan seperti "berapa orang tidak pakai sarung tangan kemarin siang?" butuh berjam-jam memutar ulang video.
- Sistem deteksi APD umum hanya mengecek helm dan rompi, memakai satu aturan untuk semua area, dan sering salah karena satu frame yang meleset.

### Pengguna

| Pengguna | Kebutuhan utama |
|---|---|
| Supervisor / petugas K3 | Melihat pelanggaran yang sedang terjadi, menerima alert, me-review evidence. |
| Manajer K3 | Melihat tren kepatuhan dan jam rawan, bertanya ke agent, membuat laporan. |

### Prinsip desain

- **Jujur terhadap ketidakpastian.** Kalau bagian tubuh tidak terlihat, status menjadi *tidak pasti*, bukan pelanggaran.
- **Angka selalu dari data.** Jawaban agent berasal dari query database, bukan dari karangan model bahasa.
- **Aksesibilitas.** Status ditampilkan dengan warna **dan** teks/simbol, tidak hanya warna. Seluruh antarmuka berbahasa Indonesia, jam dalam WIB.

---

## 2. Fitur

### Input kamera
- Video file (demo), bisa diulang (`loop`).
- Proses video offline tanpa server (`scripts.process_video`, `--start` untuk waktu rekaman).
- Stream RTSP / HP dengan sambung ulang otomatis; bisa diganti dari dashboard (validasi anti-SSRF).
- Sumber video fleksibel: upload bertahap (potongan 10 MB, bisa dilanjutkan), file di folder server, mode **simulasi live** atau **analisis rekaman** dengan jam mulai.
- Multi-kamera: satu model bersama untuk semua kamera, diproses serentak dalam batch; pelacak, zona, APD wajib, dan status terpisah per kamera.

### Deteksi dan analisis
- Deteksi orang + 17 keypoint (YOLO11n-pose) dan 4 APD (model fine-tune).
- Asosiasi APD ke orang per wilayah tubuh (kepala, torso, pergelangan tangan, pergelangan kaki; IoA ≥ 0,40).
- Tracking ID pekerja (ByteTrack).
- Tiga status dengan hysteresis: `compliant` / `violation` / `uncertain`.
- Aturan APD per zona (poligon di database, seed dari konfigurasi).
- Blur wajah di evidence dan live view, dari keypoint kepala.

### Evidence dan data
- Event pelanggaran & tidak pasti dengan waktu, durasi, kamera, zona, APD kurang.
- Foto full frame beranotasi + crop orang, masing-masing dengan hash SHA-256.
- Klip video 5 detik (2 detik sebelum + 3 detik sesudah event).
- Versi model tercatat per event; precision lapangan per versi.
- Review supervisor (benar / false positive) + audit log, ikut memengaruhi compliance rate.
- Ekspor hasil review sebagai bahan retraining.
- Retensi otomatis evidence dan backup otomatis database + evidence.

### Alert
- Telegram: foto, kamera, zona, jam, APD kurang (cooldown per pekerja).
- Review langsung dari tombol di pesan Telegram (long polling, tanpa webhook).
- Alert kualitas kamera menurun (drift).

### Dashboard (React)

| Halaman | Isi |
|---|---|
| Live | Stream MJPEG, KPI, checklist per pekerja, pelanggaran hari ini, pemilih APD aktif per zona |
| Analitik | Compliance rate, kepatuhan per APD, per jam (jam rawan), per hari, per kamera/zona |
| Evidence | Galeri dengan filter periode/jenis/APD/review, modal detail + tombol review |
| Agent | Chat bahasa Indonesia dengan strip foto evidence |
| Intervensi | Catat intervensi, bandingkan sebelum/sesudah + uji statistik |
| Sistem | Kesehatan kamera (drift) dan precision model per versi |
| Bisnis | Kalkulator biaya/ROI dengan asumsi yang bisa diganti |
| Login | Satu form; pesan gagal tidak membocorkan username |
| Pengguna (admin) | Buat akun, ubah peran, nonaktifkan, reset password, hubungkan ID Telegram |
| Pengaturan (admin) | Editor poligon zona, APD wajib per zona, sensitivitas, shift, Telegram, retensi, kelola kamera + upload |
| Audit log | Tabel aksi per aktor dengan filter periode/aktor/aksi |

### Agent K3
- Tanya jawab bahasa Indonesia via tool calling: empat tool **baca-saja** (`count_violations`, `get_evidence`, `compliance_summary`, `intervention_effect`).
- Periode relatif (hari ini, kemarin, minggu ini, 7/30 hari).
- Jawaban berisi angka + foto evidence + jam.
- Full lokal: Ollama (bawaan) dengan cadangan router offline; API cloud opsional.
- RAG regulasi K3 (Permenaker No. 8/2010, UU No. 1/1970, PP No. 50/2012) via cosine similarity dan tool `search_regulation`.
- Laporan K3 siap cetak (HTML → PDF dari browser).
- Pembatas cakupan: tolak coding, topik lain, prompt injection, upaya membobol; penolakan dicatat.

### Akun dan peran
- Login per orang, sesi dengan logout, peran viewer / supervisor / admin (dicek di server).
- Pembatas login gagal berlapis (IP+username / IP / username), tersimpan di database.
- Tolak password umum, password memuat username, deret berurutan.
- Audit log mencatat siapa melakukan apa.

### Di luar scope (disengaja)
Face recognition / identitas pekerja, re-identification lintas kamera, deteksi jatuh / intrusi zona bahaya / alat berat, multi-tenant & SSO, aplikasi mobile, membedakan sepatu safety dari sepatu biasa.

---

## 3. Arsitektur sistem

```text
Video / RTSP ─▶ worker: InferenceScheduler ─▶ CameraSession ─▶ DbSink ─▶ SQLite + evidence/
                       │                                           └─▶ Telegram
                       └─▶ JPEG + status JSON ─▶ volume /tmp/ppe_live ─┐
                                                                      ▼
Browser ─▶ Caddy HTTPS ─▶ app: FastAPI ─▶ RemoteLiveState + queries ─▶ API, MJPEG, SPA
                                      └─▶ agent ─▶ Ollama/offline ─▶ tool baca-saja
```

Pada **Compose produksi**, API dan tugas latar belakang berjalan di dua proses/container berbeda.
Service `worker` menjalankan pipeline kamera, job analisis rekaman, polling Telegram, backup, dan
retensi. Service `app` dipaksa `RUN_PIPELINE=0` dan `ANALYSIS_JOB_RUNNER=0`; frame live serta status
dibaca dari volume bersama `/tmp/ppe_live`, event dibaca dari database bersama. Halaman Analitik,
Evidence, dan Agent tetap berfungsi walaupun pipeline mati.

Pada **development native**, `RUN_PIPELINE=1` dan/atau `ANALYSIS_JOB_RUNNER=1` boleh menjalankan
pipeline/job di proses API agar developer tidak perlu menyalakan worker produksi.

### Stack teknologi

| Lapisan | Teknologi |
|---|---|
| Deteksi orang + keypoint + tracking | Ultralytics YOLO11n-pose, ByteTrack |
| Deteksi APD | YOLO hasil fine-tune (`weights/best.pt` via `models.ppe`) |
| Pemrosesan gambar | OpenCV (headless), NumPy |
| Backend | FastAPI, Uvicorn |
| ORM / DB | SQLModel di atas SQLite |
| Agent | Anthropic SDK / HTTP Ollama / router kata kunci (tool calling, tanpa SQL bebas) |
| Alert | Telegram Bot API via httpx (thread daemon) |
| Frontend | React 18, Vite 5, TypeScript (strict), Recharts; SPA navigasi hash |
| Training | Ultralytics di Google Colab |
| Kemasan | Docker multi-stage, Docker Compose (CPU saja) |

### Struktur folder

```
docs/                     dokumentasi produk, arsitektur, desain, API, database, keamanan
backend/
  config.yaml             kamera, model, class_map, asosiasi, hysteresis, zona awal, evidence
  pipeline/               logika computer vision (tanpa database)
  app/                    FastAPI, query, auth, security, agent, guard, sink, dll.
  scripts/                seed_demo, process_video, export_reviews, users, retention, backup, worker
  tests/                  pytest
  weights/                best.pt + yolo11n-pose.pt (tidak di-commit)
  data/                   ppe.db + evidence/ + videos/ (tidak di-commit, dibuat otomatis)
deploy/                   Caddyfile + docker-compose.prod.yml (app + worker + Caddy)
frontend/
  src/api.ts              semua fetch + tipe response
  src/features/           halaman per fitur (live, evidence, analytics, agent, admin, dll.)
  src/ui/ + src/components/  komponen tampilan bersama
training/                 notebook Colab + script dataset/evaluasi
```

---

## 4. Pipeline computer vision

Pipeline **tidak tahu** soal database. Ia hanya memanggil method `EventSink` (`on_open`, `on_close`,
`on_track_lost`, opsional `on_clip` / `on_quality`); implementasi ada di `app/sink.py`. Aturan zona
dinamis diberikan lewat callable `zone_provider` dari `app/main.py`, sehingga `pipeline/` tidak pernah
meng-import `app/` dan bisa diuji dengan detektor palsu.

| Modul | Tanggung jawab |
|---|---|
| `types.py` | Tipe data (`Person`, `PPEDet`, `Evidence`), geometri, indeks keypoint COCO |
| `detector.py` | Membungkus dua model YOLO; satu-satunya tempat `ultralytics` di-import |
| `association.py` | Menghitung wilayah tubuh dari keypoint, mencocokkan APD ke wilayah |
| `state.py` | Hysteresis per item, status stabil per orang, buka/tutup event |
| `zones.py` | Point-in-polygon pada titik kaki ternormalisasi |
| `evidence.py` | Blur kepala, crop, anotasi, simpan JPG, hitung SHA-256 |
| `draw.py` | Warna status, kotak + checklist, garis zona, blur |
| `clips.py` | Buffer JPEG + penulis klip WebM (VP8) di thread terpisah |
| `quality.py` | Kecerahan, ketajaman, confidence → ringkasan per menit |
| `runner.py` | Loop video, menyatukan semua modul, memanggil `EventSink`, mengisi `LiveState` |

### Siklus status dan event

```
skor item: decay 0,90 per frame, +conf jika dipakai, -1,0 jika terlihat tanpa APD, dibatasi [-3, 3]
  skor ≥ +1,0                         → present
  skor ≤ -1,5 dan evaluasi ≥ 5        → missing
  tanpa bukti ≥ 45 frame              → unknown
  di antaranya                        → status lama dipertahankan

status orang (hanya APD wajib di zonanya):
  ada missing  → violation
  ada unknown  → uncertain
  selain itu   → compliant

status stabil: violation/compliant butuh 8 frame berturut-turut; uncertain butuh 30
event: dibuka saat status stabil menjadi violation/uncertain, ditutup saat berubah
       atau saat track hilang > 30 frame
```

Semua angka berasal dari `config.yaml` dan bisa disetel per lokasi. Default zona demo/top-down hanya
mewajibkan `helmet` dan `vest`; `gloves`/`shoes` tetap terdeteksi tetapi `not_required` sampai operator
mengaktifkannya per zona.

### Multi-kamera

Satu model untuk semua kamera, diproses serentak (batch). Pengambilan gambar tidak pernah berhenti;
yang dibagi hanya waktu model.

```
CAM-01 ─ CameraReader (thread) ─┐  frame terbaru saja
CAM-02 ─ CameraReader (thread) ─┼─> InferenceScheduler (1 thread)
CAM-03 ─ CameraReader (thread) ─┘     pose.predict([f1,f2,f3])  ← satu panggilan untuk semua
                                      ppe.predict([f1,f2,f3])
                     ┌──────────────────────┼──────────────────────┐
              CameraSession CAM-01   CameraSession CAM-02   CameraSession CAM-03
              (tracker, zona, hysteresis, evidence, klip, statistik — terpisah)
                                            │
                                        EventSink → database, alert
```

Tracker wajib satu per kamera (batch campuran bisa menukar ID antarkamera). Kamera yang putus hanya
keluar dari batch sampai tersambung lagi; kamera lain tetap jalan. Kapasitas diukur dengan
`scripts/benchmark_cameras.py` di perangkat target.

### Sumber video: simulasi vs analisis

```
Simulasi live   : video ─(putar ulang, kecepatan asli)─> batch bersama kamera lain ─> event "simulasi"
Analisis rekaman: video ─> AnalysisJob (latar belakang, 5–10 pemeriksaan/dtk video,
                  bisa dijeda & dilanjutkan setelah restart) ─> event "rekaman"
                  waktu event = jam mulai rekaman + posisi frame
```

`source_kind` (`live` | `rekaman` | `simulasi`) disimpan di setiap event. Analitik, agent, dan laporan
K3 secara bawaan menghitung `live` + `rekaman`; simulasi hanya jika dipilih dan tidak pernah masuk
laporan K3.

### Agent K3 (alur)

1. `POST /api/agent/chat` menerima pesan + riwayat; rate limit per username + IP; `clean_history`
   menyisakan maks. 8 pesan yang lolos filter.
2. `guard.check_input` memeriksa pertanyaan. Jika ditolak, jawaban baku dikembalikan tanpa memanggil
   model, dan penolakan dicatat di `AuditLog`.
3. Model memanggil tool baca-saja (maks. 6 putaran); argumen divalidasi ulang (`_clean_args`) lalu
   memanggil `queries.py`.
4. `guard.check_output` memeriksa jawaban; jawaban berisi kode atau bocoran instruksi diganti penolakan.
5. Evidence dikembalikan terpisah dari teks, sehingga frontend menampilkan foto asli, bukan URL
   karangan model.

---

## 5. Model data

- **Database:** SQLite (`backend/data/ppe.db`), ORM SQLModel. Tabel dibuat saat start (`init_db`).
- **Migrasi ringan:** kolom baru ditambahkan otomatis ke tabel lama saat start. Ganti nama / ubah tipe
  / hapus kolom tidak ditangani otomatis.
- **Waktu:** semua kolom waktu `NaiveDatetime` berisi WIB tanpa zona, dibuat lewat `queries.now_local()`
  atau `Runner._now()`.

| Tabel | Isi |
|---|---|
| `Violation` | Satu event pelanggaran/tidak pasti: kamera, zona, track/worker id, waktu mulai/selesai, durasi, APD kurang & tidak pasti, confidence, path + hash evidence (foto & klip), review, versi model, `source_kind` |
| `TrackRecord` | Ringkasan satu pekerja per sesi: status terburuk, APD yang pernah kurang. Dasar compliance rate |
| `AuditLog` | Jejak aksi manusia (hanya ditambah): review, akun, zona, kamera/video/job, intervensi, pengaturan, penolakan agent, retensi |
| `User` | Akun dashboard: username, hash password scrypt, peran, status aktif, telegram_id |
| `SessionToken` | Sesi login: hash token, kedaluwarsa |
| `LoginFailure` | Percobaan login gagal berlapis untuk throttle |
| `Intervention` | Tindakan K3 untuk dibandingkan sebelum vs sesudah |
| `ZoneRule` | Aturan zona dari dashboard: poligon ternormalisasi + APD wajib per `(name, camera_id)` |
| `Setting` | Pengaturan deteksi dashboard (sensitivitas, shift, alert, retensi) |
| `QualityStat` | Kualitas input per kamera per menit (kecerahan, ketajaman, confidence) |
| `Camera` | Kamera multi-sumber: `source_kind`, URL terenkripsi (Fernet), video_id, mode, sample_rate |
| `VideoFile` / `UploadSession` / `AnalysisJob` | Registri video, upload bertahap, dan job analisis rekaman |

**Relasi** bersifat logis (tanpa foreign key): `Violation` ↔ `TrackRecord` lewat
`(session, camera_id, track_id)`; `AuditLog.violation_id` → `Violation.id`. "Orang unik" = kombinasi
`(session, camera_id, track_id)` berbeda.

**File evidence** di `backend/data/evidence/YYYY-MM-DD/`; database hanya menyimpan path relatif. File
tidak boleh diubah setelah ditulis (hash akan tidak cocok). Retensi menghapus file lama dan mengosongkan
path, tetapi baris tetap ada; event `pending` tidak disentuh.

---

## 6. Keamanan

Pertahanan berlapis untuk agent:

| Lapisan | Mekanisme |
|---|---|
| Filter input | `guard.check_input` sebelum model dipanggil: tolak prompt injection, permintaan secret/ubah data, permintaan coding, dan topik di luar K3/APD. Teks dinormalisasi dulu (aksen, karakter tak terlihat, `h4ck`). Pertanyaan yang ditolak **tidak pernah sampai ke model**. |
| Tool baca-saja | Model hanya bisa memanggil empat tool baca-saja; tidak ada akses SQL, file, shell, atau operasi tulis. |
| Validasi argumen | `_clean_args`: `item` harus salah satu dari 4 APD, `limit` dipaksa 1–12, `camera_id`/`zone` dipotong 64 karakter. |
| Riwayat | `clean_history`: pesan assistant hanya dipakai bila membawa tanda HMAC sah dari server (`sign_answer`), sehingga jawaban karangan berisi instruksi baru tidak sampai ke model. |
| Filter output | `guard.check_output`: jawaban berisi kode atau bocoran instruksi diganti penolakan. Foto diambil dari hasil tool, bukan URL yang ditulis model. |

Keamanan lain: akun + cookie sesi + peran (dicek di server), hash password scrypt (stdlib), pembatas
login gagal berlapis, redaksi error (pesan 502 umum; detail hanya di log server), validasi URL kamera
anti-SSRF, pemeriksaan Origin untuk request pengubah data, enkripsi URL kamera (Fernet). Variabel
sensitif (`DASHBOARD_PASSWORD`, API key, token Telegram, URL RTSP, `DATABASE_URL`) hanya lewat `.env`
yang tidak di-commit.

Database dan evidence berisi data pribadi (relevan UU PDP); wajah di-blur pada evidence dan live view.

---

## 7. Konvensi kode

### Stack & prinsip
- Backend/pipeline: Python 3.11, FastAPI, SQLModel, OpenCV, NumPy, Ultralytics.
- Frontend: TypeScript (strict), React 18 function component, Vite, CSS biasa, Recharts.
- Test: pytest (backend); type check frontend lewat `npm run build`.
- Kode yang mudah dibaca lebih baik daripada kode yang pintar. Satu fungsi, satu tugas. Nilai yang perlu
  disetel masuk `config.yaml`, bukan konstanta tersebar.

### Bahasa
| Hal | Bahasa |
|---|---|
| Nama variabel, fungsi, kelas, file | Inggris (`track_id`, `associate`, `StateManager`) |
| Docstring, komentar | Indonesia |
| Teks UI, pesan error, jawaban agent | Indonesia |
| Kunci JSON response API | Indonesia (`waktu`, `apd_kurang`) |
| Kode APD internal | Inggris: `helmet`, `vest`, `gloves`, `shoes` |

### Aturan lapisan
- `pipeline/` tidak boleh meng-import `app/`.
- `app/main.py` berisi route tipis: validasi → panggil `queries`/`agent` → kembalikan hasil.
- Setiap endpoint pengubah data memakai `Depends(supervisor)` / `Depends(admin)` dan mencatat
  `auth.audit(...)`.
- Akses database hanya lewat `Session(engine)` dan `select(...)`. Tidak ada SQL mentah.
- Frontend menyembunyikan aksi dengan `can(useMe(), "supervisor")`; server tetap sumber kebenaran.

### Komentar
Tulis **kenapa**, bukan **apa**. Angka ambang yang tidak jelas asalnya diberi komentar atau dipindah ke
`config.yaml`.

### Checklist sebelum menyelesaikan tugas
- `cd backend && python -m pytest -q` lolos.
- `cd frontend && npm run build` lolos (jika frontend diubah).
- Halaman yang diubah dibuka di lebar desktop dan ≤ 900px.
- Tidak ada `print`/`console.log` debug, kode mati, atau import tak terpakai.
- Dokumen di `docs/` yang terdampak sudah diperbarui.

---

## 8. Deployment

| Lingkungan | Cara | Catatan |
|---|---|---|
| Development Mac M1 | `uvicorn` + `npm run dev` | `device: mps` di config |
| Demo Docker | `docker compose up --build` | CPU, user non-root, satu worker, volume untuk data/weights/config |
| Produksi | `docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d --build` | Caddy HTTPS di depan, port 8000 tertutup, `COOKIE_SECURE=1`, healthcheck `/api/healthz` |

### Layanan eksternal

| Layanan | Untuk | Wajib? | Saat gagal |
|---|---|---|---|
| Anthropic API / Ollama | Model agent | Tidak | Endpoint agent 502; router offline tetap menjawab hitungan deterministik |
| Telegram Bot API | Alert & tombol review | Tidak | Dicatat ke log; pipeline tetap jalan, polling retry hingga 60 dtk |
| Unduh bobot pose | Saat pertama pipeline jalan | Ya, sekali | Pipeline gagal start, error di `/api/health` |

### Catatan skalabilitas
- Jalankan **satu** instance service `worker` (satu model + satu scheduler untuk semua kamera).
- SQLite cukup untuk satu penulis; multi-worker → PostgreSQL via `DATABASE_URL` (perlu migrasi).
- Evidence di disk lokal; deploy jangka panjang → object storage + retensi.
- Inference bisa diekspor ke CoreML/ONNX untuk FPS lebih tinggi; deteksi APD bisa tiap N frame karena
  hysteresis sudah meredam.
- Monitoring: `/api/healthz` (publik), `/api/health` + halaman Sistem (butuh login), healthcheck
  Compose, heartbeat worker, kesehatan kamera per menit.
