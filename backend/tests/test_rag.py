"""Uji unit untuk modul RAG Regulasi K3 dan integrasinya dengan Agent."""
from __future__ import annotations

import pytest
from app import agent, rag


def test_rag_retriever_finds_helm_loading_dock():
    hits = rag.search_regulations("mengapa wajib helm di area loading dock")
    assert len(hits) >= 1
    top = hits[0]
    assert "loading dock" in top["pasal"].lower() or "loading dock" in top["topik"].lower() or "permenaker" in top["dokumen"].lower()
    assert top["skor_relevansi"] > 0.1


def test_rag_retriever_finds_permenaker_pasal_2_sni():
    hits = rag.search_regulations("kewajiban pengusaha menyediakan apd cuma-cuma sni")
    assert any("Pasal 2" in h["pasal"] for h in hits)
    assert any("Permenaker No. 8" in h["dokumen"] for h in hits)


def test_rag_retriever_finds_uu_1_1970_pasal_12():
    hits = rag.search_regulations("uu 1 1970 kewajiban tenaga kerja memakai apd")
    assert any("UU No. 1 Tahun 1970" in h["dokumen"] or "Undang-Undang No. 1" in h["dokumen"] for h in hits)


def test_rag_retriever_filter_by_regulation():
    hits_permenaker = rag.search_regulations("kewajiban APD", regulation="permenaker_8_2010")
    assert all("Permenaker" in h["dokumen"] for h in hits_permenaker)

    hits_uu = rag.search_regulations("kewajiban APD", regulation="uu_1_1970")
    assert all("Undang-Undang" in h["dokumen"] for h in hits_uu)


def test_agent_run_tool_search_regulation():
    res = agent.run_tool("search_regulation", {"query": "sepatu safety slip loading dock"})
    assert "hasil" in res
    assert res["jumlah"] >= 1
    assert any("sepatu" in h["kutipan"].lower() or "loading dock" in h["kutipan"].lower() for h in res["hasil"])


def test_agent_offline_regulation_response():
    reply = agent._chat_offline("apa dasar hukum permenaker tentang kewajiban apd?")
    assert "Berdasarkan regulasi" in reply["answer"]
    assert "Permenaker" in reply["answer"]
    assert reply["tool_calls"][0]["name"] == "search_regulation"


def test_load_custom_regulations_from_json(tmp_path, monkeypatch):
    import json
    from app import config as cfg_mod

    reg_dir = tmp_path / "data" / "regulations"
    reg_dir.mkdir(parents=True)
    custom_file = reg_dir / "sop_tambang.json"
    custom_data = [
        {
            "id": "custom_sop_99",
            "regulation": "sop_khusus",
            "document": "SOP Tambang Bawah Tanah PT X",
            "pasal": "Pasal 99",
            "topik": "Lampu Helm dan Respirator",
            "teks": "Setiap pekerja wajib mengenakan helm dengan cap lamp terpasang dan respirator gas.",
            "keywords": ["cap lamp", "respirator", "bawah tanah"]
        }
    ]
    custom_file.write_text(json.dumps(custom_data), encoding="utf-8")

    monkeypatch.setattr(cfg_mod, "BASE_DIR", tmp_path)
    rag.reload_regulations()

    hits = rag.search_regulations("cap lamp respirator tambang bawah tanah")
    assert any(h["id"] == "custom_sop_99" for h in hits)

    # Kembalikan ke konfigurasi asli
    monkeypatch.undo()
    rag.reload_regulations()


def test_load_custom_regulations_from_text_or_markdown(tmp_path, monkeypatch):
    from app import config as cfg_mod

    reg_dir = tmp_path / "data" / "regulations"
    reg_dir.mkdir(parents=True)
    txt_file = reg_dir / "sop_crane.txt"
    txt_file.write_text(
        "Pasal 1 Ketentuan Umum\n"
        "Operator crane wajib memiliki lisensi K3 dan menggunakan helm keselamatan.\n\n"
        "Pasal 2 Zona Ayunan Beban\n"
        "Dilarang berdiri di bawah ayunan beban crane tanpa rompi reflektif dan helm baja.\n",
        encoding="utf-8"
    )

    monkeypatch.setattr(cfg_mod, "BASE_DIR", tmp_path)
    rag.reload_regulations()

    hits = rag.search_regulations("zona ayunan beban crane rompi reflektif")
    assert len(hits) >= 1
    assert any("sop crane" in h["dokumen"].lower() for h in hits)

    monkeypatch.undo()
    rag.reload_regulations()
