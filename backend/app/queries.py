"""Query data yang dipakai bersama oleh API dashboard dan tool agent."""
from __future__ import annotations

import json
import math
import re
from copy import copy
from collections import Counter
from datetime import datetime, timedelta
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from .config import TIMEZONE, load_config
from .db import TrackRecord, Violation

ITEMS = ("helmet", "vest", "gloves", "shoes")
ITEM_LABEL = {"helmet": "helm", "vest": "rompi", "gloves": "sarung tangan", "shoes": "sepatu"}
PERIODS = ("hari_ini", "kemarin", "minggu_ini", "7_hari", "bulan_ini", "30_hari", "semua")
SEVERITY = {"compliant": 0, "uncertain": 1, "violation": 2}


def item_csv(items: Sequence[str]) -> str:
    """Normalisasi daftar kode APD menjadi CSV stabil tanpa duplikat."""
    allowed = set(ITEMS)
    return ",".join(k for k in ITEMS if k in allowed and k in set(items))


def split_items(text: str) -> list[str]:
    """Pecah CSV kode APD dan abaikan nilai lama yang tidak dikenal."""
    seen = {i for i in (text or "").split(",") if i in ITEMS}
    return [k for k in ITEMS if k in seen]


def corrected_item_codes(r: Violation) -> list[str]:
    """APD yang dinyatakan sebenarnya dipakai pada review deteksi keliru.

    Event lama belum punya `corrected_items`; untuk kompatibilitas, false positive lama dianggap
    membetulkan semua APD yang dulu dilaporkan kurang.
    """
    explicit = split_items(getattr(r, "corrected_items", ""))
    if explicit:
        return explicit
    reported = f"{r.missing_items},{r.uncertain_items}"
    return split_items(reported) if r.review == "false_positive" else []


def effective_event(r: Violation) -> Violation | None:
    """Event setelah koreksi APD per kelas diterapkan tanpa mengubah baris database."""
    if r.review != "false_positive":
        return r
    corrected = set(corrected_item_codes(r))
    field = "missing_items" if r.kind == "violation" else "uncertain_items"
    remaining = [item for item in split_items(getattr(r, field, "")) if item not in corrected]
    if not remaining:
        return None
    adjusted = copy(r)
    setattr(adjusted, field, item_csv(remaining))
    return adjusted


def now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)


def _to_local(dt: datetime) -> datetime:
    """Waktu dengan zona (mis. akhiran Z) dikonversi ke WIB; waktu tanpa zona dianggap sudah WIB."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo(TIMEZONE))
    return dt.replace(tzinfo=None)


def resolve_range(period: str | None = None, start: str | None = None, end: str | None = None,
                  now: datetime | None = None) -> tuple[datetime, datetime]:
    """Rentang waktu dari nama periode atau ISO start/end. Waktu lokal (WIB), tanpa tzinfo."""
    now = now or now_local()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if start or end:
        try:
            s = _to_local(datetime.fromisoformat(start)) if start else datetime(2000, 1, 1)
            e = _to_local(datetime.fromisoformat(end)) if end else now
        except ValueError:
            raise ValueError("Format waktu harus ISO 8601, contoh 2026-10-01T08:00") from None
        if s > e:
            raise ValueError("Waktu mulai tidak boleh setelah waktu selesai")
        return s, e
    p = (period or "hari_ini").lower()
    if p == "hari_ini":
        return today, now
    if p == "kemarin":
        return today - timedelta(days=1), today
    if p == "minggu_ini":
        return today - timedelta(days=today.weekday()), now
    if p == "7_hari":
        return today - timedelta(days=6), now
    if p == "bulan_ini":
        return today.replace(day=1), now
    if p == "30_hari":
        return today - timedelta(days=29), now
    if p == "semua":
        return datetime(2000, 1, 1), now
    raise ValueError(f"Periode tidak dikenal: {period}. Pilihan: {', '.join(PERIODS)}")


# Pemetaan parameter sumber data -> daftar source_kind yang disertakan.
# "nyata" (bawaan) = data kamera live + hasil analisis rekaman, TANPA video simulasi.
SOURCE_FILTERS = {
    "nyata": ("live", "rekaman"),
    "live": ("live",),
    "semua": ("live", "rekaman", "simulasi"),
}


def source_kinds(source: str | None) -> tuple[str, ...]:
    return SOURCE_FILTERS.get(source or "nyata", SOURCE_FILTERS["nyata"])


def fetch_violations(db: Session, start: datetime, end: datetime, item: str | None = None,
                     camera_id: str | None = None, zone: str | None = None, kind: str = "violation",
                     include_false_positive: bool = False, source: str | None = None,
                     camera_ids: Sequence[str] | None = None) -> list[Violation]:
    q = select(Violation).where(Violation.started_at >= start, Violation.started_at < end)
    if kind != "all":
        q = q.where(Violation.kind == kind)
    if camera_id:
        q = q.where(Violation.camera_id == camera_id)
    if camera_ids:
        q = q.where(col(Violation.camera_id).in_(tuple(camera_ids)))
    if zone:
        q = q.where(Violation.zone == zone)
    q = q.where(col(Violation.source_kind).in_(source_kinds(source)))
    rows = list(db.exec(q.order_by(col(Violation.started_at).desc())).all())
    if not include_false_positive:
        rows = [effective for row in rows if (effective := effective_event(row)) is not None]
    if item:
        rows = [r for r in rows if item in r.missing_items.split(",")]
    return rows


def person_key(v) -> tuple:
    fallback = getattr(v, "worker_id", 0) or getattr(v, "track_id", 0)
    return (v.session, v.camera_id, getattr(v, "person_key", "") or str(fallback))


_PERSON_KEY_ID = re.compile(r"^p(\d+)(?::|$)")


def worker_id(row) -> int:
    explicit = int(getattr(row, "worker_id", 0) or 0)
    if explicit > 0:
        return explicit
    if getattr(row, "source_kind", "") != "simulasi":
        m = _PERSON_KEY_ID.match(getattr(row, "person_key", "") or "")
        if m:
            return int(m.group(1))
    return int(getattr(row, "track_id", 0) or 0)


def worker_label(row) -> str:
    return f"W{worker_id(row):03d}"


VISUAL_CLUSTER_GAP_S = 10.0
EVIDENCE_VISUAL_CLUSTER_GAP_S = 22.0
SIMULATION_EVIDENCE_VISUAL_CLUSTER_GAP_S = 45.0
FRAGMENTATION_EVENT_RATIO = 3.0
FRAGMENTATION_MIN_EXTRA_EVENTS = 5


def _worker_identity(v: Violation) -> tuple[str, str]:
    """Identitas pekerja dari tracker, stabil untuk deduplikasi normal."""
    worker_key = v.person_key or f"{v.session}:{getattr(v, 'worker_id', 0) or v.track_id}"
    return (v.camera_id, worker_key)


def visual_clusters(rows: Sequence[Violation], max_gap_s: float = VISUAL_CLUSTER_GAP_S) -> list[list[Violation]]:
    """Kelompokkan event yang tampak sebagai burst visual yang sama.

    Ini bukan re-identification penuh. Fungsinya sebagai pagar pengaman untuk kasus tracker
    memecah satu orang menjadi banyak `person_key` dalam beberapa detik yang sama, sehingga
    jawaban agent tidak kembali membesar menjadi jumlah event.
    """
    clusters: list[list[Violation]] = []
    cluster_end: datetime | None = None
    cluster_camera_zone: tuple[str, str] | None = None
    for row in sorted(rows, key=lambda r: (r.camera_id, r.zone, r.started_at, r.id or 0)):
        camera_zone = (row.camera_id, row.zone)
        gap = (row.started_at - cluster_end).total_seconds() if cluster_end else None
        row_end = row.ended_at or row.started_at
        if cluster_camera_zone == camera_zone and gap is not None and gap <= max_gap_s:
            clusters[-1].append(row)
            if row_end > cluster_end:
                cluster_end = row_end
            continue
        clusters.append([row])
        cluster_camera_zone = camera_zone
        cluster_end = row_end
    return clusters


def _evidence_visual_gap_s(rows: Sequence[Violation]) -> float:
    """Jeda klaster Evidence untuk data nyata vs simulasi/replay.

    Kamera simulasi menjalankan video yang sama berulang dan beberapa restart membuat sesi baru.
    Jika jeda Evidence terlalu pendek, pekerja yang sama pada replay LiveCAM dihitung sebagai
    pekerja baru, sehingga jumlah kartu bisa melebihi jumlah orang di frame. Ambang khusus ini
    hanya dipakai untuk tampilan Evidence saat semua row berasal dari simulasi; row mentah tetap
    disimpan dan dilaporkan lewat `jumlah_event_mentah`.
    """
    if rows and all(row.source_kind == "simulasi" for row in rows):
        return SIMULATION_EVIDENCE_VISUAL_CLUSTER_GAP_S
    return EVIDENCE_VISUAL_CLUSTER_GAP_S


def _has_tracking_fragmentation(rows: Sequence[Violation], identity_count: int | None = None,
                               visual_count: int | None = None) -> bool:
    """Deteksi konservatif saat event jauh lebih banyak daripada identitas tracking.

    Kondisi ini muncul pada data cam7: banyak row/event dalam detik yang sama untuk beberapa
    `person_key`, padahal secara visual hanya satu pekerja. Ambang dibuat ketat agar dua pekerja
    berbeda yang kebetulan berdekatan waktunya tidak otomatis digabung.
    """
    event_count = len(rows)
    identity_count = identity_count if identity_count is not None else len({_worker_identity(r) for r in rows})
    visual_count = visual_count if visual_count is not None else len(visual_clusters(rows))
    if not rows or visual_count >= identity_count:
        return False
    repeated_events = event_count >= identity_count * FRAGMENTATION_EVENT_RATIO and event_count >= identity_count + FRAGMENTATION_MIN_EXTRA_EVENTS
    many_split_ids = identity_count >= visual_count + FRAGMENTATION_MIN_EXTRA_EVENTS
    return repeated_events or many_split_ids


def _has_simulation_replay_fragmentation(rows: Sequence[Violation], visual_count: int | None = None) -> bool:
    """Deteksi tampilan saat kamera simulasi mengulang video/restart dan membuat identitas baru.

    Berbeda dari live nyata, data simulasi berasal dari replay video yang sama. Jika tracker membuat
    `person_key` baru pada tiap loop/restart tetapi row masih berdekatan secara visual, Evidence
    sebaiknya menampilkan klaster visual agar angka pekerja tidak melebihi jumlah orang pada video.
    """
    if not rows or any(row.source_kind != "simulasi" for row in rows):
        return False
    identity_count = len({_worker_identity(r) for r in rows})
    visual_count = visual_count if visual_count is not None else len(visual_clusters(rows, _evidence_visual_gap_s(rows)))
    return visual_count < identity_count


def _simulation_worker_key(row: Violation) -> tuple[str, str]:
    """Kunci worker visual untuk video simulasi yang di-replay.

    Pada kamera simulasi, `person_key` berisi sesi/restart di belakang tanda `:` sehingga orang
    visual yang sama dapat muncul sebagai `p14:2026...`, `p14:2026...` lain, dan seterusnya.
    Untuk tampilan Evidence, bagian dasar (`p14`) lebih sesuai sebagai kartu pekerja; bukti mentah
    tetap menampilkan ID event/tracker asli.
    """
    raw_key = row.person_key or f"p{row.track_id}"
    base_key = raw_key.split(":", 1)[0]
    return (row.camera_id, base_key or str(row.track_id))


def _evidence_worker_key(row: Violation) -> tuple:
    """Kunci konservatif untuk isi satu kartu Evidence.

    Evidence tidak boleh mencampur dua pekerja berbeda hanya karena muncul di kamera/zona dan waktu
    yang berdekatan. Replay simulasi boleh digabung lintas restart jika base `person_key` dan
    nomor worker/tracker sama;
    data live/rekaman nyata memakai `person_key` penuh karena suffix waktu adalah bagian identitas
    tracker yang membedakan kemunculan baru.
    """
    if row.source_kind == "simulasi":
        cam, base_key = _simulation_worker_key(row)
        stable_id = int(getattr(row, "worker_id", 0) or row.track_id)
        return (cam, row.source_kind, base_key, stable_id)
    if row.person_key:
        return (row.camera_id, row.source_kind, row.person_key)
    return (row.camera_id, row.source_kind, row.session, getattr(row, "worker_id", 0) or row.track_id)


def _split_groups_by_evidence_worker(groups: Sequence[Sequence[Violation]]) -> list[list[Violation]]:
    """Pagar terakhir agar `event_bukti` satu kartu tidak berisi pekerja berbeda."""
    split: list[list[Violation]] = []
    for group in groups:
        buckets: dict[tuple, list[Violation]] = {}
        for row in group:
            buckets.setdefault(_evidence_worker_key(row), []).append(row)
        split.extend(buckets.values())
    return split


def _simulation_fragment_groups(rows: Sequence[Violation]) -> list[list[Violation]]:
    """Gabungkan replay simulasi hanya saat kunci pekerjanya stabil sama.

    Penggabungan berdasarkan signature, kedekatan waktu, atau overlap durasi terbukti bisa
    mencampur dua orang berbeda ke satu kartu Evidence. Karena itu jalur aman adalah memakai
    `_evidence_worker_key`: base `person_key` + nomor worker/tracker yang sama boleh digabung
    lintas restart/replay, tetapi identitas berbeda selalu tetap menjadi kartu/pekerja terpisah.
    """
    grouped: dict[tuple, list[Violation]] = {}
    for row in rows:
        grouped.setdefault(_evidence_worker_key(row), []).append(row)
    return list(grouped.values())


def _worker_count_from_events(rows: Sequence[Violation]) -> int:
    """Jumlah pekerja pelanggar dari event, bukan jumlah event.

    Untuk tampilan analitik operator, satu pekerja yang memicu beberapa event tetap dihitung satu.
    Jika tracker terindikasi memecah satu pekerja menjadi banyak identitas dalam burst pendek,
    gunakan klaster visual konservatif agar angka tidak menggelembung.
    """
    if not rows:
        return 0
    simulation_rows = [row for row in rows if row.source_kind == "simulasi"]
    live_rows = [row for row in rows if row.source_kind != "simulasi"]
    count = len(_simulation_fragment_groups(simulation_rows)) if simulation_rows else 0
    if live_rows:
        count += len(visual_clusters(live_rows)) if _has_tracking_fragmentation(live_rows) else len({_worker_identity(r) for r in live_rows})
    return count


def _worker_distribution(rows: Sequence[Violation], key_fn) -> dict:
    buckets: dict[object, list[Violation]] = {}
    for row in rows:
        buckets.setdefault(key_fn(row), []).append(row)
    return {key: _worker_count_from_events(vals) for key, vals in buckets.items()}


def merge_tracks(tracks: list[TrackRecord] | tuple[TrackRecord, ...]) -> list[TrackRecord]:
    """Gabungkan beberapa rentang track untuk orang yang sama."""
    by_key: dict[tuple, TrackRecord] = {}
    for t in tracks:
        key = person_key(t)
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = t
            continue
        if t.first_seen < cur.first_seen:
            cur.first_seen = t.first_seen
        if t.last_seen > cur.last_seen:
            cur.last_seen = t.last_seen
        if SEVERITY.get(t.final_status, 0) > SEVERITY.get(cur.final_status, 0):
            cur.final_status = t.final_status
        missing = {i for i in cur.missing_items.split(",") if i} | {i for i in t.missing_items.split(",") if i}
        cur.missing_items = ",".join(sorted(missing))
    return list(by_key.values())


def count_violations(db: Session, start: datetime, end: datetime, item: str | None = None,
                     camera_id: str | None = None, zone: str | None = None,
                     source: str | None = None) -> dict:
    rows = fetch_violations(db, start, end, item, camera_id, zone, source=source)
    event_count = len(rows)
    person_count = len({_worker_identity(r) for r in rows})
    visual_count = _worker_count_from_events(rows)
    fragmented = _has_tracking_fragmentation(rows, person_count, visual_count)
    by_item = {}
    for k in ITEMS:
        by_item[k] = sum(1 for r in rows if k in r.missing_items.split(","))
    return {
        "periode": {"mulai": start.isoformat(timespec="minutes"), "selesai": end.isoformat(timespec="minutes")},
        "jumlah_event_pelanggaran": int(event_count or 0),
        "jumlah_orang_unik": int(person_count or 0),
        "jumlah_identitas_tracking": int(person_count or 0),
        "jumlah_pekerja_visual": int(visual_count or 0),
        "indikasi_fragmentasi_tracking": fragmented,
        "per_apd": {ITEM_LABEL[k]: int(by_item.get(k, 0)) for k in ITEMS},
        "catatan": ("orang unik = person_key per kamera jika tersedia; jika kosong memakai ID tracking per sesi. "
                    "Jika ada indikasi fragmentasi tracking, agent dapat memakai pekerja visual yang menggabungkan burst event berdekatan."),
    }


def evidence_list(db: Session, start: datetime, end: datetime, item: str | None = None,
                  camera_id: str | None = None, zone: str | None = None,
                  limit: int = 5, source: str | None = None) -> list[dict]:
    rows = fetch_violations(db, start, end, item, camera_id, zone, source=source)
    if _has_tracking_fragmentation(rows):
        representatives = [max(cluster, key=lambda r: (r.started_at, r.id or 0)) for cluster in visual_clusters(rows)]
        representatives.sort(key=lambda r: (r.started_at, r.id or 0), reverse=True)
        return [violation_dict(r) for r in representatives[:limit]]
    seen: set[tuple[str, str]] = set()
    unique: list[Violation] = []
    for r in rows:
        key = _worker_identity(r)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
        if len(unique) >= limit:
            break
    return [violation_dict(r) for r in unique]


def _item_labels(codes: Iterable[str]) -> list[str]:
    return [ITEM_LABEL[i] for i in ITEMS if i in set(codes)]


def _event_signature(row: Violation) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Tanda event untuk deduplikasi tampilan Evidence.

    Satu pekerja bisa menghasilkan beberapa row karena tracker/status sempat putus. Jika jenis dan
    daftar APD-nya sama, operator cukup melihatnya sebagai satu kejadian bukti. Kombinasi APD yang
    berbeda tetap dipertahankan sebagai event berbeda.
    """
    return (row.kind, tuple(split_items(row.missing_items)), tuple(split_items(row.uncertain_items)))


def _deduplicated_event_rows(rows: Sequence[Violation]) -> list[Violation]:
    latest: dict[tuple[str, tuple[str, ...], tuple[str, ...]], Violation] = {}
    for row in rows:
        key = _event_signature(row)
        cur = latest.get(key)
        if cur is None or (row.started_at, row.id or 0) > (cur.started_at, cur.id or 0):
            latest[key] = row
    return sorted(latest.values(), key=lambda r: (r.started_at, r.id or 0), reverse=True)


def worker_evidence_groups(rows: Sequence[Violation], limit: int, offset: int = 0) -> tuple[int, list[dict]]:
    """Kelompokkan event evidence menjadi satu kartu per pekerja/klaster visual.

    Event mentah tetap dikirim di `event_bukti` agar supervisor masih bisa membuka dan
    mereview setiap bukti audit, tetapi daftar utama tidak berulang untuk pekerja yang sama.
    """
    simulation_rows = [row for row in rows if row.source_kind == "simulasi"]
    live_rows = [row for row in rows if row.source_kind != "simulasi"]

    grouped: list[list[Violation]] = []
    fragmented = False
    if simulation_rows:
        simulation_visual_grouped = visual_clusters(simulation_rows, max_gap_s=_evidence_visual_gap_s(simulation_rows))
        simulation_grouped = _simulation_fragment_groups(simulation_rows)
        fragmented = fragmented or _has_simulation_replay_fragmentation(
            simulation_rows, visual_count=len(simulation_visual_grouped)
        ) or any(len({_evidence_worker_key(row) for row in group}) >= FRAGMENTATION_MIN_EXTRA_EVENTS for group in simulation_grouped)
        grouped.extend(simulation_grouped)

    if live_rows:
        live_visual_grouped = visual_clusters(live_rows, max_gap_s=_evidence_visual_gap_s(live_rows))
        live_fragmented = _has_tracking_fragmentation(live_rows, visual_count=len(live_visual_grouped))
        fragmented = fragmented or live_fragmented
        if live_fragmented:
            grouped.extend(live_visual_grouped)
        else:
            by_worker: dict[tuple[str, str], list[Violation]] = {}
            for row in live_rows:
                by_worker.setdefault(_worker_identity(row), []).append(row)
            grouped.extend(by_worker.values())

    cards: list[dict] = []
    for group in _split_groups_by_evidence_worker(grouped):
        ordered = sorted(group, key=lambda r: (r.started_at, r.id or 0), reverse=True)
        deduped = _deduplicated_event_rows(ordered)
        representative = ordered[0]
        card = violation_dict(representative)
        missing_codes = {code for row in ordered for code in split_items(row.missing_items)}
        uncertain_codes = {code for row in ordered for code in split_items(row.uncertain_items)}
        corrected_codes = {code for row in ordered for code in corrected_item_codes(row)}
        card.update({
            "jumlah_event": len(deduped),
            "jumlah_event_mentah": len(ordered),
            "event_bukti": [violation_dict(row) for row in ordered],
            "waktu_mulai": min(row.started_at for row in ordered).isoformat(timespec="seconds"),
            "waktu_selesai": max((row.ended_at or row.started_at) for row in ordered).isoformat(timespec="seconds"),
            "apd_kurang": _item_labels(missing_codes),
            "apd_kurang_kode": [i for i in ITEMS if i in missing_codes],
            "apd_tidak_pasti": _item_labels(uncertain_codes),
            "apd_tidak_pasti_kode": [i for i in ITEMS if i in uncertain_codes],
            "apd_dibenarkan": _item_labels(corrected_codes),
            "apd_dibenarkan_kode": [i for i in ITEMS if i in corrected_codes],
            "indikasi_fragmentasi_tracking": fragmented,
        })
        cards.append(card)

    cards.sort(key=lambda card: (card["waktu"], card["id"] or 0), reverse=True)
    return len(cards), cards[offset:offset + limit]


def violation_dict(r: Violation) -> dict:
    missing = split_items(r.missing_items)
    uncertain = split_items(r.uncertain_items)
    corrected = corrected_item_codes(r)
    return {
        "id": r.id,
        "waktu": r.started_at.isoformat(timespec="seconds"),
        "selesai": r.ended_at.isoformat(timespec="seconds") if r.ended_at else None,
        "durasi_detik": round(r.duration_s, 1) if r.duration_s else None,
        "kamera": r.camera_id,
        "zona": r.zone,
        "pekerja": worker_label(r),
        "worker_id": worker_id(r),
        "pekerja_unik": r.person_key or f"{r.session}:{getattr(r, 'worker_id', 0) or r.track_id}",
        "jenis": r.kind,
        "sumber": r.source_kind or "live",  # live | rekaman | simulasi — untuk mengelompokkan evidence per sumber
        "apd_kurang": [ITEM_LABEL[i] for i in missing],
        "apd_kurang_kode": missing,
        "apd_tidak_pasti": [ITEM_LABEL[i] for i in uncertain],
        "apd_tidak_pasti_kode": uncertain,
        "apd_dibenarkan": [ITEM_LABEL[i] for i in corrected],
        "apd_dibenarkan_kode": corrected,
        "foto": f"/evidence/{r.evidence_full}" if r.evidence_full else None,
        "foto_crop": f"/evidence/{r.evidence_crop}" if r.evidence_crop else None,
        "foto_clean": f"/evidence/{r.evidence_clean}" if r.evidence_clean else None,
        "deteksi": json.loads(r.detections_json) if r.detections_json else None,
        "sha256": r.evidence_sha256,
        "klip": (f"/evidence/{r.evidence_clip}?v={r.evidence_clip_sha256}" if r.evidence_clip and r.evidence_clip_sha256
                 else (f"/evidence/{r.evidence_clip}" if r.evidence_clip else None)),
        "klip_sha256": r.evidence_clip_sha256 or None,
        "versi_model": r.model_version or None,
        "review": r.review,
    }


# Event yang belum ditutup (pipeline mati mendadak) dihitung maksimal selama ini,
# supaya satu event "menggantung" tidak menggelembungkan menit paparan tanpa batas.
MAX_OPEN_EVENT_S = 15 * 60


def event_seconds(e: Violation, now: datetime) -> float:
    if e.duration_s is not None:
        return max(0.0, e.duration_s)
    if e.ended_at is not None:
        return max(0.0, (e.ended_at - e.started_at).total_seconds())
    return max(0.0, min((now - e.started_at).total_seconds(), MAX_OPEN_EVENT_S))


def event_seconds_in_range(e: Violation, now: datetime, start: datetime | None = None,
                           end: datetime | None = None) -> float:
    """Durasi event yang benar-benar beririsan dengan rentang analitik."""
    event_end = e.started_at + timedelta(seconds=event_seconds(e, now))
    overlap_start = max(e.started_at, start) if start is not None else e.started_at
    overlap_end = min(event_end, end) if end is not None else event_end
    return max(0.0, (overlap_end - overlap_start).total_seconds())


def fetch_exposure_events(db: Session, start: datetime, end: datetime, source: str | None = None,
                          camera_ids: Sequence[str] | None = None,
                          camera_id: str | None = None) -> list[Violation]:
    """Ambil pelanggaran yang overlap periode, termasuk event yang mulai sebelum periode."""
    q = select(Violation).where(
        Violation.started_at < end,
        (col(Violation.ended_at).is_(None)) | (Violation.ended_at > start),
        Violation.kind == "violation",
        col(Violation.source_kind).in_(source_kinds(source)),
    )
    if camera_id:
        q = q.where(Violation.camera_id == camera_id)
    if camera_ids:
        q = q.where(col(Violation.camera_id).in_(tuple(camera_ids)))
    now = now_local()
    rows = [effective for row in db.exec(q).all() if (effective := effective_event(row)) is not None]
    return [row for row in rows if event_seconds_in_range(row, now, start, end) > 0]


def exposure(events: list[Violation], now: datetime, start: datetime | None = None,
             end: datetime | None = None) -> dict:
    """Orang-menit tanpa APD lengkap. Risiko sebanding dengan lama paparan, bukan jumlah kejadian."""
    total = 0.0
    per_item: dict[str, float] = {}
    for e in events:
        sec = event_seconds_in_range(e, now, start, end)
        total += sec
        for i in e.missing_items.split(","):
            if i:
                per_item[i] = per_item.get(i, 0.0) + sec
    n = len(events)
    return {
        "menit_paparan": round(total / 60, 1),
        "menit_paparan_per_apd": [{"item": k, "label": ITEM_LABEL[k], "menit": round(per_item.get(k, 0) / 60, 1)}
                                  for k in ITEMS],
        "rata_durasi_pelanggaran_detik": round(total / n, 1) if n else None,
    }


def _hm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def shift_of(dt: datetime, shifts: list[dict] | None = None) -> str:
    shifts = shifts if shifts is not None else load_config().get("shifts", [])
    minute = dt.hour * 60 + dt.minute
    for sh in shifts:
        a, b = _hm(sh["start"]), _hm(sh["end"])
        inside = a <= minute < b if a < b else (minute >= a or minute < b)  # shift melewati tengah malam
        if inside:
            return sh["name"]
    return "Di luar shift"


def effective_track_status(db: Session, tracks: Sequence[TrackRecord]) -> list[tuple[str, set[str]]]:
    """Status tiap pekerja setelah hasil review diperhitungkan.

    TrackRecord menyimpan status saat pipeline berjalan. Jika supervisor kemudian
    menandai pelanggaran sebagai false positive, APD di event itu tidak lagi dihitung
    kurang, dan pekerja yang semua pelanggarannya false positive tidak lagi dihitung melanggar.
    """
    sessions = {t.session for t in tracks}
    events = db.exec(select(Violation).where(col(Violation.session).in_(sessions))).all() if sessions else []
    valid_missing: dict[tuple, set[str]] = {}
    has_violation_event: set[tuple] = set()
    has_uncertain_event: set[tuple] = set()
    has_uncertain: set[tuple] = set()
    for e in events:
        key = person_key(e)
        if e.kind == "violation":
            has_violation_event.add(key)
        else:
            has_uncertain_event.add(key)
        effective = effective_event(e)
        if effective is None:
            continue
        if effective.kind == "violation":
            valid_missing.setdefault(key, set()).update(split_items(effective.missing_items))
        else:
            has_uncertain.add(key)

    out = []
    for t in tracks:
        key = person_key(t)
        missing = {i for i in t.missing_items.split(",") if i}
        status = t.final_status
        if status == "violation" and key in has_violation_event:
            missing = valid_missing.get(key, set())
            if not missing:
                status = "uncertain" if key in has_uncertain else "compliant"
        elif status == "uncertain" and key in has_uncertain_event and key not in has_uncertain:
            status = "compliant"
        out.append((status, missing))
    return out


def summary(db: Session, start: datetime, end: datetime, source: str | None = None,
            camera_ids: Sequence[str] | None = None) -> dict:
    tq = select(TrackRecord).where(TrackRecord.first_seen >= start, TrackRecord.first_seen < end,
                                   col(TrackRecord.source_kind).in_(source_kinds(source)))
    if camera_ids:
        tq = tq.where(col(TrackRecord.camera_id).in_(tuple(camera_ids)))
    tracks_raw = db.exec(tq).all()
    tracks = merge_tracks(list(tracks_raw))
    events = fetch_violations(db, start, end, source=source, camera_ids=camera_ids)
    uncertain = fetch_violations(db, start, end, kind="uncertain", source=source, camera_ids=camera_ids)
    exposure_events = fetch_exposure_events(db, start, end, source=source, camera_ids=camera_ids)

    effective = effective_track_status(db, tracks)
    total = len(effective)
    status = Counter(s for s, _ in effective)
    missing_by_item = Counter(i for _, missing in effective for i in missing)
    per_item = [
        {"item": k, "label": ITEM_LABEL[k], "patuh": total - missing_by_item.get(k, 0), "total": total,
         "persen": round(100 * (total - missing_by_item.get(k, 0)) / total, 1) if total else None}
        for k in ITEMS
    ]
    now = now_local()
    shifts = load_config().get("shifts", [])
    shift_names = [sh["name"] for sh in shifts]
    tracks_by_shift: dict[str, list] = {}
    for t, eff in zip(tracks, effective):
        tracks_by_shift.setdefault(shift_of(t.first_seen, shifts), []).append(eff)
    events_by_shift: dict[str, list] = {}
    for e in events:
        events_by_shift.setdefault(shift_of(e.started_at, shifts), []).append(e)
    exposure_by_shift: dict[str, list] = {}
    for e in exposure_events:
        exposure_by_shift.setdefault(shift_of(e.started_at, shifts), []).append(e)
    per_shift = []
    extra_shift_names = (set(tracks_by_shift) | set(events_by_shift) | set(exposure_by_shift)) - set(shift_names)
    for name in shift_names + sorted(extra_shift_names):
        effs, evs = tracks_by_shift.get(name, []), events_by_shift.get(name, [])
        exposure_evs = exposure_by_shift.get(name, [])
        n = len(effs)
        patuh = sum(1 for st, _ in effs if st == "compliant")
        per_shift.append({"shift": name, "pekerja": n, "compliance_rate": round(100 * patuh / n, 1) if n else None,
                          "pekerja_melanggar": _worker_count_from_events(evs),
                          "pelanggaran": len(evs),
                          "menit_paparan": exposure(exposure_evs, now, start, end)["menit_paparan"]})
    by_hour = Counter(e.started_at.hour for e in events)
    by_day = Counter(e.started_at.date().isoformat() for e in events)
    by_camera = Counter(e.camera_id for e in events)
    by_zone = Counter(e.zone for e in events)
    workers_by_hour = _worker_distribution(events, lambda e: e.started_at.hour)
    workers_by_day = _worker_distribution(events, lambda e: e.started_at.date().isoformat())
    workers_by_camera = _worker_distribution(events, lambda e: e.camera_id)
    workers_by_zone = _worker_distribution(events, lambda e: e.zone)
    risky_hours = sorted(workers_by_hour, key=lambda h: (workers_by_hour[h], by_hour.get(h, 0), h), reverse=True)[:3]
    return {
        "periode": {"mulai": start.isoformat(timespec="minutes"), "selesai": end.isoformat(timespec="minutes")},
        "pekerja_terpantau": total,
        "pekerja_patuh": status.get("compliant", 0),
        "pekerja_melanggar": status.get("violation", 0),
        "pekerja_tidak_pasti": status.get("uncertain", 0),
        "compliance_rate": round(100 * status.get("compliant", 0) / total, 1) if total else None,
        "event_pelanggaran": len(events),
        "event_tidak_pasti": len(uncertain),
        "menunggu_review": sum(1 for e in events + uncertain if e.review == "pending"),
        "kepatuhan_per_apd": per_item,
        "per_jam": [{"jam": h, "jumlah": workers_by_hour.get(h, 0), "event": by_hour.get(h, 0)} for h in range(24)],
        "per_hari": [{"tanggal": d, "jumlah": workers_by_day.get(d, 0), "event": n} for d, n in sorted(by_day.items())],
        "per_kamera": [{"kamera": k, "jumlah": workers_by_camera.get(k, 0), "event": n}
                       for k, n in by_camera.most_common()],
        "per_zona": [{"zona": k, "jumlah": workers_by_zone.get(k, 0), "event": n} for k, n in by_zone.most_common()],
        "jam_rawan": risky_hours,
        "per_shift": per_shift,
        **exposure(exposure_events, now, start, end),
    }


def two_proportion_test(x1: int, n1: int, x2: int, n2: int) -> dict | None:
    """Uji z dua proporsi (dua sisi) untuk compliance rate sebelum vs sesudah."""
    if min(n1, n2) == 0:
        return None
    p1, p2 = x1 / n1, x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (p2 - p1) / se
    p_value = math.erfc(abs(z) / math.sqrt(2))
    return {"z": round(z, 2), "p_value": round(p_value, 4), "signifikan": p_value < 0.05}


def compare_intervention(db: Session, at: datetime, days: int, camera_id: str | None = None) -> dict:
    """Bandingkan N hari sebelum vs sesudah intervensi. Angka harian dinormalisasi per hari data."""
    now = now_local()
    # periode tanpa data sama sekali (sebelum sistem dipasang) tidak boleh ikut jadi pembagi per hari
    q = select(TrackRecord.first_seen).order_by("first_seen").limit(1)
    if camera_id:
        q = q.where(TrackRecord.camera_id == camera_id)
    first = db.exec(q).first()
    data_start = first.replace(hour=0, minute=0, second=0, microsecond=0) if first else at
    before = (max(at - timedelta(days=days), min(data_start, at)), at)
    after = (at, min(at + timedelta(days=days), now))

    def window(start: datetime, end: datetime) -> dict:
        if end <= start:
            return {"mulai": start.isoformat(timespec="minutes"), "selesai": end.isoformat(timespec="minutes"),
                    "hari": 0, "pekerja": 0, "compliance_rate": None, "pelanggaran_per_hari": None,
                    "menit_paparan_per_hari": None, "kepatuhan_per_apd": {}}
        q = select(TrackRecord).where(TrackRecord.first_seen >= start, TrackRecord.first_seen < end)
        if camera_id:
            q = q.where(TrackRecord.camera_id == camera_id)
        tracks = db.exec(q).all()
        eff = effective_track_status(db, tracks)
        evs = fetch_violations(db, start, end, camera_id=camera_id)
        exposure_evs = fetch_exposure_events(db, start, end, camera_id=camera_id)
        hari = max((end - start).total_seconds() / 86400, 1 / 24)
        n = len(eff)
        missing = Counter(i for _, m in eff for i in m)
        return {
            "mulai": start.isoformat(timespec="minutes"), "selesai": end.isoformat(timespec="minutes"),
            "hari": round(hari, 2), "pekerja": n,
            "patuh": sum(1 for st, _ in eff if st == "compliant"),
            "compliance_rate": round(100 * sum(1 for st, _ in eff if st == "compliant") / n, 1) if n else None,
            "pelanggaran_per_hari": round(len(evs) / hari, 2),
            "menit_paparan_per_hari": round(exposure(exposure_evs, now, start, end)["menit_paparan"] / hari, 2),
            "kepatuhan_per_apd": {ITEM_LABEL[k]: round(100 * (n - missing.get(k, 0)) / n, 1) if n else None
                                  for k in ITEMS},
        }

    b, a = window(*before), window(*after)
    test = two_proportion_test(b.get("patuh", 0), b["pekerja"], a.get("patuh", 0), a["pekerja"])

    def delta(key):
        return round(a[key] - b[key], 2) if a.get(key) is not None and b.get(key) is not None else None

    return {
        "sebelum": b, "sesudah": a,
        "perubahan": {k: delta(k) for k in ("compliance_rate", "pelanggaran_per_hari", "menit_paparan_per_hari")},
        "uji_statistik": test,
        "kesimpulan": (
            "Data belum cukup untuk dibandingkan." if test is None else
            "Perubahan compliance rate signifikan secara statistik (p < 0,05)." if test["signifikan"] else
            "Perubahan compliance rate belum signifikan secara statistik; bisa jadi hanya variasi acak."
        ),
        "catatan": ("Perbandingan sebelum-sesudah menunjukkan korelasi, bukan sebab-akibat. Faktor lain "
                    "(jumlah pekerja, jenis pekerjaan, cuaca) juga bisa berubah. Uji statistik menganggap tiap "
                    "pekerja terpantau independen, padahal orang yang sama bisa muncul berkali-kali, jadi "
                    "signifikansi bisa terlalu optimis; lihat juga tren harian sebelum menyimpulkan. "
                    + ("Data sesudah belum lengkap; tunggu sampai periode pembanding penuh. "
                       if a["hari"] < days else "")
                    + (f"Data sebelum hanya mencakup {b['hari']:g} hari (sistem belum terpasang lebih awal). "
                       if 0 < b["hari"] < days else "")
                    + ("Data terlalu sedikit untuk disimpulkan." if min(a["pekerja"], b["pekerja"]) < 30 else "")).strip(),
    }


def model_quality(db: Session) -> list[dict]:
    """Precision lapangan per versi model, dari hasil review supervisor."""
    rows = db.exec(select(Violation).where(Violation.kind == "violation")).all()
    by_version: dict[str, list[Violation]] = {}
    for r in rows:
        by_version.setdefault(r.model_version or "tidak tercatat", []).append(r)
    out = []
    for version, evs in sorted(by_version.items()):
        c = sum(1 for e in evs if e.review == "confirmed")
        fp = sum(1 for e in evs if e.review == "false_positive")
        per_item = []
        for k in ITEMS:
            ce = sum(1 for e in evs if e.review == "confirmed" and k in e.missing_items.split(","))
            fe = sum(1 for e in evs if e.review == "false_positive" and k in corrected_item_codes(e))
            per_item.append({"item": k, "label": ITEM_LABEL[k], "confirmed": ce, "false_positive": fe,
                             "precision": round(ce / (ce + fe), 3) if ce + fe else None})
        out.append({
            "versi": version, "event": len(evs), "confirmed": c, "false_positive": fp,
            "pending": len(evs) - c - fp,
            "precision": round(c / (c + fp), 3) if c + fp else None,
            "cakupan_review": round((c + fp) / len(evs), 3) if evs else None,
            "per_apd": per_item,
        })
    return out
