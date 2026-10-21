"""Tabel database. Waktu disimpan sebagai waktu lokal (WIB) tanpa zona waktu."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime

from sqlalchemy import Float, Integer, func, inspect, text, update
from sqlmodel import Field, SQLModel, create_engine, Session, col, select
from sqlmodel.sql import sqltypes as _sqltypes

if hasattr(_sqltypes, "UTCDateTime"):
    # SQLModel versi baru memetakan datetime biasa ke UTCDateTime (wajib tzinfo).
    # Aplikasi ini sengaja menyimpan waktu lokal tanpa zona, jadi tandai eksplisit sebagai naive.
    from pydantic import NaiveDatetime
else:
    # Kompatibel dengan SQLModel lama yang belum mengenal pydantic.NaiveDatetime sebagai tipe kolom.
    NaiveDatetime = datetime

from .config import BASE_DIR, DATABASE_URL


class Violation(SQLModel, table=True):
    """Satu event: pelanggaran (violation) atau status tidak pasti (uncertain)."""
    id: int | None = Field(default=None, primary_key=True)
    session: str = Field(index=True)          # satu sesi runner; track_id hanya unik per sesi
    camera_id: str = Field(index=True)
    source_kind: str = Field(default="live", index=True) # live | rekaman | simulasi
    zone: str = ""
    track_id: int
    worker_id: int = Field(default=0, index=True)
    person_key: str = Field(default="", index=True)
    kind: str = Field(default="violation", index=True)  # violation | uncertain
    started_at: NaiveDatetime = Field(index=True)
    ended_at: NaiveDatetime | None = None
    duration_s: float | None = None
    missing_items: str = ""                   # "gloves,shoes"
    uncertain_items: str = ""
    confidence: float = 0.0
    evidence_full: str = ""
    evidence_crop: str = ""
    evidence_clean: str = ""
    evidence_sha256: str = ""
    detections_json: str = ""
    review: str = Field(default="pending", index=True)  # pending | confirmed | false_positive
    reviewed_at: NaiveDatetime | None = None
    corrected_items: str = ""                  # APD yang dibenarkan reviewer saat deteksi keliru, csv kode item
    model_version: str = Field(default="", index=True)  # versi detektor APD yang menghasilkan event ini
    evidence_clip: str = ""                   # path relatif klip video pendek (webm), kosong jika tidak ada
    evidence_clip_sha256: str = ""


class TrackRecord(SQLModel, table=True):
    """Ringkasan setiap pekerja yang terpantau (dasar perhitungan compliance rate)."""
    id: int | None = Field(default=None, primary_key=True)
    session: str = Field(index=True)
    camera_id: str = Field(index=True)
    source_kind: str = Field(default="live", index=True) # live | rekaman | simulasi
    zone: str = ""
    track_id: int
    worker_id: int = Field(default=0, index=True)
    person_key: str = Field(default="", index=True)
    first_seen: NaiveDatetime = Field(index=True)
    last_seen: NaiveDatetime
    final_status: str                         # compliant | uncertain | violation (status terburuk)
    missing_items: str = ""


class AuditLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    at: NaiveDatetime
    action: str                               # review_confirmed | review_false_positive | login | ...
    violation_id: int | None = None
    detail: str = ""
    actor: str = Field(default="", index=True)  # username, "telegram:<id>", "cli", "sistem", atau "lokal"


class User(SQLModel, table=True):
    """Akun dashboard. Password disimpan sebagai hash scrypt, tidak pernah teks asli."""
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: str = "viewer"                      # viewer | supervisor | admin
    active: bool = True
    created_at: NaiveDatetime
    last_login: NaiveDatetime | None = None
    telegram_id: str = Field(default="", index=True)  # user ID Telegram untuk tombol review; kosong = tidak terhubung
    totp_secret: str = Field(default="")              # rahasia base32 untuk 2FA/TOTP


class Conversation(SQLModel, table=True):
    """Sesi percakapan agent mandiri milik pengguna."""
    id: str = Field(primary_key=True)                # UUID hex string
    owner: str = Field(index=True)                   # username pemilik
    title: str = Field(default="Percakapan baru")    # judul percakapan
    created: NaiveDatetime = Field(index=True)       # waktu dibuat (WIB)
    updated: NaiveDatetime = Field(index=True)       # waktu terakhir diperbarui (WIB)
    pinned: bool = Field(default=False, index=True)  # disematkan di atas sidebar


class ChatHistory(SQLModel, table=True):
    """Riwayat chat agent per pengguna. Mendukung mode datar legacy maupun percakapan ber-ID."""
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True)
    conversation_id: str | None = Field(default=None, index=True)
    role: str
    content: str
    tanda: str = ""
    detail_json: str = ""
    tool_calls_json: str = ""
    evidence_json: str = ""
    at: NaiveDatetime = Field(index=True)


class LoginFailure(SQLModel, table=True):
    """Satu percobaan login gagal. Disimpan di database supaya batas percobaan tetap berlaku setelah restart
    dan berlaku sama untuk semua proses server. Dibersihkan otomatis setelah jendela waktunya lewat."""
    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(index=True)       # "pair:<ip>|<user>", "ip:<ip>", atau "user:<user>"
    at: NaiveDatetime = Field(index=True)


class SessionToken(SQLModel, table=True):
    """Sesi login. Yang disimpan hanya hash token, jadi bocornya database tidak membocorkan sesi aktif."""
    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    username: str = Field(index=True)
    created_at: NaiveDatetime
    expires_at: NaiveDatetime


class Intervention(SQLModel, table=True):
    """Tindakan K3 (briefing, rambu, sanksi, dll.) untuk dibandingkan kepatuhan sebelum vs sesudahnya."""
    id: int | None = Field(default=None, primary_key=True)
    at: NaiveDatetime = Field(index=True)    # kapan intervensi mulai berlaku
    title: str
    note: str = ""
    camera_id: str = ""                       # kosong = semua kamera
    created_at: NaiveDatetime


class ZoneRule(SQLModel, table=True):
    """Aturan zona: poligon + APD wajib. Saat start, jika kosong, diisi dari config.yaml;
    setelah itu database jadi sumber kebenaran dan bisa diubah tanpa restart."""
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)                     # unik per camera_id (dicek di endpoint), bukan global
    camera_id: str = Field(default="", index=True)   # kosong = berlaku semua kamera
    polygon_json: str = ""                            # JSON list [[x,y],...] ternormalisasi 0..1
    required_csv: str = ""                            # "helmet,vest,gloves,shoes"
    updated_at: NaiveDatetime
    updated_by: str = Field(default="")


class Setting(SQLModel, table=True):
    """Pengaturan deteksi yang bisa diubah dari dashboard (sensitivitas, shift, alert, retensi).
    Disimpan sebagai satu baris JSON per kunci supaya tidak perlu menulis ulang config.yaml."""
    key: str = Field(primary_key=True)
    value_json: str = ""
    updated_at: NaiveDatetime
    updated_by: str = Field(default="")


class QualityStat(SQLModel, table=True):
    """Ringkasan kualitas input per kamera per menit, dasar deteksi drift (gelap, buram, kamera bergeser)."""
    id: int | None = Field(default=None, primary_key=True)
    camera_id: str = Field(index=True)
    minute: NaiveDatetime = Field(index=True)
    frames: int
    persons_per_frame: float
    mean_person_conf: float                   # 0 jika tidak ada orang
    mean_ppe_conf: float                      # 0 jika tidak ada APD terdeteksi
    brightness: float                         # rata-rata kecerahan 0-255
    sharpness: float                          # variance of Laplacian; turun drastis = buram/lensa kotor


log = logging.getLogger("ppe.db")

if DATABASE_URL.startswith("sqlite"):
    (BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


def _add_missing_columns() -> list[str]:
    """Migrasi ringan: tambahkan kolom baru ke tabel lama.

    create_all hanya membuat tabel yang belum ada, tidak menambah kolom. Tanpa ini, database lama
    error setelah model diberi kolom baru. Hanya menangani PENAMBAHAN kolom; mengganti nama, tipe,
    atau menghapus kolom tetap butuh migrasi manual (lihat docs/DATABASE.md).
    """
    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing or col.primary_key:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                if col.nullable:
                    default = ""
                elif isinstance(col.type, (Integer, Float)):
                    default = " NOT NULL DEFAULT 0"
                else:
                    default = " NOT NULL DEFAULT ''"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}{default}'))
                added.append(f"{table.name}.{col.name}")
    if added:
        log.info("Kolom baru ditambahkan: %s", ", ".join(added))
    return added


def extract_conversation_title(message: str) -> str:
    """Buat judul percakapan deterministik dari pertanyaan pertama."""
    text = (message or "").strip().splitlines()[0] if (message or "").strip() else ""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "Percakapan baru"
    if len(text) > 45:
        cutoff = text[:45].rfind(" ")
        text = text[:cutoff if cutoff > 20 else 45].rstrip() + "…"
    return text[0].upper() + text[1:] if len(text) > 1 else text.upper()


def backfill_legacy_conversations() -> None:
    """Migrasi pesan legacy tanpa conversation_id menjadi satu percakapan arsip per user."""
    insp = inspect(engine)
    if not insp.has_table("chathistory"):
        return
    cols = {c["name"] for c in insp.get_columns("chathistory")}
    if "conversation_id" not in cols:
        return
    with Session(engine) as db:
        unassigned_users = db.exec(
            select(ChatHistory.username).where(
                col(ChatHistory.conversation_id).is_(None) | (col(ChatHistory.conversation_id) == "")
            ).distinct()
        ).all()
        for username in unassigned_users:
            if not username:
                continue
            legacy_id = f"legacy_{hashlib.sha256(username.encode()).hexdigest()[:16]}"
            conv = db.get(Conversation, legacy_id)
            if not conv:
                first_msg = db.exec(
                    select(ChatHistory).where(
                        col(ChatHistory.username) == username,
                        col(ChatHistory.role) == "user",
                        col(ChatHistory.conversation_id).is_(None) | (col(ChatHistory.conversation_id) == "")
                    ).order_by(col(ChatHistory.at).asc(), col(ChatHistory.id).asc()).limit(1)
                ).first()
                bounds = db.exec(
                    select(func.min(ChatHistory.at), func.max(ChatHistory.at)).where(
                        col(ChatHistory.username) == username,
                        col(ChatHistory.conversation_id).is_(None) | (col(ChatHistory.conversation_id) == "")
                    )
                ).one()
                min_at = bounds[0] or datetime.now()
                max_at = bounds[1] or min_at
                title = extract_conversation_title(first_msg.content) if first_msg else "Riwayat sebelumnya"
                conv = Conversation(
                    id=legacy_id,
                    owner=username,
                    title=title,
                    created=min_at,
                    updated=max_at,
                    pinned=False,
                )
                db.add(conv)
                db.flush()
            db.exec(
                update(ChatHistory)
                .where(col(ChatHistory.username) == username, col(ChatHistory.conversation_id).is_(None) | (col(ChatHistory.conversation_id) == ""))
                .values(conversation_id=legacy_id)
            )
        db.commit()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    backfill_legacy_conversations()

class Camera(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    source_kind: str = "stream"       # stream | video
    source_enc: str = ""              # URL terenkripsi
    video_id: str = ""
    mode: str = ""                    # simulasi | analisis
    recording_start: NaiveDatetime | None = None
    sample_rate: float = 0.0
    enabled: bool = True
    created_at: NaiveDatetime
    updated_by: str = ""


class VideoFile(SQLModel, table=True):
    id: str = Field(primary_key=True)
    label: str                        # nama file asli (dibersihkan)
    path: str                         # nama file acak di disk
    size: int
    duration_s: float
    fps: float
    width: int
    height: int
    sha256: str
    uploaded_by: str
    created_at: NaiveDatetime


class UploadSession(SQLModel, table=True):
    id: str = Field(primary_key=True)
    label: str
    size: int
    received: int
    created_by: str
    created_at: NaiveDatetime
    expires_at: NaiveDatetime


class AnalysisJob(SQLModel, table=True):
    id: str = Field(primary_key=True)
    camera_id: str = Field(index=True)
    video_id: str
    recording_start: NaiveDatetime
    sample_rate: float
    status: str                       # menunggu | berjalan | dijeda | selesai | dibatalkan | gagal
    position_s: float = 0.0
    events: int = 0
    output_video: str = ""            # path relatif video hasil deteksi (WebM), kosong jika belum ada
    output_video_sha256: str = ""
    started_at: NaiveDatetime | None = None
    finished_at: NaiveDatetime | None = None
