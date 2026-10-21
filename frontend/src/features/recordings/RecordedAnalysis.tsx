import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, fmtNum, fmtTime, type CameraRow, type Job, type JobStatus, type VideoRow, type Violation } from "../../api";
import { uploadVideo } from "./utils/uploadVideo";
import { SafeVideo } from "./components/SafeVideo";
import { EvidenceModal } from "../../components";
import { GlassSelect } from "../../ui";
import "./recorded-analysis.css";

const JOB_STATUS: Record<JobStatus, string> = {
  menunggu: "Menunggu",
  berjalan: "Berjalan",
  dijeda: "Dijeda",
  selesai: "Selesai",
  dibatalkan: "Dibatalkan",
  gagal: "Gagal",
};

function RecordedVideoDialog({ job, onClose }: { job: Job; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
      }
      if (event.key !== "Tab") return;
      const dialog = closeRef.current?.closest(".rec-video-dialog");
      const focusable = dialog ? Array.from(dialog.querySelectorAll<HTMLElement>("button, video[controls]")) : [];
      if (!focusable.length) return;
      if (event.shiftKey && document.activeElement === focusable[0]) {
        event.preventDefault();
        focusable[focusable.length - 1].focus();
      } else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) {
        event.preventDefault();
        focusable[0].focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); previous?.focus(); };
  }, []);

  return createPortal(
    <div className="modal-bg rec-video-backdrop" onClick={onClose}>
      <div className="modal rec-video-dialog" role="dialog" aria-modal="true" aria-label="Video hasil rekaman" onClick={(event) => event.stopPropagation()}>
        <div className="rec-video-dialog-head"><div><b>{job.camera_id} · {job.zona || "Tanpa zona"}</b><span>{job.nama_file || job.video_id}</span></div><button ref={closeRef} type="button" className="modal-close inline" onClick={onClose} aria-label="Tutup video hasil">×</button></div>
        <SafeVideo src={job.video_hasil!} className="modal-video" label="Video hasil di jendela" />
      </div>
    </div>, document.body,
  );
}

function fmtEta(s: number | null): string {
  if (s == null) return "–";
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m >= 60 ? `${Math.floor(m / 60)}j ${m % 60}m` : `${m}m ${String(sec).padStart(2, "0")}d`;
}

function fmtDuration(s: number | null): string {
  if (s == null) return "–";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.round(s % 60);
  return h ? `${h}j ${m}m` : `${m}m ${String(sec).padStart(2, "0")}d`;
}

function jobProgress(j: Job | null): number | null {
  if (!j?.durasi_video_s) return null;
  return Math.min(100, Math.max(0, (j.position_s / j.durasi_video_s) * 100));
}

function jobEndIso(j: Job): string | null {
  if (!j.recording_start || !j.durasi_video_s) return null;
  const start = new Date(j.recording_start);
  if (Number.isNaN(start.getTime())) return null;
  return new Date(start.getTime() + j.durasi_video_s * 1000).toISOString();
}

function fmtClock(iso: string | null): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "–";
  return new Intl.DateTimeFormat("id-ID", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date).replace(".", ".");
}

function timelineLabels(job: Job): { left: number; label: string }[] {
  const start = new Date(job.recording_start);
  if (Number.isNaN(start.getTime()) || !job.durasi_video_s) return [];
  return [0, 0.25, 0.5, 0.75, 1].map((part) => ({
    left: part * 100,
    label: fmtClock(new Date(start.getTime() + job.durasi_video_s! * part * 1000).toISOString()),
  }));
}

export default function RecordedAnalysis() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [recent, setRecent] = useState<Violation[]>([]);
  const [open, setOpen] = useState<Violation | null>(null);
  const [videoWindowJobId, setVideoWindowJobId] = useState<string | null>(null);
  const videoTriggerRef = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [eventError, setEventError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [videos, setVideos] = useState<VideoRow[]>([]);
  const [newCamera, setNewCamera] = useState("");
  const [creatingCamera, setCreatingCamera] = useState(false);
  const [newCameraId, setNewCameraId] = useState("");
  const [newCameraName, setNewCameraName] = useState("");
  const [newVideo, setNewVideo] = useState("");
  const [recordingStart, setRecordingStart] = useState("");
  const [sampleRate, setSampleRate] = useState("5");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [controlling, setControlling] = useState<"pause" | "resume" | "cancel" | null>(null);
  const uploadIdRef = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => {
      api.jobs()
        .then((rows) => {
          if (!active) return;
          const sorted = [...rows].sort((a, b) => (b.started_at || b.recording_start || "").localeCompare(a.started_at || a.recording_start || ""));
          setJobs(sorted);
          setSelected((cur) => cur || sorted.find((j) => j.video_hasil)?.id || sorted[0]?.id || "");
          setError(null);
        })
        .catch((e: Error) => active && setError(e.message));
    };
    load();
    const t = window.setInterval(load, 3000);
    return () => { active = false; window.clearInterval(t); };
  }, []);

  useEffect(() => {
    if (!addOpen) return;
    Promise.all([api.cameras(), api.videos()])
      .then(([cameraRows, videoRows]) => {
        setCameras(cameraRows);
        setVideos(videoRows);
        setNewCamera((current) => current || cameraRows[0]?.id || "");
      })
      .catch((e: Error) => setError(e.message));
  }, [addOpen]);

  const uploadRecording = async (file: File) => {
    uploadIdRef.current = null;
    setUploading(true);
    setUploadProgress(0);
    try {
      const videoId = await uploadVideo(file, uploadIdRef, setUploadProgress, () => false);
      setNewVideo(videoId);
      setUploadProgress(100);
      const videoRows = await api.videos();
      setVideos(videoRows);
      setError(null);
    } catch (e) {
      setError(`Unggah rekaman gagal: ${(e as Error).message}`);
      setUploadProgress(null);
    } finally {
      setUploading(false);
    }
  };

  const createRecordingJob = async (e: React.FormEvent) => {
    e.preventDefault();
    const cameraId = creatingCamera ? newCameraId.trim() : newCamera;
    const cameraName = newCameraName.trim();
    if (creatingCamera && cameras.some((camera) => camera.id === cameraId)) {
      setError(`ID kamera ${cameraId} sudah terdaftar. Pilih kamera yang ada atau gunakan ID lain.`);
      return;
    }
    setSaving(true);
    let cameraRegistered = false;
    try {
      if (creatingCamera) {
        const camera = await api.createCamera({
          id: cameraId, name: cameraName, source_kind: "video", video_id: newVideo,
          mode: "analisis", enabled: false,
        });
        cameraRegistered = true;
        setCameras((current) => [...current, camera]);
        setNewCamera(camera.id);
        setCreatingCamera(false);
      }
      const created = await api.createJob({
        camera_id: cameraId,
        video_id: newVideo,
        recording_start: recordingStart,
        sample_rate: Number(sampleRate),
      });
      setJobs((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setSelected(created.id);
      setAddOpen(false);
      setNewVideo("");
      setRecordingStart("");
      setNewCameraId("");
      setNewCameraName("");
      setUploadProgress(null);
      setError(null);
    } catch (err) {
      const detail = (err as Error).message;
      setError(cameraRegistered
        ? `Kamera ${cameraId} berhasil didaftarkan, tetapi analisis gagal dibuat: ${detail}. Pilih kamera tersebut dan coba lagi.`
        : `Analisis rekaman gagal dibuat: ${detail}`);
    } finally {
      setSaving(false);
    }
  };

  const job = useMemo(() => jobs.find((j) => j.id === selected) || jobs.find((j) => j.video_hasil) || jobs[0] || null, [jobs, selected]);
  const progress = jobProgress(job);
  const playable = !!job?.video_hasil;
  const videoWindowOpen = !!job?.video_hasil && videoWindowJobId === job.id;

  const controlJob = async (action: "pause" | "resume" | "cancel") => {
    if (!job || controlling) return;
    setControlling(action);
    try {
      const updated = await api.controlJob(job.id, action);
      setJobs((current) => current.map((item) => item.id === updated.id ? updated : item));
      setError(null);
    } catch (e) {
      setError(`Gagal mengendalikan analisis rekaman: ${(e as Error).message}`);
    } finally {
      setControlling(null);
    }
  };

  useEffect(() => {
    if (!job) {
      setRecent([]);
      return;
    }
    let active = true;
    const params: Record<string, string> = {
      start: job.recording_start,
      camera_id: job.camera_id,
      kind: "violation",
      source: "nyata",
      limit: "20",
    };
    const end = jobEndIso(job);
    if (end) params.end = end;
    api.violations(params)
      .then((r) => { if (active) { setRecent(r.items); setEventError(null); } })
      .catch((e: Error) => { if (active) { setRecent([]); setEventError(e.message); } });
    return () => { active = false; };
  }, [job]);

  const selesai = jobs.filter((j) => j.video_hasil).length;
  const berjalan = jobs.filter((j) => j.status === "berjalan" || j.status === "dijeda" || j.status === "menunggu").length;
  const videoHasil = job?.video_hasil || "";
  const progressValue = progress ?? (job?.status === "selesai" ? 100 : 0);
  const sedangMenyiapkanVideo = !!job && !playable && (job.status === "menunggu" || job.status === "berjalan" || job.status === "selesai");
  const judulVideoKosong = job
    ? job.status === "menunggu"
      ? "Menunggu giliran analisis"
      : job.status === "berjalan"
        ? "Menganalisis rekaman"
        : job.status === "dijeda"
          ? "Analisis rekaman dijeda"
          : job.status === "selesai"
            ? "Menyiapkan video hasil"
            : job.status === "gagal"
              ? "Analisis rekaman gagal"
              : job.status === "dibatalkan"
                ? "Analisis rekaman dibatalkan"
                : "Video hasil belum tersedia"
    : "Belum ada analisis rekaman";
  const pesanVideoKosong = job
    ? job.status === "selesai"
      ? "Pekerjaan sudah selesai. Video hasil deteksi sedang disiapkan dan akan tampil otomatis setelah tersedia."
      : job.status === "berjalan"
        ? "Sistem sedang memproses rekaman dan membuat video hasil deteksi. Dashboard akan diperbarui otomatis."
        : job.status === "menunggu"
          ? "Rekaman sudah masuk antrean dan akan diproses oleh worker."
          : job.status === "dijeda"
            ? "Pekerjaan dijeda. Lanjutkan pekerjaan agar video hasil deteksi bisa dibuat."
            : job.status === "gagal"
              ? "Pekerjaan gagal diproses. Periksa log worker atau ulangi analisis rekaman."
              : job.status === "dibatalkan"
                ? "Pekerjaan dibatalkan sehingga video hasil deteksi tidak dibuat."
                : `Status rekaman ${JOB_STATUS[job.status] || job.status}. Tunggu pekerjaan selesai agar video hasil deteksi bisa diputar di dashboard ini.`
    : "Tambahkan kamera video dengan mode Analisis rekaman di Pengaturan, lalu tunggu pekerjaan selesai.";

  const openEvidence = () => {
    location.hash = "evidence";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  };

  return (
    <div className="recorded-page">
      <div className="recorded-heading">
        <div>
          <div className="av-label">Rekaman CCTV</div>
          <h1>Analisis rekaman</h1>
        </div>
        <div className="recorded-heading-controls">
          {jobs.length > 0 && (
            <GlassSelect
              ariaLabel="Pilih rekaman"
              value={job?.id || ""}
              onChange={setSelected}
              className="rec-picker"
              panelMinWidth={460}
              renderValue={(opt) => {
                const j = jobs.find((x) => x.id === opt?.value) ?? job;
                if (!j) return "Pilih rekaman";
                return (
                  <span className="rec-picker-val">
                    {j.thumbnail ? <img className="rec-picker-thumb" src={j.thumbnail} alt="" /> : <span className="rec-picker-thumb empty" />}
                    <span className="rec-picker-copy">
                      <b>{j.camera_id} · {j.zona || "Tanpa zona"}</b>
                      <small>{j.nama_file || j.video_id}</small>
                    </span>
                    <span className={`rec-status ${j.status}`}><i aria-hidden="true" />{JOB_STATUS[j.status] || j.status}{j.status === "berjalan" && progress != null ? ` ${fmtNum(jobProgress(j), 0)}%` : ""}</span>
                  </span>
                );
              }}
              options={jobs.map((j) => ({
                value: j.id,
                label: (
                  <span className="rec-picker-opt">
                    {j.thumbnail ? <img className="rec-picker-thumb" src={j.thumbnail} alt="" /> : <span className="rec-picker-thumb empty" />}
                    <span className="rec-picker-copy">
                      <b>{j.camera_id} · {j.zona || "Tanpa zona"}</b>
                      <small>{j.nama_file || j.video_id} · {fmtTime(j.recording_start)}</small>
                    </span>
                    <span className="rec-picker-state">
                      <span className={`rec-status ${j.status}`}><i aria-hidden="true" />{JOB_STATUS[j.status] || j.status}{j.status === "berjalan" && jobProgress(j) != null ? ` ${fmtNum(jobProgress(j), 0)}%` : ""}</span>
                      <span className="rec-picker-progress"><i style={{ width: `${jobProgress(j) ?? (j.status === "selesai" ? 100 : 0)}%` }} /></span>
                    </span>
                  </span>
                ),
              }))}
            />
          )}
          <button type="button" className={`av-btn rec-add-toggle${addOpen ? " on" : ""}`} onClick={() => setAddOpen((openForm) => !openForm)}>{addOpen ? "Tutup form" : "+ Tambah rekaman"}</button>
        </div>
      </div>

      {addOpen && (
        <form className="av-card rec-add-form" onSubmit={createRecordingJob}>
            <div className="row between wrap"><h3>Tambah rekaman</h3><span className="muted small">Video diproses sekali di latar belakang.</span></div>
            <div className="form-grid">
              <div className="av-field"><span>Kamera asal</span>
                <div className="rec-camera-choice" role="group" aria-label="Asal kamera rekaman">
                  <button type="button" className={`av-btn sm${!creatingCamera ? " primary" : ""}`} aria-pressed={!creatingCamera} onClick={() => setCreatingCamera(false)}>Kamera terdaftar</button>
                  <button type="button" className={`av-btn sm${creatingCamera ? " primary" : ""}`} aria-pressed={creatingCamera} onClick={() => setCreatingCamera(true)}>Kamera baru</button>
                </div>
                {creatingCamera ? (
                  <div className="rec-new-camera-fields">
                    <label className="av-field"><span>ID kamera</span><input className="av-input" required maxLength={32} pattern="[A-Za-z0-9](?:[A-Za-z0-9._]|-)*" title="Gunakan huruf, angka, titik, garis bawah, atau strip" placeholder="Mis. CAM-05" value={newCameraId} onChange={(event) => setNewCameraId(event.target.value)} /></label>
                    <label className="av-field"><span>Nama lokasi</span><input className="av-input" required maxLength={64} placeholder="Mis. Area penyimpanan" value={newCameraName} onChange={(event) => setNewCameraName(event.target.value)} /></label>
                    <span className="muted small">Kamera rekaman baru terdaftar tanpa mengaktifkan stream live.</span>
                  </div>
                ) : (
                  <GlassSelect ariaLabel="Kamera asal rekaman" value={newCamera} onChange={setNewCamera}
                    options={cameras.map((camera) => ({ value: camera.id, label: camera.name === camera.id ? camera.id : `${camera.name} — ${camera.id}` }))} />
                )}
              </div>
              <label className="av-field"><span>Jam mulai rekaman</span>
                <input className="av-input" required type="datetime-local" value={recordingStart} onChange={(e) => setRecordingStart(e.target.value)} />
              </label>
            </div>
            <label className="av-field"><span>Unggah video baru</span>
              <input type="file" accept="video/*" disabled={uploading} onChange={(e) => { const file = e.target.files?.[0]; if (file) uploadRecording(file); }} />
            </label>
            {uploadProgress != null && <div className="row gap"><div className="bar-track" style={{ flex: 1 }}><div className="bar-fill" style={{ width: `${uploadProgress}%` }} /></div><span className="small">{uploadProgress}%</span></div>}
            <div className="form-grid">
              <label className="av-field"><span>Video terdaftar</span>
                <GlassSelect ariaLabel="Video rekaman" value={newVideo} onChange={setNewVideo} placeholder="Pilih atau unggah video"
                  options={videos.map((video) => ({ value: video.id, label: video.label, hint: `${fmtDuration(video.duration_s)} · ${fmtNum(video.size / 1024 / 1024)} MB` }))} />
              </label>
              <label className="av-field"><span>Pemeriksaan per detik</span>
                <GlassSelect ariaLabel="Pemeriksaan per detik rekaman" value={sampleRate} onChange={setSampleRate}
                  options={[{ value: "5", label: "5×/detik" }, { value: "10", label: "10×/detik" }]} />
              </label>
            </div>
            <div><button className="av-btn primary" disabled={saving || uploading || (creatingCamera ? !newCameraId.trim() || !newCameraName.trim() : !newCamera) || !newVideo || !recordingStart}>{saving ? "Menambahkan…" : "Tambahkan ke antrean"}</button></div>
        </form>
      )}

      {error && <p className="error rec-page-error">Gagal memuat analisis rekaman: {error}</p>}

      <div className="recorded-layout">
        <section className="recorded-main">
        <div className="av-stage recorded-video">
          {playable ? (
            videoWindowOpen ? <div className="video-off"><b>Video hasil sedang dibuka di jendela kecil</b></div> : <SafeVideo src={videoHasil} label="Video hasil analisis rekaman" speedClassName="recorded-speed av-speed floating" />
          ) : (
            <div className={`video-off${sedangMenyiapkanVideo ? " recorded-loading" : ""}`}>
              {job?.thumbnail && <img className="recorded-state-poster" src={job.thumbnail} alt="" />}
              {job?.thumbnail && <span className="recorded-state-dim" aria-hidden="true" />}
              {sedangMenyiapkanVideo ? <span className="av-spinner" aria-hidden="true" /> : job && <span className={`recorded-state-icon ${job.status}`} aria-hidden="true">{job.status === "gagal" ? "!" : job.status === "dibatalkan" ? "×" : "Ⅱ"}</span>}
              <b>{judulVideoKosong}</b>
              <span className="muted small">{pesanVideoKosong}</span>
              {job && !playable && (
                <div className="recorded-loading-progress" aria-label="Progres pembuatan video hasil">
                  <span style={{ width: `${progressValue}%` }} />
                </div>
              )}
              {job && !playable && ["menunggu", "berjalan", "dijeda"].includes(job.status) && (
                <div className="row gap wrap">
                  {job.status === "berjalan" && <button type="button" className="av-btn sm" disabled={controlling !== null} onClick={() => controlJob("pause")}>Jeda</button>}
                  {job.status === "dijeda" && <button type="button" className="av-btn sm" disabled={controlling !== null} onClick={() => controlJob("resume")}>Lanjutkan</button>}
                  <button type="button" className="av-btn ghost sm" disabled={controlling !== null} onClick={() => controlJob("cancel")}>Batalkan</button>
                </div>
              )}
            </div>
          )}
          {job && <span className={`av-stage-tag rec-stage-label ${job.status}`}><i aria-hidden="true" />{playable ? "HASIL REKAMAN" : JOB_STATUS[job.status].toUpperCase()} · {job.camera_id}</span>}
          {job && <span className="av-stage-tag rec-stage-progress">{fmtNum(progressValue, 0)}%</span>}
        </div>

        {job && (
          <div className="av-card rec-detail" aria-label="Detail rekaman">
            <div className="rec-detail-head">
              <div className="rec-detail-title">
                <h3>{job.camera_id}<span> · {job.zona || "Tanpa zona"}</span></h3>
                <p className="muted small">{job.nama_file || job.video_id} · mulai rekaman {fmtTime(job.recording_start)} · pemeriksaan {job.sample_rate}×/detik</p>
              </div>
              <div className="rec-detail-actions">
                {job.video_hasil && <button ref={videoTriggerRef} type="button" className="av-btn sm" onClick={() => setVideoWindowJobId(job.id)}>Buka video hasil</button>}
                <button type="button" className="av-btn ghost sm" onClick={openEvidence}>Lihat di Evidence</button>
              </div>
            </div>
            <div className="recorded-progress" aria-label="Progres analisis rekaman">
              <span style={{ width: `${progressValue}%` }} />
            </div>
            <div className="rec-position muted small">
              <span>Posisi {fmtDuration(job.position_s)} dari {fmtDuration(job.durasi_video_s)}</span>
              <span>sisa {fmtEta(job.perkiraan_sisa_s)}</span>
              {job.video_hasil_sha256 && <span className="av-mono">SHA-256 {job.video_hasil_sha256}</span>}
            </div>
            {job.durasi_video_s != null && job.durasi_video_s > 0 && (
              <div className="rec-timeline-wrap">
                <div className="rec-timeline" aria-label="Lini waktu pelanggaran rekaman">
                  {[12.5, 25, 37.5, 50, 62.5, 75, 87.5].map((left) => <span key={left} className="rec-timeline-grid" style={{ left: `${left}%` }} />)}
                {recent.map((v) => {
                  const endIso = jobEndIso(job);
                  const startMs = new Date(job.recording_start).getTime();
                  const endMs = endIso ? new Date(endIso).getTime() : startMs + 1;
                  const atMs = new Date(v.waktu).getTime();
                  const pct = endMs > startMs ? Math.min(100, Math.max(0, ((atMs - startMs) / (endMs - startMs)) * 100)) : 0;
                  return <i key={v.id} className={v.jenis === "violation" ? "bad" : "warn"} style={{ left: `${pct}%` }} title={`${v.pekerja} · ${fmtTime(v.waktu)}`} />;
                })}
                </div>
                <div className="rec-timeline-hours muted">
                  {timelineLabels(job).map(({ left, label }) => <span key={`${left}-${label}`} style={{ left: `${left}%` }}>{label}</span>)}
                </div>
                <div className="rec-timeline-legend muted small"><span><i className="bad" />pelanggaran</span><span><i className="warn" />tidak pasti</span></div>
              </div>
            )}
          </div>
        )}

        <div className="av-card rec-kpis">
          <div className="av-kpi"><div className="av-label">Rekaman selesai</div><b className="good">{selesai}</b><div className="muted small">{jobs.length} pekerjaan analisis</div></div>
          <div className="av-kpi"><div className="av-label">Dalam antrean</div><b>{berjalan}</b><div className="muted small">Menunggu, berjalan, atau dijeda</div></div>
          <div className="av-kpi"><div className="av-label">Event rekaman</div><b className={(job?.events ?? 0) > 0 ? "bad" : ""}>{job?.events ?? 0}</b><div className="muted small">{job ? `Dari ${job.camera_id}` : "Belum ada rekaman"}</div></div>
        </div>
        <p className="muted small">
          Analisis rekaman diproses di latar belakang dan tidak membutuhkan pipeline live aktif. Jika ingin video diputar langsung seperti kamera, gunakan mode Simulasi live di Pengaturan.
        </p>
        </section>

        <aside className="av-card rec-events" aria-label="Pelanggaran rekaman">
        <div className="row between wrap">
          <h3>Pelanggaran rekaman</h3>
          {recent.length > 0 && <span className="rec-event-count">{recent.length} event</span>}
        </div>
        {job && (job.status === "berjalan" || job.status === "menunggu") && (
          <p className="muted small rec-live-note"><i className="av-live-dot" aria-hidden="true" /> Daftar bertambah selama analisis berjalan</p>
        )}
        {eventError ? (
          <p className="error small">Gagal memuat event rekaman: {eventError}</p>
        ) : recent.length ? (
          <div className="rec-event-list">
            {recent.map((v) => (
              <button type="button" className="rec-event-row" key={v.id} onClick={() => setOpen(v)} aria-label={`Buka bukti ${v.pekerja}`}>
                {v.foto_crop ? <img src={v.foto_crop} alt="" /> : <span className="rec-event-thumb-empty" />}
                <span className="rec-event-copy">
                  <b className={v.jenis === "violation" ? "bad" : "warn"}>{v.jenis === "violation" ? `Tanpa ${v.apd_kurang.join(", ")}` : `Tidak pasti: ${v.apd_tidak_pasti.join(", ")}`}</b>
                  <span className="muted small">{v.pekerja} · {v.kamera} · {fmtTime(v.waktu)}</span>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="rec-event-empty"><p className="muted small">Belum ada pelanggaran tercatat untuk rekaman ini.</p></div>
        )}
        </aside>
      </div>
      {videoWindowOpen && job && <RecordedVideoDialog key={job.id} job={job} onClose={() => { setVideoWindowJobId(null); videoTriggerRef.current?.focus(); }} />}
      <EvidenceModal v={open} onClose={() => setOpen(null)} />
    </div>
  );
}
