from datetime import datetime, timedelta

import numpy as np

from pipeline.state import ActiveEvent, HysteresisConfig, StateManager
from pipeline.types import ITEMS, Evidence, Person
from pipeline.zones import Zone, point_in_polygon, zone_for

CFG = HysteresisConfig()
T0 = datetime(2026, 10, 1, 8, 0, 0)
ZONE = {1: ("Area", ITEMS)}


def run(manager, frames, values, start=0):
    results = []
    for f in range(start, start + frames):
        p = Person(1, (0, 0, 10, 10), 0.9)
        results.append(manager.step(f, T0 + timedelta(seconds=f / 10), [p], {1: Evidence(dict(values))}, ZONE))
    return results


FULL = {i: 0.8 for i in ITEMS}


def _person_with_keypoints(track_id: int, box=(0, 0, 100, 300)) -> Person:
    keypoints = np.zeros((17, 3))
    for idx, xy in {
        0: (50, 30),
        5: (25, 90),
        6: (75, 90),
        11: (35, 210),
        12: (65, 210),
    }.items():
        keypoints[idx] = (*xy, 0.9)
    return Person(track_id, box, 0.9, keypoints)


def test_compliant_person_opens_no_event():
    m = StateManager(CFG)
    res = run(m, 40, FULL)
    assert not any(r.opened for r in res)
    assert m.tracks[1].stable_status == "compliant"


def test_single_noisy_frame_does_not_trigger_violation():
    m = StateManager(CFG)
    run(m, 20, FULL)
    res = run(m, 1, {**FULL, "helmet": -1.0}, start=20) + run(m, 20, FULL, start=21)
    assert not any(r.opened for r in res)


def test_sustained_missing_helmet_opens_then_closes_event():
    m = StateManager(CFG)
    run(m, 20, FULL)
    res = run(m, 30, {**FULL, "helmet": -1.0}, start=20)
    opened = [t for r in res for t in r.opened]
    assert len(opened) == 1 and opened[0].event.kind == "violation"
    assert opened[0].event.missing == {"helmet"}
    res = run(m, 40, FULL, start=50)
    assert sum(len(r.closed) for r in res) == 1


def test_short_detection_recovery_does_not_reopen_same_violation():
    """Pelanggaran aktif tidak boleh jadi banyak evidence hanya karena deteksi pulih sebentar."""
    cfg = HysteresisConfig(event_close_grace_frames=12)
    m = StateManager(cfg)
    run(m, 20, FULL)
    res = run(m, 30, {**FULL, "helmet": -1.0}, start=20)
    assert sum(len(r.opened) for r in res) == 1
    assert m.tracks[1].event and m.tracks[1].event.kind == "violation"

    # Status sempat pulih lebih pendek dari grace period, lalu pelanggaran muncul lagi.
    res = run(m, 5, FULL, start=50)
    assert not any(r.closed for r in res)
    assert m.tracks[1].event and m.tracks[1].event.kind == "violation"
    res = run(m, 20, {**FULL, "helmet": -1.0}, start=55)
    assert not any(r.opened for r in res)
    assert m.tracks[1].event and m.tracks[1].event.kind == "violation"


def test_non_evaluated_frames_only_update_track_position():
    """Frame pose tanpa evaluasi APD tidak boleh mengubah status atau menutup event."""
    m = StateManager(CFG)
    run(m, 20, FULL)
    run(m, 30, {**FULL, "helmet": -1.0}, start=20)
    t = m.tracks[1]
    assert t.stable_status == "violation"
    assert t.event and t.event.kind == "violation"
    before_score = t.items["helmet"].score
    before_evaluations = t.items["helmet"].evaluations
    before_without_evidence = t.items["helmet"].frames_without_evidence

    for f in range(50, 80):
        p = Person(1, (f, 0, f + 10, 10), 0.9)
        res = m.step(f, T0 + timedelta(seconds=f / 10), [p], {}, ZONE, evaluated=False)
        assert not res.opened
        assert not res.closed

    t = m.tracks[1]
    assert t.box == (79, 0, 89, 10)
    assert t.stable_status == "violation"
    assert t.event and t.event.kind == "violation"
    assert t.items["helmet"].score == before_score
    assert t.items["helmet"].evaluations == before_evaluations
    assert t.items["helmet"].frames_without_evidence == before_without_evidence


def test_hidden_hands_become_uncertain_not_violation():
    m = StateManager(CFG)
    res = run(m, 120, {**FULL, "gloves": None})
    kinds = [t.event.kind for r in res for t in r.opened]
    assert kinds == ["uncertain"]


def test_not_required_items_are_ignored():
    m = StateManager(CFG)
    for f in range(40):
        m.step(f, T0, [Person(1, (0, 0, 10, 10), 0.9)],
               {1: Evidence({**FULL, "gloves": -1.0, "shoes": -1.0})},
               {1: ("Gudang", ("helmet", "vest"))})
    t = m.tracks[1]
    assert t.stable_status == "compliant"
    assert t.checklist()["gloves"] == "not_required"
    assert t.checklist()["shoes"] == "not_required"


def test_lost_track_is_reported_and_closes_event():
    m = StateManager(CFG)
    run(m, 20, {**FULL, "vest": -1.0})
    res = m.step(100, T0, [], {}, {})
    assert len(res.lost) == 1 and res.lost[0].worst_status == "violation"
    assert len(res.closed) == 1


def test_reentry_reuses_person_key_within_gap():
    cfg = HysteresisConfig(reentry_enabled=True, track_lost_frames=30, reentry_max_gap_frames=90)
    m = StateManager(cfg)
    run(m, 20, FULL)
    key = m.tracks[1].person_key
    assert key
    # track hilang (lewat track_lost_frames -> masuk recent_lost), lalu ID baru muncul di posisi serupa
    m.step(60, T0 + timedelta(seconds=6), [], {}, {})
    assert 1 not in m.tracks
    p = Person(55, (0, 0, 10, 10), 0.9)
    m.step(70, T0 + timedelta(seconds=7), [p], {55: Evidence(dict(FULL))}, {55: ("Area", ITEMS)})
    assert m.tracks[55].track_id == 55
    assert m.tracks[55].worker_id == 1
    assert m.tracks[55].worker_label() == "W001"
    assert m.tracks[55].person_key == key


def test_reentry_reuses_active_inactive_track_before_lost():
    cfg = HysteresisConfig(reentry_enabled=True, track_lost_frames=30, reentry_max_gap_frames=90)
    m = StateManager(cfg)
    run(m, 20, FULL)
    key = m.tracks[1].person_key
    # ID baru muncul di posisi serupa sebelum track lama melewati track_lost_frames.
    p = Person(55, (1, 0, 11, 10), 0.9)
    m.step(25, T0 + timedelta(seconds=2.5), [p], {55: Evidence(dict(FULL))}, {55: ("Area", ITEMS)})
    assert 1 not in m.tracks
    assert m.tracks[55].track_id == 55
    assert m.tracks[55].worker_id == 1
    assert m.tracks[55].worker_label() == "W001"
    assert m.tracks[55].person_key == key


def test_reentry_merges_duplicate_active_track_in_same_frame():
    """Jika ByteTrack mengembalikan ID lama dan baru untuk box yang sama, pakai worker lama."""
    cfg = HysteresisConfig(reentry_enabled=True, duplicate_active_min_iou=0.80, duplicate_active_max_center_dist=0.25)
    m = StateManager(cfg)
    run(m, 20, FULL)
    key = m.tracks[1].person_key

    old = Person(1, (0, 0, 10, 10), 0.9)
    new = Person(55, (0.2, 0, 10.2, 10), 0.9)
    m.step(21, T0 + timedelta(seconds=2.1), [old, new],
           {1: Evidence(dict(FULL)), 55: Evidence(dict(FULL))},
           {1: ("Area", ITEMS), 55: ("Area", ITEMS)})

    assert 1 not in m.tracks
    assert m.tracks[55].track_id == 55
    assert m.tracks[55].worker_id == 1
    assert m.tracks[55].worker_label() == "W001"
    assert m.tracks[55].person_key == key


def test_reentry_merges_same_keypoints_even_when_bbox_changes_by_occlusion():
    """ID baru orang yang sama tidak boleh menjadi W baru saat bbox berubah karena tertutup."""
    cfg = HysteresisConfig(reentry_enabled=True, duplicate_active_min_iou=0.80, duplicate_active_max_center_dist=0.25)
    m = StateManager(cfg)
    for f in range(20):
        p = _person_with_keypoints(1)
        m.step(f, T0 + timedelta(seconds=f / 10), [p], {1: Evidence(dict(FULL))}, {1: ("Area", ITEMS)})
    key = m.tracks[1].person_key

    old = _person_with_keypoints(1)
    # Bbox bergeser/berubah cukup jauh sehingga tidak lolos ambang duplicate bbox,
    # tetapi keypoint yang terlihat masih sama: ini harus dianggap orang lama.
    new = _person_with_keypoints(55, box=(35, 0, 135, 300))
    m.step(21, T0 + timedelta(seconds=2.1), [old, new],
           {1: Evidence(dict(FULL)), 55: Evidence(dict(FULL))},
           {1: ("Area", ITEMS), 55: ("Area", ITEMS)})

    assert 1 not in m.tracks
    assert m.tracks[55].track_id == 55
    assert m.tracks[55].worker_id == 1
    assert m.tracks[55].worker_label() == "W001"
    assert m.tracks[55].person_key == key


def test_reentry_collapses_existing_parallel_ids_when_keypoints_match():
    """ID duplikat yang sudah terlanjur aktif harus kembali ke worker lama saat pose cocok."""
    cfg = HysteresisConfig(reentry_enabled=True, duplicate_active_min_iou=0.80, duplicate_active_max_center_dist=0.25)
    m = StateManager(cfg)
    p_old = _person_with_keypoints(24)
    p_dup = _person_with_keypoints(37, box=(35, 0, 135, 300))

    for f in range(20):
        m.step(f, T0 + timedelta(seconds=f / 10), [p_old],
               {24: Evidence(dict(FULL))}, {24: ("Area", ITEMS)})
    key = m.tracks[24].person_key

    # Simulasikan celah runtime lama: ID 37 sudah terlanjur dibuat sebagai pekerja baru.
    m.tracks[37] = m.tracks[24].__class__(37, T0 + timedelta(seconds=2), T0 + timedelta(seconds=2),
                                          20, p_dup.box, p_dup.conf, p_dup.keypoints,
                                          worker_id=37, person_key="p37:duplikat", zone="Area")
    m.tracks[37].event = ActiveEvent("violation", T0 + timedelta(seconds=2), {"helmet"}, set(), 123)

    res = m.step(21, T0 + timedelta(seconds=2.1), [p_old, p_dup],
                 {24: Evidence(dict(FULL)), 37: Evidence(dict(FULL))},
                 {24: ("Area", ITEMS), 37: ("Area", ITEMS)})

    assert 37 not in m.tracks
    assert [(event.db_id, track.worker_id) for track, event, _ in res.closed] == [(123, 37)]
    assert m.tracks[24].worker_id == 24
    assert m.tracks[24].worker_label() == "W024"
    assert m.tracks[24].person_key == key


def test_reentry_new_person_after_long_gap():
    cfg = HysteresisConfig(reentry_enabled=True, track_lost_frames=30, reentry_max_gap_frames=40)
    m = StateManager(cfg)
    run(m, 20, FULL)
    key = m.tracks[1].person_key
    m.step(60, T0 + timedelta(seconds=6), [], {}, {})
    assert 1 not in m.tracks
    # gap dari last_frame (19) jauh melebihi reentry_max_gap_frames -> orang baru
    p = Person(2, (0, 0, 10, 10), 0.9)
    m.step(500, T0 + timedelta(seconds=50), [p], {2: Evidence(dict(FULL))}, {2: ("Area", ITEMS)})
    assert m.tracks[2].person_key != key


def test_zones():
    square = [(0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)]
    assert point_in_polygon(0.7, 0.5, square) and not point_in_polygon(0.2, 0.5, square)
    zones = [Zone("Kanan", square, ("helmet",)), Zone("Semua", [(0, 0), (1, 0), (1, 1), (0, 1)], ITEMS)]
    assert zone_for((800, 100, 900, 700), 1000, 700, zones).name == "Kanan"
    assert zone_for((100, 100, 200, 700), 1000, 700, zones).name == "Semua"
