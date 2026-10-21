import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { api, fmtNum, PERIODS, type CameraRow, type Source, type Stats } from "../../api";
import { PeriodPicker } from "../../components";
import { GlassSelect } from "../../ui";
import "./analytics.css";

// Warna grafik mengikuti token tema (var()), bukan hex lepas.
// Batang biasa = putih redup; jam rawan = putih terang (bukan oranye/merah). Koral hanya untuk pelanggaran.
const AXIS = { stroke: "var(--av-mute-2)", fontSize: 12 };
const TIP = { contentStyle: { background: "var(--av-pop)", border: "1px solid var(--av-line-2)", borderRadius: 12, color: "var(--av-ink)" } };
const BAR_NORMAL = "rgba(255,255,255,.34)";
const BAR_RAWAN = "var(--av-ink)";

const SOURCES: [string, string][] = [["nyata", "Kamera + rekaman"], ["live", "Kamera saja"], ["semua", "Termasuk simulasi"]];

const cameraLabel = (c: CameraRow) => c.name || c.id;

export default function Analytics() {
  const [period, setPeriod] = useState("7_hari");
  const [source, setSource] = useState<Source>("nyata");
  const [camera, setCamera] = useState("semua");
  const [s, setS] = useState<Stats | null>(null);
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.cameras().then(setCameras).catch(() => setCameras([]));
  }, []);

  const cameraOptions = useMemo(() => [...cameras].sort((a, b) => cameraLabel(a).localeCompare(cameraLabel(b), "id")), [cameras]);
  const selectedCamera = cameraOptions.find((c) => c.id === camera);
  const selectedCameraIds = selectedCamera ? [selectedCamera.id] : [];

  useEffect(() => {
    if (camera !== "semua" && !selectedCamera) setCamera("semua");
  }, [camera, selectedCamera]);

  useEffect(() => {
    let active = true; // abaikan respons lama jika periode sudah diganti
    setError(null);
    api.stats(period, source, selectedCameraIds)
      .then((r) => active && setS(r))
      .catch((e: Error) => active && setError(e.message));
    return () => { active = false; };
  }, [period, source, selectedCameraIds.join(",")]);

  if (error) return <p className="error">Gagal memuat analitik: {error}</p>;
  if (!s) return <p className="muted">Memuat…</p>;
  const hours = s.per_jam.filter((h) => h.jam >= 6 && h.jam <= 19);
  const rawan = new Set(s.jam_rawan);

  return (
    <div className="analytics-page">
      <div className="analytics-toolbar">
        <div className="analytics-period">
          <PeriodPicker value={period} onChange={setPeriod} options={PERIODS} />
        </div>
        <div className="analytics-filters">
          <span className="analytics-source-label">Sumber data</span>
          <PeriodPicker value={source} onChange={(v) => setSource(v as Source)} options={SOURCES} />
          <div className="analytics-camera">
            <GlassSelect
              ariaLabel="Kamera"
              value={camera}
              onChange={setCamera}
              options={[{ value: "semua", label: "Semua kamera" }, ...cameraOptions.map((c) => ({ value: c.id, label: cameraLabel(c) }))]}
            />
          </div>
          <a className="av-btn analytics-report" href={`/api/reports/k3?period=${period}`} target="_blank" rel="noreferrer">
            Laporan K3 (cetak / PDF)
          </a>
        </div>
      </div>
      {(source === "semua" || selectedCamera) && (
        <div className="analytics-notes">
          {source === "semua" && <p className="note small">Termasuk data dari video simulasi. Laporan K3 tetap tanpa simulasi.</p>}
          {selectedCamera && <p className="note small">Analitik difilter ke kamera {cameraLabel(selectedCamera)}.</p>}
        </div>
      )}

      <div className="analytics-kpis">
        <div className="av-card analytics-kpi">
          <div className="analytics-kpi-label">Compliance rate</div>
          <div className="analytics-kpi-value good">{s.compliance_rate != null ? `${fmtNum(s.compliance_rate)}%` : "–"}</div>
          <div className="muted small">{s.pekerja_patuh} dari {s.pekerja_terpantau} pekerja patuh</div>
        </div>
        <div className="av-card analytics-kpi analytics-kpi-exposure">
          <div className="analytics-kpi-label">Paparan tanpa APD</div>
          <div className="analytics-kpi-value bad">{fmtNum(s.menit_paparan)} mnt</div>
          <div className="muted small">orang-menit, rata-rata {fmtNum(s.rata_durasi_pelanggaran_detik, 0)} detik per pelanggaran</div>
        </div>
        <div className="av-card analytics-kpi">
          <div className="analytics-kpi-label">Pekerja melanggar</div>
          <div className="analytics-kpi-value">{s.pekerja_melanggar}</div>
          <div className="muted small">dari {s.event_pelanggaran} event · {s.event_tidak_pasti} tidak pasti</div>
        </div>
        <div className="av-card analytics-kpi">
          <div className="analytics-kpi-label">Menunggu review</div>
          <div className="analytics-kpi-value warn">{s.menunggu_review}</div>
          <a className="analytics-review-link small" href="#evidence">Buka antrean review</a>
        </div>
      </div>

      <div className="analytics-grid">
        <section className="av-card analytics-card analytics-compliance">
          <h3>Kepatuhan per APD</h3>
          <p className="muted small">Bar = persentase pekerja patuh. Paparan = total orang-menit tanpa APD tersebut.</p>
          <div className="analytics-bars">
            {s.kepatuhan_per_apd.map((x) => (
              <div key={x.item} className="analytics-bar-row">
                <span className="analytics-apd-label">{x.label}</span>
                <div className="analytics-bar-track">
                  <div className="analytics-bar-fill" style={{ width: `${x.persen ?? 0}%` }} />
                </div>
                <span className="analytics-bar-value num">
                  {fmtNum(x.persen)}% <span className="muted small">({x.patuh}/{x.total})</span>
                  <br /><span className="muted small">{fmtNum(s.menit_paparan_per_apd.find((m) => m.item === x.item)?.menit)} mnt</span>
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="av-card analytics-card analytics-chart-card">
          <h3>Pekerja melanggar per jam</h3>
          <p className="muted small">Pekerja yang memicu beberapa event tetap dihitung satu orang. Batang yang lebih terang menandai jam rawan.</p>
          <div className="analytics-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={hours} margin={{ top: 24, right: 2, bottom: 0, left: 2 }}>
              <XAxis dataKey="jam" tickFormatter={(h) => String(h).padStart(2, "0")} tickLine={false} axisLine={{ stroke: "rgba(255,255,255,.12)" }} {...AXIS} />
              <Tooltip {...TIP} cursor={{ fill: "rgba(255,255,255,.05)" }} labelFormatter={(h) => `Jam ${h}:00`} formatter={(v) => [v, "Pekerja melanggar"]} />
              <Bar dataKey="jumlah" radius={[8, 8, 2, 2]} maxBarSize={20} isAnimationActive={false}>
                {hours.map((h) => <Cell key={h.jam} fill={rawan.has(h.jam) ? BAR_RAWAN : BAR_NORMAL} />)}
                <LabelList dataKey="jumlah" position="top" fill="var(--av-mute-2)" fontSize={11} />
              </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="av-card analytics-card analytics-chart-card analytics-daily">
          <h3>Pekerja melanggar per hari</h3>
          <div className="analytics-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={s.per_hari} margin={{ top: 24, right: 2, bottom: 0, left: 2 }}>
              <XAxis dataKey="tanggal" tickFormatter={(d: string) => {
                const [, month, day] = d.split("-");
                return `${Number(day)}/${Number(month)}`;
              }} tickLine={false} axisLine={{ stroke: "rgba(255,255,255,.12)" }} {...AXIS} />
              <Tooltip {...TIP} cursor={{ fill: "rgba(255,255,255,.05)" }} formatter={(v) => [v, "Pekerja melanggar"]} />
              <Bar dataKey="jumlah" fill={BAR_NORMAL} radius={[8, 8, 2, 2]} maxBarSize={20} isAnimationActive={false}>
                <LabelList dataKey="jumlah" position="top" fill="var(--av-mute-2)" fontSize={11} />
              </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="av-card analytics-card analytics-breakdown">
          <h3>Per shift</h3>
          <div className="table-wrap">
            <table className="av-table analytics-table analytics-shift-table">
              <thead><tr><th>Shift</th><th className="num">Pekerja</th><th className="num">Kepatuhan</th>
                <th className="num">Pekerja melanggar</th><th className="num">Event</th><th className="num">Paparan (mnt)</th></tr></thead>
              <tbody>{s.per_shift.map((x) => (
                <tr key={x.shift}><td>{x.shift}</td><td className="num">{x.pekerja}</td>
                  <td className="num">{x.compliance_rate != null ? `${fmtNum(x.compliance_rate)}%` : "–"}</td>
                  <td className="num">{x.pekerja_melanggar ?? x.pelanggaran}</td><td className="num">{x.pelanggaran}</td><td className="num">{fmtNum(x.menit_paparan)}</td></tr>
              ))}</tbody>
            </table>
          </div>
          <h3>Per kamera & zona</h3>
          <div className="analytics-area-tables">
            <div className="table-wrap">
              <table className="av-table analytics-table">
                <thead><tr><th>Kamera</th><th className="num">Pekerja melanggar</th><th className="num">Event</th></tr></thead>
                <tbody>{s.per_kamera.length ? s.per_kamera.map((c) => {
                  const cam = cameras.find((x) => x.id === c.kamera);
                  return <tr key={c.kamera}><td>{cam ? cameraLabel(cam) : c.kamera}</td><td className="num">{c.jumlah}</td><td className="num">{c.event ?? c.jumlah}</td></tr>;
                }) : <tr><td className="analytics-empty" colSpan={3}>Belum ada data kamera.</td></tr>}</tbody>
              </table>
            </div>
            <div className="table-wrap">
              <table className="av-table analytics-table">
                <thead><tr><th>Zona</th><th className="num">Pekerja melanggar</th><th className="num">Event</th></tr></thead>
                <tbody>{s.per_zona.length ? s.per_zona.map((z) => <tr key={z.zona}><td>{z.zona}</td><td className="num">{z.jumlah}</td><td className="num">{z.event ?? z.jumlah}</td></tr>) : <tr><td className="analytics-empty" colSpan={3}>Belum ada data zona.</td></tr>}</tbody>
              </table>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
