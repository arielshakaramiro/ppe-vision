# Dokumen Regulasi & SOP Kustom untuk RAG APD Vision

Folder ini digunakan untuk menambahkan dokumen regulasi K3, standar SNI, atau SOP internal perusahaan ke dalam sistem **RAG (Retrieval-Augmented Generation)** APD Vision.

## Cara Menambah Data Baru (Format JSON)

Buat file baru berekstensi `.json` (misalnya `sop_perusahaan.json`) di folder ini dengan struktur berikut:

```json
[
  {
    "id": "sop_internal_01",
    "regulation": "sop",
    "document": "SOP Keselamatan Kerja PT Tambang Sejahtera",
    "pasal": "Bagian 4.2 - Area Pengolahan",
    "topik": "Kewajiban Penggunaan Rompi Reflektif dan Helm di Area Mills",
    "teks": "Semua pekerja, kontraktor, dan pengawas yang memasuki area pengolahan/mills wajib mengenakan rompi reflektif bersertifikasi EN 471 Kelas 2 dan helm keselamatan dengan tali dagu terpasang.",
    "keywords": ["rompi", "helm", "mills", "tali dagu", "kontraktor", "pengawas"]
  },
  {
    "id": "sop_internal_02",
    "regulation": "sop",
    "document": "SOP Keselamatan Kerja PT Tambang Sejahtera",
    "pasal": "Bagian 5.1 - Bengkel & Fabrikasi",
    "topik": "Kewajiban Sarung Tangan Kulit Tahan Panas",
    "teks": "Pada saat pengelasan dan gerinda plat baja, pekerja wajib mengenakan sarung tangan kulit tahan percikan api dan pelindung muka penuh (face shield).",
    "keywords": ["sarung tangan", "pengelasan", "gerinda", "bengkel", "face shield"]
  }
]
```

### Keterangan Field:
- `id`: ID unik untuk potongan aturan (bebas, huruf kecil, angka, garis bawah).
- `regulation`: Kode regulasi (`sop`, `permenaker_8_2010`, `uu_1_1970`, `pp_50_2012`, atau kode baru).
- `document`: Judul dokumen resmi atau SOP.
- `pasal`: Nomor pasal, ayat, atau bab SOP.
- `topik`: Ringkasan topik bahasan.
- `teks`: Isi lengkap aturan atau bunyi pasal (bagian ini yang akan dicari semantik dan dikutip oleh Agent AI).
- `keywords`: (Opsional) Daftar kata kunci untuk memperkuat akurasi pencarian.

Setelah file `.json` disimpan, sistem akan otomatis mengindeksnya saat aplikasi dinyalakan ulang.
