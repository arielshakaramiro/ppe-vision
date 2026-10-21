import numpy as np

from pipeline.draw import _compact_detail_line, draw_person


def test_draw_person_label_stays_visible_near_frame_edges():
    img = np.zeros((180, 240, 3), np.uint8)
    checklist = {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}

    draw_person(img, (205, 4, 238, 120), 8, "violation", checklist, draw_regions=False)

    assert np.count_nonzero(img) > 0
    # Panel label harus tetap digambar di area frame, bukan terpotong seluruhnya di luar kanan/atas.
    label_area = img[:170, :240]
    dark_panel_pixels = np.count_nonzero(np.all(label_area == (20, 20, 20), axis=2))
    assert dark_panel_pixels > 0
    # Teks/border berwarna membuat variasi pixel yang cukup untuk terbaca.
    assert len(np.unique(label_area.reshape(-1, 3), axis=0)) > 3


def test_draw_person_compact_mode_for_recorded_video():
    img = np.zeros((180, 240, 3), np.uint8)
    checklist = {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}
    draw_person(img, (40, 50, 110, 150), 8, "violation", checklist, draw_regions=False, compact=True)
    assert np.count_nonzero(img) > 0
    # Mode compact lebih ringkas dan tetap menghasilkan panel gelap + teks
    dark_panel_pixels = np.count_nonzero(np.all(img == (20, 20, 20), axis=2))
    assert dark_panel_pixels > 0


def test_compact_recorded_label_includes_ppe_detail():
    checklist = {"helmet": "missing", "vest": "present", "gloves": "unknown", "shoes": "present"}
    assert _compact_detail_line("violation", checklist) == "Kurang: Helm"
    checklist["helmet"] = "unknown"
    assert _compact_detail_line("uncertain", checklist) == "Cek: Helm, Sarung tangan"
