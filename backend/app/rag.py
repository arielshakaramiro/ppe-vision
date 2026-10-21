"""Modul RAG (Retrieval-Augmented Generation) Regulasi K3.

Mengindeks dokumen regulasi resmi:
- Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri
- UU No. 1 Tahun 1970 tentang Keselamatan Kerja
- PP No. 50 Tahun 2012 tentang Penerapan SMK3
- Standar Operasional Prosedur (SOP) K3 Area Kerja

Menyediakan pencarian semantik (vektor kemiripan kosinus) dan kata kunci
secara 100% lokal dan deterministik menggunakan numpy tanpa dependensi cloud.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np


@dataclass
class RegulationChunk:
    id: str
    regulation: str          # permenaker_8_2010 | uu_1_1970 | pp_50_2012 | sop
    document: str            # nama resmi dokumen
    pasal: str               # "Pasal 3 Ayat (1) huruf f" dsb
    topik: str               # label topik ringkas
    teks: str                # kutipan isi pasal/aturan resmi
    keywords: list[str]      # kata kunci penting untuk indexing


REGULATION_CORPUS: list[RegulationChunk] = [
    # --- Permenaker No. 8 Tahun 2010 ---
    RegulationChunk(
        id="permenaker_8_2010_p2",
        regulation="permenaker_8_2010",
        document="Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri",
        pasal="Pasal 2",
        topik="Kewajiban Pengusaha Menyediakan APD Standar SNI",
        teks=(
            "Pengusaha wajib menyediakan Alat Pelindung Diri (APD) bagi pekerja/buruh di tempat kerja secara cuma-cuma. "
            "APD yang disediakan wajib memenuhi Standar Nasional Indonesia (SNI) atau standar yang berlaku dan harus sesuai "
            "dengan potensi bahaya serta risiko di tempat kerja."
        ),
        keywords=["pengusaha", "wajib", "cuma-cuma", "gratis", "sni", "standar", "potensi", "bahaya", "risiko", "menyediakan"],
    ),
    RegulationChunk(
        id="permenaker_8_2010_p3_helm",
        regulation="permenaker_8_2010",
        document="Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri",
        pasal="Pasal 3 Ayat (1) huruf a",
        topik="Pelindung Kepala (Safety Helmet)",
        teks=(
            "Pelindung kepala (safety helmet / helm keselamatan) berfungsi untuk melindungi kepala dari benturan, terantuk, "
            "kejatuhan atau terpukul benda tajam atau benda keras yang melayang atau meluncur di udara, terpapar oleh radiasi panas, "
            "percikan bahan kimia, atau suhu yang ekstrem."
        ),
        keywords=["helm", "safety helmet", "kepala", "benturan", "kejatuhan", "terantuk", "benda tajam", "keras", "radiasi", "panas"],
    ),
    RegulationChunk(
        id="permenaker_8_2010_p3_rompi",
        regulation="permenaker_8_2010",
        document="Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri",
        pasal="Pasal 3 Ayat (1) huruf g",
        topik="Pakaian Pelindung (Safety Vest / Rompi)",
        teks=(
            "Pakaian pelindung meliputi rompi keselamatan (safety vest) dengan pemantul cahaya (reflector/hi-vis) dan pakaian kerja. "
            "Berfungsi untuk meningkatkan visibilitas pekerja di area lalu lintas kendaraan atau alat berat, serta melindungi tubuh "
            "dari percikan bahan berbahaya, suhu ekstrem, goresan, dan kontak bahaya fisik."
        ),
        keywords=["rompi", "safety vest", "pakaian pelindung", "reflektif", "visibilitas", "alat berat", "lalu lintas", "kendaraan", "tubuh"],
    ),
    RegulationChunk(
        id="permenaker_8_2010_p3_sarung",
        regulation="permenaker_8_2010",
        document="Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri",
        pasal="Pasal 3 Ayat (1) huruf e",
        topik="Pelindung Tangan (Sarung Tangan / Safety Gloves)",
        teks=(
            "Pelindung tangan (sarung tangan keselamatan) berfungsi melindungi tangan dan jari-jari tangan dari pajanan api, suhu panas, "
            "suhu dingin, radiasi elektromagnetik, radiasi mengion, bahan kimia, benturan, pukulan, tergores, terpotong, atau infeksi zat patogen."
        ),
        keywords=["sarung tangan", "gloves", "tangan", "jari", "panas", "bahan kimia", "tergores", "terpotong", "benturan", "mekanis"],
    ),
    RegulationChunk(
        id="permenaker_8_2010_p3_sepatu",
        regulation="permenaker_8_2010",
        document="Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri",
        pasal="Pasal 3 Ayat (1) huruf f",
        topik="Pelindung Kaki (Safety Shoes / Sepatu Keselamatan)",
        teks=(
            "Pelindung kaki (sepatu keselamatan / safety boots) berfungsi untuk melindungi kaki dari tertimpa atau benturan benda berat, "
            "tertusuk benda tajam, terkena cairan panas atau dingin, uap panas, terpajan suhu ekstrem, terkena bahan kimia berbahaya, "
            "dan bahaya terpeleset (slip resistance)."
        ),
        keywords=["sepatu", "safety shoes", "safety boots", "kaki", "tertimpa", "benda berat", "tertusuk", "benda tajam", "terpeleset", "slip"],
    ),
    RegulationChunk(
        id="permenaker_8_2010_p4",
        regulation="permenaker_8_2010",
        document="Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri",
        pasal="Pasal 4",
        topik="Kondisi Tempat Kerja yang Wajib Menggunakan APD",
        teks=(
            "APD wajib digunakan di tempat kerja di mana: dilakukan pekerjaan di ketinggian; terdapat bahaya kejatuhan benda; "
            "terdapat mesin, pesawat, atau instalasi bergerak; terdapat pekerjaan pengangkutan atau bongkar muat (loading dock); "
            "dan di area pertambangan, konstruksi, atau industri pengolahan."
        ),
        keywords=["tempat kerja", "wajib", "loading dock", "bongkar muat", "konstruksi", "tambang", "ketinggian", "alat berat", "kejatuhan benda"],
    ),
    RegulationChunk(
        id="permenaker_8_2010_p5",
        regulation="permenaker_8_2010",
        document="Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri",
        pasal="Pasal 5",
        topik="Hak Pekerja Menyatakan Keberatan Terkait APD",
        teks=(
            "Tenaga kerja berhak menyatakan keberatan untuk melakukan pekerjaan apabila APD yang disediakan oleh pengusaha tidak memenuhi "
            "ketentuan keselamatan dan kesehatan kerja yang dipersyaratkan."
        ),
        keywords=["hak", "pekerja", "keberatan", "menolak", "tidak memenuhi", "syarat", "keselamatan"],
    ),
    RegulationChunk(
        id="permenaker_8_2010_p6",
        regulation="permenaker_8_2010",
        document="Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri",
        pasal="Pasal 6",
        topik="Kewajiban Penggunaan APD bagi Setiap Orang di Tempat Kerja",
        teks=(
            "Pekerja/buruh dan orang lain yang memasuki tempat kerja wajib memakai APD sesuai dengan potensi bahaya dan risiko. "
            "Setiap orang yang tidak mematuhi kewajiban penggunaan APD tidak diperbolehkan memasuki area kerja."
        ),
        keywords=["kewajiban", "pekerja", "tamu", "orang lain", "masuk", "wajib memakai", "dilarang masuk", "tidak patuh"],
    ),
    RegulationChunk(
        id="permenaker_8_2010_p7",
        regulation="permenaker_8_2010",
        document="Permenaker No. 8/MEN/VII/2010 tentang Alat Pelindung Diri",
        pasal="Pasal 7",
        topik="Pemasangan Rambu dan Petunjuk APD",
        teks=(
            "Pengusaha atau pengurus wajib memasang rambu-rambu petunjuk kewajiban penggunaan APD di tempat-tempat yang mudah dilihat, "
            "terbaca, dan dapat dipahami oleh setiap pekerja serta orang yang memasuki tempat kerja."
        ),
        keywords=["rambu", "tanda", "petunjuk", "wajib pasang", "papan peringatan", "area kerja"],
    ),

    # --- UU No. 1 Tahun 1970 ---
    RegulationChunk(
        id="uu_1_1970_p3",
        regulation="uu_1_1970",
        document="Undang-Undang No. 1 Tahun 1970 tentang Keselamatan Kerja",
        pasal="Pasal 3 Ayat (1)",
        topik="Syarat-Syarat Keselamatan Kerja Nasional",
        teks=(
            "Syarat-syarat keselamatan kerja ditetapkan untuk: mencegah dan mengurangi kecelakaan; mencegah dan mengurangi bahaya kebakaran; "
            "mencegah terkena aliran listrik; serta memberi alat-alat perlindungan diri pada para pekerja guna menjamin keselamatan setiap tenaga kerja."
        ),
        keywords=["uu 1 1970", "undang-undang", "syarat keselamatan", "kecelakaan", "kebakaran", "perlindungan diri", "tenaga kerja"],
    ),
    RegulationChunk(
        id="uu_1_1970_p12",
        regulation="uu_1_1970",
        document="Undang-Undang No. 1 Tahun 1970 tentang Keselamatan Kerja",
        pasal="Pasal 12 huruf b",
        topik="Kewajiban Tenaga Kerja Memakai APD",
        teks=(
            "Dengan peraturan perundangan diatur kewajiban dan/atau hak tenaga kerja untuk memakai alat-alat perlindungan diri yang diwajibkan, "
            "memenuhi dan menaati semua syarat-syarat keselamatan dan kesehatan kerja yang diwajibkan."
        ),
        keywords=["kewajiban tenaga kerja", "memakai apd", "menaati syarat", "hukum", "pasal 12"],
    ),
    RegulationChunk(
        id="uu_1_1970_p13",
        regulation="uu_1_1970",
        document="Undang-Undang No. 1 Tahun 1970 tentang Keselamatan Kerja",
        pasal="Pasal 13",
        topik="Kewajiban Bagi Setiap Orang yang Memasuki Tempat Kerja",
        teks=(
            "Barang siapa akan memasuki sesuatu tempat kerja, diwajibkan menaati semua petunjuk keselamatan kerja dan memakai alat-alat "
            "perlindungan diri yang diwajibkan."
        ),
        keywords=["siapapun", "tamu", "pengunjung", "masuk tempat kerja", "wajib menaati", "pasal 13"],
    ),
    RegulationChunk(
        id="uu_1_1970_p14",
        regulation="uu_1_1970",
        document="Undang-Undang No. 1 Tahun 1970 tentang Keselamatan Kerja",
        pasal="Pasal 14 huruf c",
        topik="Kewajiban Pengurus Menyediakan APD dan Petunjuk",
        teks=(
            "Pengurus diwajibkan menyediakan secara cuma-cuma semua alat perlindungan diri yang diwajibkan pada tenaga kerja yang berada di bawah "
            "pimpinannya dan menyediakan bagi setiap orang lain yang memasuki tempat kerja tersebut, disertai petunjuk yang diperlukan."
        ),
        keywords=["pengurus", "perusahaan", "cuma-cuma", "menyediakan", "petunjuk", "pasal 14"],
    ),

    # --- PP No. 50 Tahun 2012 ---
    RegulationChunk(
        id="pp_50_2012_smk3",
        regulation="pp_50_2012",
        document="PP No. 50 Tahun 2012 tentang Penerapan Sistem Manajemen K3 (SMK3)",
        pasal="Pasal 10 & Lampiran II",
        topik="Pengendalian Risiko dan Audit Kepatuhan APD",
        teks=(
            "Perusahaan wajib menerapkan hierarki pengendalian risiko: eliminasi, substitusi, rekayasa teknik, pengendalian administratif, "
            "dan Alat Pelindung Diri (APD) sebagai benteng pertahanan terakhir. Pemantauan kepatuhan wajib diaudit secara berkala melalui "
            "pemeriksaan fisik, pemantauan area kerja, dan evaluasi kepatuhan APD."
        ),
        keywords=["smk3", "pp 50 2012", "hierarki pengendalian", "audit", "evaluasi", "kepatuhan", "inspeksi", "risiko"],
    ),

    # --- SOP K3 Area Khusus ---
    RegulationChunk(
        id="sop_loading_dock",
        regulation="sop",
        document="Standar Operasional Prosedur (SOP) K3 Area Kerja",
        pasal="SOP-K3-04: Area Loading Dock / Bongkar Muat",
        topik="SOP APD Wajib di Area Loading Dock & Gudang",
        teks=(
            "Di area loading dock dan bongkar muat kargo: (1) Wajib menggunakan Sepatu Keselamatan (Safety Shoes) bersol baja untuk mencegah "
            "crush hazard dari kejatuhan kontainer/pallet dan manuver forklift; (2) Wajib menggunakan Helm Keselamatan (Safety Helmet) untuk "
            "mencegah cedera kepala akibat ayunan kargo atau kejatuhan barang; (3) Wajib Rompi Reflektif (Safety Vest) agar terlihat oleh operator truk."
        ),
        keywords=["loading dock", "bongkar muat", "sepatu", "crush hazard", "forklift", "pallet", "gudang", "helm", "rompi", "sop"],
    ),
    RegulationChunk(
        id="sop_tambang_pabrik",
        regulation="sop",
        document="Standar Operasional Prosedur (SOP) K3 Area Kerja",
        pasal="SOP-K3-02: Area Tambang & Pabrik Aktif",
        topik="SOP APD Wajib Area Tambang dan Operasional Alat Berat",
        teks=(
            "Di area penambangan dan pabrik aktif: Semua personel wajib mengenakan Rompi Visibilitas Tinggi (Hi-Vis Safety Vest) standar Kelas 2/3, "
            "Helm Keselamatan dengan tali dagu (chin strap) terpasang kencang, dan Sepatu Keselamatan anti-slip. Tanpa APD lengkap dilarang melintas "
            "di jalur operasional alat berat dan konveyor."
        ),
        keywords=["tambang", "mining", "alat berat", "pabrik", "chin strap", "tali dagu", "rompi", "helm", "sepatu", "sop"],
    ),
    RegulationChunk(
        id="sop_fabrikasi_bengkel",
        regulation="sop",
        document="Standar Operasional Prosedur (SOP) K3 Area Kerja",
        pasal="SOP-K3-03: Area Fabrikasi & Pengelasan",
        topik="SOP APD Wajib Area Fabrikasi dan Penanganan Logam",
        teks=(
            "Di area fabrikasi, pemotongan, dan pengelasan: Personel wajib menggunakan Sarung Tangan Pelindung Tahan Panas/Mekanis (Safety Gloves), "
            "Pelindung Mata/Muka, dan Sepatu Keselamatan bertutup baja untuk menghindari percikan api pijar, benda tajam, dan potongan plat besi."
        ),
        keywords=["fabrikasi", "pengelasan", "sarung tangan", "gloves", "bengkel", "percikan api", "benda tajam", "plat", "sop"],
    ),
]


def _tokenize(text: str) -> list[str]:
    """Tokenisasi kata sederhana, huruf kecil, tanpa tanda baca."""
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 2]


class RegulationRetriever:
    """Retriever semantik & leksikal berbasis TF-IDF dan Cosine Similarity."""

    def __init__(self, corpus: Sequence[RegulationChunk]):
        self.corpus = list(corpus)
        # Bangun kosa kata (vocabulary)
        self.doc_tokens: list[list[str]] = []
        df: dict[str, int] = {}
        for chunk in self.corpus:
            content = f"{chunk.topik} {chunk.pasal} {chunk.teks} {' '.join(chunk.keywords)}"
            tokens = _tokenize(content)
            self.doc_tokens.append(tokens)
            for w in set(tokens):
                df[w] = df.get(w, 0) + 1

        self.vocab = sorted(df.keys())
        self.vocab_idx = {w: idx for idx, w in enumerate(self.vocab)}
        n_docs = len(self.corpus)
        self.idf = np.zeros(len(self.vocab), dtype=np.float32)
        for w, count in df.items():
            # Formula IDF standar
            self.idf[self.vocab_idx[w]] = math.log((n_docs + 1) / (count + 1)) + 1.0

        # Bangun matriks TF-IDF dokumen dan normalisasi L2 untuk kemiripan kosinus
        self.doc_vectors = np.zeros((n_docs, len(self.vocab)), dtype=np.float32)
        for i, tokens in enumerate(self.doc_tokens):
            for t in tokens:
                idx = self.vocab_idx.get(t)
                if idx is not None:
                    self.doc_vectors[i, idx] += 1.0
            # Kalikan dengan IDF
            self.doc_vectors[i] *= self.idf
            # Normalisasi L2
            norm = np.linalg.norm(self.doc_vectors[i])
            if norm > 0:
                self.doc_vectors[i] /= norm

    def _query_vector(self, query: str) -> np.ndarray:
        q_tokens = _tokenize(query)
        vec = np.zeros(len(self.vocab), dtype=np.float32)
        for t in q_tokens:
            idx = self.vocab_idx.get(t)
            if idx is not None:
                vec[idx] += 1.0
        vec *= self.idf
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def search(self, query: str, regulation_filter: str = "semua", top_k: int = 3) -> list[dict]:
        """Cari pasal regulasi K3 paling relevan berdasarkan query pengguna."""
        query = query.strip()
        if not query:
            return []

        q_vec = self._query_vector(query)
        if np.all(q_vec == 0):
            # Jika semua kata di luar vocabulary, cari substring kata kunci
            return self._fallback_substring_search(query, regulation_filter, top_k)

        # Hitung dot product (kosinus kemiripan karena kedua vektor ternormalisasi)
        sims = np.dot(self.doc_vectors, q_vec)

        # Urutkan dari skor tertinggi
        ranked_indices = np.argsort(-sims)

        results: list[dict] = []
        for idx in ranked_indices:
            score = float(sims[idx])
            chunk = self.corpus[idx]
            if regulation_filter != "semua" and chunk.regulation != regulation_filter:
                continue
            if score < 0.05 and len(results) >= 1:
                break
            results.append({
                "id": chunk.id,
                "dokumen": chunk.document,
                "pasal": chunk.pasal,
                "topik": chunk.topik,
                "kutipan": chunk.teks,
                "skor_relevansi": round(score, 3),
            })
            if len(results) >= top_k:
                break

        return results

    def _fallback_substring_search(self, query: str, regulation_filter: str, top_k: int) -> list[dict]:
        q_words = _tokenize(query)
        scored = []
        for chunk in self.corpus:
            if regulation_filter != "semua" and chunk.regulation != regulation_filter:
                continue
            text_low = f"{chunk.topik} {chunk.pasal} {chunk.teks}".lower()
            matches = sum(1 for w in q_words if w in text_low)
            if matches > 0:
                scored.append((matches, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{
            "id": c.id,
            "dokumen": c.document,
            "pasal": c.pasal,
            "topik": c.topik,
            "kutipan": c.teks,
            "skor_relevansi": round(m / max(len(q_words), 1), 3),
        } for m, c in scored[:top_k]]


def _chunk_text_file(path: Path) -> list[RegulationChunk]:
    """Bagi file .txt atau .md menjadi potongan aturan (chunk) berdasarkan Pasal atau paragraf."""
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    doc_title = path.stem.replace("_", " ").title()
    chunks: list[RegulationChunk] = []

    # Cek apakah memuat pola 'Pasal X'
    pasal_splits = re.split(r"(?i)(?=(?:^|\n)\s*pasal\s+\d+)", raw)
    if len(pasal_splits) > 1:
        for idx, block in enumerate(pasal_splits):
            block = block.strip()
            if not block:
                continue
            first_line = block.splitlines()[0].strip()
            pasal_match = re.search(r"(?i)pasal\s+\d+", first_line)
            pasal_label = pasal_match.group(0).title() if pasal_match else f"Bagian {idx + 1}"
            chunks.append(RegulationChunk(
                id=f"{path.stem}_{idx + 1}",
                regulation="sop",
                document=doc_title,
                pasal=pasal_label,
                topik=first_line[:60],
                teks=block,
                keywords=_tokenize(block),
            ))
    else:
        # Jika bukan format pasal, potong per paragraf (pemisah baris ganda)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if len(p.strip()) > 20]
        for idx, p in enumerate(paragraphs):
            first_line = p.splitlines()[0].strip()
            chunks.append(RegulationChunk(
                id=f"{path.stem}_{idx + 1}",
                regulation="sop",
                document=doc_title,
                pasal=f"Bagian {idx + 1}",
                topik=first_line[:60],
                teks=p,
                keywords=_tokenize(p),
            ))
    return chunks


def load_custom_regulations() -> list[RegulationChunk]:
    """Muat dokumen regulasi/SOP kustom tambahan dari backend/data/regulations/ (*.json, *.txt, *.md)."""
    from .config import BASE_DIR
    reg_dir: Path = Path(BASE_DIR) / "data" / "regulations"
    custom_chunks: list[RegulationChunk] = []
    if not reg_dir.is_dir():
        return custom_chunks

    # 1. Muat file JSON terstruktur
    for json_file in sorted(reg_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "teks" in item:
                        text = str(item.get("teks") or "")
                        custom_chunks.append(RegulationChunk(
                            id=str(item.get("id") or f"{json_file.stem}_{len(custom_chunks)}"),
                            regulation=str(item.get("regulation") or "sop"),
                            document=str(item.get("document") or "SOP K3 Tambahan"),
                            pasal=str(item.get("pasal") or "-"),
                            topik=str(item.get("topik") or "-"),
                            teks=text,
                            keywords=item.get("keywords") or _tokenize(f"{item.get('topik', '')} {text}"),
                        ))
        except Exception:
            continue

    # 2. Muat file teks biasa (.txt) dan markdown (.md) hasil copy-paste dari PDF
    for text_file in sorted(list(reg_dir.glob("*.txt")) + list(reg_dir.glob("*.md"))):
        if text_file.name.lower().startswith("readme"):
            continue
        try:
            custom_chunks.extend(_chunk_text_file(text_file))
        except Exception:
            continue

    return custom_chunks


def _build_retriever() -> RegulationRetriever:
    all_chunks = list(REGULATION_CORPUS) + load_custom_regulations()
    return RegulationRetriever(all_chunks)


_default_retriever = _build_retriever()


def reload_regulations() -> None:
    """Muat ulang seluruh korpus dan bangun kembali index vektor jika ada file JSON baru."""
    global _default_retriever
    _default_retriever = _build_retriever()


def search_regulations(query: str, regulation: str = "semua", top_k: int = 3) -> list[dict]:
    """Titik masuk publik pencarian RAG Regulasi K3."""
    return _default_retriever.search(query, regulation_filter=regulation, top_k=top_k)
