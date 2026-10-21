"""Test regresi untuk bug logika yang ditemukan saat audit."""
import copy
import json
import threading
from datetime import datetime, timedelta

import cv2
import numpy as np
from sqlmodel import Session, delete

from app import agent as agent_mod
from app.agent import parse_offline
from app.alerts import Cooldown
from app.config import BASE_DIR, load_config
from app.db import AuditLog, Camera, TrackRecord, Violation, engine, init_db
from app.queries import count_violations, evidence_list, fetch_violations, resolve_range, summary, violation_dict, worker_evidence_groups
from pipeline.evidence import save_evidence
from pipeline.runner import LiveState, CameraSession
from pipeline.camera import CameraReader
from tests.test_association import make_person
import app.main as main_mod

init_db()


def test_semua_in_question_is_not_all_time():
    assert parse_offline("apakah semua pekerja pakai helm hari ini?")["period"] == "hari_ini"
    assert parse_offline("rangkum semua data")["period"] == "semua"
    assert parse_offline("pelanggaran sebulan terakhir")["period"] == "30_hari"


def test_agent_resolves_livecam_name_and_does_not_hide_demo_events(monkeypatch):
    t0 = agent_mod.Q.now_local() - timedelta(seconds=1)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog, Camera):
            db.exec(delete(table))
        db.add(Camera(id="CAM-01", name="CAM-01", source_kind="video", created_at=t0, updated_by="uji"))
        db.add(Camera(id="LiveCAM-01", name="Loc1a", source_kind="video", created_at=t0, updated_by="uji"))
        db.add(Violation(session="sim", camera_id="LiveCAM-01", source_kind="simulasi", track_id=14,
                         person_key="p14:stable", started_at=t0, missing_items="helmet,vest",
                         evidence_crop="loc1a.jpg"))
        db.commit()

    monkeypatch.setattr(agent_mod, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(agent_mod, "_finalize_offline_answer_with_ollama", lambda _message, result: result)
    out = agent_mod.chat("berapa pelanggaran di Loc1a (LiveCAM-01)", [], "uji")

    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog, Camera):
            db.exec(delete(table))
        db.commit()

    assert parse_offline("berapa pelanggaran di Loc1a (LiveCAM-01)")["camera_id"] == "LiveCAM-01"
    assert "0 orang" not in out["answer"]
    assert "1 orang" in out["answer"]
    assert "kamera LiveCAM-01" in out["answer"]
    assert "Data nyata pada filter ini kosong" not in out["answer"]
    assert "demo/simulasi" not in out["answer"]
    assert any("demo/simulasi" in batasan for batasan in out["audit_data"]["batasan"])
    assert out["tool_calls"][0]["input"]["camera_id"] == "LiveCAM-01"
    assert out["tool_calls"][0]["input"]["source"] == "semua"


def test_agent_prefers_explicit_livecam_even_when_camera_row_missing(monkeypatch):
    t0 = agent_mod.Q.now_local() - timedelta(seconds=1)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog, Camera):
            db.exec(delete(table))
        db.add(Camera(id="CAM-01", name="CAM-01", source_kind="video", created_at=t0, updated_by="uji"))
        db.add(Violation(session="sim", camera_id="LiveCAM-01", source_kind="simulasi", track_id=14,
                         person_key="p14:stable", started_at=t0, missing_items="helmet",
                         evidence_crop="loc1a.jpg"))
        db.commit()

    monkeypatch.setattr(agent_mod, "LLM_PROVIDER", "anthropic")
    out = agent_mod.chat("berapa pelanggaran di Loc1a (LiveCAM-01)", [], "uji")

    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog, Camera):
            db.exec(delete(table))
        db.commit()

    assert parse_offline("berapa pelanggaran di Loc1a (LiveCAM-01)")["camera_id"] == "LiveCAM-01"
    assert "kamera LiveCAM-01" in out["answer"]
    assert "0 orang" not in out["answer"]
    assert out["tool_calls"][0]["input"]["camera_id"] == "LiveCAM-01"
    assert out["tool_calls"][0]["input"]["source"] == "semua"


def test_timezone_aware_input_converted_to_wib():
    s, e = resolve_range(start="2026-10-01T01:00:00Z", end="2026-10-01T02:00:00+00:00")
    assert (s.hour, e.hour) == (8, 9)


def test_false_positive_updates_compliance():
    t0 = datetime(2026, 9, 30, 9, 0)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # W001: satu pelanggaran helm, lalu ditandai false positive
        db.add(TrackRecord(session="s", camera_id="C", track_id=1, first_seen=t0, last_seen=t0,
                           final_status="violation", missing_items="helmet"))
        db.add(Violation(session="s", camera_id="C", track_id=1, started_at=t0, missing_items="helmet",
                         review="false_positive"))
        # W002: dua pelanggaran, hanya sarung tangan yang valid
        db.add(TrackRecord(session="s", camera_id="C", track_id=2, first_seen=t0, last_seen=t0,
                           final_status="violation", missing_items="gloves,helmet"))
        db.add(Violation(session="s", camera_id="C", track_id=2, started_at=t0, missing_items="helmet",
                         review="false_positive"))
        db.add(Violation(session="s", camera_id="C", track_id=2, started_at=t0, missing_items="gloves"))
        db.commit()
        s = summary(db, t0 - timedelta(hours=1), t0 + timedelta(hours=1))
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert s["pekerja_patuh"] == 1 and s["pekerja_melanggar"] == 1
    per = {x["item"]: x["patuh"] for x in s["kepatuhan_per_apd"]}
    assert per["helmet"] == 2 and per["gloves"] == 1


def test_partial_apd_correction_keeps_remaining_violation_in_analytics():
    t0 = datetime(2026, 9, 30, 9, 30)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        db.add(TrackRecord(session="s", camera_id="C", track_id=1, person_key="p1",
                           first_seen=t0, last_seen=t0, final_status="violation",
                           missing_items="helmet,vest"))
        db.add(Violation(session="s", camera_id="C", track_id=1, person_key="p1",
                         started_at=t0, duration_s=60, missing_items="helmet,vest",
                         review="false_positive", corrected_items="helmet"))
        db.commit()
        result = summary(db, t0 - timedelta(hours=1), t0 + timedelta(hours=1))
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()

    assert result["pekerja_patuh"] == 0
    assert result["pekerja_melanggar"] == 1
    assert result["event_pelanggaran"] == 1
    assert result["menit_paparan"] == 1.0
    per = {row["item"]: row["patuh"] for row in result["kepatuhan_per_apd"]}
    assert per["helmet"] == 1
    assert per["vest"] == 0


def test_legacy_uncertain_false_positive_is_excluded_from_analytics():
    t0 = datetime(2026, 9, 30, 9, 45)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        db.add(TrackRecord(session="s", camera_id="C", track_id=1, person_key="p1",
                           first_seen=t0, last_seen=t0, final_status="uncertain"))
        db.add(Violation(session="s", camera_id="C", track_id=1, person_key="p1",
                         kind="uncertain", started_at=t0, uncertain_items="gloves",
                         review="false_positive", corrected_items=""))
        db.commit()
        result = summary(db, t0 - timedelta(hours=1), t0 + timedelta(hours=1))
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()

    assert result["event_tidak_pasti"] == 0
    assert result["menunggu_review"] == 0
    assert result["pekerja_patuh"] == 1
    assert result["pekerja_tidak_pasti"] == 0


def test_summary_groups_same_person_key_once():
    t0 = datetime(2026, 9, 30, 10, 0)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # satu orang (person_key sama) muncul dua rentang track berbeda (track_id 1 lalu 5)
        db.add(TrackRecord(session="s", camera_id="C", track_id=1, person_key="pX",
                           first_seen=t0, last_seen=t0, final_status="compliant", missing_items=""))
        db.add(TrackRecord(session="s", camera_id="C", track_id=5, person_key="pX",
                           first_seen=t0, last_seen=t0, final_status="violation", missing_items="helmet"))
        db.commit()
        s = summary(db, t0 - timedelta(hours=1), t0 + timedelta(hours=1))
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert s["pekerja_terpantau"] == 1
    assert s["pekerja_melanggar"] == 1


def test_violation_dict_uses_stable_worker_label_from_person_key_or_worker_id():
    t0 = datetime(2026, 9, 30, 10, 15)
    legacy = Violation(session="s", camera_id="cam7", track_id=55, person_key="p41:20260930101500000000",
                       started_at=t0, missing_items="helmet")
    current = Violation(session="s", camera_id="cam7", track_id=55, worker_id=41, person_key="p41:stable",
                        started_at=t0, missing_items="helmet")
    assert violation_dict(legacy)["pekerja"] == "W041"
    assert violation_dict(legacy)["worker_id"] == 41
    assert violation_dict(current)["pekerja"] == "W041"
    assert violation_dict(current)["worker_id"] == 41


def test_violation_dict_exposes_source_kind():
    t0 = datetime(2026, 9, 30, 10, 15)
    live = Violation(session="s", camera_id="cam1", track_id=1, started_at=t0, missing_items="helmet")
    rekaman = Violation(session="s", camera_id="cam2", track_id=2, started_at=t0, missing_items="vest",
                        source_kind="rekaman")
    assert violation_dict(live)["sumber"] == "live"  # default saat tidak diisi
    assert violation_dict(rekaman)["sumber"] == "rekaman"


def test_count_violations_groups_same_person_key_once():
    t0 = datetime(2026, 9, 30, 10, 30)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # Satu orang yang sama bisa menghasilkan dua event/track. Hitungan orang unik
        # harus memakai person_key, bukan jumlah event atau track_id.
        db.add(Violation(session="s", camera_id="cam7", track_id=1, person_key="pX",
                         started_at=t0, missing_items="helmet"))
        db.add(Violation(session="s", camera_id="cam7", track_id=5, person_key="pX",
                         started_at=t0 + timedelta(seconds=5), missing_items="helmet"))
        # Data lama tanpa person_key tetap dibedakan berdasarkan track_id.
        db.add(Violation(session="s", camera_id="cam7", track_id=9, person_key="",
                         started_at=t0 + timedelta(seconds=10), missing_items="helmet"))
        db.commit()
        c = count_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), item="helmet", camera_id="cam7")
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert c["jumlah_event_pelanggaran"] == 3
    assert c["jumlah_orang_unik"] == 2


def test_count_violations_groups_person_key_across_sessions():
    t0 = datetime(2026, 9, 30, 10, 45)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # Rekaman yang sama bisa diproses ulang dalam session runner berbeda. Jika person_key
        # sama, agent harus menjawab orang unik 1, bukan jumlah session/event.
        db.add(Violation(session="satu", camera_id="cam7", track_id=7, person_key="p7:20260930104500000000",
                         started_at=t0, missing_items="helmet"))
        db.add(Violation(session="dua", camera_id="cam7", track_id=7, person_key="p7:20260930104500000000",
                         started_at=t0 + timedelta(seconds=5), missing_items="helmet"))
        db.commit()
        c = count_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), item="helmet", camera_id="cam7")
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert c["jumlah_event_pelanggaran"] == 2
    assert c["jumlah_orang_unik"] == 1


def test_summary_distributions_count_workers_not_repeated_events():
    t0 = datetime(2026, 9, 30, 10, 45)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        db.add(TrackRecord(session="satu", camera_id="cam7", track_id=7, person_key="p7",
                           first_seen=t0, last_seen=t0 + timedelta(seconds=20),
                           final_status="violation", missing_items="helmet"))
        for idx in range(5):
            db.add(Violation(session=f"s-{idx}", camera_id="cam7", track_id=7 + idx,
                             person_key="p7", started_at=t0 + timedelta(seconds=idx * 2),
                             missing_items="helmet", duration_s=5))
        db.commit()
        s = summary(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1))
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert s["pekerja_melanggar"] == 1
    assert s["event_pelanggaran"] == 5
    assert s["per_jam"][10]["jumlah"] == 1
    assert s["per_jam"][10]["event"] == 5
    assert s["per_hari"] == [{"tanggal": "2026-09-30", "jumlah": 1, "event": 5}]
    assert s["per_kamera"] == [{"kamera": "cam7", "jumlah": 1, "event": 5}]
    pagi = next(x for x in s["per_shift"] if x["shift"] == "Pagi")
    assert pagi["pekerja_melanggar"] == 1
    assert pagi["pelanggaran"] == 5


def test_evidence_list_is_deduplicated_per_person_key():
    t0 = datetime(2026, 9, 30, 10, 50)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        db.add(Violation(session="satu", camera_id="cam7", track_id=7, person_key="p7",
                         started_at=t0, missing_items="helmet", evidence_crop="a.jpg"))
        db.add(Violation(session="dua", camera_id="cam7", track_id=7, person_key="p7",
                         started_at=t0 + timedelta(seconds=5), missing_items="helmet", evidence_crop="b.jpg"))
        db.add(Violation(session="satu", camera_id="cam7", track_id=8, person_key="p8",
                         started_at=t0 + timedelta(seconds=10), missing_items="helmet", evidence_crop="c.jpg"))
        db.commit()
        rows = evidence_list(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), item="helmet", camera_id="cam7", limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert [r["pekerja_unik"] for r in rows] == ["p8", "p7"]


def test_worker_evidence_groups_keep_all_event_evidence():
    t0 = datetime(2026, 9, 30, 10, 52)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        for idx, missing in enumerate(["helmet", "vest", "helmet,vest"]):
            db.add(Violation(session=f"s-{idx}", camera_id="cam7", track_id=7, person_key="p7",
                             started_at=t0 + timedelta(seconds=idx * 3), missing_items=missing,
                             evidence_crop=f"bukti-{idx}.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), camera_id="cam7")
        total, cards = worker_evidence_groups(raw_rows, limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert total == 1
    assert len(cards) == 1
    assert cards[0]["jumlah_event"] == 3
    assert len(cards[0]["event_bukti"]) == 3
    assert cards[0]["apd_kurang"] == ["helm", "rompi"]


def test_worker_evidence_groups_deduplicate_same_violation_signature():
    t0 = datetime(2026, 9, 30, 10, 53)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        for idx in range(4):
            db.add(Violation(session=f"s-{idx}", camera_id="cam7", track_id=7, person_key="p7",
                             started_at=t0 + timedelta(seconds=idx * 4), missing_items="helmet",
                             evidence_crop=f"helm-{idx}.jpg"))
        db.add(Violation(session="s-vest", camera_id="cam7", track_id=7, person_key="p7",
                         started_at=t0 + timedelta(seconds=20), missing_items="vest",
                         evidence_crop="rompi.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), camera_id="cam7")
        total, cards = worker_evidence_groups(raw_rows, limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert total == 1
    assert cards[0]["jumlah_event"] == 2
    assert cards[0]["jumlah_event_mentah"] == 5
    assert len(cards[0]["event_bukti"]) == 5
    assert [event["apd_kurang"] for event in cards[0]["event_bukti"]][:2] == [["rompi"], ["helm"]]


def test_worker_evidence_groups_use_visual_cluster_for_fragmented_live_burst():
    t0 = datetime(2026, 9, 30, 10, 54)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # Satu pekerja visual pecah menjadi banyak event pada person_key yang sama.
        # Evidence boleh menggabungkan event pekerja yang identitasnya sama.
        for repeat in range(8):
            db.add(Violation(session=f"s-{repeat}", camera_id="cam7", track_id=7,
                             person_key="p7:stable",
                             started_at=t0 + timedelta(seconds=repeat * 2),
                             missing_items="helmet", evidence_crop=f"frag-{repeat}.jpg"))
        # Pekerja visual lain muncul cukup jauh setelah burst pertama.
        db.add(Violation(session="s-lain", camera_id="cam7", track_id=99, person_key="p99:stable",
                         started_at=t0 + timedelta(seconds=50), missing_items="helmet",
                         evidence_crop="lain.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=2), camera_id="cam7")
        total, cards = worker_evidence_groups(raw_rows, limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert total == 2
    assert [card["jumlah_event_mentah"] for card in cards] == [1, 8]
    assert [card["jumlah_event"] for card in cards] == [1, 1]


def test_worker_evidence_groups_split_fragmented_live_burst_by_worker_identity():
    t0 = datetime(2026, 9, 30, 10, 55)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # Dua pekerja berbeda pada kamera/zona/waktu yang rapat tidak boleh masuk kartu yang sama.
        # Ini meregresi kasus operator melihat W014 dan W351 di satu modal Evidence.
        for repeat in range(4):
            db.add(Violation(session=f"w14-{repeat}", camera_id="LiveCAM-01", source_kind="live",
                             zone="Area Produksi", track_id=14, person_key="p14:stable",
                             started_at=t0 + timedelta(seconds=repeat),
                             missing_items="helmet", evidence_crop=f"w14-{repeat}.jpg"))
            db.add(Violation(session=f"w351-{repeat}", camera_id="LiveCAM-01", source_kind="live",
                             zone="Area Produksi", track_id=351, person_key="p351:stable",
                             started_at=t0 + timedelta(seconds=repeat, milliseconds=200),
                             missing_items="helmet", evidence_crop=f"w351-{repeat}.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1),
                                    camera_id="LiveCAM-01", source="semua")
        total, cards = worker_evidence_groups(raw_rows, limit=10)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert total == 2
    worker_sets = [{event["pekerja"] for event in card["event_bukti"]} for card in cards]
    assert {frozenset(workers) for workers in worker_sets} == {frozenset({"W014"}), frozenset({"W351"})}
    assert [card["jumlah_event_mentah"] for card in cards] == [4, 4]


def test_worker_evidence_groups_use_wider_visual_gap_for_simulation_replay():
    t0 = datetime(2026, 9, 30, 10, 56)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # LiveCAM simulasi dapat mencatat pekerja visual yang sama berulang lintas loop/restart.
        # Jeda 30 detik masih digabung hanya jika base person_key tetap sama.
        for idx in range(6):
            db.add(Violation(session=f"sim-{idx}", camera_id="LiveCAM-01", source_kind="simulasi",
                             track_id=14, person_key=f"p14:restart-{idx}",
                             started_at=t0 + timedelta(seconds=idx * 30),
                             missing_items="helmet", evidence_crop=f"sim-{idx}.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=5),
                                    camera_id="LiveCAM-01", source="semua")
        total, cards = worker_evidence_groups(raw_rows, limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert total == 1
    assert cards[0]["jumlah_event"] == 1
    assert cards[0]["jumlah_event_mentah"] == 6
    assert len(cards[0]["event_bukti"]) == 6


def test_worker_evidence_groups_do_not_mix_different_simulation_workers_in_same_visual_cluster():
    t0 = datetime(2026, 9, 30, 10, 59)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # Dua pekerja berbeda bisa muncul berdekatan dalam satu klaster waktu/visual.
        # Base person_key berbeda harus tetap menjadi kartu terpisah, misalnya W014 dan W351.
        db.add(Violation(session="sim-a", camera_id="LiveCAM-01", source_kind="simulasi",
                         track_id=14, person_key="p14:restart-a",
                         started_at=t0, missing_items="helmet", evidence_crop="p14.jpg"))
        db.add(Violation(session="sim-b", camera_id="LiveCAM-01", source_kind="simulasi",
                         track_id=351, person_key="p24:restart-a",
                         started_at=t0 + timedelta(seconds=10), missing_items="helmet,vest",
                         evidence_crop="p24.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1),
                                    camera_id="LiveCAM-01", source="semua")
        total, cards = worker_evidence_groups(raw_rows, limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert total == 2
    assert [card["jumlah_event_mentah"] for card in cards] == [1, 1]
    assert {card["pekerja_unik"].split(":", 1)[0] for card in cards} == {"p14", "p24"}


def test_worker_evidence_groups_do_not_mix_simulation_workers_with_same_base_person_key():
    t0 = datetime(2026, 9, 30, 11, 0)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # Data historis bisa membawa person_key dasar yang sama saat tracker/state salah reuse ID,
        # tetapi label worker yang terlihat berbeda jauh (W014 vs W351) tidak boleh dicampur.
        db.add(Violation(session="sim-a", camera_id="LiveCAM-01", source_kind="simulasi",
                         zone="Area Produksi", track_id=14, person_key="p14:restart-a",
                         started_at=t0, missing_items="helmet", evidence_crop="w14.jpg"))
        db.add(Violation(session="sim-b", camera_id="LiveCAM-01", source_kind="simulasi",
                         zone="Area Produksi", track_id=351, person_key="p14:restart-b",
                         started_at=t0 + timedelta(seconds=1), missing_items="helmet",
                         evidence_crop="w351.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1),
                                    camera_id="LiveCAM-01", source="semua")
        total, cards = worker_evidence_groups(raw_rows, limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert total == 2
    worker_sets = [{event["pekerja"] for event in card["event_bukti"]} for card in cards]
    assert {frozenset(workers) for workers in worker_sets} == {frozenset({"W014"}), frozenset({"W351"})}


def test_worker_evidence_groups_do_not_mix_overlapping_simulation_tracker_fragments():
    t0 = datetime(2026, 10, 13, 15, 39, 27)
    worker_ids = [39, 41, 55, 63, 72, 90, 131, 137, 562]
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        for idx, wid in enumerate(worker_ids):
            start = t0 + timedelta(seconds=idx * 12)
            db.add(Violation(session="sim-overlap", camera_id="LiveCAM-01", source_kind="simulasi",
                             zone="Area Produksi", track_id=wid, person_key=f"p{wid}:fragment",
                             started_at=start, ended_at=start + timedelta(seconds=120), duration_s=120,
                             missing_items="helmet", evidence_crop=f"w{wid}.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=5),
                                    camera_id="LiveCAM-01", source="semua")
        total, cards = worker_evidence_groups(raw_rows, limit=10)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()

    assert total == len(worker_ids)
    assert [card["jumlah_event_mentah"] for card in cards] == [1] * len(worker_ids)
    assert all(len({event["pekerja"] for event in card["event_bukti"]}) == 1 for card in cards)


def test_worker_evidence_groups_do_not_mix_large_simulation_burst_with_same_signature():
    t0 = datetime(2026, 10, 13, 15, 39, 27)
    worker_ids = [39, 41, 55, 63, 72, 90, 131, 137, 562]
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        for idx, wid in enumerate(worker_ids):
            start = t0 + timedelta(seconds=idx * 5)
            db.add(Violation(session="sim-burst", camera_id="LiveCAM-01", source_kind="simulasi",
                             zone="Area Produksi", track_id=wid, person_key=f"p{wid}:fragment",
                             started_at=start, ended_at=start + timedelta(seconds=2), duration_s=2,
                             missing_items="helmet", evidence_crop=f"w{wid}.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=2),
                                    camera_id="LiveCAM-01", source="semua")
        total, cards = worker_evidence_groups(raw_rows, limit=10)
        c = count_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=2),
                             item="helmet", camera_id="LiveCAM-01", source="semua")
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()

    assert total == len(worker_ids)
    assert [card["jumlah_event_mentah"] for card in cards] == [1] * len(worker_ids)
    assert all(len({event["pekerja"] for event in card["event_bukti"]}) == 1 for card in cards)
    assert c["jumlah_pekerja_visual"] == len(worker_ids)
    assert c["indikasi_fragmentasi_tracking"] is False


def test_worker_evidence_groups_merge_same_simulation_worker_across_replay_sessions():
    t0 = datetime(2026, 9, 30, 11, 1)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # Worker visual p14 muncul berulang setelah replay/restart dengan suffix sesi berbeda.
        # Worker p22 tetap kartu terpisah agar UI tidak mencampur dua orang utama.
        for loop in range(4):
            db.add(Violation(session=f"sim-{loop}", camera_id="LiveCAM-01", source_kind="simulasi",
                             track_id=14, person_key=f"p14:restart-{loop}",
                             started_at=t0 + timedelta(minutes=loop * 3),
                             missing_items="helmet", evidence_crop=f"p14-{loop}.jpg"))
            db.add(Violation(session=f"sim-{loop}", camera_id="LiveCAM-01", source_kind="simulasi",
                             track_id=22, person_key=f"p22:restart-{loop}",
                             started_at=t0 + timedelta(minutes=loop * 3, seconds=20),
                             missing_items="vest", evidence_crop=f"p22-{loop}.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=12),
                                    camera_id="LiveCAM-01", source="semua")
        total, cards = worker_evidence_groups(raw_rows, limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert total == 2
    assert [card["jumlah_event_mentah"] for card in cards] == [4, 4]
    assert [len(card["event_bukti"]) for card in cards] == [4, 4]


def test_worker_evidence_groups_keep_normal_gap_for_real_live_data():
    t0 = datetime(2026, 9, 30, 10, 57)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        for idx in range(4):
            db.add(Violation(session=f"live-{idx}", camera_id="CAM-01", source_kind="live",
                             track_id=idx + 1, person_key=f"p{idx}",
                             started_at=t0 + timedelta(seconds=idx * 30),
                             missing_items="helmet", evidence_crop=f"live-{idx}.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=3),
                                    camera_id="CAM-01", source="semua")
        total, cards = worker_evidence_groups(raw_rows, limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert total == 4
    assert [card["jumlah_event_mentah"] for card in cards] == [1, 1, 1, 1]


def test_count_violations_uses_visual_cluster_when_tracking_is_fragmented():
    t0 = datetime(2026, 9, 30, 10, 55)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        # Satu pekerja visual terpecah menjadi tiga person_key dalam beberapa detik yang sama.
        # Event jauh lebih banyak daripada identitas tracking, sehingga agent boleh memakai
        # klaster visual sebagai angka tampilan agar tidak menjawab 9 orang.
        for idx in range(9):
            db.add(Violation(session=f"s-{idx}", camera_id="cam7", track_id=idx + 1,
                             person_key=f"p{idx % 3}", started_at=t0 + timedelta(seconds=idx % 4),
                             missing_items="helmet", evidence_crop=f"frag-{idx}.jpg"))
        db.commit()
        c = count_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), item="helmet", camera_id="cam7")
        rows = evidence_list(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), item="helmet", camera_id="cam7", limit=6)
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert c["jumlah_event_pelanggaran"] == 9
    assert c["jumlah_orang_unik"] == 3
    assert c["jumlah_pekerja_visual"] == 1
    assert c["indikasi_fragmentasi_tracking"] is True
    assert len(rows) == 1


def test_overlapping_live_worker_ids_are_not_mixed_in_one_evidence_card():
    t0 = datetime(2026, 10, 13, 15, 39, 27)
    worker_ids = [39, 41, 55, 63, 72, 90, 131, 137, 562]
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        for idx, wid in enumerate(worker_ids):
            start = t0 + timedelta(seconds=idx * 12)
            db.add(Violation(session=f"live-{wid}", camera_id="LiveCAM-01", source_kind="live",
                             zone="Area Produksi", track_id=wid, worker_id=wid,
                             person_key=f"p{wid}:fragment", started_at=start,
                             ended_at=start + timedelta(seconds=120), duration_s=120,
                             missing_items="helmet", evidence_crop=f"w{wid}.jpg"))
        db.commit()
        raw_rows = fetch_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=5),
                                    camera_id="LiveCAM-01", source="semua")
        total, cards = worker_evidence_groups(raw_rows, limit=10)
        c = count_violations(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=5),
                             item="helmet", camera_id="LiveCAM-01", source="semua")
        s = summary(db, t0 - timedelta(minutes=1), t0 + timedelta(minutes=5), source="semua",
                    camera_ids=["LiveCAM-01"])
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()

    assert total == len(worker_ids)
    assert [card["jumlah_event_mentah"] for card in cards] == [1] * len(worker_ids)
    assert all(len({event["pekerja"] for event in card["event_bukti"]}) == 1 for card in cards)
    assert all(card["indikasi_fragmentasi_tracking"] is True for card in cards)
    assert c["jumlah_orang_unik"] == len(worker_ids)
    assert c["jumlah_pekerja_visual"] == 1
    assert c["indikasi_fragmentasi_tracking"] is True
    assert s["per_kamera"] == [{"kamera": "LiveCAM-01", "jumlah": 1, "event": len(worker_ids)}]
    assert s["per_zona"] == [{"zona": "Area Produksi", "jumlah": 1, "event": len(worker_ids)}]


def test_summary_can_be_filtered_by_camera_group():
    t0 = datetime(2026, 9, 30, 11, 0)
    with Session(engine) as db:
        for table in (Violation, TrackRecord, AuditLog):
            db.exec(delete(table))
        db.add(TrackRecord(session="s-a", camera_id="CAM-A", track_id=1, first_seen=t0, last_seen=t0,
                           final_status="violation", missing_items="helmet"))
        db.add(TrackRecord(session="s-b", camera_id="CAM-B", track_id=1, first_seen=t0, last_seen=t0,
                           final_status="compliant", missing_items=""))
        db.add(TrackRecord(session="s-c", camera_id="CAM-C", track_id=1, first_seen=t0, last_seen=t0,
                           final_status="violation", missing_items="gloves"))
        db.add(Violation(session="s-a", camera_id="CAM-A", track_id=1, started_at=t0, missing_items="helmet"))
        db.add(Violation(session="s-c", camera_id="CAM-C", track_id=1, started_at=t0, missing_items="gloves"))
        db.commit()
        s = summary(db, t0 - timedelta(hours=1), t0 + timedelta(hours=1), camera_ids=["CAM-A", "CAM-B"])
        for table in (Violation, TrackRecord):
            db.exec(delete(table))
        db.commit()
    assert s["pekerja_terpantau"] == 2
    assert s["pekerja_patuh"] == 1
    assert s["event_pelanggaran"] == 1
    assert {x["kamera"] for x in s["per_kamera"]} == {"CAM-A"}


def test_detector_passes_iou_and_quantize_kwargs():
    import inspect

    from pipeline.detector import Detector
    src = inspect.getsource(Detector.batch_predict)
    assert "iou=self.pose_iou" in src and "iou=self.ppe_iou" in src
    assert src.count("quantize=16 if self.half else None") == 2


def test_alert_cooldown():
    c = Cooldown(60)
    assert c.ready(("CAM-01", 7)) and not c.ready(("CAM-01", 7)) and c.ready(("CAM-01", 8))


def test_violation_dict_exposes_real_detection_metadata_and_legacy_fallback():
    now = datetime(2026, 10, 15, 8)
    old = Violation(session="s", camera_id="C", track_id=1, started_at=now)
    assert violation_dict(old)["foto_clean"] is None
    assert violation_dict(old)["deteksi"] is None

    data = {"frame": {"width": 800, "height": 600}, "worker": {"box": {"x": 25, "y": 10, "w": 15, "h": 60}, "confidence": 0.9}, "items": []}
    newer = Violation(session="s", camera_id="C", track_id=2, started_at=now,
                      evidence_full="a_full.jpg", evidence_clean="a_clean.jpg",
                      evidence_sha256="abc", detections_json=json.dumps(data))
    result = violation_dict(newer)
    assert result["foto_clean"] == "/evidence/a_clean.jpg"
    assert result["deteksi"] == data
    assert result["foto"] == "/evidence/a_full.jpg"
    assert result["sha256"] == "abc"


def test_clean_evidence_has_no_annotations_and_keeps_annotated_hash(tmp_path):
    from pipeline.evidence import sha256_file

    rng = np.random.default_rng(3)
    frame = rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)
    when = datetime(2026, 10, 15, 8)
    saved = save_evidence(frame, tmp_path, "CAM-01", 7, when, (100, 20, 200, 220),
                          "violation", {"helmet": "missing"}, [(120, 20, 170, 75)])
    clean = cv2.imread(str(tmp_path / saved.clean_path))
    annotated = cv2.imread(str(tmp_path / saved.full_path))
    assert clean is not None and annotated is not None
    assert not np.array_equal(clean, annotated)
    assert not np.array_equal(clean[20:75, 120:170], frame[20:75, 120:170])
    assert sha256_file(tmp_path / saved.full_path) == saved.sha256


def test_evidence_is_never_overwritten(tmp_path):
    frame = np.zeros((200, 200, 3), np.uint8)
    when = datetime(2026, 10, 1, 8, 0, 0)
    a = save_evidence(frame, tmp_path, "CAM-01", 7, when, (10, 10, 100, 190), "violation", {}, [])
    b = save_evidence(frame + 50, tmp_path, "CAM-01", 7, when, (10, 10, 100, 190), "violation", {}, [])
    assert a.full_path != b.full_path and a.sha256 != b.sha256
    assert a.clean_path != b.clean_path
    assert (tmp_path / a.clean_path).exists() and (tmp_path / b.clean_path).exists()


def test_video_time_clock():
    cfg = copy.deepcopy(load_config())
    cfg["camera"]["start_time"] = "2026-09-30T08:00"
    cam = CameraSession("CAM-01", cfg, None, LiveState(), base_dir=BASE_DIR)
    cam.src_fps = 10
    assert cam._now(150) == datetime(2026, 9, 30, 8, 0, 15)


def test_rtsp_is_not_looped_or_throttled():
    cfg = copy.deepcopy(load_config())
    cfg["camera"]["source"] = "rtsp://cam/stream"
    cfg["camera"]["loop"] = True
    cfg["camera"]["realtime"] = True
    reader = CameraReader("CAM-01", cfg["camera"]["source"],
                          loop=cfg["camera"]["loop"], realtime=cfg["camera"]["realtime"])
    # URL stream: loop/realtime tidak berlaku walaupun di-set (dicek saat baca)
    assert reader.is_stream


def test_corrupt_file_does_not_spin_forever(tmp_path):
    bad = tmp_path / "rusak.mp4"
    bad.write_bytes(b"bukan video")
    reader = CameraReader("CAM-01", str(bad), loop=True)
    reader.start()
    reader.thread.join(timeout=10)  # file rusak: thread harus keluar, bukan berputar selamanya
    assert not reader.thread.is_alive()


def test_latest_frame_does_not_consume_scheduler_frame():
    reader = CameraReader("CAM-01", "0")
    frame = np.full((4, 4, 3), 7, np.uint8)
    with reader._lock:
        reader._frame = frame
        reader._latest_frame = frame

    latest = reader.latest()
    assert latest is not None and int(latest[0, 0, 0]) == 7
    latest[0, 0, 0] = 99

    scheduled = reader.read()
    assert scheduled is frame
    assert int(scheduled[0, 0, 0]) == 7


def test_stream_interval_uses_24_fps_target(monkeypatch):
    monkeypatch.setattr(main_mod, "load_config", lambda: {"stream": {"target_fps": 24}})
    assert main_mod._stream_interval_s() == 1 / 24


def test_live_display_uses_raw_frame_when_overlay_is_stale():
    cfg = copy.deepcopy(load_config())
    cfg["stream"] = {"target_fps": 24, "overlay_max_age_frames": 0, "display_raw_when_stale": True}
    cfg["evidence"]["blur_faces"] = False  # tanpa blur, frame mentah 24fps boleh diterbitkan
    live = LiveState()
    cam = CameraSession("CAM-01", cfg, None, live, base_dir=BASE_DIR)
    frame = np.zeros((8, 8, 3), np.uint8)
    calls = []

    class FakeReader:
        fps = 24.0
        frames_total = 2
        last_read_seq = 1

        def latest_with_seq(self):
            cam._display_stop.set()
            return frame, 2

    cam.reader = FakeReader()
    cam._display_stop = threading.Event()
    cam._last_overlay_seq = 1
    cam._render_jpeg = lambda *a, **k: calls.append("overlay") or b"overlay"
    cam._encode_raw_jpeg = lambda *a, **k: calls.append("raw") or b"raw"
    cam._encode_clean_jpeg = lambda *a, **k: b"clean"

    cam._display_loop()

    assert calls == ["raw"]
    assert live.jpeg == b"raw"
    assert live.clean_jpeg == b"clean"


def test_live_display_skips_fresh_frame_when_blur_on_to_avoid_leak():
    # Saat blur wajah aktif, posisi wajah hanya diketahui pada frame inferensi. Menerbitkan frame
    # kamera baru dengan posisi blur lama akan membocorkan wajah yang sudah berpindah, jadi
    # _display_loop tidak boleh menerbitkan apa pun; stream mengikuti _publish (sinkron inferensi).
    cfg = copy.deepcopy(load_config())
    cfg["stream"] = {"target_fps": 24, "overlay_max_age_frames": 0, "display_raw_when_stale": True}
    cfg["evidence"]["blur_faces"] = True
    live = LiveState()
    cam = CameraSession("CAM-01", cfg, None, live, base_dir=BASE_DIR)
    frame = np.zeros((8, 8, 3), np.uint8)
    calls = []

    class FakeReader:
        fps = 24.0
        frames_total = 2
        last_read_seq = 1

        def latest_with_seq(self):
            cam._display_stop.set()
            return frame, 2

    cam.reader = FakeReader()
    cam._display_stop = threading.Event()
    cam._last_overlay_seq = 1
    cam._encode_raw_jpeg = lambda *a, **k: calls.append("raw") or b"raw"
    cam._encode_clean_jpeg = lambda *a, **k: calls.append("clean") or b"clean"
    # Saat blur aktif, loop melewati reader; hentikan setelah satu iterasi lewat wait().
    cam._display_stop.wait = lambda *a, **k: cam._display_stop.set()

    cam._display_loop()

    assert calls == []
    assert live.jpeg is None
    assert live.clean_jpeg is None


def test_live_display_uses_held_overlay_when_overlay_is_fresh():
    cfg = copy.deepcopy(load_config())
    cfg["stream"] = {"target_fps": 24, "overlay_max_age_frames": 3, "display_raw_when_stale": True}
    cfg["evidence"]["blur_faces"] = False
    cfg["zones"] = []
    live = LiveState()
    cam = CameraSession("CAM-01", cfg, None, live, base_dir=BASE_DIR)
    frame = np.zeros((80, 120, 3), np.uint8)
    person = make_person(7, x=20)

    class FakeReader:
        fps = 24.0
        frames_total = 2
        last_read_seq = 1

        def latest_with_seq(self):
            cam._display_stop.set()
            return frame, 2

    class Track:
        track_id = 7
        box = person.box
        stable_status = "violation"
        last_frame = 0
        def checklist(self):
            return {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    cam.reader = FakeReader()
    cam._display_stop = threading.Event()
    with cam._render_lock:
        cam.manager.tracks = {7: Track()}
        cam.frame_idx = 0
        cam._refresh_recorded_overlay_cache([person])
        cam._last_overlay_seq = 1

    cam._display_loop()

    assert live.jpeg is not None
    assert live.jpeg != b"raw"


def test_recorded_video_holds_last_overlay_between_inference_frames():
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["blur_faces"] = False
    cfg["zones"] = []
    cam = CameraSession("CAM-REC", cfg, None, LiveState("CAM-REC"), base_dir=BASE_DIR)
    frame = np.zeros((120, 160, 3), np.uint8)
    person = make_person(7, x=30)

    class Track:
        track_id = 7
        box = person.box
        stable_status = "violation"
        def checklist(self):
            return {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    with cam._render_lock:
        cam._last_people = [person]
        cam._held_overlay_people = [person]
        cam._last_items = []
        cam.manager.tracks = {7: Track()}

    raw = cam.render_raw_frame(frame)
    held = cam.render_held_overlay_frame(frame)

    assert np.count_nonzero(raw) == 0
    assert np.count_nonzero(held) > 0


def test_recorded_clip_jpeg_uses_held_overlay_snapshot():
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["blur_faces"] = False
    cfg["evidence"]["clip"]["enabled"] = False
    cfg["zones"] = []

    class Sink:
        source_kind = "rekaman"

    cam = CameraSession("CAM-REC", cfg, Sink(), LiveState("CAM-REC"), base_dir=BASE_DIR)
    frame = np.zeros((120, 160, 3), np.uint8)
    person = make_person(7, x=30)

    class Track:
        track_id = 7
        box = person.box
        stable_status = "violation"
        last_frame = 0
        def checklist(self):
            return {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    with cam._render_lock:
        cam.manager.tracks = {7: Track()}
        cam._refresh_recorded_overlay_cache([person])
        jpeg = cam._render_jpeg(frame, [], [])

    import cv2
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert np.count_nonzero(decoded) > 0


def test_recorded_overlay_survives_short_empty_inference():
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["blur_faces"] = False
    cfg["zones"] = []
    cfg.setdefault("overlay", {})["recorded_hold_inference_frames"] = 2
    cfg["overlay"]["recorded_hold_seconds"] = 1.0
    cam = CameraSession("CAM-REC", cfg, None, LiveState("CAM-REC"), base_dir=BASE_DIR)
    cam.set_recorded_output_fps(25)
    frame = np.zeros((120, 160, 3), np.uint8)
    person = make_person(7, x=30)

    class Track:
        track_id = 7
        box = person.box
        stable_status = "violation"
        last_frame = 0
        def checklist(self):
            return {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    with cam._render_lock:
        cam.manager.tracks = {7: Track()}
        cam.frame_idx = 0
        cam._refresh_recorded_overlay_cache([person])

    cam.frame_idx = 1
    cam._publish(frame, [], [])
    held = cam.render_held_overlay_frame(frame)

    assert np.count_nonzero(held) > 0

    cam.frame_idx = 26
    cam._publish(frame, [], [])
    expired = cam.render_held_overlay_frame(frame)

    assert np.count_nonzero(expired) == 0


def test_recorded_overlay_holds_after_tracker_temporarily_drops_track():
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["blur_faces"] = False
    cfg["zones"] = []
    cfg.setdefault("overlay", {})["recorded_hold_inference_frames"] = 2
    cfg["overlay"]["recorded_hold_seconds"] = 3.0
    cam = CameraSession("CAM-REC", cfg, None, LiveState("CAM-REC"), base_dir=BASE_DIR)
    cam.set_recorded_output_fps(25)
    frame = np.zeros((120, 160, 3), np.uint8)
    person = make_person(7, x=30)

    class Track:
        track_id = 7
        box = person.box
        stable_status = "violation"
        last_frame = 0
        def checklist(self):
            return {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    with cam._render_lock:
        cam.manager.tracks = {7: Track()}
        cam.frame_idx = 0
        cam._refresh_recorded_overlay_cache([person])

    with cam._render_lock:
        cam.manager.tracks = {}
        cam.frame_idx = 40
        cam._refresh_recorded_overlay_cache([])
    held = cam.render_held_overlay_frame(frame)

    assert np.count_nonzero(held) > 0

    with cam._render_lock:
        cam.frame_idx = 80
        cam._refresh_recorded_overlay_cache([])
    expired = cam.render_held_overlay_frame(frame)

    assert np.count_nonzero(expired) == 0


def test_recorded_overlay_holds_stale_track_until_seconds_limit():
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["blur_faces"] = False
    cfg["zones"] = []
    cfg.setdefault("overlay", {})["recorded_hold_inference_frames"] = 2
    cfg["overlay"]["recorded_hold_seconds"] = 3.0
    cam = CameraSession("CAM-REC", cfg, None, LiveState("CAM-REC"), base_dir=BASE_DIR)
    cam.set_recorded_output_fps(25)
    frame = np.zeros((120, 160, 3), np.uint8)
    person = make_person(7, x=30)

    class Track:
        track_id = 7
        box = person.box
        stable_status = "violation"
        last_frame = 0
        def checklist(self):
            return {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    with cam._render_lock:
        cam.manager.tracks = {7: Track()}
        cam.frame_idx = 0
        cam._refresh_recorded_overlay_cache([person])

    with cam._render_lock:
        cam.frame_idx = 40
        cam._refresh_recorded_overlay_cache([])
    held = cam.render_held_overlay_frame(frame)

    assert np.count_nonzero(held) > 0

    with cam._render_lock:
        cam.frame_idx = 80
        cam._refresh_recorded_overlay_cache([])
    expired = cam.render_held_overlay_frame(frame)

    assert np.count_nonzero(expired) == 0


def test_recorded_overlay_uses_pose_candidate_when_tracker_temporarily_returns_no_track():
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["blur_faces"] = False
    cfg["zones"] = []
    cfg.setdefault("overlay", {})["recorded_hold_inference_frames"] = 2
    cfg["overlay"]["recorded_hold_seconds"] = 3.0
    cfg["overlay"]["recorded_smooth_alpha"] = 1.0
    cam = CameraSession("CAM-REC", cfg, None, LiveState("CAM-REC"), base_dir=BASE_DIR)
    cam.set_recorded_output_fps(25)
    frame = np.zeros((120, 220, 3), np.uint8)
    person = make_person(7, x=30)
    candidate = make_person(-1, x=45)

    class Track:
        track_id = 7
        box = person.box
        stable_status = "violation"
        last_frame = 0
        def checklist(self):
            return {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    with cam._render_lock:
        cam.manager.tracks = {7: Track()}
        cam.frame_idx = 0
        cam._refresh_recorded_overlay_cache([person])

    with cam._render_lock:
        cam.manager.tracks = {}
        cam.frame_idx = 1
        cam._refresh_recorded_overlay_cache([], [candidate])

    assert set(cam._recorded_overlay_snapshots) == {7}
    assert cam._recorded_overlay_snapshots[7]["last_seen_frame"] == 1
    assert cam._recorded_overlay_snapshots[7]["box"] == candidate.box
    held = cam.render_held_overlay_frame(frame)
    assert np.count_nonzero(held) > 0


def test_recorded_overlay_keeps_missing_track_during_partial_inference():
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["blur_faces"] = False
    cfg["zones"] = []
    cfg.setdefault("overlay", {})["recorded_hold_inference_frames"] = 2
    cam = CameraSession("CAM-REC", cfg, None, LiveState("CAM-REC"), base_dir=BASE_DIR)
    p1 = make_person(1, x=20)
    p2 = make_person(2, x=220)

    class Track:
        def __init__(self, person):
            self.track_id = person.track_id
            self.box = person.box
            self.conf = person.conf
            self.stable_status = "violation"
            self.last_frame = 0
        def checklist(self):
            return {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    with cam._render_lock:
        cam.manager.tracks = {1: Track(p1), 2: Track(p2)}
        cam.frame_idx = 0
        cam._refresh_recorded_overlay_cache([p1, p2])

    with cam._render_lock:
        cam.frame_idx = 1
        cam.manager.tracks[1].last_frame = 1
        cam.manager.tracks[2].last_frame = 0
        cam._refresh_recorded_overlay_cache([p1])

    assert set(cam._recorded_overlay_snapshots) == {1, 2}

    with cam._render_lock:
        cam.frame_idx = 3
        cam.manager.tracks[1].last_frame = 3
        cam._refresh_recorded_overlay_cache([p1])

    assert set(cam._recorded_overlay_snapshots) == {1, 2}

    with cam._render_lock:
        cam.frame_idx = 80
        cam.manager.tracks[1].last_frame = 80
        cam._refresh_recorded_overlay_cache([p1])

    assert set(cam._recorded_overlay_snapshots) == {1}


def test_recorded_overlay_smooths_box_and_reuses_snapshot_when_track_id_changes():
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["blur_faces"] = False
    cfg["zones"] = []
    cfg.setdefault("overlay", {})["recorded_hold_inference_frames"] = 5
    cfg["overlay"]["recorded_smooth_alpha"] = 0.5
    cfg["overlay"]["recorded_match_min_iou"] = 0.1
    cfg["overlay"]["recorded_match_max_center_dist"] = 1.0
    cam = CameraSession("CAM-REC", cfg, None, LiveState("CAM-REC"), base_dir=BASE_DIR)
    p_old = make_person(7, x=20)
    p_new = make_person(8, x=40)

    class Track:
        def __init__(self, person, last_frame):
            self.track_id = person.track_id
            self.box = person.box
            self.conf = person.conf
            self.stable_status = "violation"
            self.last_frame = last_frame
        def checklist(self):
            return {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    with cam._render_lock:
        cam.manager.tracks = {7: Track(p_old, 0)}
        cam.frame_idx = 0
        cam._refresh_recorded_overlay_cache([p_old])

    with cam._render_lock:
        cam.manager.tracks = {8: Track(p_new, 1)}
        cam.frame_idx = 1
        cam._refresh_recorded_overlay_cache([p_new])

    assert set(cam._recorded_overlay_snapshots) == {8}
    smoothed_box = cam._recorded_overlay_snapshots[8]["box"]
    assert smoothed_box[0] == 30
    assert smoothed_box[2] == 150
