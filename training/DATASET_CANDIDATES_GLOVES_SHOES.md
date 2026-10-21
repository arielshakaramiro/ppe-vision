# Kandidat dataset fine-tuning gloves dan shoes

Dokumen ini mencatat kandidat dari tautan Roboflow Universe yang sebelumnya
dipakai atau dibahas dalam proyek. Daftar ini bukan pernyataan bahwa seluruh
dataset layak langsung dilatih. Lisensi, kualitas anotasi, dan kecocokan domain
tetap harus diperiksa manusia sebelum notebook dijalankan.

## Pilihan default untuk pilot dua jam

| Peran | Spec Roboflow | Status default | Alasan pemakaian |
|---|---|---:|---|
| Jangkar full-PPE | [`akfa-beqxl/safety-rd-v1`](https://universe.roboflow.com/akfa-beqxl/safety-rd-v1) | Aktif | Kandidat utama yang terakhir dipilih untuk audit empat kelas. Wajib lolos pemeriksaan bahwa `helmet`, `vest`, `gloves`, dan `shoes` dianotasi lengkap pada gambar full-body/CCTV. |
| Pengayaan gloves | [`construction-images/gloves-qx5sc/2`](https://universe.roboflow.com/construction-images/gloves-qx5sc/2) | Nonaktif | Preview menunjukkan helmet dan vest yang terlihat tidak dianotasi. Memakainya sebagai single-class dapat mengajarkan kelas tersebut sebagai background. |
| Pengayaan shoes | [`test-pkubl/gloves-xjuja/2`](https://universe.roboflow.com/test-pkubl/gloves-xjuja/2) | Nonaktif | Nama proyek menyesatkan dan kelas aktualnya hanya `Boots` (628 instance), sehingga mapping `shoes` tetap benar. Preview full-body menampilkan helmet, vest, dan gloves tanpa anotasi; risiko regresi terlalu tinggi. |
| Pengayaan shoes | [`bns218/safety-shoes-yawyh`](https://universe.roboflow.com/bns218/safety-shoes-yawyh) | Nonaktif | Sebagian domain CCTV relevan, tetapi preview menampilkan APD lain tanpa anotasi. Ukurannya yang dominan (18.356 gambar train; 47.890 instance) memperbesar risiko regresi. |
| Pengayaan shoes | [`nedrick-chandra-gpg1l/safety-shoes-detection-5qgkg`](https://universe.roboflow.com/nedrick-chandra-gpg1l/safety-shoes-detection-5qgkg) | Nonaktif | Preview dominan close-up/rotasi berkualitas rendah dan memperlihatkan box tumpang tindih pada objek yang sama; domainnya tidak sesuai CCTV/full-body. |

Default konservatif hanya mengaktifkan jangkar full-PPE. Jika specialist baru sudah
lolos audit, notebook menyampling minimal 65% gambar train dari sumber full-PPE dan
maksimal 17,5% dari masing-masing kelompok gloves dan shoes. Jika specialist
kekurangan gambar unik, porsinya dibiarkan lebih kecil dan tidak diulang secara berlebihan.
Validation dan test internal hanya berasal dari sumber full-PPE agar objek APD
lain yang tidak dilabeli pada dataset single-class tidak menjadi ground truth
yang menyesatkan.

## Alternatif yang tersedia tetapi nonaktif

| Peran | Spec Roboflow | Kapan dipertimbangkan |
|---|---|---|
| Jangkar full-PPE | [`skcet-g4h72/construction-ppe-rdhzo`](https://universe.roboflow.com/skcet-g4h72/construction-ppe-rdhzo) | Aktifkan sebagai pengganti atau tambahan hanya setelah audit empat kelas, duplikat, dan domain. |
| Jangkar full-PPE | [`constructionsite/ppe-fruwx`](https://universe.roboflow.com/constructionsite/ppe-fruwx) | Alternatif dari notebook produksi lama; audit ulang urutan dan kelengkapan kelas. |
| Pengayaan gloves | [`marks-workspace-zns5d/gloves-em4xn/2`](https://universe.roboflow.com/marks-workspace-zns5d/gloves-em4xn/2) | Jika jumlah gloves setelah sampling masih terlalu sedikit dan domain visual sesuai. |
| Pengayaan gloves | [`pattharadanai-khamtrai-iuitd/gloves-detect/2`](https://universe.roboflow.com/pattharadanai-khamtrai-iuitd/gloves-detect/2) | Hanya setelah memastikan bukan dominan close-up yang jauh dari CCTV/full-body. |
| Pengayaan gloves | [`pblberdikari/gloves-yosp5/4`](https://universe.roboflow.com/pblberdikari/gloves-yosp5/4) | Hanya setelah audit visual dan lisensi. |
| Pengayaan shoes | [`parshvas-workspace-skr5a/safety-shoes-detection-uhdwv`](https://universe.roboflow.com/parshvas-workspace-skr5a/safety-shoes-detection-uhdwv) | Jika sumber shoes aktif tidak cukup atau kualitas labelnya buruk. |

## Dataset yang dilarang untuk train

- `datasets/gold/` reviewed 340 gambar: khusus evaluasi akhir.
- `sdp-lfigk/ppe-detection-ozhfb`: pernah dipakai sebagai hold-out, sehingga
  memasukkannya ke train akan mencemari perbandingan historis.
- Dataset gloves-only atau shoes-only yang menampilkan helmet/vest tanpa anotasi
  tidak boleh menjadi mayoritas. Objek yang terlihat tetapi tidak dilabeli akan
  dipelajari sebagai background.

## Pemeriksaan manusia sebelum mengaktifkan training

1. Buka setiap halaman Universe yang statusnya aktif dan catat lisensinya.
2. Pastikan lisensi mengizinkan tujuan penggunaan proyek.
3. Tinjau sampel acak train/validation: bounding box tepat, bukan label hasil
   prediksi yang belum direview, dan tidak didominasi close-up produk.
4. Untuk sumber full-PPE, pastikan semua APD target yang terlihat diberi label.
5. Untuk specialist, pastikan proporsi gambar dengan APD lain yang tidak diberi
   label cukup rendah; jika tidak, nonaktifkan sumber tersebut.
6. Jangan mengaktifkan `LICENSES_CONFIRMED` dan `VISUAL_AUDIT_CONFIRMED` di
   notebook sebelum pemeriksaan selesai.

Informasi lisensi halaman Universe belum diverifikasi dari lingkungan lokal ini
karena halaman publik mengembalikan HTTP 403. Notebook sengaja berhenti sebelum
download sampai owner mengonfirmasi lisensi dan audit visual.

Audit unduhan Colab pada 2026-10-16 mengoreksi peran
`test-pkubl/gloves-xjuja/2` dari gloves menjadi shoes. Guard cakupan label sengaja
tetap keras agar nama proyek atau riwayat pemakaian tidak mengalahkan kelas aktual
di `data.yaml` dan label hasil unduhan.