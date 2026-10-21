import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, can, fmtNum, type CameraHealth, type Job, type JobStatus, type ModelQuality, type SystemStatus } from "../../api";
import { useMe } from "../../session";
import "./admin-pages.css";

const JOB_STATUS: Record<JobStatus, string> = {
  menunggu: "Menunggu", berjalan: "Berjalan", dijeda: "Dijeda",
  selesai: "Selesai", dibatalkan: "Dibatalkan", gagal: "Gagal",
};

function fmtEta(s: number | null): string {
  if (s == null) return "–";
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m >= 60 ? `${Math.floor(m / 60)}j ${m % 60}m` : `${m}m ${String(sec).padStart(2, "0")}d`;
}

function fmtRecordingStart(iso: string): string {
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return "–";
  return value.toLocaleString("id-ID", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

// Warna grafik mengikuti token di styles.css (var()), bukan hex lepas.
const AXIS = { stroke: "var(--muted)", fontSize: 11 };
const TIP = { contentStyle: { background: "var(--panel-2)", border: "1px solid var(--line)", borderRadius: 6 } };
const STATUS = { baik: "Baik", peringatan: "Perlu dicek", tidak_ada_data: "Belum ada data" };

function jobProgress(job: Job): number {
  if (job.status === "selesai") return 100;
  if (!job.durasi_video_s) return 0;
  return Math.min(100, Math.max(0, (job.position_s / job.durasi_video_s) * 100));
}

function jobStatusLabel(job: Job, progress: number): string {
  if (job.status === "menunggu") return "Menunggu antrean";
  if (job.status === "berjalan") return `Berjalan ${fmtNum(progress, 0)}%`;
  return JOB_STATUS[job.status] || job.status;
}

function jobStatusTone(status: JobStatus): string {
  if (status === "selesai") return "good";
  if (status === "berjalan" || status === "dijeda" || status === "menunggu") return "warn";
  return status === "gagal" || status === "dibatalkan" ? "bad" : "muted";
}

function CameraCard({ c }: { c: CameraHealth }) {
  const data = c.per_jam.map((x) => ({ ...x, label: x.jam.slice(11, 16) }));
  return (
    <article className="av-card system-camera-card">
      <div className="row between">
        <b><span className={`status-dot ${c.status}`} />{c.kamera}</b>
        <span className={`${c.status === "peringatan" ? "bad" : c.status === "baik" ? "good" : "muted"} small`}>{STATUS[c.status]}</span>
      </div>
      {c.masalah.length > 0 && <ul className="plain small">{c.masalah.map((m) => <li key={m}>{m}</li>)}</ul>}
      {c.info.length > 0 && <ul className="plain small muted">{c.info.map((m) => <li key={m}>{m}</li>)}</ul>}
      <div className="muted small">
        {c.jendela_menit} menit terakhir: kecerahan {fmtNum(c.terkini.brightness, 0)} · ketajaman {fmtNum(c.terkini.sharpness, 0)}
        {" "}(biasanya {fmtNum(c.baseline.sharpness, 0)}) · conf orang {fmtNum(c.terkini.person_conf, 2)}
        {" "}(biasanya {fmtNum(c.baseline.person_conf, 2)})
      </div>
      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={data}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="label" {...AXIS} interval="preserveStartEnd" />
          <YAxis {...AXIS} width={34} />
          <Tooltip {...TIP} />
          <Line type="monotone" dataKey="ketajaman" name="Ketajaman" stroke="var(--accent)" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="kecerahan" name="Kecerahan" stroke="var(--warn)" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
      <div className="muted small">24 jam terakhir, rata-rata per jam. Putih = ketajaman, kuning = kecerahan.</div>
    </article>
  );
}

function EmptyCameraCard({ camera }: { camera: SystemStatus["kamera"][number] }) {
  return (
    <article className="av-card system-camera-card system-camera-empty">
      <div className="row between">
        <b><span className="status-dot tidak_ada_data" />{camera.kamera}</b>
        <span className="muted small">Belum ada data</span>
      </div>
      <p className="muted small">Data kualitas belum terkumpul. Statistik tercatat otomatis saat pipeline kamera berjalan.</p>
      <p className="muted small">
        Status runtime: {camera.running ? "aktif" : "belum aktif"} · {fmtNum(camera.fps)} fps · {fmtNum(camera.pemeriksaan_per_detik)}×/dtk diperiksa
      </p>
      <div className="system-camera-placeholder" role="img" aria-label={`Grafik kesehatan ${camera.kamera} belum tersedia`}>
        <span className="muted small">Grafik 24 jam belum tersedia</span>
      </div>
      <div className="muted small">Ketajaman dan kecerahan akan tampil setelah data kualitas tersedia.</div>
    </article>
  );
}

export default function System() {
  const [cams, setCams] = useState<CameraHealth[] | null>(null);
  const [quality, setQuality] = useState<ModelQuality[] | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [projectionCameras, setProjectionCameras] = useState(1);
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState<string | null>(null);

  const me = useMe();
  const canJobs = can(me, "supervisor");
  const isAdmin = can(me, "admin");
  const loadJobs = () => api.jobs().then(setJobs).catch(() => {});
  useEffect(() => {
    api.camerasHealth().then(setCams).catch((e: Error) => setError(e.message));
    api.modelQuality().then(setQuality).catch((e: Error) => setError(e.message));
    api.systemStatus().then(setStatus).catch(() => {});
    if (canJobs) loadJobs();
  }, [canJobs]);
  const controlJob = async (id: string, action: "pause" | "resume" | "cancel") => {
    await api.controlJob(id, action).then(loadJobs).catch((e) => alert(e.message));
  };
  const exportReviews = async () => {
    setExporting(true);
    setExportMessage(null);
    try {
      const filename = await api.exportModelReviews();
      setExportMessage(`Ekspor selesai diunduh sebagai ${filename}.`);
      setError(null);
    } catch (e) {
      setError(`Ekspor gagal: ${(e as Error).message}`);
    } finally {
      setExporting(false);
    }
  };
  const capacity = status?.kapasitas;
  const projection = capacity?.proyeksi_kamera.find((item) => item.jumlah === projectionCameras);

  return (
    <div className="system-page">
      {error && <p className="error">{error}</p>}
      <div className="system-health-heading">
        <h3>Kesehatan kamera</h3>
        <p className="muted small">
          Dibandingkan dengan jam yang sama di hari-hari sebelumnya. Peringatan muncul jika gambar terlalu gelap, jauh lebih buram,
          atau keyakinan deteksi turun. Itu tanda lensa kotor, kamera bergeser, atau penerangan berubah.
        </p>
      </div>
      {!cams ? <p className="muted">Memuat…</p> : cams.length
        ? <div className="cards-3">{cams.map((c) => <CameraCard key={c.kamera} c={c} />)}</div>
        : status?.kamera.length
          ? <div className="cards-3">{status.kamera.map((camera) => <EmptyCameraCard key={camera.kamera} camera={camera} />)}</div>
          : <p className="muted">Belum ada data kualitas. Data tercatat otomatis saat pipeline kamera berjalan.</p>}

      <section className="av-card system-quality-card">
        <div className="system-section-heading">
          <h3>Kualitas model di lapangan</h3>
          {isAdmin && <button type="button" className="av-btn sm" disabled={exporting} onClick={exportReviews}>{exporting ? "Menyiapkan ekspor…" : "Ekspor hasil review untuk training ulang"}</button>}
        </div>
        {exportMessage && <p className="good small">{exportMessage}</p>}
        <p className="muted small">
          Precision dihitung dari review supervisor: benar ÷ (benar + false positive). Bandingkan antarversi setelah model dilatih ulang
          dengan data hasil review. Angka hanya bermakna jika cakupan review cukup tinggi.
        </p>
        {!quality ? <p className="muted">Memuat…</p> : !quality.length ? <p className="muted small">Belum ada data.</p> : (
          <div className="table-wrap">
            <table className="av-table">
              <thead><tr><th>Versi</th><th className="num">Event</th><th className="num">Cakupan review</th>
                <th className="num">Precision</th>
                {quality[0].per_apd.map((x) => <th key={x.item} className="num">{x.label}</th>)}</tr></thead>
              <tbody>{quality.map((q) => (
                <tr key={q.versi}>
                  <td>{q.versi}</td><td className="num">{q.event}</td>
                  <td className="num">{q.cakupan_review != null ? `${fmtNum(q.cakupan_review * 100, 0)}%` : "–"}</td>
                  <td className="num"><b>{q.precision != null ? `${fmtNum(q.precision * 100)}%` : "–"}</b></td>
                  {q.per_apd.map((x) => (
                    <td key={x.item} className="num">{x.precision != null ? `${fmtNum(x.precision * 100, 0)}%` : "–"}
                      <span className="muted small"> ({x.confirmed}/{x.confirmed + x.false_positive})</span></td>
                  ))}
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>

      <section className="av-card system-capacity-card">
        <div className="system-section-heading">
          <h3>Kapasitas pemrosesan</h3>
          {capacity && <span className={status?.kamera.some((camera) => camera.di_bawah_batas) ? "bad small" : "good small"}>{status?.kamera.some((camera) => camera.di_bawah_batas) ? "Ada kamera di bawah batas aman" : "Semua kamera di atas batas aman"}</span>}
        </div>
        <p className="muted small" style={{ marginBottom: 12 }}>
          Setiap kamera dibaca terus-menerus oleh prosesnya sendiri. Frame terbaru dari semua kamera diproses serentak dalam satu batch oleh model yang sama,
          jadi tidak ada kamera yang menunggu giliran. Peringatan muncul jika sebuah kamera diperiksa kurang dari {fmtNum(capacity?.batas_aman_per_kamera)} kali per detik.
        </p>
        <div className="system-capacity-kpis">
          <div className="av-card av-kpi"><div className="av-label">Waktu per batch</div><b>{capacity?.waktu_batch_ms != null ? `${fmtNum(capacity.waktu_batch_ms)} ms` : "–"}</b><div className="muted small">{capacity?.jumlah_frame_batch ?? 0} frame diproses sekaligus</div></div>
          <div className="av-card av-kpi"><div className="av-label">Pemeriksaan per kamera</div><b>{capacity?.pemeriksaan_per_kamera != null ? `${fmtNum(capacity.pemeriksaan_per_kamera)}×/dtk` : "–"}</b><div className="muted small">batas aman {fmtNum(capacity?.batas_aman_per_kamera)}×/dtk</div></div>
          <div className="av-card av-kpi"><div className="av-label">Beban perangkat</div><b>{capacity?.beban_perangkat_persen != null ? `${fmtNum(capacity.beban_perangkat_persen)}%` : "–"}</b><div className="muted small">{capacity?.perangkat ?? "–"} · {capacity?.beban_perangkat_sumber ?? "belum tersedia"}</div></div>
        </div>
        <div className="table-wrap">
          <table className="av-table">
            <thead><tr><th>Kamera</th><th className="num">Fps kamera</th><th className="num">Diperiksa</th><th className="num">Frame dilewati</th><th>Keterangan</th></tr></thead>
            <tbody>
              {status?.kamera.map((k) => (
                <tr key={k.kamera}>
                  <td><span className={`status-dot ${k.di_bawah_batas ? "peringatan" : "baik"}`} />{k.kamera}</td>
                  <td className="num">{fmtNum(k.fps)}</td>
                  <td className="num">{fmtNum(k.pemeriksaan_per_detik)}×/dtk</td>
                  <td className="num">{fmtNum(k.persen_frame_dilewati)}%</td>
                  <td className={k.di_bawah_batas ? "bad small" : "muted small"}>{k.di_bawah_batas ? "Di bawah batas aman" : "Normal"}</td>
                </tr>
              ))}
              {!status?.kamera.length && <tr><td colSpan={5} className="muted small">Pipeline belum berjalan.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="muted small system-capacity-note">Frame dilewati berarti kamera mengirim gambar lebih cepat daripada yang perlu diperiksa; yang dipakai selalu frame terbaru, jadi tidak ada jeda yang menumpuk. Pelanggaran baru dicatat setelah statusnya stabil, sehingga pemeriksaan tetap cukup untuk menangkapnya.</p>
        <div className="row gap wrap" style={{ marginTop: 12 }}>
          <span className="muted small">Simulasi jumlah kamera:</span>
          <div className="seg" role="group" aria-label="Simulasi jumlah kamera">
            {(capacity?.proyeksi_kamera.map((item) => item.jumlah) ?? [1, 2, 3, 4]).map((count) => <button key={count} type="button" className={projectionCameras === count ? "on" : ""} onClick={() => setProjectionCameras(count)}>{count} kamera</button>)}
          </div>
          <span className={projection?.memenuhi_batas === false ? "bad small" : "muted small"}>
            {projection?.pemeriksaan_per_kamera != null ? `${fmtNum(projection.pemeriksaan_per_kamera)}×/dtk per kamera${projection.memenuhi_batas ? " · memenuhi batas" : " · di bawah batas"}` : "Belum ada throughput runtime untuk diproyeksikan"}
          </span>
        </div>
        {capacity && <p className="muted small" style={{ marginTop: 8 }}>{capacity.catatan_proyeksi}</p>}
      </section>

      <section className="av-card system-pipeline-card">
        <h3>Pipeline</h3>
        <div className="table-wrap">
          <table className="av-table">
            <tbody>
              <tr><td className="muted">Status</td><td><span className={`status-dot ${status?.pipeline.running ? "baik" : "peringatan"}`} />{status?.pipeline.running ? `Berjalan, ${status.kamera.filter((camera) => camera.running).length} kamera aktif` : `Berhenti${status?.pipeline.error ? `: ${status.pipeline.error}` : ""}`}</td></tr>
              <tr><td className="muted">Worker</td><td><span className={`status-dot ${status?.worker.sehat ? "baik" : "peringatan"}`} />{status ? (status.worker.sehat ? `Sehat, heartbeat ${fmtNum(status.worker.usia_heartbeat_detik)} detik lalu, ${status.worker.kamera_aktif} kamera aktif` : status.worker.status === "heartbeat_kedaluwarsa" ? `Heartbeat kedaluwarsa (${fmtNum(status.worker.usia_heartbeat_detik)} detik)` : "Heartbeat belum tersedia") : "Memuat…"}</td></tr>
              <tr><td className="muted">Model</td><td>{status ? `Pose ${status.model.pose} + APD ${status.versi_model} (${status.model.apd}), deteksi APD setiap ${status.model.apd_setiap_n_frame} frame` : "Memuat…"}</td></tr>
              <tr><td className="muted">Alert Telegram</td><td>{status ? `${status.alert.telegram ? "Aktif" : "Nonaktif"}, jeda ${status.alert.cooldown_s} detik, ${status.alert.reviewer_terhubung} akun reviewer terhubung` : "Memuat…"}</td></tr>
              <tr><td className="muted">Retensi</td><td>{status ? `Evidence ${status.retensi.evidence_days} hari, data kualitas ${status.retensi.quality_days} hari, video ${status.retensi.video_days} hari` : "Memuat…"}</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      {canJobs && (
        <section className="av-card system-jobs-card">
          <div className="system-section-heading">
            <h3>Pemrosesan rekaman</h3>
            <p className="muted small">Video mode analisis diproses di latar belakang, satu per satu, bersama model yang sama dengan kamera live.</p>
          </div>
          <div className="table-wrap">
            <table className="av-table system-jobs-table">
              <thead><tr><th>Rekaman</th><th>Mulai rekaman</th><th>Progres</th><th>Event</th><th></th></tr></thead>
              <tbody>
                {jobs?.map((j) => {
                  const progress = jobProgress(j);
                  return (
                    <tr key={j.id}>
                      <td><b>{j.camera_id}{j.zona ? ` ${j.zona}` : ""}</b> <span className="muted small">{j.nama_file || j.video_id}</span></td>
                      <td className="small">{fmtRecordingStart(j.recording_start)}</td>
                      <td>
                        <span className={`small ${jobStatusTone(j.status)}`}>{jobStatusLabel(j, progress)}</span>
                        <span className="system-job-progress"><i style={{ width: `${progress}%` }} /></span>
                        {j.perkiraan_sisa_s != null && <span className="muted small">sisa ±{fmtEta(j.perkiraan_sisa_s)}</span>}
                      </td>
                      <td className="small">{j.events ? `${j.events} pelanggaran${j.status === "berjalan" ? " sejauh ini" : ""}` : "–"}</td>
                      <td><span className="system-job-actions">
                        {j.video_hasil && <a className="small" href={j.video_hasil} target="_blank" rel="noreferrer">Buka hasil</a>}
                        {isAdmin && j.status === "berjalan" && <button className="link small" onClick={() => controlJob(j.id, "pause")}>Jeda</button>}
                        {isAdmin && j.status === "dijeda" && <button className="link small" onClick={() => controlJob(j.id, "resume")}>Lanjut</button>}
                        {isAdmin && j.status !== "selesai" && j.status !== "dibatalkan" && j.status !== "gagal" && <button className="link small" onClick={() => controlJob(j.id, "cancel")}>Batalkan</button>}
                      </span></td>
                    </tr>
                  );
                })}
                {!jobs?.length && <tr><td colSpan={5} className="muted small">Tidak ada pekerjaan analisis.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="muted small system-jobs-note">Pemrosesan yang terhenti karena server dimulai ulang dilanjutkan dari posisi terakhir. Hasilnya muncul di Evidence dan Analitik dengan waktu sesuai jam rekaman.</p>
        </section>
      )}
    </div>
  );
}
