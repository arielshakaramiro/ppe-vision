import { useEffect, useState } from "react";
import { api, can, fmtNum, type Comparison, type Intervention } from "../../api";
import { useMe } from "../../session";
import { GlassSelect } from "../../ui";

const nowLocal = () => {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
};

// lebih tinggi lebih baik untuk compliance; lebih rendah lebih baik untuk pelanggaran & paparan
function Delta({ v, higherIsBetter, suffix = "" }: { v: number | null; higherIsBetter: boolean; suffix?: string }) {
  if (v == null) return <span className="muted">–</span>;
  const good = higherIsBetter ? v > 0 : v < 0;
  return <span className={v === 0 ? "" : good ? "delta-good" : "delta-bad"}>{v > 0 ? "+" : ""}{fmtNum(v, 2)}{suffix}</span>;
}

export default function Interventions() {
  const me = useMe();
  const canEdit = can(me, "supervisor");
  const [items, setItems] = useState<Intervention[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [days, setDays] = useState(7);
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [form, setForm] = useState({ at: nowLocal(), title: "", note: "", camera_id: "" });
  const [error, setError] = useState<string | null>(null);

  const load = () => api.interventions().then((r) => {
    setItems(r);
    if (r.length && selected == null) setSelected(r[0].id);
  }).catch((e: Error) => setError(e.message));

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (selected == null) { setCmp(null); return; }
    let active = true;
    api.compare(selected, days).then((r) => active && setCmp(r)).catch((e: Error) => active && setError(e.message));
    return () => { active = false; };
  }, [selected, days]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const created = await api.addIntervention(form);
      setForm({ at: nowLocal(), title: "", note: "", camera_id: "" });
      setSelected(created.id);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const remove = async (id: number) => {
    if (!confirm("Hapus intervensi ini?")) return;
    try {
      await api.deleteIntervention(id);
      if (selected === id) setSelected(null);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="stack">
      {canEdit && <div className="av-card">
        <h3>Catat intervensi K3</h3>
        <p className="muted small">
          Contoh: safety briefing, pemasangan rambu, pembagian APD baru. Sistem membandingkan kepatuhan sebelum dan sesudahnya.
        </p>
        <form className="form-grid" onSubmit={add}>
          <label className="av-field"><span>Mulai berlaku</span>
            <input className="av-input" type="datetime-local" required value={form.at} onChange={(e) => setForm({ ...form, at: e.target.value })} /></label>
          <label className="av-field"><span>Judul</span>
            <input className="av-input" required maxLength={120} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="Safety briefing sarung tangan" /></label>
          <label className="av-field"><span>Kamera (kosong = semua)</span>
            <input className="av-input" maxLength={64} value={form.camera_id} onChange={(e) => setForm({ ...form, camera_id: e.target.value })}
              placeholder="CAM-01" /></label>
          <label className="av-field"><span>Catatan</span>
            <input className="av-input" maxLength={1000} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
          <div className="av-field"><span>&nbsp;</span><button className="av-btn primary">Simpan</button></div>
        </form>
      </div>}
      {error && <p className="error">{error}</p>}

      <div className="grid-2">
        <div className="av-card">
          <h3>Daftar intervensi</h3>
          {!items.length && <p className="muted small">Belum ada intervensi.</p>}
          <div className="table-wrap">
            <table className="av-table">
              <tbody>{items.map((i) => (
                <tr key={i.id}>
                  <td>
                    <button className="link" onClick={() => setSelected(i.id)} aria-pressed={selected === i.id}>
                      {selected === i.id ? <b>{i.judul}</b> : i.judul}
                    </button>
                    <div className="muted small">{new Date(i.waktu).toLocaleString("id-ID")} · {i.kamera ?? "semua kamera"}</div>
                  </td>
                  <td className="num">{canEdit && <button className="link small" onClick={() => remove(i.id)}>Hapus</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>

        <div className="av-card panel">
          <div className="row gap between wrap">
            <h3>Sebelum vs sesudah</h3>
            <label className="row gap small">Periode pembanding
              <GlassSelect
                ariaLabel="Periode pembanding"
                value={String(days)}
                onChange={(v) => setDays(Number(v))}
                options={[3, 7, 14, 30].map((d) => ({ value: String(d), label: `${d} hari` }))}
              />
            </label>
          </div>
          {!cmp ? <p className="muted small">Pilih intervensi.</p> : (
            <>
              <div className="table-wrap">
                <table className="av-table">
                  <thead><tr><th></th><th className="num">Sebelum</th><th className="num">Sesudah</th><th className="num">Perubahan</th></tr></thead>
                  <tbody>
                    <tr><td>Compliance rate</td><td className="num">{fmtNum(cmp.sebelum.compliance_rate)}%</td>
                      <td className="num">{fmtNum(cmp.sesudah.compliance_rate)}%</td>
                      <td className="num"><Delta v={cmp.perubahan.compliance_rate} higherIsBetter suffix=" poin" /></td></tr>
                    <tr><td>Pelanggaran / hari</td><td className="num">{fmtNum(cmp.sebelum.pelanggaran_per_hari)}</td>
                      <td className="num">{fmtNum(cmp.sesudah.pelanggaran_per_hari)}</td>
                      <td className="num"><Delta v={cmp.perubahan.pelanggaran_per_hari} higherIsBetter={false} /></td></tr>
                    <tr><td>Paparan / hari (mnt)</td><td className="num">{fmtNum(cmp.sebelum.menit_paparan_per_hari)}</td>
                      <td className="num">{fmtNum(cmp.sesudah.menit_paparan_per_hari)}</td>
                      <td className="num"><Delta v={cmp.perubahan.menit_paparan_per_hari} higherIsBetter={false} /></td></tr>
                    <tr><td className="muted">Pekerja terpantau</td><td className="num muted">{cmp.sebelum.pekerja}</td>
                      <td className="num muted">{cmp.sesudah.pekerja}</td><td /></tr>
                    <tr><td className="muted">Hari data</td><td className="num muted">{fmtNum(cmp.sebelum.hari)}</td>
                      <td className="num muted">{fmtNum(cmp.sesudah.hari)}</td><td /></tr>
                  </tbody>
                </table>
              </div>
              <p><b>{cmp.kesimpulan}</b>
                {cmp.uji_statistik && <span className="muted small"> (uji z dua proporsi, p = {fmtNum(cmp.uji_statistik.p_value, 4)})</span>}</p>
              <p className="note small">{cmp.catatan}</p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
