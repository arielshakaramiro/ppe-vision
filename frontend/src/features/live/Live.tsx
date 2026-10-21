import { useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";
import { api, ITEM_LABEL, type CameraRow, type Live as LiveT, type LivePerson, type Violation, type Zone } from "../../api";
import { EvidenceModal } from "../../components";
import { ViolationRow } from "./components/ViolationRow";
import { ApdSelector } from "./components/ApdSelector";
import { WorkerBox, WorkerCard, type PpeItem, type PpeStatus } from "../../ui";
import { useMe } from "../../session";

const MARK: Record<string, string> = { present: "✓", missing: "✕", unknown: "?", not_required: "–" };
const STATUS: Record<string, string> = { compliant: "APD lengkap", violation: "Pelanggaran", uncertain: "Tidak pasti" };
const ITEM_SHORT: Record<keyof LivePerson["checklist"], string> = { helmet: "H", vest: "V", gloves: "G", shoes: "S" };
const ZOOM_LEVELS = [0.25, 0.5, 0.75, 1, 1.5, 2, 4];

function workerBoxLabel(person: LivePerson) {
  const worker = person.pekerja || `W${String(person.worker_id ?? person.track_id).padStart(3, "0")}`;
  const itemStatus = person.status === "violation" ? "missing" : person.status === "uncertain" ? "unknown" : null;
  if (!itemStatus) return worker;
  const items = (Object.keys(ITEM_SHORT) as Array<keyof LivePerson["checklist"]>)
    .filter((item) => person.checklist[item] === itemStatus)
    .map((item) => ITEM_SHORT[item]);
  return items.length ? `${worker} ${itemStatus === "missing" ? "✗" : "?"} ${items.join(" ")}` : worker;
}

const Chevron = ({ up = false }: { up?: boolean }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
    <path d={up ? "M6 15l6-6 6 6" : "M6 9l6 6 6-6"} />
  </svg>
);

const GridIcon = () => (
  <svg className="live-grid-art" viewBox="0 0 80 80" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">
    <rect x="18" y="22" width="19" height="14" rx="3" /><rect x="43" y="22" width="19" height="14" rx="3" />
    <rect x="18" y="42" width="19" height="14" rx="3" /><rect x="43" y="42" width="19" height="14" rx="3" />
    <circle cx="40" cy="39" r="34" strokeDasharray="1.5 4" opacity=".45" />
  </svg>
);

const LocationIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 21s-6.5-5.6-6.5-11A6.5 6.5 0 0 1 18.5 10c0 5.4-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.4" />
  </svg>
);

const BellIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M10 21h4" />
  </svg>
);

const PanelIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
    <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M3.5 14h17" />
  </svg>
);

const ResetZoomIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 9V4h5" /><path d="M20 15v5h-5" /><path d="M4 4l6 6" /><path d="M20 20l-6-6" />
  </svg>
);

function CameraThumbnail({ camera }: { camera: CameraRow }) {
  const [status, setStatus] = useState<"loading" | "loaded" | "error">("loading");
  useEffect(() => setStatus("loading"), [camera.id, camera.running]);

  if (!camera.running) return <span className="live-camera-thumb"><i /></span>;
  return (
    <span className={`live-camera-thumb running ${status}`}>
      <img
        src={`/api/stream.mjpg?camera=${encodeURIComponent(camera.id)}`}
        alt={`Pratinjau ${camera.id}`}
        onLoad={() => setStatus("loaded")}
        onError={() => setStatus("error")}
      />
      {status !== "loaded" && <span className="live-camera-thumb-state">{status === "error" ? "Video tidak tersedia" : "Memuat video"}</span>}
    </span>
  );
}

function CameraTile({ c, zones, onOpen }: { c: CameraRow; zones: string[]; onOpen: (id: string) => void }) {
  const isSim = c.source_kind === "video" && c.mode === "simulasi";
  const src = `/api/stream.mjpg?camera=${encodeURIComponent(c.id)}`;
  const status = c.running ? (isSim ? "Simulasi" : "Live") : c.enabled ? "Terputus" : "Nonaktif";
  const zoneLabel = zones.length ? zones.join(", ") : c.name && c.name !== c.id ? c.name : "Tanpa zona";
  return (
    <button className={`camera-tile${c.running ? " running" : " offline"}`} onClick={() => onOpen(c.id)} aria-label={`Buka ${c.id} ${zoneLabel}`}>
      {c.running ? <img src={src} alt="" aria-hidden="true" /> : <div className="video-off"><span className="live-offline-icon" aria-hidden="true"><span /></span><b>{c.enabled ? "Kamera terputus" : "Kamera nonaktif"}</b></div>}
      <span className="live-camera-tile-scrim" aria-hidden="true" />
      <span className="live-camera-tile-title"><b>{c.id}</b><span>·</span><span>{c.name && c.name !== c.id ? c.name : zoneLabel}</span></span>
      <span className={`live-camera-tile-status${c.running ? " running" : ""}`}><i aria-hidden="true" />{status}</span>
      <span className="live-camera-tile-meta"><span>{zoneLabel}</span><span>{c.running ? `${c.fps} fps · ${c.pemeriksaan_per_detik}×/dtk` : "Klik untuk melihat detail"}</span></span>
    </button>
  );
}

export default function Live({ modelVersion, onNavigate }: { modelVersion?: string; onNavigate?: (tab: string) => void } = {}) {
  const me = useMe();
  const [data, setData] = useState<LiveT | null>(null);
  const [open, setOpen] = useState<Violation | null>(null);
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [cameraFilter, setCameraFilter] = useState<"all" | "active" | "offline">("all");
  const [zoneFilter, setZoneFilter] = useState("all");
  const [gridColumns, setGridColumns] = useState<3 | 4 | 6>(3);
  const [cam, setCam] = useState<string>("");
  const [dockCameraId, setDockCameraId] = useState<string>("");
  const [selectedWorker, setSelectedWorker] = useState<number | null>(null);
  const [cardWorker, setCardWorker] = useState<number | null>(null);
  const [cardClosing, setCardClosing] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [recentOpen, setRecentOpen] = useState(false);
  const [dockHidden, setDockHidden] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [zoomMenuOpen, setZoomMenuOpen] = useState(false);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const pickerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number; moved: boolean; captured: boolean } | null>(null);
  const suppressWorkerClickRef = useRef(false);
  const closeTimerRef = useRef<number | null>(null);

  const loadCameras = () => api.cameras().then(setCameras).catch(() => {});
  useEffect(() => { loadCameras(); }, []);
  useEffect(() => { api.zones().then(setZones).catch(() => {}); }, []);
  useEffect(() => {
    if (!cam && cameras.length) {
      const initialCameraId = cameras.find((camera) => camera.id === "CAM-01")?.id || cameras[0].id;
      setCam(initialCameraId);
      setDockCameraId(initialCameraId);
    }
  }, [cam, cameras]);

  const allView = cam === "__all__";
  const liveCameraId = allView ? (dockCameraId || cameras[0]?.id || "") : cam;
  useEffect(() => {
    if (!allView && cam) setDockCameraId(cam);
  }, [allView, cam]);
  useEffect(() => {
    const load = () => api.live(liveCameraId || undefined).then(setData).catch(() => {});
    load();
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [liveCameraId]);
  useEffect(() => {
    if (!allView) return;
    loadCameras();
    const timer = setInterval(loadCameras, 2000);
    return () => clearInterval(timer);
  }, [allView]);

  useEffect(() => {
    setSelectedWorker(null); setCardWorker(null); setCardClosing(false); setPan({ x: 0, y: 0 }); setZoom(1);
  }, [cam]);
  useEffect(() => {
    if (selectedWorker != null && !data?.people.some((person) => person.track_id === selectedWorker)) {
      setSelectedWorker(null); setCardWorker(null);
    }
  }, [data, selectedWorker]);
  useEffect(() => () => { if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current); }, []);
  useEffect(() => {
    if (!pickerOpen && !zoomMenuOpen) return;
    const onOutside = (event: PointerEvent) => { if (pickerOpen && !pickerRef.current?.contains(event.target as Node)) setPickerOpen(false); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { setPickerOpen(false); setZoomMenuOpen(false); } };
    window.addEventListener("pointerdown", onOutside); window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("pointerdown", onOutside); window.removeEventListener("keydown", onKey); };
  }, [pickerOpen, zoomMenuOpen]);

  const curCam = cameras.find((camera) => camera.id === liveCameraId);
  const isSim = curCam?.source_kind === "video" && curCam?.mode === "simulasi";
  const overlayReady = !!data?.frame?.width && !!data.frame.height && !!data.people.some((person) => person.box);
  const streamParams = new URLSearchParams();
  if (liveCameraId) streamParams.set("camera", liveCameraId);
  if (overlayReady) streamParams.set("clean", "true");
  const streamSrc = `/api/stream.mjpg${streamParams.size ? `?${streamParams}` : ""}`;
  const selected = data?.people.find((person) => person.track_id === cardWorker) ?? null;
  const workerLabel = (person: LivePerson) => person.pekerja || `W${String(person.worker_id ?? person.track_id).padStart(3, "0")}`;
  const workerItems = (person: LivePerson): PpeItem[] => Object.keys(ITEM_LABEL).map((key) => {
    const item = key as keyof typeof person.checklist;
    const raw = person.checklist[item];
    const status: PpeStatus = raw === "not_required" ? "unchecked" : raw as PpeStatus;
    return { key: item, status, confidence: status === "present" ? person.confidence?.[item] : undefined };
  });

  const closeWorkerCard = () => {
    setSelectedWorker(null); setCardClosing(true);
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = window.setTimeout(() => { setCardWorker(null); setCardClosing(false); }, 240);
  };
  const toggleWorker = (trackId: number) => {
    if (suppressWorkerClickRef.current) return;
    if (selectedWorker === trackId) { closeWorkerCard(); return; }
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
    setCardClosing(false); setSelectedWorker(trackId); setCardWorker(trackId);
  };
  const selectCamera = (id: string) => { setDockCameraId(id); setCam(id); setPickerOpen(false); };
  const setZoomLevel = (level: number) => { setZoom(level); setZoomMenuOpen(false); setPan({ x: 0, y: 0 }); };
  const stepZoom = (direction: 1 | -1) => {
    const current = ZOOM_LEVELS.reduce((best, value, index) => Math.abs(value - zoom) < Math.abs(ZOOM_LEVELS[best] - zoom) ? index : best, 0);
    setZoomLevel(ZOOM_LEVELS[Math.max(0, Math.min(ZOOM_LEVELS.length - 1, current + direction))]);
  };
  const onWheel = (event: WheelEvent<HTMLDivElement>) => { event.preventDefault(); stepZoom(event.deltaY < 0 ? 1 : -1); };
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const interactive = event.target instanceof Element ? event.target.closest("button, a, input, select, textarea, [role='button']") : null;
    const workerBox = !!interactive?.classList.contains("av-box");
    if (interactive && !workerBox) return;
    if (!workerBox) event.currentTarget.setPointerCapture(event.pointerId);
    suppressWorkerClickRef.current = false;
    dragRef.current = { x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y, moved: false, captured: !workerBox };
    event.currentTarget.classList.add("dragging");
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    const deltaX = event.clientX - dragRef.current.x;
    const deltaY = event.clientY - dragRef.current.y;
    if (Math.hypot(deltaX, deltaY) > 4) {
      dragRef.current.moved = true;
      suppressWorkerClickRef.current = true;
      if (!dragRef.current.captured) {
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current.captured = true;
      }
    }
    const limitX = Math.max(event.currentTarget.clientWidth * .25, event.currentTarget.clientWidth * Math.abs(zoom - 1) / 2);
    const limitY = Math.max(event.currentTarget.clientHeight * .25, event.currentTarget.clientHeight * Math.abs(zoom - 1) / 2);
    setPan({ x: Math.max(-limitX, Math.min(limitX, dragRef.current.panX + deltaX)), y: Math.max(-limitY, Math.min(limitY, dragRef.current.panY + deltaY)) });
  };
  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const moved = dragRef.current?.moved;
    dragRef.current = null;
    event.currentTarget.classList.remove("dragging");
    if (moved) window.setTimeout(() => { suppressWorkerClickRef.current = false; }, 0);
  };

  const kpi = data?.kpi;
  const people = data?.people ?? [];
  const compliant = people.filter((person) => person.status === "compliant").length;
  const uncertain = people.filter((person) => person.status === "uncertain").length;
  const violation = people.filter((person) => person.status === "violation").length;
  const total = Math.max(people.length, 1);
  const stopped = cameras.filter((camera) => !camera.running).length;
  const active = cameras.length - stopped;
  const cameraZones = (cameraId: string) => zones.filter((zone) => !zone.camera_id || zone.camera_id === cameraId).map((zone) => zone.name);
  const zoneOptions = [...new Set(zones.map((zone) => zone.name))].sort((a, b) => a.localeCompare(b, "id-ID"));
  const filteredCameras = cameras.filter((camera) => {
    if (cameraFilter === "active" && !camera.running) return false;
    if (cameraFilter === "offline" && camera.running) return false;
    return zoneFilter === "all" || cameraZones(camera.id).includes(zoneFilter);
  });
  const frameRatio = data?.frame?.width && data.frame.height ? `${data.frame.width} / ${data.frame.height}` : "16 / 9";
  const canvasStyle = { "--live-frame-ratio": frameRatio, transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) scale(${zoom})`, "--live-inverse-zoom": 1 / zoom } as CSSProperties;
  const openCameraPicker = () => { if (allView) setCam(liveCameraId); setPickerOpen(true); };
  const openRecentViolations = () => { if (allView) setCam(liveCameraId); setRecentOpen(true); };
  const liveDock = (
    <>
      <button type="button" className={`live-dock-show${dockHidden ? " on" : ""}`} onClick={() => setDockHidden(false)} aria-label="Tampilkan panel bawah"><Chevron up /></button>
      <div className={`live-dock${allView ? " live-dock-grid" : ""}`}>
        <button type="button" className="live-dock-handle" onClick={() => setDockHidden(true)} aria-label="Sembunyikan panel bawah"><Chevron /></button>
        <article className="live-camera-card live-glass-panel"><div className="live-card-head"><b>{liveCameraId}{curCam?.name && curCam.name !== liveCameraId ? ` · ${curCam.name}` : ""}</b><small>{isSim ? "⌁ Simulasi" : data?.running ? "Live" : "Terputus"}</small></div><button type="button" className="live-camera-preview" onClick={openCameraPicker} aria-label="Kelola kamera">{data?.running ? <img src={streamSrc} alt="" aria-hidden="true" /> : <span className="live-camera-placeholder" />}<span>Kelola kamera</span></button><div className="live-camera-foot"><span>{kpi?.pekerja_terpantau ?? 0} pekerja</span><span>{curCam?.fps ?? data?.fps ?? 0} fps</span><span>{curCam?.pemeriksaan_per_detik ?? 0}×/dtk</span></div></article>

        <div className="live-control-panel live-glass-panel">
          <ApdSelector cameraId={liveCameraId || undefined} compact />
          <div className="live-crowd-card"><span className="live-tile-head">Pekerja di frame</span><span className="live-crowd-value"><b>{kpi?.pekerja_terpantau ?? 0}</b> pekerja</span><span className="live-segment-bar" aria-label={`${compliant} patuh, ${uncertain} tidak pasti, ${violation} melanggar`}><i className="good" style={{ width: `${compliant / total * 100}%` }} /><i className="warn" style={{ width: `${uncertain / total * 100}%` }} /><i className="bad" style={{ width: `${violation / total * 100}%` }} /></span><span className="muted small">{compliant} patuh · {violation} melanggar</span></div>
          <aside className="live-recent-tile"><div className="live-card-head"><span>Pelanggaran terbaru</span><button type="button" onClick={openRecentViolations}>Lihat semua</button></div><div className="live-event-thumbs">{data?.recent.slice(0, 3).map((event) => <button type="button" key={event.id} onClick={() => setOpen(event)}><span>{event.foto_crop ? <img src={event.foto_crop} alt="" /> : "!"}</span><small>{event.pekerja || `#${event.id}`}</small></button>)}{!data?.recent.length && <span className="muted small">Belum ada event</span>}</div></aside>
          <div className="live-kpis-mini" aria-label="Ringkasan keselamatan"><span><small>Pelanggaran aktif</small><b>{kpi?.pelanggaran_aktif ?? 0}</b></span><span><small>Safety score</small><b>{kpi?.safety_score != null ? `${kpi.safety_score}%` : "–"}</b></span></div>
          {!!people.length && <details className="live-worker-details"><summary>Lihat pekerja</summary><div className="live-worker-list">{people.map((person) => <div key={`${person.worker_id ?? person.track_id}-${person.track_id}`}><span><b>{workerLabel(person)}</b><small>{person.zone || "Tanpa zona"}</small></span><span className={person.status === "violation" ? "bad" : person.status === "compliant" ? "good" : "warn"}>{STATUS[person.status]}</span><span className="live-worker-marks">{Object.keys(ITEM_LABEL).map((item) => <i key={item} className={person.checklist[item as keyof typeof person.checklist]}>{MARK[person.checklist[item as keyof typeof person.checklist]]}</i>)}</span></div>)}</div></details>}
        </div>

        <aside className="live-camera-picker live-glass-panel" aria-label="Kamera prioritas"><div className="live-camera-options">{cameras.slice(0, 3).map((camera) => <button key={camera.id} type="button" className={!allView && liveCameraId === camera.id ? "on" : ""} onClick={() => selectCamera(camera.id)} aria-pressed={!allView && liveCameraId === camera.id}><span className="live-card-head"><b>{camera.id}</b><small>{camera.running ? `${camera.fps} fps` : "Terputus"}</small></span><CameraThumbnail camera={camera} /></button>)}<button type="button" className={`live-all-cameras${allView ? " on" : ""}`} onClick={() => { setDockCameraId(liveCameraId); setCam("__all__"); }} aria-pressed={allView}><span className="live-card-head"><b>Semua kamera</b><small>{cameras.length}</small></span><GridIcon /></button></div></aside>
      </div>
    </>
  );

  return (
    <div className={`live-page${allView ? " live-page-grid" : ""}`}>
      {allView ? (
        <section className={`live-all-view${dockHidden ? " dock-hidden" : ""}`}>
          <div className="live-all-scrim" aria-hidden="true" />
          <div className="live-all-strip">
            <button className="live-camera-trigger" type="button" onClick={() => selectCamera(liveCameraId)} aria-label="Kembali ke live">
              <GridIcon /><h1>Semua kamera</h1><i>/</i><span>{cameras.length} kamera</span><Chevron up />
            </button>
            <span className="live-summary-chip">{cameras.length} kamera · {active} aktif · {stopped} terputus</span>
            <span className="live-pipeline-chip"><i className={active ? "running" : ""} />{active ? "Pipeline berjalan" : "Pipeline berhenti"} · model {modelVersion || "–"}</span>
          </div>
          <div className="live-all-toolbar">
            <div className="live-all-filters" role="group" aria-label="Saring status kamera">
              <button type="button" className={cameraFilter === "all" ? "on" : ""} onClick={() => setCameraFilter("all")} aria-pressed={cameraFilter === "all"}>Semua {cameras.length}</button>
              <button type="button" className={cameraFilter === "active" ? "on" : ""} onClick={() => setCameraFilter("active")} aria-pressed={cameraFilter === "active"}>Aktif {active}</button>
              <button type="button" className={cameraFilter === "offline" ? "on" : ""} onClick={() => setCameraFilter("offline")} aria-pressed={cameraFilter === "offline"}>Terputus {stopped}</button>
            </div>
            <label className="live-zone-filter"><span className="sr-only">Saring berdasarkan zona</span><select value={zoneFilter} onChange={(event) => setZoneFilter(event.target.value)}><option value="all">Semua zona</option>{zoneOptions.map((zone) => <option key={zone} value={zone}>{zone}</option>)}</select><Chevron /></label>
            <div className="live-grid-columns" role="group" aria-label="Jumlah kamera per baris">
              {([3, 4, 6] as const).map((columns) => <button key={columns} type="button" className={gridColumns === columns ? "on" : ""} onClick={() => setGridColumns(columns)} aria-pressed={gridColumns === columns}>{columns} per baris</button>)}
            </div>
            <span className="live-all-info">{filteredCameras.length} kamera · scroll untuk melihat semua</span>
          </div>
          <div className="live-camera-grid" style={{ "--live-grid-columns": gridColumns } as CSSProperties}>
            {filteredCameras.map((camera) => <CameraTile key={camera.id} c={camera} zones={cameraZones(camera.id)} onOpen={selectCamera} />)}
            {!filteredCameras.length && <div className="live-camera-empty"><GridIcon /><b>Tidak ada kamera</b><span>Ubah filter status atau zona untuk menampilkan kamera lain.</span></div>}
          </div>
          {liveDock}
        </section>
      ) : (
        <section className={`live-hud${dockHidden ? " dock-hidden" : ""}`}>
          <div className={`live-stage-frame pannable${zoom > 1 ? " zoomed-in" : zoom < 1 ? " zoomed-out" : ""}`} onWheel={onWheel} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
            <div className="av-stage live-stage" style={canvasStyle}>
              {data?.running ? <img src={streamSrc} alt={`Siaran langsung ${cam || "kamera"}`} draggable={false} /> : <div className="video-off"><b>Pipeline belum berjalan</b><span className="muted small">{data?.error ?? "Jalankan backend dengan RUN_PIPELINE=1 dan pastikan ada kamera aktif. Kelola kamera di Pengaturan."}</span></div>}
              {overlayReady && people.map((person) => person.box && <WorkerBox key={person.track_id} box={person.box} tone={person.status === "violation" ? "bad" : person.status === "uncertain" ? "warn" : "good"} label={workerBoxLabel(person)} muted={person.status === "compliant"} selected={selectedWorker === person.track_id} onClick={() => toggleWorker(person.track_id)} ariaLabel={`Buka detail pekerja ${workerLabel(person)}`} />)}
            </div>
          </div>
          {selected && <div className={`live-worker-card-layer${zoom > 1 ? " zoomed-in" : zoom < 1 ? " zoomed-out" : ""}${cardClosing ? " closing" : ""}`}><WorkerCard id={workerLabel(selected)} person={selected.box!} kind={selected.status === "violation" ? "violation" : selected.status === "uncertain" ? "uncertain" : "ok"} subtitle={<>{selected.zone || "Tanpa zona"} · sejak {new Date(selected.since).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}</>} items={workerItems(selected)} onClose={closeWorkerCard} gap={18} /></div>}
          <div className="live-scrim live-scrim-top" aria-hidden="true" /><div className="live-scrim live-scrim-bottom" aria-hidden="true" />

          <div className="live-strip" aria-label="Status live">
            <div className="live-picker-wrap" ref={pickerRef}>
              <button className="live-camera-trigger" type="button" aria-haspopup="dialog" aria-expanded={pickerOpen} onClick={() => setPickerOpen((value) => !value)}><b>{cam || "Live"}</b>{curCam?.name && curCam.name !== cam ? <><i>/</i><span>{curCam.name}</span></> : null}<Chevron /></button>
              <div className={`live-picker-pop${pickerOpen ? " open" : ""}`} role="dialog" aria-label="Pilih kamera">
                <span className="av-label">Kamera live</span>
                <button type="button" className="live-picker-row all" onClick={() => selectCamera("__all__")}><GridIcon /><span><b>Semua kamera</b><small>{cameras.length} kamera tersedia</small></span></button>
                <div className="live-picker-list">{cameras.map((camera) => <button type="button" key={camera.id} className={`live-picker-row${cam === camera.id ? " on" : ""}`} onClick={() => selectCamera(camera.id)}><i className={`live-camera-dot${camera.running ? " running" : ""}`} /><span><b>{camera.id}</b><small>{camera.name || "Tanpa nama"}</small></span><em>{camera.running ? `${camera.fps} fps` : "Terputus"}</em></button>)}</div>
              </div>
            </div>
            <span className="live-summary-chip">{cameras.length} kamera · {kpi?.pelanggaran_aktif ?? 0} ada pelanggaran · {stopped} terputus</span>
            {isSim && <span className="live-source-chip">Video simulasi · {curCam?.pemeriksaan_per_detik ?? 0}×/dtk</span>}
            <span className="live-pipeline-chip"><i className={`lamp ${data?.running ? "baik" : "peringatan"}`} />Pipeline {data?.running ? "berjalan" : "berhenti"} · model {modelVersion || data?.recent[0]?.versi_model || "–"}</span>
            <span className="live-active-chip">{kpi?.pelanggaran_aktif ?? 0} pelanggaran aktif</span>
            <div className="live-strip-tools">
              <button type="button" className="live-location-icon" onClick={() => onNavigate?.("pengaturan")} disabled={me?.role !== "admin"} aria-label="Atur zona dan kamera" title={me?.role === "admin" ? "Atur zona dan kamera" : "Hanya admin yang dapat mengatur zona dan kamera"}><LocationIcon /></button>
              <button type="button" className={recentOpen ? "on" : ""} onClick={() => setRecentOpen((value) => !value)} aria-label={recentOpen ? "Sembunyikan pelanggaran terbaru" : "Tampilkan pelanggaran terbaru"} aria-pressed={recentOpen}><BellIcon />{data?.recent.length ? <b>{data.recent.length}</b> : null}</button>
              <button type="button" className={dockHidden ? "on" : ""} onClick={() => setDockHidden((value) => !value)} aria-label={dockHidden ? "Tampilkan panel bawah" : "Sembunyikan panel bawah"} aria-pressed={dockHidden}><PanelIcon /></button>
            </div>
          </div>

          <aside className={`live-recent-drawer${recentOpen ? " open" : ""}`} aria-hidden={!recentOpen}><div className="live-card-head"><h3>Pelanggaran terbaru</h3><button type="button" className="av-icon-btn" onClick={() => setRecentOpen(false)} aria-label="Tutup pelanggaran terbaru">✕</button></div><div className="live-recent-list">{data?.recent.length ? data.recent.map((event) => <ViolationRow key={event.id} v={event} onOpen={setOpen} />) : <p className="muted small">Belum ada pelanggaran tercatat hari ini.</p>}</div></aside>

          <div className="live-zoom" role="group" aria-label="Zoom video"><button type="button" onClick={() => stepZoom(1)} disabled={zoom >= 4} aria-label="Perbesar">+</button><button type="button" className="live-zoom-value" aria-label="Pilih ukuran zoom" aria-expanded={zoomMenuOpen} onClick={() => setZoomMenuOpen((value) => !value)}>{Math.round(zoom * 100)}%</button><button type="button" onClick={() => stepZoom(-1)} disabled={zoom <= .25} aria-label="Perkecil">−</button><button type="button" onClick={() => setZoomLevel(1)} disabled={zoom === 1 && pan.x === 0 && pan.y === 0} aria-label="Kembali ke ukuran semula"><ResetZoomIcon /></button><div className={`live-zoom-menu${zoomMenuOpen ? " open" : ""}`} role="menu" aria-label="Ukuran zoom"><span className="av-label">Ukuran video</span>{ZOOM_LEVELS.map((level) => <button type="button" key={level} role="menuitemradio" aria-checked={zoom === level} className={zoom === level ? "on" : ""} onClick={() => setZoomLevel(level)}>{Math.round(level * 100)}%{level === 1 ? " · ukuran semula" : ""}</button>)}</div></div>
          {zoom !== 1 && <button type="button" className="live-zoom-reset" onClick={() => setZoomLevel(1)} aria-label="Kembali ke ukuran semula"><ResetZoomIcon />Kembali ke ukuran semula · {Math.round(zoom * 100)}%</button>}

          {liveDock}
        </section>
      )}
      <EvidenceModal v={open} onClose={() => setOpen(null)} />
    </div>
  );
}
