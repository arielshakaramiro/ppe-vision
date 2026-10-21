import { useEffect, useRef, useState } from "react";
import { api, type CameraRow, type Settings as SettingsType, type Zone } from "../../api";
import { useMe } from "../../session";
import { CameraManager } from "./components/CameraManager";
import { ApdIcon } from "../../ApdIcon";
import { GlassSelect } from "../../ui";

const APD_ITEMS = [
  { key: "helmet", label: "Helm" },
  { key: "vest", label: "Rompi" },
  { key: "gloves", label: "Sarung tangan" },
  { key: "shoes", label: "Sepatu" },
];
const PRESETS = [
  { label: "Lengkap", req: ["helmet", "vest", "gloves", "shoes"] },
  { label: "Helm saja", req: ["helmet"] },
  { label: "Rompi saja", req: ["vest"] },
  { label: "Sarung tangan saja", req: ["gloves"] },
  { label: "Sepatu saja", req: ["shoes"] },
  { label: "Helm + rompi", req: ["helmet", "vest"] },
];
const SENSITIVITIES = [
  { key: "ketat", label: "Ketat" },
  { key: "seimbang", label: "Seimbang" },
  { key: "longgar", label: "Longgar" },
] as const;

const cloneSettings = (value: SettingsType) => structuredClone(value);
const cloneZones = (value: Zone[]) => structuredClone(value);
const sameItems = (left: string[], right: string[]) => left.length === right.length && left.every((item) => right.includes(item));

export default function Settings() {
  const me = useMe();
  const isAdmin = me?.role === "admin";
  const [zones, setZones] = useState<Zone[]>([]);
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [zoneCameraId, setZoneCameraId] = useState("");
  const [settings, setSettings] = useState<SettingsType | null>(null);
  const [busy, setBusy] = useState(false);
  const [simResult, setSimResult] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState("Tidak ada perubahan.");
  const [curZoneIndex, setCurZoneIndex] = useState(0);
  const [poly, setPoly] = useState<[number, number][]>([]);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const savedSettings = useRef<SettingsType | null>(null);
  const savedZones = useRef<Zone[]>([]);

  const load = () => {
    api.cameras().then((rows) => {
      setCameras(rows);
      setZoneCameraId((previous) => previous || rows.find((camera) => camera.id === "CAM-01")?.id || rows[0]?.id || "");
    }).catch(() => {});
    api.zones().then((rows) => {
      savedZones.current = cloneZones(rows);
      setZones(rows);
      setCurZoneIndex((previous) => Math.min(previous, Math.max(rows.length - 1, 0)));
    }).catch(() => {});
    api.settings().then((value) => {
      savedSettings.current = cloneSettings(value);
      setSettings(value);
    }).catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const visibleZones = zones;
  const curZone = visibleZones[curZoneIndex];
  const previewCameraId = curZone?.camera_id || zoneCameraId;

  useEffect(() => {
    const nextIndex = Math.min(curZoneIndex, Math.max(visibleZones.length - 1, 0));
    if (curZoneIndex !== nextIndex) setCurZoneIndex(nextIndex);
    setPoly(visibleZones[nextIndex]?.polygon || []);
  }, [zoneCameraId, zones.length, curZoneIndex]);

  const setChanged = () => setSaveStatus("Perubahan belum disimpan.");
  const updateCurrentZone = (changes: Partial<Zone>) => {
    if (!curZone) return;
    setChanged();
    setZones((previous) => previous.map((zone) => zone.name === curZone.name && zone.camera_id === curZone.camera_id ? { ...zone, ...changes } : zone));
  };

  const pointFromEvent = (event: React.PointerEvent<SVGSVGElement>): [number, number] | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    return [
      Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
    ];
  };

  const onSvgPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!isAdmin || (event.target as SVGElement).tagName.toLowerCase() === "circle") return;
    const point = pointFromEvent(event);
    if (!point) return;
    const next = [...poly, point];
    setPoly(next);
    updateCurrentZone({ polygon: next });
  };
  const onPointDown = (event: React.PointerEvent<SVGCircleElement>, index: number) => {
    if (!isAdmin) return;
    event.stopPropagation();
    svgRef.current?.setPointerCapture(event.pointerId);
    setDragIndex(index);
  };
  const onSvgPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    if (dragIndex == null) return;
    const point = pointFromEvent(event);
    if (!point) return;
    setPoly((previous) => {
      const next = previous.map((item, index) => index === dragIndex ? point : item);
      updateCurrentZone({ polygon: next });
      return next;
    });
  };
  const onSvgPointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    if (dragIndex != null && svgRef.current?.hasPointerCapture(event.pointerId)) svgRef.current.releasePointerCapture(event.pointerId);
    setDragIndex(null);
  };

  const resetPoly = () => {
    setPoly([]);
    updateCurrentZone({ polygon: [] });
  };

  const updateSetting = (updater: (value: SettingsType) => void) => {
    setChanged();
    setSettings((previous) => {
      if (!previous) return previous;
      const next = cloneSettings(previous);
      updater(next);
      return next;
    });
  };

  const curZoneReq = new Set(curZone?.required || []);
  const setRequired = (required: string[]) => updateCurrentZone({ required });
  const toggleZoneReq = (item: string) => {
    if (!curZone) return;
    const next = new Set(curZoneReq);
    if (next.has(item)) next.delete(item); else next.add(item);
    if (!next.size) return alert("Minimal satu APD wajib dipilih.");
    setRequired(Array.from(next));
  };

  const copyZoneToCamera = async () => {
    if (!curZone || !zoneCameraId) return;
    if (curZone.camera_id === zoneCameraId) return alert("Zona ini sudah khusus untuk kamera terpilih.");
    try {
      await api.createZone({ ...curZone, camera_id: zoneCameraId, polygon: poly });
      setSaveStatus("Zona berhasil disalin.");
      load();
    } catch (error) {
      alert(`Gagal menyalin zona: ${(error as Error).message}`);
    }
  };

  const addZone = async () => {
    if (!zoneCameraId) return alert("Pilih kamera sebelum menambah zona.");
    const name = window.prompt("Nama zona baru:");
    if (!name?.trim()) return;
    try {
      const created = await api.createZone({
        name: name.trim(), camera_id: zoneCameraId,
        polygon: [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        required: ["helmet", "vest"],
      });
      setZones((previous) => [...previous, created]);
      setSaveStatus("Zona berhasil ditambahkan.");
      load();
    } catch (error) {
      alert(`Gagal menambah zona: ${(error as Error).message}`);
    }
  };

  const deleteCurrentZone = async () => {
    if (!curZone || !confirm(`Hapus zona ${curZone.name}?`)) return;
    try {
      await api.deleteZone(curZone.name, curZone.camera_id || "");
      setCurZoneIndex(0);
      setSaveStatus("Zona berhasil dihapus.");
      load();
    } catch (error) {
      alert(`Gagal menghapus zona: ${(error as Error).message}`);
    }
  };

  const saveZonesAndSettings = async () => {
    if (!isAdmin) return;
    setSaving(true);
    setSaveStatus("Menyimpan perubahan…");
    try {
      for (const [index, zone] of zones.entries()) {
        const originalZone = savedZones.current[index];
        if (!originalZone || JSON.stringify(zone) === JSON.stringify(originalZone)) continue;
        if (zone.polygon.length < 3) throw new Error(`Poligon zona ${zone.name} minimal memiliki 3 titik.`);
        await api.updateZone(originalZone.name, zone, originalZone.camera_id || "");
      }
      if (settings) await api.updateSettings(settings);
      setSaveStatus("Tidak ada perubahan.");
      load();
    } catch (error) {
      alert(`Gagal: ${(error as Error).message}`);
      setSaveStatus("Gagal menyimpan perubahan.");
    } finally {
      setSaving(false);
    }
  };

  const cancelChanges = () => {
    if (savedSettings.current) setSettings(cloneSettings(savedSettings.current));
    setZones(cloneZones(savedZones.current));
    const savedVisible = savedZones.current.filter((zone) => !zoneCameraId || zone.camera_id === "" || zone.camera_id === zoneCameraId);
    setPoly(savedVisible[Math.min(curZoneIndex, Math.max(savedVisible.length - 1, 0))]?.polygon || []);
    setSimResult(null);
    setSaveStatus("Tidak ada perubahan.");
  };

  const simulateRetention = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await api.retentionSimulate();
      setSimResult(result.message);
    } catch (error) {
      alert(`Gagal: ${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  if (!isAdmin) return <div className="av-card"><p className="muted">Halaman ini hanya untuk admin.</p></div>;
  if (!settings) return <p className="muted">Memuat pengaturan…</p>;

  const sensitivity = settings.preset_values[settings.sensitivity];
  const isChanged = saveStatus === "Perubahan belum disimpan." || saveStatus === "Gagal menyimpan perubahan.";

  return (
    <div className="settings-page">
      <div className="settings-heading">
        <h1>Pengaturan deteksi</h1>
        <p className="muted small">Semua perubahan di halaman ini dicatat di audit log atas nama Anda.</p>
      </div>

      <CameraManager />

      <section className="av-card settings-zone-card">
        <h3>Zona dan APD wajib</h3>
        <p className="muted small">Setiap zona punya daftar APD wajib sendiri. Pekerja di luar semua zona tidak dinilai.</p>
        <div className="settings-zone-layout">
          <aside className="settings-zone-list" aria-label="Daftar zona">
            {visibleZones.map((zone, index) => (
              <button key={`${zone.camera_id || "semua"}:${zone.name}`} type="button" className={`settings-zone-item ${index === curZoneIndex ? "on" : ""}`} onClick={() => { setCurZoneIndex(index); setPoly(zone.polygon); if (zone.camera_id) setZoneCameraId(zone.camera_id); }}>
                <b>{zone.name}</b>
                <span>{zone.camera_id || "Semua kamera"} · {zone.required.length === APD_ITEMS.length ? "semua APD wajib" : zone.required.map((item) => APD_ITEMS.find((entry) => entry.key === item)?.label || item).join(" + ")}</span>
              </button>
            ))}
            {!visibleZones.length && <p className="muted small">Belum ada zona untuk kamera ini.</p>}
            <button type="button" className="settings-add-zone" onClick={addZone}>+ Tambah zona</button>
          </aside>

          <div className="settings-zone-editor">
            <div className="settings-zone-stage" style={{ cursor: dragIndex == null ? "crosshair" : "grabbing" }}>
              {previewCameraId && <img src={`/api/stream.mjpg?camera=${encodeURIComponent(previewCameraId)}`} alt="Pratinjau zona kamera" />}
              <svg ref={svgRef} viewBox="0 0 100 100" preserveAspectRatio="none" onPointerDown={onSvgPointerDown} onPointerMove={onSvgPointerMove} onPointerUp={onSvgPointerUp} onPointerCancel={onSvgPointerUp}>
                {poly.length > 0 && <polygon points={poly.map((point) => `${point[0] * 100},${point[1] * 100}`).join(" ")} />}
                {poly.map((point, index) => <circle key={index} cx={point[0] * 100} cy={point[1] * 100} r="1.1" onPointerDown={(event) => onPointDown(event, index)} />)}
              </svg>
            </div>
            <div className="settings-zone-toolbar">
              <button type="button" className="av-btn" onClick={resetPoly}>Gambar ulang poligon</button>
              <span className="muted small">Klik titik-titik sudut di gambar. Seret titik untuk mengubah bentuk.</span>
              {curZone && <button type="button" className="link bad settings-delete-zone" onClick={deleteCurrentZone}>Hapus zona</button>}
            </div>
            {curZone && <>
              <div className="settings-zone-fields">
                <label className="av-field"><span>Nama zona</span><input className="av-input" value={curZone.name} maxLength={64} onChange={(event) => updateCurrentZone({ name: event.target.value })} /></label>
                <label className="av-field"><span>Kamera</span><GlassSelect ariaLabel="Kamera zona" value={curZone.camera_id || ""} onChange={(value) => updateCurrentZone({ camera_id: value })} options={[{ value: "", label: "Semua kamera" }, ...cameras.map((camera) => ({ value: camera.id, label: camera.id, hint: camera.name !== camera.id ? camera.name : undefined }))]} /></label>
              </div>
              {!curZone.camera_id && <p className="note settings-zone-note">Zona ini berlaku untuk semua kamera. Untuk sudut kamera {zoneCameraId}, salin dulu menjadi zona khusus kamera.</p>}
              <div className="settings-required">
                <span className="small muted">APD yang wajib di zona ini</span>
                <div className="settings-apd-list">
                  {APD_ITEMS.map((item) => <button key={item.key} type="button" className={`settings-apd ${curZoneReq.has(item.key) ? "on" : ""}`} aria-pressed={curZoneReq.has(item.key)} onClick={() => toggleZoneReq(item.key)}><span className="settings-apd-icon"><ApdIcon item={item.key} /></span><span><b>{item.label}</b><small>{curZoneReq.has(item.key) ? "wajib" : "tidak wajib"}</small></span></button>)}
                </div>
                <div className="settings-presets"><span className="muted small">Pilihan cepat</span>{PRESETS.map((preset) => <button key={preset.label} type="button" className={sameItems(curZone.required, preset.req) ? "on" : ""} onClick={() => setRequired(preset.req)}>{preset.label}</button>)}</div>
                {!curZone.camera_id && zoneCameraId && <button type="button" className="av-btn settings-copy-zone" onClick={copyZoneToCamera}>Salin ke kamera {zoneCameraId}</button>}
              </div>
            </>}
          </div>
        </div>
      </section>

      <div className="settings-grid">
        <section className="av-card settings-detail-card">
          <h3>Sensitivitas deteksi</h3>
          <p className="muted small">Berlaku untuk semua kamera. Lebih ketat berarti pelanggaran tercatat lebih cepat, tetapi positif palsu lebih banyak.</p>
          <div className="av-seg settings-sensitivity" role="group" aria-label="Sensitivitas deteksi">{SENSITIVITIES.map((item) => <button key={item.key} type="button" className={settings.sensitivity === item.key ? "on" : ""} onClick={() => updateSetting((value) => { value.sensitivity = item.key; })}>{item.label}</button>)}</div>
          <table className="settings-detail-table"><tbody>
            <tr><td>Pelanggaran dicatat setelah stabil</td><td>{sensitivity?.event_min_frames ?? "–"} frame</td></tr>
            <tr><td>Ambang APD terdeteksi</td><td>{sensitivity?.present_threshold?.toLocaleString("id-ID") ?? "–"}</td></tr>
            <tr><td>Ambang APD tidak terdeteksi</td><td>{sensitivity?.missing_threshold?.toLocaleString("id-ID") ?? "–"}</td></tr>
          </tbody></table>
        </section>

        <section className="av-card settings-detail-card">
          <h3>Shift kerja</h3>
          <p className="muted small">Dipakai untuk rekap per shift di Analitik dan laporan K3.</p>
          <div className="table-wrap"><table className="av-table settings-shift-table"><thead><tr><th>Nama</th><th>Mulai</th><th>Selesai</th></tr></thead><tbody>{settings.shifts.map((shift, index) => <tr key={shift.name}><td>{shift.name}</td><td><input className="av-input" aria-label={`Mulai shift ${shift.name}`} type="time" value={shift.start} onChange={(event) => updateSetting((value) => { value.shifts[index].start = event.target.value; })} /></td><td><input className="av-input" aria-label={`Selesai shift ${shift.name}`} type="time" value={shift.end} onChange={(event) => updateSetting((value) => { value.shifts[index].end = event.target.value; })} /></td></tr>)}</tbody></table></div>
        </section>

        <section className="av-card settings-detail-card settings-telegram-card">
          <div className="row between"><h3>Alert Telegram</h3><button type="button" className={`settings-switch ${settings.alert.telegram ? "on" : ""}`} role="switch" aria-checked={settings.alert.telegram} aria-label="Alert Telegram" onClick={() => updateSetting((value) => { value.alert.telegram = !value.alert.telegram; })} /></div>
          <label className="av-field settings-cooldown"><span>Jeda alert per pekerja (detik)</span><input className="av-input" type="number" min={0} max={86400} value={settings.alert.cooldown_s} onChange={(event) => updateSetting((value) => { value.alert.cooldown_s = Number(event.target.value); })} /></label>
          <span className="small muted">Contoh pesan</span>
          <div className="settings-telegram-preview" aria-label="Contoh pesan Telegram">
            <div className="settings-demo-photo"><span>Contoh data</span><svg viewBox="0 0 60 80" aria-hidden="true"><circle cx="30" cy="17" r="9" /><path d="M12 80V46a18 18 0 0 1 36 0v34z" /></svg></div>
            <p className="small">Pelanggaran APD<br />Pekerja W015, CAM-01, Gudang<br />Kurang: Helm, 09.41 WIB</p>
            <div><span>Benar pelanggaran</span><span>Positif palsu</span></div>
          </div>
        </section>

        <section className="av-card settings-detail-card settings-retention-card">
          <h3>Retensi data</h3>
          <p className="muted small">Foto dan klip pekerja adalah data pribadi (UU PDP). File lama dihapus; baris event tetap ada untuk statistik. Event yang belum direview tidak disentuh.</p>
          <div className="settings-retention-fields">
            <label className="av-field"><span>Simpan foto dan klip (hari)</span><input className="av-input" type="number" min={1} max={3650} value={settings.retention.evidence_days} onChange={(event) => updateSetting((value) => { value.retention.evidence_days = Number(event.target.value); })} /></label>
            <label className="av-field"><span>Simpan data kualitas kamera (hari)</span><input className="av-input" type="number" min={1} max={3650} value={settings.retention.quality_days} onChange={(event) => updateSetting((value) => { value.retention.quality_days = Number(event.target.value); })} /></label>
          </div>
          <div className="row wrap gap"><button type="button" className="av-btn" onClick={simulateRetention} disabled={busy}>{busy ? "Memuat simulasi…" : "Simulasi (tanpa menghapus)"}</button><span className="muted small">Jadwal: setiap hari 02.00</span></div>
          {simResult && <p className="note">{simResult}</p>}
        </section>
      </div>

      <div className="settings-savebar">
        <span className="muted small">{saveStatus}</span>
        <button type="button" className="av-btn" onClick={cancelChanges} disabled={!isChanged || saving}>Batalkan</button>
        <button type="button" className="av-btn primary" onClick={saveZonesAndSettings} disabled={!isChanged || saving}>{saving ? "Menyimpan…" : "Simpan perubahan"}</button>
      </div>
    </div>
  );
}