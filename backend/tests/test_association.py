import numpy as np

from pipeline.association import AssocConfig, associate, body_regions
from pipeline.types import (L_ANKLE, L_EYE, L_HIP, L_SHOULDER, L_WRIST, NOSE, R_ANKLE, R_EYE, R_HIP,
                            R_SHOULDER, R_WRIST, PPEDet, Person)

CFG = AssocConfig()


def make_person(
    tid=1,
    x=100,
    hands_visible=True,
    feet_visible=True,
    head_visible=True,
    torso_visible=True,
):
    """Orang berdiri setinggi 400px, kotak (x, 100)-(x+120, 500)."""
    k = np.zeros((17, 3))
    cx = x + 60

    def put(i, px, py, c=0.9):
        k[i] = (px, py, c)

    put(NOSE, cx, 140, 0.9 if head_visible else 0.1)
    put(L_EYE, cx - 8, 132, 0.9 if head_visible else 0.1)
    put(R_EYE, cx + 8, 132, 0.9 if head_visible else 0.1)
    torso_conf = 0.9 if torso_visible else 0.1
    put(L_SHOULDER, cx - 35, 190, torso_conf)
    put(R_SHOULDER, cx + 35, 190, torso_conf)
    put(L_HIP, cx - 25, 320, torso_conf)
    put(R_HIP, cx + 25, 320, torso_conf)
    put(L_WRIST, cx - 50, 320, 0.9 if hands_visible else 0.1)
    put(R_WRIST, cx + 50, 320, 0.9 if hands_visible else 0.1)
    put(L_ANKLE, cx - 20, 480, 0.9 if feet_visible else 0.1)
    put(R_ANKLE, cx + 20, 480, 0.9 if feet_visible else 0.1)
    return Person(tid, (x, 100, x + 120, 500), 0.9, k)


def full_ppe(x=100):
    cx = x + 60
    return [
        PPEDet("helmet", (cx - 25, 100, cx + 25, 135), 0.8),
        PPEDet("vest", (cx - 40, 185, cx + 40, 310), 0.85),
        PPEDet("gloves", (cx - 60, 315, cx - 40, 345), 0.7),
        PPEDet("gloves", (cx + 40, 315, cx + 60, 345), 0.7),
        PPEDet("shoes", (cx - 32, 478, cx - 8, 500), 0.7),
        PPEDet("shoes", (cx + 8, 478, cx + 32, 500), 0.7),
    ]


def test_association_keeps_real_boxes_and_confidence_per_side():
    detections = full_ppe()
    ev = associate([make_person()], detections, CFG)[1]
    assert ev.detections["helmet"] == [detections[0]]
    assert ev.detections["gloves"] == detections[2:4]
    assert ev.detections["shoes"] == detections[4:6]
    assert ev.detections["helmet"][0].conf == 0.8


def test_missing_item_has_no_detected_box():
    ev = associate([make_person()], [d for d in full_ppe() if d.item != "helmet"], CFG)[1]
    assert ev.values["helmet"] == -1.0
    assert ev.detections["helmet"] == []


def test_full_ppe_is_present():
    ev = associate([make_person()], full_ppe(), CFG)[1].values
    assert all(v is not None and v > 0 for v in ev.values()), ev


def test_missing_glove_on_one_hand_counts_as_missing():
    dets = [d for d in full_ppe() if not (d.item == "gloves" and d.box[0] > 160)]
    ev = associate([make_person()], dets, CFG)[1].values
    assert ev["gloves"] == -1.0


def test_hidden_hands_are_unknown_not_violation():
    dets = [d for d in full_ppe() if d.item != "gloves"]
    ev = associate([make_person(hands_visible=False)], dets, CFG)[1].values
    assert ev["gloves"] is None


def test_helmet_far_from_head_is_discarded():
    dets = [d for d in full_ppe() if d.item != "helmet"] + [PPEDet("helmet", (500, 300, 540, 330), 0.9)]
    ev = associate([make_person()], dets, CFG)[1].values
    assert ev["helmet"] == -1.0


def test_ppe_goes_to_the_right_person():
    p1, p2 = make_person(1, x=100), make_person(2, x=400)
    ev = associate([p1, p2], full_ppe(x=400), CFG)
    assert ev[2].values["helmet"] > 0
    assert ev[1].values["helmet"] == -1.0


def test_no_keypoints_falls_back_to_bands():
    p = Person(1, (100, 100, 220, 500), 0.9, None)
    regs = body_regions(p, CFG)
    assert regs["helmet"][0].visible and not regs["gloves"][0].visible


def test_required_helmet_and_vest_fallback_to_person_box_when_keypoints_hidden():
    """Pekerja jauh/tertutup meja tidak boleh lolos sebagai unknown permanen."""
    p = make_person(head_visible=False, torso_visible=False, hands_visible=False, feet_visible=False)
    ev = associate([p], [], CFG)[1].values

    assert ev["helmet"] == -1.0
    assert ev["vest"] == -1.0
    assert ev["gloves"] is None
    assert ev["shoes"] is None


def test_precise_keypoint_region_wins_over_large_fallback_region():
    """APD pekerja depan tidak boleh dicuri fallback bbox pekerja belakang."""
    belakang = make_person(1, x=90, head_visible=False, torso_visible=False)
    depan = make_person(2, x=140)
    dets = [
        PPEDet("helmet", (195, 100, 245, 135), 0.8),
        PPEDet("vest", (160, 185, 240, 310), 0.85),
    ]

    ev = associate([belakang, depan], dets, CFG)

    assert ev[1].values["helmet"] == -1.0
    assert ev[1].values["vest"] == -1.0
    assert ev[2].values["helmet"] > 0
    assert ev[2].values["vest"] > 0


def test_fallback_region_uses_inner_body_band_to_avoid_side_ppe_theft():
    p = make_person(head_visible=False, torso_visible=False)
    side_ppe = [
        PPEDet("helmet", (100, 105, 130, 135), 0.8),
        PPEDet("vest", (100, 200, 135, 300), 0.85),
    ]

    ev = associate([p], side_ppe, CFG)[1].values

    assert ev["helmet"] == -1.0
    assert ev["vest"] == -1.0


def test_fallback_region_still_accepts_centered_ppe():
    p = make_person(head_visible=False, torso_visible=False)
    centered_ppe = [
        PPEDet("helmet", (145, 105, 175, 135), 0.8),
        PPEDet("vest", (135, 200, 185, 300), 0.85),
    ]

    ev = associate([p], centered_ppe, CFG)[1].values

    assert ev["helmet"] > 0
    assert ev["vest"] > 0
