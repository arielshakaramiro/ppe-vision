import { useEffect, useMemo, useState } from "react";
import { api, fmtNum } from "../../api";
import "./admin-pages.css";

// Semua angka awal adalah CONTOH untuk mencoba kalkulator, bukan data pasar.
// Ganti dengan penawaran vendor, gaji, dan data kecelakaan perusahaan yang sebenarnya.
const DEFAULTS = {
  kamera: 4,
  biayaPerKamera: 1_500_000,
  biayaKomputer: 15_000_000,
  operasionalBulanan: 500_000,
  umurTahun: 3,
  pengawasDibutuhkan: 2,
  gajiPengawas: 5_000_000,
  biayaPerInsiden: 50_000_000,
  insidenPerTahun: 4,
  penurunanPersen: 25,
};

type Inputs = typeof DEFAULTS;

const rp = (v: number) => "Rp " + Math.round(v).toLocaleString("id-ID");

const FIELDS: { key: keyof Inputs; label: string; hint: string; step?: number }[] = [
  { key: "kamera", label: "Jumlah kamera", hint: "Titik yang dipantau" },
  { key: "biayaPerKamera", label: "Biaya per kamera (Rp)", hint: "Kamera IP + pemasangan", step: 100000 },
  { key: "biayaKomputer", label: "Komputer / server (Rp)", hint: "Satu kali, untuk inference", step: 1000000 },
  { key: "operasionalBulanan", label: "Operasional per bulan (Rp)", hint: "Listrik, internet, API LLM", step: 50000 },
  { key: "umurTahun", label: "Umur pakai sistem (tahun)", hint: "Untuk menyebar biaya awal" },
  { key: "pengawasDibutuhkan", label: "Pengawas manual setara", hint: "Orang yang dibutuhkan untuk cakupan sama" },
  { key: "gajiPengawas", label: "Gaji pengawas per bulan (Rp)", hint: "Termasuk tunjangan", step: 100000 },
  { key: "biayaPerInsiden", label: "Biaya rata-rata per insiden (Rp)", hint: "Pengobatan, hari kerja hilang, perbaikan", step: 1000000 },
  { key: "insidenPerTahun", label: "Insiden terkait APD per tahun", hint: "Dari catatan K3 perusahaan" },
  { key: "penurunanPersen", label: "Perkiraan penurunan insiden (%)", hint: "Asumsi; buktikan dengan halaman Intervensi" },
];

export default function Business() {
  const [v, setV] = useState<Inputs>(DEFAULTS);
  const [paparan, setPaparan] = useState<number | null>(null);

  useEffect(() => {
    api.stats("30_hari").then((s) => setPaparan(s.menit_paparan)).catch(() => setPaparan(null));
  }, []);

  const r = useMemo(() => {
    const awal = v.kamera * v.biayaPerKamera + v.biayaKomputer;
    const sistemPerTahun = awal / Math.max(1, v.umurTahun) + v.operasionalBulanan * 12;
    const manualPerTahun = v.pengawasDibutuhkan * v.gajiPengawas * 12;
    const risikoDihindari = v.biayaPerInsiden * v.insidenPerTahun * (v.penurunanPersen / 100);
    const manfaat = risikoDihindari;
    const bersih = manfaat - sistemPerTahun;
    const paybackBulan = manfaat > v.operasionalBulanan * 12 ? awal / (manfaat / 12 - v.operasionalBulanan) : null;
    return {
      awal, sistemPerTahun, manualPerTahun, risikoDihindari, bersih,
      roi: sistemPerTahun > 0 ? (bersih / sistemPerTahun) * 100 : null,
      perKameraBulan: sistemPerTahun / 12 / Math.max(1, v.kamera),
      paybackBulan,
    };
  }, [v]);

  return (
    <div className="business-page">
      <aside className="note small business-note">
        Kalkulator ini untuk diskusi kelayakan. <b>Semua angka awal hanyalah contoh</b>; ganti dengan penawaran vendor, gaji,
        dan catatan insiden perusahaan. Penurunan insiden adalah asumsi yang perlu dibuktikan dengan data intervensi.
      </aside>

      <section className="av-card business-assumptions">
        <h3>Asumsi</h3>
        <div className="business-form-grid">
          {FIELDS.map((f) => (
            <label key={f.key} className="av-field">
              <span>{f.label}</span>
              <input className="av-input" type="number" min={0} step={f.step ?? 1} value={v[f.key]}
                onChange={(e) => setV({ ...v, [f.key]: Math.max(0, Number(e.target.value) || 0) })} />
              <span className="muted small">{f.hint}</span>
            </label>
          ))}
        </div>
        <button className="link small" onClick={() => setV(DEFAULTS)}>Kembalikan ke contoh</button>
      </section>

      <section className="business-kpis" aria-label="Ringkasan kelayakan">
        <div className="av-card av-kpi"><div className="av-label">Biaya sistem / tahun</div>
          <b>{rp(r.sistemPerTahun)}</b>
          <div className="muted small">{rp(r.perKameraBulan)} per kamera per bulan</div></div>
        <div className="av-card av-kpi"><div className="av-label">Pengawasan manual / tahun</div>
          <b>{rp(r.manualPerTahun)}</b>
          <div className="muted small">Pembanding, bukan pengganti penuh</div></div>
        <div className="av-card av-kpi"><div className="av-label">Risiko dihindari / tahun</div>
          <b className="good">{rp(r.risikoDihindari)}</b>
          <div className="muted small">Insiden × biaya × penurunan</div></div>
        <div className="av-card av-kpi"><div className="av-label">Balik modal</div>
          <b>{r.paybackBulan != null ? `${fmtNum(r.paybackBulan, 1)} bln` : "–"}</b>
          <div className="muted small">ROI tahunan {r.roi != null ? `${fmtNum(r.roi, 0)}%` : "–"}</div></div>
      </section>

      <section className="av-card business-context">
        <h3>Konteks dari data sistem</h3>
        <p>
          Dalam 30 hari terakhir tercatat <b>{paparan != null ? `${fmtNum(paparan)} orang-menit` : "–"}</b> pekerja berada di area
          tanpa APD lengkap. Angka ini lebih bermakna untuk menilai risiko daripada jumlah pelanggaran, karena peluang cedera
          sebanding dengan lama paparan.
        </p>
        <p className="muted small">
          Sistem ini tidak menggantikan pengawas: ia memberi pengawas mata tambahan 24 jam, bukti untuk pembinaan, dan data untuk
          mengukur apakah program K3 berhasil.
        </p>
      </section>
    </div>
  );
}
