import { useEffect, useRef, useState } from "react";
import { api, fmtNum, type CameraRow, type ServerVideo, type VideoRow } from "../../../api";
import { GlassSelect } from "../../../ui";
import { uploadVideo } from "../../recordings/utils/uploadVideo";

const EMPTY = { id: "", name: "", url: "", video_id: "", mode: "simulasi", recording_start: "", sample_rate: "5" };

export function CameraManager() {
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [videos, setVideos] = useState<VideoRow[]>([]);
  const [serverVideos, setServerVideos] = useState<ServerVideo[]>([]);
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [kind, setKind] = useState<"stream" | "video">("stream");
  const [form, setForm] = useState(EMPTY);
  const [err, setErr] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [curFile, setCurFile] = useState<File | null>(null);
  const stopRef = useRef(false);
  const uploadIdRef = useRef<string | null>(null);

  const load = () => {
    api.cameras().then(setCameras).catch(() => {});
    api.videos().then(setVideos).catch(() => {});
    api.serverVideos().then(setServerVideos).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const runUpload = async (file: File) => {
    setErr(null); setPct((p) => p ?? 0); stopRef.current = false; setUploading(true);
    try {
      const vid = await uploadVideo(file, uploadIdRef, setPct, () => stopRef.current);
      setForm((f) => ({ ...f, video_id: vid }));
      setPct(100); setCurFile(null);
      load();
    } catch (e) {
      if ((e as Error).message !== "dijeda") { setErr(`Upload gagal: ${(e as Error).message}`); setPct(null); }
    } finally {
      setUploading(false);
    }
  };

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    uploadIdRef.current = null;  // file baru -> sesi upload baru
    setCurFile(file);
    runUpload(file);
  };

  const registerServer = async (path: string) => {
    setErr(null);
    try {
      const v = await api.registerVideo(path);
      setForm((f) => ({ ...f, video_id: v.id }));
      load();
    } catch (e) {
      setErr(`Gagal mendaftarkan video: ${(e as Error).message}`);
    }
  };

  const testConn = async () => {
    setTestMsg("Menguji…");
    try {
      // Endpoint uji memakai model kamera lengkap, jadi id/name wajib ikut.
      const r = await api.testCamera(form.id || "CAM-uji", { id: form.id || "CAM-uji", name: form.name || "uji",
        source_kind: "stream", url: form.url });
      setTestMsg(r.pesan);
    } catch (e) {
      setTestMsg(`Gagal: ${(e as Error).message}`);
    }
  };

  const openCreate = () => {
    setEditingId(null);
    setForm(EMPTY);
    setKind("stream");
    setErr(null);
    setTestMsg(null);
    setOpen(true);
  };

  const openEdit = (camera: CameraRow) => {
    setEditingId(camera.id);
    setKind(camera.source_kind);
    setForm({
      id: camera.id,
      name: camera.name,
      url: "",
      video_id: camera.video_id,
      mode: camera.mode || "simulasi",
      recording_start: camera.recording_start?.slice(0, 16) || "",
      sample_rate: String(camera.sample_rate || 5),
    });
    setErr(null);
    setTestMsg(null);
    setOpen(true);
  };

  const closeForm = () => {
    setOpen(false);
    setEditingId(null);
    setForm(EMPTY);
    setErr(null);
    setTestMsg(null);
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const body: Record<string, unknown> = { id: form.id, name: form.name, source_kind: kind };
    if (kind === "stream") body.url = form.url;
    else {
      body.video_id = form.video_id;
      body.mode = form.mode;
      if (form.mode === "analisis") {
        body.recording_start = form.recording_start || null;
        body.sample_rate = Number(form.sample_rate);
      }
    }
    try {
      if (editingId) {
        const patch = { ...body };
        delete patch.id;
        if (kind === "stream" && !form.url) delete patch.url;
        await api.updateCamera(editingId, patch);
      } else {
        await api.createCamera(body);
      }
      closeForm();
      setPct(null); setCurFile(null); uploadIdRef.current = null;
      load();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const toggle = async (c: CameraRow) => {
    await api.updateCamera(c.id, { enabled: !c.enabled }).then(load).catch((e) => alert(e.message));
  };

  const remove = async (id: string) => {
    if (!confirm(`Hapus kamera ${id}? Zona dan riwayatnya tidak ikut terhapus.`)) return;
    await api.deleteCamera(id).then(load).catch((e) => alert(e.message));
  };

  return (
    <section className="av-card settings-camera-card">
      <div className="row between wrap">
        <h3 style={{ margin: 0 }}>Kamera</h3>
        <button className="av-btn settings-camera-add" type="button" onClick={open ? closeForm : openCreate}>{open ? "Tutup" : "Tambah kamera"}</button>
      </div>
      <p className="muted small settings-camera-copy">Semua kamera memakai satu model yang sama dan diproses serentak. Setiap kamera punya zona, pelacak pekerja, dan sambungan ulang sendiri. Menambah kamera mengurangi pemeriksaan per detik untuk semua kamera.</p>

      <div className="table-wrap settings-camera-table-wrap">
        <table className="av-table settings-camera-table">
          <thead><tr><th>Kamera</th><th>Sumber</th><th>Diperiksa</th><th>Aktif</th><th><span className="sr-only">Aksi</span></th></tr></thead>
          <tbody>
            {cameras.map((c) => (
              <tr key={c.id}>
                <td><span className={`settings-camera-dot ${!c.enabled ? "off" : c.running ? "online" : "warning"}`} aria-hidden="true" /><b>{c.id}</b>{c.name !== c.id && <span className="muted small"> {c.name}</span>}</td>
                <td><span className="settings-source-kind">{c.source_kind === "video" ? c.mode === "analisis" ? "Video, analisis rekaman" : "Video, simulasi live" : c.sumber.startsWith("http") ? "Kamera HTTP" : "Kamera RTSP"}</span> <span className="muted small settings-source-value">{c.sumber || "Sumber tidak tersedia"}</span></td>
                <td className="settings-camera-rate">{c.running && c.pemeriksaan_per_detik > 0 ? `${fmtNum(c.pemeriksaan_per_detik)}×/dtk` : c.enabled ? "Belum berjalan" : "Nonaktif"}</td>
                <td><button type="button" className={`settings-switch ${c.enabled ? "on" : ""}`} role="switch" aria-checked={c.enabled} aria-label={`${c.enabled ? "Nonaktifkan" : "Aktifkan"} kamera ${c.id}`} onClick={() => toggle(c)} /></td>
                <td><div className="settings-camera-actions">
                  <button className="link small" type="button" onClick={() => openEdit(c)}>Ubah</button>
                  {c.source_kind === "stream" && <button className="link small" type="button" onClick={() => openEdit(c)}>Uji koneksi</button>}
                  <button className="link small bad" type="button" onClick={() => remove(c.id)}>Hapus</button>
                </div></td>
              </tr>
            ))}
            {!cameras.length && <tr><td colSpan={5} className="muted small">Belum ada kamera.</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="muted small settings-camera-security">Kredensial pada URL kamera disimpan terenkripsi dan tidak pernah ditampilkan kembali.</p>

      {open && (
        <form className="panel settings-camera-form" onSubmit={save}>
          <h4>{editingId ? `Ubah kamera ${editingId}` : "Tambah kamera"}</h4>
          <div className="av-seg" role="group" aria-label="Jenis sumber">
            <button type="button" className={kind === "stream" ? "on" : ""} onClick={() => setKind("stream")}>Kamera RTSP/HTTP</button>
            <button type="button" className={kind === "video" ? "on" : ""} onClick={() => setKind("video")}>Video</button>
          </div>
          <div className="form-grid">
            <label className="av-field"><span>ID kamera (mis. CAM-02)</span>
              <input className="av-input" required maxLength={32} disabled={Boolean(editingId)} value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} /></label>
            <label className="av-field"><span>Nama lokasi</span>
              <input className="av-input" required maxLength={64} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          </div>

          {kind === "stream" && (
            <>
              <label className="av-field"><span>URL kamera (RTSP/HTTP)</span>
                <input className="av-input" required={!editingId} maxLength={500} value={form.url} autoComplete="off"
                  placeholder={editingId ? "Kosongkan bila URL tidak diubah" : "rtsp://user:password@192.168.1.64:554/stream1"}
                  onChange={(e) => setForm({ ...form, url: e.target.value })} /></label>
              <div className="row gap wrap">
                <button type="button" className="av-btn" onClick={testConn} disabled={!form.url}>Uji koneksi</button>
                {testMsg && <span className="small muted">{testMsg}</span>}
              </div>
              <p className="muted small">URL disimpan terenkripsi dan tidak pernah ditampilkan kembali utuh.</p>
            </>
          )}

          {kind === "video" && (
            <>
              <label className="av-field"><span>Unggah video baru</span>
                <input type="file" accept="video/*" onChange={onFile} disabled={uploading} /></label>
              {pct !== null && (
                <div className="row gap">
                  <div className="bar-track" style={{ flex: 1 }}><div className="bar-fill" style={{ width: `${pct}%` }} /></div>
                  <span className="small">{pct}%</span>
                  {uploading && pct < 100 && <button type="button" className="link small" onClick={() => (stopRef.current = true)}>Jeda</button>}
                  {!uploading && pct < 100 && curFile && <button type="button" className="link small" onClick={() => runUpload(curFile)}>Lanjutkan</button>}
                </div>
              )}
              <label className="av-field"><span>Atau pilih video hasil upload</span>
                <GlassSelect
                  ariaLabel="Pilih video hasil upload"
                  placeholder="— pilih video —"
                  value={form.video_id}
                  onChange={(v) => setForm({ ...form, video_id: v })}
                  options={[{ value: "", label: "— pilih video —" }, ...videos.map((v) => ({ value: v.id, label: `${v.label} (${fmtNum(v.duration_s / 60)} mnt)` }))]}
                /></label>
              {serverVideos.length > 0 && (
                <div className="av-field"><span>Video di folder server (belum terdaftar)</span>
                  <div className="row gap wrap">
                    {serverVideos.map((v) => (
                      <button key={v.path} type="button" className="av-btn sm" onClick={() => registerServer(v.path)}>
                        Daftarkan {v.path}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="av-seg" role="group" aria-label="Mode video">
                <button type="button" className={form.mode === "simulasi" ? "on" : ""} onClick={() => setForm({ ...form, mode: "simulasi" })}>Simulasi live</button>
                <button type="button" className={form.mode === "analisis" ? "on" : ""} onClick={() => setForm({ ...form, mode: "analisis" })}>Analisis rekaman</button>
              </div>
              {form.mode === "analisis" && (
                <div className="form-grid">
                  <label className="av-field"><span>Jam mulai rekaman</span>
                    <input className="av-input" type="datetime-local" value={form.recording_start} onChange={(e) => setForm({ ...form, recording_start: e.target.value })} /></label>
                  <label className="av-field"><span>Pemeriksaan per detik</span>
                    <GlassSelect
                      ariaLabel="Pemeriksaan per detik"
                      value={form.sample_rate}
                      onChange={(v) => setForm({ ...form, sample_rate: v })}
                      options={[{ value: "5", label: "5×/detik" }, { value: "10", label: "10×/detik" }]}
                    /></label>
                </div>
              )}
            </>
          )}

          {err && <p className="error">{err}</p>}
          <div className="row gap"><button className="av-btn primary">Simpan kamera</button><button type="button" className="av-btn" onClick={closeForm}>Batalkan</button></div>
        </form>
      )}
    </section>
  );
}
